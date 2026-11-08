"""Offline coverage of GUI snapshots, HTTP boundaries and scrape decisions."""

from __future__ import annotations

from copy import deepcopy
import http.client
import json
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import paths
from gui import services
from gui.jobs import Jobs, ScrapeJob
from gui.server import WorkspaceServer
from scraping import scraper, store
from scraping.parser import Comment, ThreadRef


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        env = patch.dict(
            os.environ,
            {
                "BAKUSAI_DATA_ROOT": self.directory.name + "/data",
                "BAKUSAI_VAR_ROOT": self.directory.name + "/var",
            },
        )
        env.start()
        self.addCleanup(env.stop)
        self.ref = ThreadRef(
            3,
            103,
            1,
            "100",
            "測試店",
            board_label="東京風俗・お店",
            description="ナナ 開串文",
            reply_count=4,
        )
        self.payload = store.merge(
            None,
            [
                (
                    self.ref,
                    [
                        Comment(1, "2025/08/29 23:59", "ナナ 舊留言"),
                        Comment(2, "2025/08/30 00:00", "ナナ 腕にタトゥー >>3 >>1"),
                        Comment(3, "2025/08/31 23:59", "黒い服の子"),
                        Comment(
                            4, "2025/08/31 12:00", "其他人 <script>alert(1)</script>"
                        ),
                    ],
                )
            ],
            "測試店",
            3,
        )
        store.save(paths.bakusai_path(3, "測試店"), self.payload)
        store.save_info(paths.info_path(3, "測試店"), self.payload)
        self.data = {
            "acode": 3,
            "store": "測試店",
            "start": "2025-08-30",
            "end": "2025-08-31",
            "mode": "name",
            "target": "ナナ",
            "depth": 0,
        }
        self.workspace = services.Workspace()

    def test_store_summary_and_partial_status(self):
        result = services.store_list()
        self.assertEqual(len(result["regions"]), 3)
        self.assertEqual(result["stores"][0]["count"], 4)
        self.assertTrue(result["stores"][0]["complete"])
        incomplete = deepcopy(self.payload)
        incomplete["scrape_meta"]["last_run_completed"] = False
        store.save_info(paths.info_path(3, "測試店"), incomplete)
        self.assertFalse(services.store_list()["stores"][0]["complete"])

    def test_store_without_info_and_corrupt_store_are_visible(self):
        paths.info_path(3, "測試店").unlink()
        self.assertEqual(services.store_list()["stores"][0]["count"], 4)
        paths.bakusai_path(3, "測試店").write_text("broken", encoding="utf-8")
        result = services.store_list()["stores"][0]
        self.assertFalse(result["complete"])
        self.assertTrue(result["error"])

    def test_partial_scrape_takes_precedence_over_stale_complete_info(self):
        partial = deepcopy(self.payload)
        partial["scrape_meta"]["last_run_completed"] = False
        partial["total_comment_count"] = 5
        store.save(paths.bakusai_path(3, "測試店"), partial)
        # Equal timestamps are deliberately treated as potentially stale too.
        stamp = paths.bakusai_path(3, "測試店").stat().st_mtime_ns
        os.utime(paths.info_path(3, "測試店"), ns=(stamp, stamp))
        summary = services.store_list()["stores"][0]
        self.assertFalse(summary["complete"])
        self.assertEqual(summary["count"], 5)

    def test_generate_preserves_scoped_snapshot_and_does_not_write(self):
        before = paths.bakusai_path(3, "測試店").read_bytes()
        result = self.workspace.generate(self.data)
        self.assertEqual(result["count"], 1)
        self.assertIn("T1-2", result["prompt"])
        self.assertNotIn("ナナ 舊留言", result["prompt"])
        self.assertNotIn("ナナ 開串文", result["prompt"])
        self.assertEqual(result["characters"], len(result["prompt"]))
        self.assertEqual(before, paths.bakusai_path(3, "測試店").read_bytes())
        # Updating disk after generation must not change what the prompt sent.
        changed = deepcopy(self.payload)
        changed["comments"] = []
        store.save(paths.bakusai_path(3, "測試店"), changed)
        checked = self.workspace.verify(
            {
                **self.data,
                "session": result["session"],
                "report": "T1-2 T1-4 T1-1 T1-0 T9-999",
            }
        )
        self.assertEqual(
            [c["status"] for c in checked["citations"]],
            ["known", "not_sent", "outside_period", "outside_period", "missing"],
        )
        self.assertEqual(checked["known"], 1)
        self.assertEqual(checked["missing"], 1)

    def test_discovery_does_not_filter_by_literal_features(self):
        for mode in ("features", "roster"):
            result = self.workspace.generate(
                {**self.data, "mode": mode, "target": "穿白衣的女生"}
            )
            self.assertEqual(result["count"], 3)
            self.assertIn("T1-4", result["prompt"])
            self.assertNotIn("T1-1 2025", result["prompt"])

    def test_two_step_only_accepts_sent_ids_and_stays_in_period(self):
        first = self.workspace.generate({**self.data, "two_step": True})
        self.assertEqual(first["stage"], "filter")
        second = self.workspace.refine(
            {**self.data, "session": first["session"], "answer": "T1-2 T1-4 T1-1 T8-99"}
        )
        self.assertEqual(second["stage"], "report")
        self.assertEqual(second["count"], 2)
        self.assertIn("T1-3", second["prompt"])
        self.assertNotIn("T1-1 2025", second["prompt"])
        self.assertIn("T1-4", second["notes"][1])
        with self.assertRaises(ValueError):
            self.workspace.refine(
                {**self.data, "session": second["session"], "answer": "T1-2"}
            )
        with self.assertRaises(ValueError):
            self.workspace.refine(
                {**self.data, "session": first["session"], "answer": "T1-4"}
            )

    def test_missing_citations_are_not_perfect_score(self):
        checked = self.workspace.verify({**self.data, "report": "沒有引用的報告"})
        self.assertEqual(checked["total"], 0)
        self.assertEqual(checked["known"], 0)

    def test_standalone_verification_uses_selected_period(self):
        checked = self.workspace.verify({**self.data, "report": "T1-2 T1-2 T1-1 T9-99"})
        self.assertEqual(checked["total"], 3)
        self.assertEqual(
            [c["status"] for c in checked["citations"]],
            ["known", "outside_period", "missing"],
        )
        all_time = self.workspace.verify(
            {**self.data, "start": "", "end": "", "report": "T1-0"}
        )
        self.assertEqual(all_time["known"], 1)

    def test_browse_search_and_dates_preserve_raw_text(self):
        result = services.browse({**self.data, "query": "script"})
        self.assertEqual(result["total"], 1)
        self.assertIn("<script>", result["comments"][0]["body"])
        self.assertEqual(services.browse({**self.data, "query": "T1-1"})["total"], 0)
        self.assertEqual(services.browse({**self.data, "offset": 40})["comments"], [])

    def test_inputs_and_session_identity_are_validated(self):
        for override in (
            {"acode": True},
            {"acode": 999},
            {"store": "../escape"},
            {"mode": "other"},
            {"target": ""},
            {"depth": 4},
            {"start": "2025-09", "end": "2025-08"},
            {"end": "bad"},
        ):
            with self.subTest(override=override), self.assertRaises(ValueError):
                self.workspace.generate({**self.data, **override})
        session = self.workspace.generate(self.data)["session"]
        with self.assertRaises(ValueError):
            self.workspace.verify(
                {**self.data, "store": "其他店", "session": session, "report": "T1-2"}
            )
        with self.assertRaises(ValueError):
            self.workspace.verify({**self.data, "session": "expired", "report": "T1-2"})

    def test_session_cache_is_bounded(self):
        first = self.workspace.generate(self.data)["session"]
        for _ in range(8):
            self.workspace.generate(self.data)
        with self.assertRaises(ValueError):
            self.workspace.verify({**self.data, "session": first, "report": "T1-2"})

    def wait_until(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate():
            if time.monotonic() > deadline:
                self.fail("background job did not reach its checkpoint")
            time.sleep(0.005)

    def test_job_waits_for_plan_and_cancellation_prevents_fetch(self):
        manager = Jobs()
        self.addCleanup(manager.close)
        with (
            patch.object(scraper, "search_all_threads", return_value=[self.ref]),
            patch.object(scraper, "fetch_thread") as fetch,
            patch("gui.jobs.client.create_fetch", return_value=lambda _: ""),
        ):
            started = manager.start({**self.data, "existing": True})
            self.wait_until(lambda: manager.current()["status"] == "waiting")
            waiting = manager.current()
            self.assertEqual(waiting["decision"]["kind"], "plan")
            fetch.assert_not_called()
            with self.assertRaises(ValueError):
                manager.start(self.data)
            manager.respond({"id": started["id"], "action": "cancel"})
            self.wait_until(lambda: manager.current()["status"] == "cancelled")
            fetch.assert_not_called()

    def test_job_confirmed_plan_uses_existing_store_and_preserves_ids(self):
        manager = Jobs()
        self.addCleanup(manager.close)
        newer = ThreadRef(
            3,
            103,
            1,
            "100",
            "測試店",
            "2026/11/09 10:00",
            "東京風俗・お店",
            reply_count=5,
        )
        with (
            patch.object(scraper, "search_all_threads", return_value=[newer]),
            patch.object(
                scraper,
                "fetch_thread",
                return_value=(newer, [Comment(5, "2026/11/09 10:00", "新留言")]),
            ) as fetch,
            patch("gui.jobs.client.create_fetch", return_value=lambda _: ""),
        ):
            started = manager.start({**self.data, "existing": True})
            self.wait_until(lambda: manager.current()["status"] == "waiting")
            decision = manager.current()["decision"]["id"]
            with self.assertRaises(ValueError):
                manager.respond(
                    {
                        "id": started["id"],
                        "action": "answer",
                        "decision": "wrong",
                        "value": True,
                    }
                )
            manager.respond(
                {
                    "id": started["id"],
                    "action": "answer",
                    "decision": decision,
                    "value": True,
                }
            )
            self.wait_until(lambda: manager.current()["status"] == "done")
            self.assertEqual(fetch.call_count, 1)
            saved = store.load(paths.bakusai_path(3, "測試店"))
            self.assertEqual(
                {c["comment_id"] for c in saved["comments"]},
                {"T1-1", "T1-2", "T1-3", "T1-4", "T1-5"},
            )

    def test_decision_expires_and_reports_cancellation(self):
        job = ScrapeJob(self.data)
        with (
            patch.object(scraper, "search_all_threads", return_value=[self.ref]),
            patch("gui.jobs.client.create_fetch", return_value=lambda _: ""),
            patch("gui.jobs.DECISION_TIMEOUT_SECONDS", 0.01),
        ):
            job.thread_handle.start()
            job.thread_handle.join(timeout=3)
        self.assertEqual(job.snapshot()["status"], "cancelled")


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        env = patch.dict(os.environ, {"BAKUSAI_DATA_ROOT": self.directory.name})
        env.start()
        self.addCleanup(env.stop)
        self.server = WorkspaceServer(0)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method="GET", path="/", body=None, headers=None):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=5
        )
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def test_root_serves_local_assets_and_security_headers(self):
        status, headers, body = self.request()
        self.assertEqual(status, 200)
        self.assertIn(self.server.token.encode(), body)
        self.assertNotIn(b"__WORKSPACE_TOKEN__", body)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["Cache-Control"], "no-store")
        for path in ("/app.js", "/style.css", "/icon.svg"):
            self.assertEqual(self.request(path=path)[0], 200)
        self.assertEqual(self.request(path="/../paths.py")[0], 404)
        self.assertEqual(self.request(path="/data/test.json")[0], 404)

    def test_api_requires_token_and_refuses_foreign_host_origin(self):
        headers = {"X-Workspace-Token": self.server.token}
        self.assertEqual(self.request(path="/api/stores")[0], 403)
        self.assertEqual(self.request(path="/api/stores", headers=headers)[0], 200)
        for extra in (
            {"Host": "attacker.example"},
            {"Origin": "https://attacker.example"},
        ):
            self.assertEqual(
                self.request(path="/api/stores", headers={**headers, **extra})[0], 403
            )
        self.assertEqual(self.request(headers={"Host": "attacker.example"})[0], 403)

    def test_post_body_limits_and_validation(self):
        headers = {
            "X-Workspace-Token": self.server.token,
            "Content-Type": "application/json",
        }
        for body in ("[]", "bad", "null"):
            self.assertEqual(
                self.request("POST", "/api/generate", body, headers)[0], 400
            )
        self.assertEqual(self.request("POST", "/api/generate", "{}", headers)[0], 400)
        large = {**headers, "Content-Length": str(9 * 1024 * 1024)}
        self.assertEqual(self.request("POST", "/api/generate", "", large)[0], 413)
        self.assertEqual(self.request("POST", "/api/unknown", "{}", headers)[0], 404)

    def test_generate_and_verify_round_trip(self):
        ref = ThreadRef(3, 103, 1, "100", "測試店", board_label="東京風俗・お店")
        payload = store.merge(
            None, [(ref, [Comment(1, "2026/11/09 12:00", "テスト留言")])], "測試店", 3
        )
        store.save(paths.bakusai_path(3, "測試店"), payload)
        headers = {
            "X-Workspace-Token": self.server.token,
            "Content-Type": "application/json",
        }
        data = {"acode": 3, "store": "測試店", "mode": "roster"}
        status, _, body = self.request(
            "POST", "/api/generate", json.dumps(data), headers
        )
        self.assertEqual(status, 200)
        generated = json.loads(body)
        self.assertEqual(generated["count"], 1)
        verified = {**data, "session": generated["session"], "report": "T1-1 T9-99"}
        status, _, body = self.request(
            "POST", "/api/verify", json.dumps(verified), headers
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["known"], 1)
        self.assertEqual(json.loads(body)["missing"], 1)

    def test_non_ascii_token_is_rejected_without_handler_failure(self):
        self.assertEqual(
            self.request(path="/api/stores", headers={"X-Workspace-Token": "é"})[0], 403
        )


if __name__ == "__main__":
    unittest.main()
