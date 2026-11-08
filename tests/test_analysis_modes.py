"""Offline regression tests for scoped analysis and manual-model workflows."""
import unittest
import io
from contextlib import redirect_stdout
from datetime import date
from unittest.mock import patch

from analysis import cli, prompt, select
from analysis.period import Period, parse_bound


def payload():
    return {
        "threads": [{"index": "T1", "scope": "store", "thread_title": "店"}],
        "comments": [
            {"comment_id": "T1-0", "thread_index": "T1", "comment_number": 0,
             "datetime": "", "body": "未注明日期的線索"},
            {"comment_id": "T1-1", "thread_index": "T1", "comment_number": 1,
             "datetime": "2025/08/29 23:59", "body": "ナナ 舊資料", "reply": []},
            {"comment_id": "T1-2", "thread_index": "T1", "comment_number": 2,
             "datetime": "2025/08/30 00:00", "body": "ナナ 腕にタトゥー",
             "reply": ["T1-1", "T1-3", "T1-4", "T1-0"]},
            {"comment_id": "T1-3", "thread_index": "T1", "comment_number": 3,
             "datetime": "2025/08/31 23:59", "body": "黒い服の子", "reply": []},
            {"comment_id": "T1-4", "thread_index": "T1", "comment_number": 4,
             "datetime": "2025/09/01 00:00", "body": "區間外回覆", "reply": []},
            {"comment_id": "T1-5", "thread_index": "T1", "comment_number": 5,
             "datetime": "invalid", "body": "日期無效", "reply": []},
            {"comment_id": "T1-6", "thread_index": "T1", "comment_number": 6,
             "datetime": "2025/08/31 12:00", "body": "未送出的店內留言", "reply": []},
        ],
    }


PERIOD = Period(date(2025, 8, 30), date(2025, 8, 31))


class PeriodTests(unittest.TestCase):
    def test_partial_bounds_and_leap_year(self):
        self.assertEqual(parse_bound("2023"), date(2023, 1, 1))
        self.assertEqual(parse_bound("2025", end=True), date(2025, 12, 31))
        self.assertEqual(parse_bound("2024-02", end=True), date(2024, 2, 29))
        self.assertEqual(parse_bound("now"), date.today())
        self.assertIsNone(parse_bound(""))

    def test_invalid_dates_and_reversed_range(self):
        for value in ("2025-02-29", "2025-13", "2025-00", "2025-8", "garbage"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_bound(value, end=True)
        with self.assertRaises(ValueError):
            Period(date(2025, 1, 2), date(2025, 1, 1))

    def test_strict_bounds_and_no_source_mutation(self):
        source = payload()
        scoped = PERIOD.apply(source)
        self.assertEqual([c["comment_id"] for c in scoped["comments"]],
                         ["T1-2", "T1-3", "T1-6"])
        self.assertEqual(len(source["comments"]), 7)
        self.assertIs(Period().apply(source), source)

    def test_open_bounds(self):
        self.assertEqual(len(Period(end=date(2025, 8, 29)).apply(payload())["comments"]), 1)
        self.assertEqual(len(Period(start=date(2025, 9, 1)).apply(payload())["comments"]), 1)

    def test_reply_expansion_cannot_escape_period(self):
        scoped = PERIOD.apply(payload())
        for depth in (0, 1, 2, None):
            with self.subTest(depth=depth):
                chosen = select.select(scoped, "ナナ", depth)
                self.assertEqual({c.comment_id for c in chosen},
                                 {"T1-2"} if depth == 0 else {"T1-2", "T1-3"})


    def test_real_opening_post_storage_is_excluded_with_dates(self):
        source = payload()
        source["comments"] = [c for c in source["comments"] if c["comment_id"] != "T1-0"]
        source["threads"][0]["description"] = "ナナ 無日期開串文"
        self.assertIn("T1-0", {c.comment_id for c in select.all_comments(source)})
        scoped = PERIOD.apply(source)
        self.assertNotIn("T1-0", {c.comment_id for c in select.all_comments(scoped)})
        self.assertNotIn("T1-0", {c.comment_id for c in select.select(scoped, "ナナ", None)})
        self.assertEqual(select.from_ids(scoped, ["T1-0"]), ())
        self.assertEqual(source["threads"][0]["description"], "ナナ 無日期開串文")


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        output = redirect_stdout(io.StringIO())
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)
        delivery = patch.object(cli, "deliver", side_effect=self.deliver)
        delivery.start()
        self.addCleanup(delivery.stop)

    @staticmethod
    def deliver(text, copy):
        copy(text)
        return "  提示詞已交付"

    def test_discovery_keeps_unnamed_and_no_literal_feature_filter(self):
        for features in (None, "手臂有紋身，穿黑衣"):
            copied = []
            with patch.object(cli, "load_store", return_value=payload()):
                cli.analyse_discovery(3, "店", features=features, period=PERIOD,
                                      copy=lambda text: copied.append(text) or True)
            text = copied[0]
            self.assertIn("T1-2", text)
            self.assertIn("T1-3", text)
            self.assertIn("T1-6", text)
            self.assertNotIn("區間外回覆", text)
            self.assertNotIn("未注明日期的線索", text)
            self.assertIn("2025-08-30", text)
            self.assertIn("[店內資料]", text)
            self.assertNotIn("[確認]", text)

    def test_all_time_keeps_undated(self):
        text = prompt.build_discovery("店", select.all_comments(payload()))
        self.assertIn("未注明日期的線索", text)
        self.assertIn("日期不明", text)

    def test_single_pass_respects_period(self):
        copied = []
        with patch.object(cli, "load_store", return_value=payload()):
            cli.analyse_once(3, "店", "ナナ", period=PERIOD,
                             copy=lambda text: copied.append(text) or True)
        self.assertNotIn("舊資料", copied[0])
        self.assertNotIn("區間外回覆", copied[0])

    def test_two_step_rejects_unsent_and_outside_ids(self):
        copied = []
        with patch.object(cli, "load_store", return_value=payload()):
            result = cli.analyse_two_step(
                3, "店", "ナナ", ask=lambda _: "", period=PERIOD,
                copy=lambda text: copied.append(text) or True,
                paste=lambda: '["T1-2", "T1-1", "T1-6", "T99-1"]',
            )
        self.assertEqual(len(copied), 2)
        self.assertIn("模型確認 1 則", result)
        self.assertIn("3 個 ID", result)
        self.assertNotIn("未送出的店內留言", copied[1])
        self.assertNotIn("區間外回覆", copied[1])

    def test_two_step_rejects_unsent_synthesized_opening_post(self):
        source = payload()
        source["threads"].append({"index": "T2", "description": "其他人的無日期開串文"})
        copied = []
        with patch.object(cli, "load_store", return_value=source):
            result = cli.analyse_two_step(
                3, "店", "ナナ", ask=lambda _: "", paste=lambda: "T2-0",
                copy=lambda text: copied.append(text) or True,
            )
        self.assertEqual(len(copied), 1)
        self.assertIn("沒有讀到任何本輪送出的留言 ID", result)

    def test_discovery_includes_relevant_thread_titles(self):
        source = payload()
        source["threads"][0].update(thread_title="店 ナナ 個人板", scope="girl")
        source["threads"].append({"index": "T9", "thread_title": "不相關討論串"})
        copied = []
        with patch.object(cli, "load_store", return_value=source):
            cli.analyse_discovery(3, "店", period=PERIOD,
                                  copy=lambda text: copied.append(text) or True)
        self.assertIn("店 ナナ 個人板", copied[0])
        self.assertNotIn("不相關討論串", copied[0])
        self.assertIn("不能當作期間內在籍", copied[0])

    def test_empty_period_does_not_deliver(self):
        with patch.object(cli, "load_store", return_value=payload()), patch.object(cli, "deliver") as send:
            result = cli.analyse_discovery(3, "店", period=Period(start=date(2030, 1, 1)))
        send.assert_not_called()
        self.assertIn("沒有可用留言", result)

    def test_verification_uses_scoped_payload(self):
        with patch.object(cli, "load_store", return_value=payload()):
            result = cli.verify_report(3, "店", ask=lambda _: "",
                                       paste=lambda: "T1-1 T1-2", period=PERIOD)
        self.assertIn("1 個不在驗證範圍", result)
        self.assertIn("T1-1", result)
        self.assertNotIn("舊資料", result)

    def test_interactive_targets_and_name_workflow(self):
        for target, extra in (("2", ["黑衣紋身"]), ("3", []),
                              ("1", ["1", "ナナ", ""])):
            answers = iter(["3", "1", target, "2025-08-30", "2025-08-31", *extra, "q"])
            copied = []
            with patch.object(cli.paths, "store_names", return_value=["店"]), patch.object(
                cli, "load_store", return_value=payload()
            ):
                self.assertEqual(cli.main(ask=lambda _: next(answers),
                                          copy=lambda text: copied.append(text) or True), 0)
            self.assertEqual(len(copied), 1)
            self.assertNotIn("區間外回覆", copied[0])

    def test_period_prompt_retry_and_quit(self):
        answers = iter(["2025", "2023", "2023", "2025"])
        self.assertEqual(cli.prompt_period(lambda _: next(answers)),
                         Period(date(2023, 1, 1), date(2025, 12, 31)))
        self.assertIsNone(cli.prompt_period(lambda _: "q"))


if __name__ == "__main__":
    unittest.main()
