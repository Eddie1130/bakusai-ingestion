"""Feature analysis supports direct batch reports or evidence-ID filtering."""

import unittest

import test_full_filter as full_fixture


class FeatureBatchTests(unittest.TestCase):
    def setUp(self):
        full_fixture.FullFilterTests.setUp(self)
        self.data.update(
            mode="features", target="短髮、手臂有刺青", feature_analysis="filter"
        )

    start = full_fixture.FullFilterTests.start
    reply = full_fixture.FullFilterTests.reply

    def complete(self, first, report=False):
        result = first
        chosen = first["filter"]["primary_ids"][0]
        while not result["filter"]["all_done"]:
            answer = (
                (
                    f"暫時候選線索，引用 {chosen}"
                    if result["filter"]["current"] == 0
                    else "本批沒有可確認人選。"
                )
                if report
                else ([chosen] if result["filter"]["current"] == 0 else [])
            )
            if report:
                result = self.workspace.refine(
                    {
                        **self.data,
                        "session": result["session"],
                        "batch_index": result["filter"]["current"],
                        "filter_revision": result["filter"]["revision"],
                        "answer": answer,
                    }
                )
            else:
                result = self.reply(result, answer)
        return result, chosen

    def test_filter_reads_all_comments_without_literal_feature_matching(self):
        result = self.start()
        self.assertEqual(result["stage"], "full_filter")
        self.assertEqual(result["analysis_mode"], "features")
        self.assertEqual(result["output_kind"], "ids")
        self.assertEqual(result["filter"]["total_comments"], 31)
        self.assertIn("短髮、手臂有刺青", result["prompt"])
        self.assertIn("矛盾", result["prompt"])
        self.assertNotIn("是否在談目標人物", result["prompt"])

    def test_filtered_final_report_uses_features_and_frozen_template(self):
        first = self.start(templates={"features_spec.md": "FEATURE_REPORT $store"})
        result, chosen = self.complete(first)
        final = self.workspace.full_filter_report(
            {
                **self.data,
                "session": first["session"],
                "filter_revision": result["filter"]["revision"],
                "templates": {"features_spec.md": "CHANGED"},
            }
        )
        self.assertIn("FEATURE_REPORT テスト店", final["prompt"])
        self.assertIn("短髮、手臂有刺青", final["prompt"])
        self.assertNotIn("針對「テスト店」的女生「短髮", final["prompt"])
        checked = self.workspace.verify(
            {**self.data, "session": final["session"], "report": chosen}
        )
        self.assertEqual(checked["known"], 1)

    def test_direct_one_batch_preserves_existing_single_prompt(self):
        result = self.start(feature_analysis="direct", batch_comments=200)
        self.assertEqual(result["stage"], "report")
        self.assertEqual(result["count"], 31)
        self.assertIn("候選人表", result["prompt"])
        self.assertNotIn("只輸出 JSON 字串陣列", result["prompt"])

    def test_direct_multiple_batches_merge_all_reports_with_cited_evidence(self):
        first = self.start(
            feature_analysis="direct",
            templates={"features_merge_spec.md": "MERGE_ALL $store"},
        )
        self.assertEqual(first["output_kind"], "reports")
        self.assertNotIn("只輸出 JSON 字串陣列", first["prompt"])
        result, chosen = self.complete(first, report=True)
        final = self.workspace.full_filter_report(
            {
                **self.data,
                "session": first["session"],
                "filter_revision": result["filter"]["revision"],
            }
        )
        self.assertIn("MERGE_ALL テスト店", final["prompt"])
        self.assertIn("暫時候選線索", final["prompt"])
        self.assertIn("本批沒有可確認人選", final["prompt"])
        self.assertIn(f"\n{chosen} ", final["prompt"])
        self.assertIn("短髮、手臂有刺青", final["prompt"])

    def test_direct_reports_reject_unknown_and_wrong_batch_citations(self):
        first = self.start(feature_analysis="direct")
        with self.assertRaises(ValueError):
            self.workspace.refine(
                {
                    **self.data,
                    "session": first["session"],
                    "batch_index": 0,
                    "filter_revision": 0,
                    "answer": "看起來符合 T999-999",
                }
            )
        current = self.workspace.filter_batch(
            {**self.data, "session": first["session"], "batch_index": 0}
        )
        self.assertEqual(current["filter"]["completed"], 0)

    def test_direct_reports_without_candidates_can_still_be_merged(self):
        first = self.start(feature_analysis="direct")
        result = first
        while not result["filter"]["all_done"]:
            result = self.workspace.refine(
                {
                    **self.data,
                    "session": first["session"],
                    "batch_index": result["filter"]["current"],
                    "filter_revision": result["filter"]["revision"],
                    "answer": "本批沒有可確認的相關人選。",
                }
            )
        final = self.workspace.full_filter_report(
            {**self.data, "session": first["session"]}
        )
        self.assertEqual(final["count"], 0)
        self.assertIn("沒有可確認的相關人選", final["prompt"])
        self.assertTrue(final.get("warning"))

    def test_date_and_count_constraints_apply_to_both_methods(self):
        for method in ("filter", "direct"):
            result = self.start(
                feature_analysis=method, start="2025-02", batch_comments=3
            )
            ids = []
            for index in range(result["filter"]["total"]):
                page = self.workspace.filter_batch(
                    {**self.data, "session": result["session"], "batch_index": index}
                )
                self.assertLessEqual(page["count"], 3)
                ids += page["filter"]["primary_ids"]
            self.assertNotIn("T1-0", ids)
            self.assertNotIn("T2-1", ids)
            self.assertEqual(len(ids), 29)

    def test_invalid_feature_method_and_count_are_rejected(self):
        for extra in (
            {"feature_analysis": "unknown"},
            {"batch_comments": 0},
            {"batch_comments": True},
        ):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.start(**extra)


if __name__ == "__main__":
    unittest.main()
