"""Update menu and incremental refresh regressions without live site requests."""
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from unittest.mock import patch

import paths
from scraping import cli, refresh, scraper, store, stores
from scraping.candidates import Grouping, StoreCandidate, StoreTarget, group_candidates
from scraping.parser import Comment, ThreadRef
from scraping.progress import NullProgress


TARGET = stores.ScrapedStore(3, "渋谷 店名")
KEY = frozenset({"渋谷"})
NOW = datetime(2026, 11, 8)


def ref(tid, title="店名", stamp="2026/11/08 12:00", count=2):
    return ThreadRef(3, 103, 1, tid, title, stamp, "東京風俗・お店", reply_count=count)


def candidate(key, refs):
    return StoreCandidate(key, "搜尋上的店名", tuple(refs), frozenset({"東京風俗"}))


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, {
            "BAKUSAI_DATA_ROOT": self.temp.name + "/data",
            "BAKUSAI_VAR_ROOT": self.temp.name + "/var",
        })
        env.start()
        self.addCleanup(env.stop)
        output = redirect_stdout(io.StringIO())
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)

    def save_initial(self, *, key=KEY):
        data = store.merge(None, [
            (ref("100", count=1), [Comment(1, "2026/11/01 12:00", "原有留言")]),
            (ref("101", count=1), [Comment(1, "2025/01/01 12:00", "搜尋消失的舊串")]),
        ], TARGET.name, TARGET.acode)
        if key is not None:
            data = store.with_store_anchor(data, key)
        store.save(paths.bakusai_path(TARGET.acode, TARGET.name), data)
        store.save_info(paths.info_path(TARGET.acode, TARGET.name), data)
        return data

    def test_hanakage_plan_does_not_reuse_yae_directory_with_same_district_anchor(self):
        yae_ref = ThreadRef(7, 103, 3913, "100", "飛田新地 八重", board_label="大阪風俗・お店")
        yae = store.with_store_anchor(store.merge(
            None, [(yae_ref, [Comment(1, "2026/11/01 12:00", "八重留言")])], "飛田新地 八重", 7,
        ), frozenset({"飛田新地"}))
        yae_path = paths.bakusai_path(7, "飛田新地 八重")
        store.save(yae_path, yae)
        store.save_info(paths.info_path(7, "飛田新地 八重"), yae)
        before = yae_path.read_bytes()
        flower = ThreadRef(7, 103, 3913, "200", "飛田新地 花影", board_label="大阪風俗・お店")
        plans = []
        with patch.object(scraper, "resolve_target", return_value=StoreTarget("花影", ("花影",), "飛田新地")), patch.object(
            scraper, "search_all_threads", return_value=[flower]
        ), patch.object(scraper, "fetch_thread") as fetch:
            with self.assertRaises(scraper.SearchCancelled):
                scraper.scrape_store(
                    7, StoreTarget("飛田新地 花影", ("飛田新地 花影",)), fetch=lambda _: "",
                    full=True, on_plan=lambda plan: plans.append(plan) or False,
                )
        self.assertEqual(plans[0].store_name, "飛田新地 花影")
        fetch.assert_not_called()
        self.assertEqual(yae_path.read_bytes(), before)

    def test_same_anchor_stores_are_saved_separately(self):
        refs = [ThreadRef(7, 103, 3913, "100", "飛田新地 八重", board_label="大阪風俗・お店")]
        yae = store.with_store_anchor(store.merge(
            None, [(refs[0], [Comment(1, "2026/11/01 12:00", "八重留言")])], "飛田新地 八重", 7,
        ), frozenset({"飛田新地"}))
        yae_path = paths.bakusai_path(7, "飛田新地 八重")
        store.save(yae_path, yae)
        store.save_info(paths.info_path(7, "飛田新地 八重"), yae)
        before = yae_path.read_bytes()
        flower = ThreadRef(7, 103, 3913, "200", "飛田新地 花影", board_label="大阪風俗・お店")
        with patch.object(scraper, "resolve_target", return_value=StoreTarget("花影", ("花影",), "飛田新地")), patch.object(
            scraper, "search_all_threads", return_value=[flower]
        ), patch.object(scraper, "fetch_thread", return_value=(flower, [Comment(1, "2026/11/08 12:00", "花影留言")])):
            path = scraper.scrape_store(
                7, StoreTarget("飛田新地 花影", ("飛田新地 花影",)), fetch=lambda _: "", full=True,
            )
        self.assertEqual(path, paths.bakusai_path(7, "飛田新地 花影"))
        self.assertEqual(store.load(path)["store_name"], "飛田新地 花影")
        self.assertEqual(store.load(path)["comments"][0]["body"], "花影留言")
        self.assertEqual(yae_path.read_bytes(), before)

    def test_cached_anchor_does_not_bias_candidate_selection(self):
        self.save_initial()
        new = candidate(KEY, [ref("200")])
        other = candidate(frozenset({"池袋"}), [ref("900")])
        self.assertIs(scraper.resolve_candidate(
            3, Grouping((new, other), ()), StoreTarget("店名", ("店名",)), select=lambda _: other,
        ), other)

    def test_normal_scrape_keeps_cached_directory_when_same_thread_is_renamed(self):
        self.save_initial()
        changed = ref("100", title="新店名")
        with patch.object(scraper, "search_all_threads", return_value=[changed]), patch.object(
            scraper, "fetch_thread", return_value=(changed, [Comment(2, "2026/11/08 12:00", "新留言")])
        ):
            path = scraper.scrape_store(3, StoreTarget("新店名", ("新店名",)), fetch=lambda _: "", full=True)
        self.assertEqual(path, paths.bakusai_path(3, TARGET.name))
        self.assertEqual(store.load(path)["comments"][0]["comment_id"], "T1-2")
        self.assertEqual(store.load(path)["comments"][1]["comment_id"], "T1-1")

    def test_update_merges_new_comments_and_threads_and_preserves_ids(self):
        original = self.save_initial()
        refs = [ref("100"), ref("200", count=1)]
        grouping = Grouping((candidate(KEY, refs),), ())
        def fetched(thread, _fetch, _cutoff, known, _report):
            if thread.tid == "100":
                self.assertEqual(known, {1})
                return thread, [Comment(2, "2026/11/08 12:00", "最新留言")]
            self.assertEqual(known, set())
            return thread, [Comment(1, "2026/11/08 12:00", "新討論串留言")]
        with patch.object(scraper, "resolve_target", side_effect=lambda _a, t, _f: t), patch.object(
            scraper, "search_all_threads", return_value=refs
        ), patch.object(scraper, "group_candidates", return_value=grouping), patch.object(
            scraper, "fetch_thread", side_effect=fetched
        ) as walk:
            outcome = refresh.attempt(TARGET, NullProgress(), fetch=lambda _: "", now=NOW)
            self.assertTrue(outcome.succeeded, outcome.message)
            self.assertEqual(walk.call_count, 2)
            walk.reset_mock()
            # Search still reports exactly the timestamps now cached: no walk.
            again = refresh.attempt(TARGET, NullProgress(), fetch=lambda _: "", now=NOW)
            self.assertTrue(again.succeeded)
            walk.assert_not_called()
        data = store.load(paths.bakusai_path(TARGET.acode, TARGET.name))
        by_id = store.comments_by_id(data)
        self.assertEqual(by_id["T1-1"]["body"], "原有留言")
        self.assertEqual(by_id["T1-2"]["body"], "最新留言")
        self.assertEqual(by_id["T2-1"]["body"], "搜尋消失的舊串")
        self.assertEqual(by_id["T3-1"]["body"], "新討論串留言")
        self.assertEqual(data["total_comment_count"], 4)
        self.assertEqual(data["scrape_meta"]["first_scraped_at"],
                         original["scrape_meta"]["first_scraped_at"])

    def test_pinned_identity_does_not_choose_other_store(self):
        self.save_initial()
        other = candidate(frozenset({"池袋"}), [ref("900")])
        self.assertIsNone(scraper.resolve_candidate(
            3, Grouping((other,), ()), StoreTarget("店名", ("店名",)),
            existing_store=TARGET.name,
        ))

    def test_pinned_identity_wins_among_multiple_saved_stores(self):
        self.save_initial()
        own = candidate(KEY, [ref("100"), ref("200")])
        other = candidate(frozenset({"池袋"}), [ref("900")])
        self.assertIs(scraper.resolve_candidate(
            3, Grouping((other, own), ()), StoreTarget("店名", ("店名",)),
            existing_store=TARGET.name,
        ), own)

    def test_empty_anchor_still_resolves_with_stable_thread_ids(self):
        self.save_initial(key=frozenset())
        own = candidate(frozenset(), [ref("100"), ref("200")])
        self.assertIs(scraper.resolve_candidate(
            3, Grouping((own,), ()), StoreTarget("店名", ("店名",)),
            existing_store=TARGET.name,
        ), own)

    def test_legacy_store_resolves_from_existing_thread(self):
        self.save_initial(key=None)
        own = candidate(KEY, [ref("100"), ref("200")])
        self.assertIs(scraper.resolve_candidate(
            3, Grouping((own,), ()), StoreTarget("店名", ("店名",)),
            existing_store=TARGET.name,
        ), own)

    def test_real_grouping_resolves_when_saved_name_changes_anchor(self):
        thread = ref("100", title="渋谷 マテリアル 元祖清楚系デリヘル 74")
        original = group_candidates([thread], StoreTarget("マテリアル", ("マテリアル",)))
        chosen = original.candidates[0]
        saved = stores.ScrapedStore(3, chosen.name)
        data = store.with_store_anchor(store.merge(
            None, [(thread, [Comment(1, "2026/11/01 12:00", "舊留言")])],
            saved.name, saved.acode,
        ), chosen.key)
        store.save(paths.bakusai_path(3, saved.name), data)
        store.save_info(paths.info_path(3, saved.name), data)
        for target in (StoreTarget(saved.name, (saved.name,)),
                       StoreTarget("マテリアル 元祖清楚系デリヘル", ("マテリアル 元祖清楚系デリヘル",))):
            grouped = group_candidates([thread], target)
            self.assertNotEqual(grouped.candidates[0].key, chosen.key)
            self.assertIs(scraper.resolve_candidate(
                3, grouped, target, existing_store=saved.name,
            ), grouped.candidates[0])

    def test_no_known_thread_overlap_never_falls_back_to_matching_tokens(self):
        self.save_initial()
        unrelated = candidate(KEY, [ref("900")])
        self.assertIsNone(scraper.resolve_candidate(
            3, Grouping((unrelated,), ()), StoreTarget("店名", ("店名",)),
            existing_store=TARGET.name,
        ))

    def test_cancel_plan_does_not_fetch_or_modify_data(self):
        self.save_initial()
        path = paths.bakusai_path(TARGET.acode, TARGET.name)
        before = path.read_bytes()
        refs = [ref("100"), ref("200")]
        with patch.object(scraper, "resolve_target", side_effect=lambda _a, t, _f: t), patch.object(
            scraper, "search_all_threads", return_value=refs
        ), patch.object(scraper, "group_candidates", return_value=Grouping((candidate(KEY, refs),), ())), patch.object(
            scraper, "fetch_thread"
        ) as walk:
            outcome = refresh.attempt(TARGET, NullProgress(), fetch=lambda _: "", now=NOW,
                                      on_plan=lambda _: False)
        self.assertTrue(outcome.cancelled)
        walk.assert_not_called()
        self.assertEqual(path.read_bytes(), before)

    def test_update_menu_uses_chosen_store_without_region_or_name_entry(self):
        options = (TARGET, stores.ScrapedStore(5, "另一家店"))
        answers = iter(["2", "2", "q"])
        with patch.object(stores, "scraped", return_value=options), patch.object(
            cli, "update_once", return_value="更新完成"
        ) as update:
            self.assertEqual(cli.main(ask=lambda _: next(answers)), 0)
        self.assertEqual(update.call_args.args, (options[1],))

    def test_empty_update_list_and_back_do_not_start_requests(self):
        for options, answers in (((), ["2", "q"]), ((TARGET,), ["2", "q", "q"])):
            iterator = iter(answers)
            with patch.object(stores, "scraped", return_value=options), patch.object(cli, "update_once") as update:
                self.assertEqual(cli.main(ask=lambda _: next(iterator)), 0)
            update.assert_not_called()

    def test_invalid_store_number_retries(self):
        answers = iter(["0", "bad", "2", "1"])
        self.assertEqual(cli.prompt_update_store((TARGET,), lambda _: next(answers)), TARGET)

    def test_original_scrape_flow_remains_available(self):
        answers = iter(["", "3", "店名", "n", "q"])
        with patch.object(cli, "scrape_once", return_value="抓取完成") as scrape:
            self.assertEqual(cli.main(ask=lambda _: next(answers)), 0)
        self.assertEqual(scrape.call_args.args, (3, StoreTarget("店名", ("店名",))))

    def test_update_once_wires_plan_preview_and_errors(self):
        with patch.object(cli, "ScrapeProgress", return_value=NullContext()), patch.object(
            refresh, "attempt", return_value=refresh.Outcome(False, "網站暫時無法存取")
        ) as attempt, patch.object(cli, "confirm_plan", return_value=False) as confirm:
            self.assertEqual(cli.update_once(TARGET, ask=lambda _: "q"), "網站暫時無法存取")
            self.assertFalse(attempt.call_args.kwargs["on_plan"]("plan"))
            confirm.assert_called_once()


class NullContext:
    def __enter__(self):
        return NullProgress()

    def __exit__(self, *_args):
        return False


if __name__ == "__main__":
    unittest.main()
