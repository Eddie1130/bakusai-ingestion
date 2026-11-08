"""Interactive loop / 互動主迴圈.

One store at a time, on demand. All operator-facing output is Traditional Chinese;
technical detail from an exception is shown on its own line, clearly labelled.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path

from . import client, failures, parser, refresh, store, stores
from .lock import ScrapeInProgress, scrape_lock
from .parser import ThreadRef
from .progress import ScrapeProgress, describe_wait
from .candidates import ANCHOR_SCOPE_ORDER, Grouping, StoreCandidate, StoreTarget
from .scraper import (
    AREA_CODES,
    DEFAULT_CUTOFF_MONTHS,
    SKIP_CAPPED,
    SKIP_DORMANT,
    SKIP_NOTHING_NEW,
    ScrapePlan,
    SearchCancelled,
    SearchSize,
    months_before,
    parse_store_target,
    scrape_store,
)

Ask = Callable[[str], str]

QUIT_COMMANDS = frozenset({"q", "quit", "exit"})
AFFIRMATIVE = frozenset({"y", "yes"})
VIEW_COMMANDS = frozenset({"v", "V"})

#: Below this many comments inside the fetch window, the sample is too thin to
#: draw conclusions from. The measured 24-month median for a store is ~307.
MIN_USEFUL_RECENT_COMMENTS = 100

#: Above this many search pages the operator is asked before the walk starts. Set
#: above `金太郎`, the longest search measured at 504 hits over 17 pages: that is a
#: real store name and interrupting it would be noise. A search this much longer is
#: a word too generic to be a store, and the operator's fix is to type a district in
#: front of it rather than to sit through it.
SEARCH_CONFIRM_PAGES = 30

#: What each board is for. Store-level and girl-level talk answer different
#: questions, so the operator should see which is which before choosing.
SCOPE_LABELS = {
    parser.STORE_SCOPE: "【店家】",
    parser.GIRL_SCOPE: "【女生】",
    parser.GENERAL_SCOPE: "【泛談】",
    parser.UNKNOWN_SCOPE: "【不明】",
}

#: Why a thread is not being fetched, in the operator's words. Ordered as the run
#: itself decides them, so the summary reads in a stable order run to run, and cut
#: to one width so the detail listing lines up without a table.
SKIP_LABELS: dict[str, str] = {
    SKIP_CAPPED: "已滿千則封串",
    SKIP_DORMANT: "超出抓取期間",
    SKIP_NOTHING_NEW: "沒有新的留言",
}

#: The widest skip label, so a longer one added later still aligns.
SKIP_LABEL_WIDTH = max(len(label) for label in SKIP_LABELS.values())


def _area_menu() -> str:
    return "  ".join(f"{code}={name}" for code, name in sorted(AREA_CODES.items()))


def prompt_area(ask: Ask = input) -> int | None:
    """Ask which region to search. Returns `None` when the operator quits."""
    while True:
        raw = ask(f"請選擇地區 ({_area_menu()})，q 離開：").strip()
        if raw.lower() in QUIT_COMMANDS:
            return None
        if raw.isdigit() and int(raw) in AREA_CODES:
            return int(raw)
        print("  地區代碼無效，請重新輸入")


def prompt_store(ask: Ask = input) -> StoreTarget | None:
    """Ask for a store name. Supports the `嵐(錦)` alias syntax."""
    while True:
        raw = ask("請輸入店名（可加小區，例 渋谷 マテリアル；別名寫成 嵐(錦)），q 離開：").strip()
        if raw.lower() in QUIT_COMMANDS:
            return None
        target = parse_store_target(raw)
        if target is not None:
            return target
        print("  店名無效，請重新輸入")


def prompt_full_history(ask: Ask = input) -> bool:
    """Ask whether to ignore the fetch window. Full history can take far longer."""
    raw = ask(
        f"抓取範圍：預設只取最近 {DEFAULT_CUTOFF_MONTHS} 個月。"
        "要改抓全部歷史嗎？(y/N，熱門店可能需要數十分鐘)："
    ).strip()
    return raw.lower() in AFFIRMATIVE


#: Shared with every other entry point's progress reporting, so the same wait
#: quoted in two places cannot come out different.
_describe_wait = describe_wait


def report_search_size(size: SearchSize, ask: Ask = input) -> bool:
    """State how long the search will take, and ask only when that is long.

    The length is knowable after one request and the walk is not interruptible, so
    stating it is what stops a mistyped word from becoming an unexplained wait. The
    question is held back until the length is beyond any real store name, because a
    prompt on every ordinary search is one the operator learns to answer blindly.
    """
    print(f"  共 {size.total:,} 件 / 約 {size.pages} 頁 / {_describe_wait(size.pages)}")
    if size.pages <= SEARCH_CONFIRM_PAGES:
        return True
    print("  ⚠️ 這個詞太泛，可能不是店名。建議加上小區縮小範圍（例：渋谷 マテリアル）")
    return ask("  仍要繼續嗎？(y/N)：").strip().lower() in AFFIRMATIVE


def render_candidates(grouping: Grouping) -> str:
    """Show every store the search found, so the operator can recognise theirs.

    Each is named by its own thread title rather than by what was typed, because that
    is what tells `渋谷 マテリアル 元祖清楚系デリヘル` from `巣鴨 マテリアル` and from
    `町田 マテリアルガールズ` — three businesses one search returns together.
    """
    lines = [f"  找到 {len(grouping.candidates)} 家店："]
    for position, candidate in enumerate(grouping.candidates, start=1):
        scopes = {parser.board_scope(label) for label in candidate.board_labels}
        kinds = "".join(SCOPE_LABELS.get(scope, "") for scope in ANCHOR_SCOPE_ORDER if scope in scopes)
        lines.append(
            f"   [{position}] {candidate.name}"
            f"　{len(candidate.threads)} 串"
            f"　最新 {candidate.latest_post or '不明'}"
        )
        lines.append(f"       {'　'.join(candidate.board_labels) or '板塊不明'}　{kinds}")
    return "\n".join(lines)


def render_candidate_threads(candidate: StoreCandidate, position: int) -> str:
    """Every thread of one store, newest first, for confirming before committing."""
    lines = [f"  [{position}] {candidate.name}　共 {len(candidate.threads)} 串："]
    for thread in candidate.threads:
        lines.append(
            f"      {thread.last_post_text or '日期不明':17} {thread.title}"
        )
    return "\n".join(lines)


def render_unassigned(unassigned: Sequence[ThreadRef]) -> str:
    """The threads that name the store but cannot be said to belong to any one of them."""
    lines = [f"  無法判定歸屬的 {len(unassigned)} 串："]
    for thread in unassigned:
        lines.append(
            f"      {thread.last_post_text or '日期不明':17} {thread.title}"
            f"　（{thread.board_label or '板塊不明'}）"
        )
    return "\n".join(lines)


def describe_skips(plan: ScrapePlan) -> str:
    """The skipped threads counted by reason, as one line.

    Counted rather than listed, because the count is what tells a re-run that found
    nothing new from a window that is quietly excluding half the store.
    """
    tallies = [
        f"{label} {sum(1 for planned in plan.skipped if planned.skip == reason)} 串"
        for reason, label in SKIP_LABELS.items()
        if any(planned.skip == reason for planned in plan.skipped)
    ]
    return "・".join(tallies)


def render_plan(plan: ScrapePlan) -> str:
    """What the run is about to do, before it spends a single request.

    Every figure here is known from the search page and the cache alone, so the
    whole thing is free — and stating it is what turns a half-hour walk from
    something that happens to the operator into something they agreed to.
    """
    lines = [f"  抓取計畫：{plan.store_name}"]
    if plan.walkable:
        lines.append(
            f"    要抓 {len(plan.walkable)} 串"
            f" / 約 {plan.pages} 頁 / {_describe_wait(plan.pages)}"
        )
    else:
        lines.append("    要抓 0 串——這家店已經是最新的，不會發出任何請求")
    if plan.skipped:
        lines.append(f"    跳過 {len(plan.skipped)} 串（{describe_skips(plan)}）")
    if plan.unassigned:
        lines.append(
            f"    ⚠️ 另有 {len(plan.unassigned)} 串提及店名但無法判定屬於哪一家，已排除"
        )
    return "\n".join(lines)


def render_plan_detail(plan: ScrapePlan) -> str:
    """Every thread the plan touched, named, so the summary's counts can be checked.

    A thread naming the store that answers to two of them at once, or to none while its
    board holds several, is a coin toss. Guessing puts another store's girls under this
    one, which nothing downstream can see; leaving it out costs data, which the operator
    can see — so it is left out, and named here rather than merely counted.
    """
    sections: list[str] = []
    if plan.walkable:
        lines = [f"  要抓的 {len(plan.walkable)} 串："]
        for planned in plan.walkable:
            lines.append(
                f"      {planned.ref.last_post_text or '日期不明':17}"
                f" {planned.pages:>3} 頁  {planned.ref.title}"
            )
        sections.append("\n".join(lines))
    if plan.skipped:
        lines = [f"  跳過的 {len(plan.skipped)} 串："]
        for planned in plan.skipped:
            lines.append(
                f"      {SKIP_LABELS.get(planned.skip, planned.skip).ljust(SKIP_LABEL_WIDTH, '　')}"
                f"  {planned.ref.title}"
            )
        sections.append("\n".join(lines))
    if plan.unassigned:
        sections.append(render_unassigned(plan.unassigned))
    return "\n\n".join(sections)


#: Shown under the plan. `v` is a loop rather than a one-shot: printing the detail
#: and falling straight through to the walk put the listing on screen for exactly
#: as long as it took the progress bar to scroll it away.
PLAN_PROMPT = "  Enter 開始抓取，v 檢視明細，q 取消："


def confirm_plan(plan: ScrapePlan, ask: Ask = input) -> bool:
    """Show the run and wait. `False` means the operator declined it."""
    print(render_plan(plan))
    while True:
        raw = ask(PLAN_PROMPT).strip()
        if raw.lower() in QUIT_COMMANDS:
            return False
        if not raw:
            return True
        if raw in VIEW_COMMANDS:
            print(render_plan_detail(plan))
            continue
        print("  請按 Enter 開始，或輸入 v 檢視明細、q 取消")


def prompt_candidate(grouping: Grouping, ask: Ask = input) -> StoreCandidate | None:
    """Ask which of the stores the search found is the one meant.

    Reached only when nothing else settled it — not on a re-run, not when the operator
    typed a district, and not when the search found one store. `None` means they quit.
    """
    print(render_candidates(grouping))
    while True:
        raw = ask(
            f"  請選擇店家編號（1-{len(grouping.candidates)}；?1 展開該家全部標題），q 離開："
        ).strip()
        if raw.lower() in QUIT_COMMANDS:
            return None

        if raw.startswith(("?", "？")):
            wanted = raw[1:].strip()
            if not wanted:
                print(render_candidates(grouping))
            elif wanted.isdigit() and 1 <= int(wanted) <= len(grouping.candidates):
                print(render_candidate_threads(grouping.candidates[int(wanted) - 1], int(wanted)))
            else:
                print("  編號無效，? 後面請接店家編號，或單獨輸入 ? 重看清單")
            continue

        if raw.isdigit() and 1 <= int(raw) <= len(grouping.candidates):
            return grouping.candidates[int(raw) - 1]
        print("  編號無效，請重新輸入")


def describe_result(
    target: StoreTarget, path: Path | str | None, payload: dict | None, recent_count: int
) -> str:
    """The message shown after a scrape finishes."""
    if path is None:
        return f"  找不到「{target.name}」的討論串"
    total = (payload or {}).get("total_comment_count", 0)
    threads = len((payload or {}).get("threads", []))
    message = f"  已儲存：{path}\n  共 {threads} 串、{total} 則留言"
    if recent_count < MIN_USEFUL_RECENT_COMMENTS:
        message += f"\n  ⚠️ 期間內僅 {recent_count} 則，資料不足以判斷"
    return message


#: Shown when another process — another terminal, an unattended refresh — is
#: already scraping. It refuses rather than queues, because blocking would hang
#: for however long that scrape has left and read as a crash.
SCRAPE_IN_PROGRESS = "  已經有抓取在進行中，請等它結束再試"


def _under_scrape_lock(work: Callable[[], str]) -> str:
    """Run one scraping action, or say why it cannot start.

    The lock is taken before anything is printed and before a single request is
    spent, so a refusal costs nothing and says so immediately.
    """
    try:
        with scrape_lock():
            return work()
    except ScrapeInProgress as exc:
        return f"{SCRAPE_IN_PROGRESS}\n  技術細節：{exc}"


def scrape_once(
    acode: int,
    target: StoreTarget,
    fetch: client.Fetch | None = None,
    cutoff_months: int = DEFAULT_CUTOFF_MONTHS,
    full: bool = False,
    now: datetime | None = None,
    ask: Ask = input,
) -> str:
    """Scrape one store and return the message to show the operator."""
    return _under_scrape_lock(
        lambda: _scrape_holding_lock(acode, target, fetch, cutoff_months, full, now, ask)
    )


def _scrape_holding_lock(
    acode: int,
    target: StoreTarget,
    fetch: client.Fetch | None,
    cutoff_months: int,
    full: bool,
    now: datetime | None,
    ask: Ask,
) -> str:
    window = "全部歷史" if full else f"最近 {cutoff_months} 個月"
    print(f"  搜尋「{target.name}」（{AREA_CODES[acode]}，{window}）…")
    try:
        with ScrapeProgress() as report:
            path = scrape_store(
                acode,
                target,
                fetch,
                cutoff_months=cutoff_months,
                full=full,
                now=now,
                on_progress=report,
                select_candidate=lambda grouping: prompt_candidate(grouping, ask),
                on_size=lambda size: report_search_size(size, ask),
                on_plan=lambda plan: confirm_plan(plan, ask),
            )
    except SearchCancelled:
        return "  已取消，沒有抓取任何資料"
    except client.ScrapeError as exc:
        return failures.indented(failures.describe(exc))

    payload = store.load(path) if path is not None else None
    cutoff = None if full else months_before(now or datetime.now(), cutoff_months)
    return describe_result(target, path, payload, store.count_since(payload, cutoff))


def main(ask: Ask = input) -> int:
    """Run the interactive loop until the operator quits.

    Choose a new scrape or update a store already on disk, then show the plan.
    """
    print("爆サイ 店家留言抓取工具")
    while True:
        mode = prompt_mode(ask)
        if mode is None:
            break
        if mode == "update":
            target_store = prompt_update_store(stores.scraped(), ask)
            if target_store is not None:
                print(update_once(target_store, ask=ask))
            continue
        acode = prompt_area(ask)
        if acode is None:
            break
        target = prompt_store(ask)
        if target is None:
            break
        print(scrape_once(acode, target, full=prompt_full_history(ask), ask=ask))
    print("結束")
    return 0


def prompt_mode(ask: Ask = input) -> str | None:
    while True:
        raw = ask("請選擇模式（Enter=1 抓取店家；2=更新已抓店家），q 離開：").strip().lower()
        if raw in QUIT_COMMANDS:
            return None
        if raw in {"", "1", "2"}:
            return "update" if raw == "2" else "scrape"
        print("  模式無效，請重新輸入")


def prompt_update_store(
    options: Sequence[stores.ScrapedStore], ask: Ask = input,
) -> stores.ScrapedStore | None:
    """Choose a specific local store, including its region, without a search."""
    if not options:
        print("  尚未抓取任何店家，請先使用抓取模式")
        return None
    for position, option in enumerate(options, start=1):
        print(f"  [{position}] {option.label}")
    while True:
        raw = ask(f"請選擇要更新的店家（1-{len(options)}），q 返回：").strip()
        if raw.lower() in QUIT_COMMANDS:
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]
        print("  編號無效，請重新輸入")


def update_once(
    target: stores.ScrapedStore, ask: Ask = input,
    fetch: client.Fetch | None = None,
) -> str:
    """Search for new threads and incrementally merge the chosen store's data."""
    print(f"  更新「{target.label}」：搜尋最新討論串與留言，保留原有歷史資料。")
    print(f"  新抓取範圍為最近 {DEFAULT_CUTOFF_MONTHS} 個月，已快取留言不會重複加入。")
    with ScrapeProgress() as report:
        outcome = refresh.attempt(
            target, report, fetch=fetch,
            on_plan=lambda plan: confirm_plan(plan, ask),
        )
    return outcome.message


if __name__ == "__main__":
    raise SystemExit(main())
