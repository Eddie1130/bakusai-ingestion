"""Custom tasks select exactly the previewed evidence, without live requests."""

from copy import deepcopy
import os
import tempfile
import unittest
from unittest.mock import patch

import paths
from gui import services
from scraping import store
from scraping.parser import Comment, ThreadRef


class CustomSelectionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {"BAKUSAI_DATA_ROOT": temp.name})
        env.start()
        self.addCleanup(env.stop)
        refs = [
            ThreadRef(3, 103, 1, "100", "第一串", description="開串文"),
            ThreadRef(3, 103, 1, "200", "第二串"),
        ]
        self.payload = store.merge(
            None,
            [
                (
                    refs[0],
                    [
                        Comment(1, "2025/01/01 00:00", "最舊"),
                        Comment(2, "2025/02/01 00:00", "中間 >>1"),
                        Comment(3, "2025/03/01 00:00", "最新 T1"),
                    ],
                ),
                (
                    refs[1],
                    [
                        Comment(1, "2025/02/15 00:00", "中間 T2"),
                        Comment(2, "2025/04/01 00:00", "最新 T2"),
                    ],
                ),
            ],
            "測試店",
            3,
        )
        store.save(paths.bakusai_path(3, "測試店"), self.payload)
        self.data = {"acode": 3, "store": "測試店"}
        self.workspace = services.Workspace()

    def ids(self, result):
        return [c["id"] for c in result["comments"]]

    def generate(self, preview, **extra):
        return self.workspace.generate(
            {
                **self.data,
                "mode": "other",
                "instructions": "比較 $store 的討論變化。",
                "selection_session": preview["session"],
                **extra,
            }
        )

    def test_date_thread_range_then_count_in_that_order(self):
        preview = self.workspace.custom_preview(
            {
                **self.data,
                "start": "2025-02",
                "threads": ["T1"],
                "ranges": "T1-1～T1-3",
                "limit": 1,
            }
        )
        self.assertEqual(preview["matched"], 2)
        self.assertEqual(self.ids(preview), ["T1-3"])

    def test_global_latest_and_oldest_caps_and_unknown_dates_last(self):
        newest = self.workspace.custom_preview({**self.data, "limit": 2})
        self.assertEqual(self.ids(newest), ["T2-2", "T1-3"])
        oldest = self.workspace.custom_preview(
            {**self.data, "limit": 2, "order": "oldest"}
        )
        self.assertEqual(self.ids(oldest), ["T1-1", "T1-2"])
        all_time = self.workspace.custom_preview(self.data)
        self.assertEqual(self.ids(all_time)[-1], "T1-0")
        self.assertEqual(all_time["count"], 6)

    def test_explicit_ids_and_ranges_union_intersects_threads(self):
        preview = self.workspace.custom_preview(
            {**self.data, "ranges": "T1-1, T1-2 ~ T1-3，T2-2", "threads": ["T1"]}
        )
        self.assertEqual(set(self.ids(preview)), {"T1-1", "T1-2", "T1-3"})
        bare = self.workspace.custom_preview(
            {**self.data, "ranges": "2～3", "threads": ["T1"]}
        )
        self.assertEqual(set(self.ids(bare)), {"T1-2", "T1-3"})

    def test_checked_evidence_only_no_context_expansion(self):
        preview = self.workspace.custom_preview(self.data)
        result = self.generate(preview, selection_mode="include", comment_ids=["T1-2"])
        self.assertEqual(result["count"], 1)
        self.assertIn("比較 測試店 的討論變化", result["prompt"])
        self.assertIn("T1-2 2025", result["prompt"])
        self.assertNotIn("T1-1 2025", result["prompt"])
        verified = self.workspace.verify(
            {**self.data, "session": result["session"], "report": "T1-2 T1-1"}
        )
        self.assertEqual(
            [c["status"] for c in verified["citations"]], ["known", "not_sent"]
        )

    def test_exclusion_never_refills_count_and_disk_changes_do_not_change_preview(self):
        preview = self.workspace.custom_preview({**self.data, "limit": 2})
        changed = deepcopy(self.payload)
        changed["comments"] = []
        store.save(paths.bakusai_path(3, "測試店"), changed)
        page = self.workspace.custom_page({**self.data, "session": preview["session"]})
        self.assertEqual(self.ids(page), ["T2-2", "T1-3"])
        result = self.generate(preview, comment_ids=["T2-2"])
        self.assertEqual(result["count"], 1)
        self.assertIn("T1-3 2025", result["prompt"])
        self.assertNotIn("T2-1 2025", result["prompt"])

    def test_invalid_filters_and_forged_ids_are_rejected(self):
        for extra in (
            {"limit": True},
            {"limit": -1},
            {"limit": "5"},
            {"threads": ["T99"]},
            {"ranges": "1~2"},
            {"ranges": "T1-1~T2-2"},
            {"ranges": "T1-3~1"},
            {"ranges": "broken"},
            {"order": "random"},
        ):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.workspace.custom_preview({**self.data, **extra})
        preview = self.workspace.custom_preview({**self.data, "threads": ["T1"]})
        for extra in (
            {"comment_ids": ["T2-2"]},
            {"instructions": ""},
            {"selection_mode": "include", "comment_ids": []},
            {"start": "2025"},
        ):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.generate(preview, **extra)

    def test_empty_thread_choice_stays_empty_and_preview_is_not_report_scope(self):
        preview = self.workspace.custom_preview({**self.data, "threads": []})
        self.assertEqual(preview["count"], 0)
        with self.assertRaises(ValueError):
            self.generate(preview)
        with self.assertRaises(ValueError):
            self.workspace.verify(
                {**self.data, "session": preview["session"], "report": "T1-1"}
            )

    def test_preview_pagination_and_manual_selection_across_pages(self):
        ref = ThreadRef(3, 103, 1, "100", "第一串")
        payload = store.merge(
            self.payload,
            [
                (
                    ref,
                    [
                        Comment(number, "2025/05/01 12:00", f"留言 {number}")
                        for number in range(4, 55)
                    ],
                )
            ],
            "測試店",
            3,
        )
        store.save(paths.bakusai_path(3, "測試店"), payload)
        preview = self.workspace.custom_preview({**self.data, "limit": 45})
        self.assertEqual(len(preview["comments"]), 40)
        page = self.workspace.custom_page(
            {**self.data, "session": preview["session"], "offset": 40}
        )
        self.assertEqual(len(page["comments"]), 5)
        ids = [preview["comments"][0]["id"], page["comments"][-1]["id"]]
        result = self.generate(preview, selection_mode="include", comment_ids=ids)
        self.assertEqual(result["count"], 2)
        for comment_id in ids:
            self.assertIn(f"\n{comment_id} ", result["prompt"])

    def test_dates_cannot_be_bypassed_with_explicit_ids(self):
        preview = self.workspace.custom_preview({**self.data, "start": "2025-02"})
        for comment_id in ("T1-0", "T1-1"):
            with self.subTest(comment_id=comment_id), self.assertRaises(ValueError):
                self.generate(
                    preview,
                    start="2025-02",
                    selection_mode="include",
                    comment_ids=[comment_id],
                )

    def test_thread_counts_respect_dates_and_include_opening_posts_only_for_all_time(
        self,
    ):
        all_time = services.custom_threads(self.data)
        self.assertEqual([t["count"] for t in all_time["threads"]], [4, 2])
        scoped = services.custom_threads(
            {**self.data, "start": "2025-02", "end": "2025-03"}
        )
        self.assertEqual([t["count"] for t in scoped["threads"]], [2, 1])


if __name__ == "__main__":
    unittest.main()
