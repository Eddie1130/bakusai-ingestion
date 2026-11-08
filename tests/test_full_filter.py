"""Full two-step filtering covers every scoped comment and validates each batch."""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

import paths
from analysis import select, tokens
from gui.services import Workspace
from scraping import store
from scraping.parser import Comment, ThreadRef


class FullFilterTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {"BAKUSAI_DATA_ROOT": temp.name})
        env.start()
        self.addCleanup(env.stop)
        self.payload = store.merge(
            None,
            [
                (
                    ThreadRef(
                        3, 103, 1, "100", "テスト店", description="日付のない開串文"
                    ),
                    [
                        Comment(
                            i,
                            "2025/02/01 12:00",
                            ("ナナ " if i == 1 else "名前なし ") + "資料" * 110,
                        )
                        for i in range(1, 29)
                    ],
                ),
                (
                    ThreadRef(
                        3, 103, 1, "200", "ナナの専用スレ", board_label="東京風俗・個人"
                    ),
                    [
                        Comment(1, "2025/01/01 12:00", "期間外"),
                        Comment(2, "2025/02/02 12:00", "名前なし >>1"),
                    ],
                ),
            ],
            "テスト店",
            3,
        )
        store.save(paths.bakusai_path(3, "テスト店"), self.payload)
        self.workspace = Workspace()
        self.data = {
            "acode": 3,
            "store": "テスト店",
            "mode": "name",
            "target": "ナナ",
            "two_step": True,
            "filter_strategy": "full",
            "batch_comments": 4,
            "templates": {
                "full_filter_spec.md": "$store の $girl に関するIDをJSON配列で返す。"
            },
        }

    def start(self, **extra):
        return self.workspace.generate({**self.data, **extra})

    def reply(self, result, ids):
        return self.workspace.refine(
            {
                **self.data,
                "session": result["session"],
                "batch_index": result["filter"]["current"],
                "filter_revision": result["filter"]["revision"],
                "answer": json.dumps(ids),
            }
        )

    def test_every_comment_is_primary_once_and_batches_respect_total_count(self):
        result = self.start()
        self.assertEqual(result["stage"], "full_filter")
        self.assertGreater(result["filter"]["total"], 1)
        ids = []
        for index in range(result["filter"]["total"]):
            page = self.workspace.filter_batch(
                {**self.data, "session": result["session"], "batch_index": index}
            )
            ids.extend(page["filter"]["primary_ids"])
            self.assertLessEqual(
                len(page["filter"]["primary_ids"]) + len(page["filter"]["context_ids"]),
                4,
            )
            self.assertEqual(page["filter"]["comment_limit"], 4)
            self.assertEqual(
                page["tokens"]["upper"], tokens.estimate(page["prompt"]).upper
            )
        expected = {c.comment_id for c in select.all_comments(self.payload)}
        self.assertEqual(set(ids), expected)
        self.assertEqual(len(ids), len(expected))

    def test_one_comment_limit_includes_no_extra_context_and_keeps_all_comments(self):
        result = self.start(batch_comments=1)
        expected = {c.comment_id for c in select.all_comments(self.payload)}
        self.assertEqual(result["filter"]["total"], len(expected))
        actual = []
        for index in range(result["filter"]["total"]):
            page = self.workspace.filter_batch(
                {**self.data, "session": result["session"], "batch_index": index}
            )
            self.assertEqual(page["count"], 1)
            self.assertEqual(page["filter"]["context_ids"], [])
            actual.extend(page["filter"]["primary_ids"])
        self.assertEqual(set(actual), expected)

    def test_default_count_is_two_hundred_and_small_threads_stay_together(self):
        data = {
            key: value for key, value in self.data.items() if key != "batch_comments"
        }
        result = self.workspace.generate(data)
        self.assertEqual(result["filter"]["comment_limit"], 200)
        self.assertEqual(result["filter"]["total"], 2)
        self.assertTrue(
            all(key.startswith("T1-") for key in result["filter"]["primary_ids"])
        )

    def test_empty_batches_progress_but_report_requires_all_batches(self):
        result = self.start()
        with self.assertRaises(ValueError):
            self.workspace.full_filter_report(
                {**self.data, "session": result["session"]}
            )
        for _ in range(result["filter"]["total"]):
            result = self.reply(result, [])
        self.assertTrue(result["filter"]["all_done"])
        self.assertEqual(result["filter"]["confirmed_count"], 0)
        with self.assertRaises(ValueError):
            self.workspace.full_filter_report(
                {**self.data, "session": result["session"]}
            )

    def test_merge_deduplicates_and_freezes_templates_and_source_data(self):
        result = self.start()
        session = result["session"]
        chosen = result["filter"]["primary_ids"][0]
        store.save(paths.bakusai_path(3, "テスト店"), {"comments": [], "threads": []})
        result = self.reply(result, [chosen, chosen])
        while not result["filter"]["all_done"]:
            result = self.reply(result, [])
        report = self.workspace.full_filter_report(
            {
                **self.data,
                "session": session,
                "templates": {"report_spec.md": "changed afterwards"},
            }
        )
        self.assertEqual(report["stage"], "report")
        self.assertIn(chosen, report["prompt"])
        self.assertNotIn("changed afterwards", report["prompt"])
        check = self.workspace.verify(
            {**self.data, "session": report["session"], "report": chosen}
        )
        self.assertEqual(check["known"], 1)

    def test_dates_exclude_unknown_opening_posts_and_old_replies(self):
        result = self.start(start="2025-02")
        all_ids = []
        for index in range(result["filter"]["total"]):
            page = self.workspace.filter_batch(
                {**self.data, "session": result["session"], "batch_index": index}
            )
            all_ids += page["filter"]["primary_ids"] + page["filter"]["context_ids"]
        self.assertNotIn("T1-0", all_ids)
        self.assertNotIn("T2-1", all_ids)
        self.assertIn("T2-2", all_ids)

    def test_wrong_batch_and_stale_replies_do_not_mark_complete(self):
        result = self.start()
        other = self.workspace.filter_batch(
            {
                **self.data,
                "session": result["session"],
                "batch_index": result["filter"]["total"] - 1,
            }
        )
        absent = next(
            key
            for key in other["filter"]["primary_ids"]
            if key
            not in result["filter"]["primary_ids"] + result["filter"]["context_ids"]
        )
        with self.assertRaises(ValueError):
            self.reply(result, [absent])
        self.reply(result, [])
        with self.assertRaises(ValueError):
            self.reply(result, [])

    def test_no_usable_names_in_full_scope_still_generates(self):
        full = self.start(target="完全に未出現の名前")
        self.assertGreater(full["filter"]["total_comments"], 0)
        with self.assertRaises(ValueError):
            self.start(target="完全に未出現の名前", filter_strategy="quick")

    def test_invalid_strategy_count_and_reply_types(self):
        for extra in (
            {"filter_strategy": "unknown"},
            {"batch_comments": True},
            {"batch_comments": 0},
            {"batch_comments": -1},
            {"batch_comments": 2001},
            {"batch_comments": 1.5},
            {"batch_comments": "10"},
        ):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.start(**extra)
        result = self.start()
        for answer in ("", "沒有相關留言", "{}", "[1]", '["unknown"]'):
            with self.subTest(answer=answer), self.assertRaises(ValueError):
                self.workspace.refine(
                    {
                        **self.data,
                        "session": result["session"],
                        "batch_index": 0,
                        "filter_revision": 0,
                        "answer": answer,
                    }
                )

    def test_overlap_ids_are_accepted_and_deduplicated_across_batches(self):
        result = self.start(batch_comments=4)
        self.assertTrue(result["filter"]["context_ids"])
        shared = result["filter"]["context_ids"][0]
        selections = 0
        while not result["filter"]["all_done"]:
            allowed = result["filter"]["primary_ids"] + result["filter"]["context_ids"]
            ids = [shared] if shared in allowed else []
            selections += bool(ids)
            result = self.reply(result, ids)
        self.assertGreaterEqual(selections, 2)
        self.assertEqual(result["filter"]["confirmed_count"], 1)

    def test_mixed_invalid_ids_are_dropped_and_revisiting_restores_saved_answer(self):
        result = self.start()
        key = result["filter"]["primary_ids"][0]
        updated = self.reply(result, [key, "T999-999"])
        self.assertTrue(any("T999-999" in note for note in updated["notes"]))
        previous = self.workspace.filter_batch(
            {**self.data, "session": result["session"], "batch_index": 0}
        )
        self.assertEqual(json.loads(previous["filter"]["saved_answer"]), [key])
        changed = self.reply(previous, [])
        self.assertEqual(changed["filter"]["confirmed_count"], 0)

    def test_long_templates_and_comments_are_not_truncated_or_split_by_tokens(self):
        full_spec = "規格" * 10000
        result = self.start(templates={"full_filter_spec.md": full_spec})
        self.assertIn(full_spec, result["prompt"])
        huge = store.merge(
            None,
            [
                (
                    ThreadRef(3, 103, 1, "100", "テスト店"),
                    [Comment(1, "2025/02/01 12:00", "長文" * 5000)],
                )
            ],
            "テスト店",
            3,
        )
        store.save(paths.bakusai_path(3, "テスト店"), huge)
        result = self.start(batch_comments=1)
        self.assertEqual(result["count"], 1)
        self.assertIn("長文" * 5000, result["prompt"])
        self.assertGreater(result["tokens"]["upper"], 2000)

    def test_fenced_empty_array_and_invalid_batch_indices(self):
        result = self.start()
        updated = self.workspace.refine(
            {
                **self.data,
                "session": result["session"],
                "batch_index": 0,
                "filter_revision": 0,
                "answer": "```json\n[]\n```",
            }
        )
        self.assertEqual(updated["filter"]["completed"], 1)
        for index in (-1, True, "1", 999999):
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.workspace.filter_batch(
                    {**self.data, "session": result["session"], "batch_index": index}
                )


if __name__ == "__main__":
    unittest.main()
