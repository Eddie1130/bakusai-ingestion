"""Search, pagination and incremental fetching / 搜尋、翻頁與增量抓取.

Pagination runs newest page first: page 1 holds the newest comments and later
pages go back in time. The cutoff and the "already cached" check therefore both
look backwards, and whichever fires first ends the walk.
"""
from __future__ import annotations

import calendar
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import paths

from . import client, parser, store
from .candidates import (
    Grouping,
    StoreCandidate,
    StoreTarget,
    group_candidates,
    # Re-exported: `scraping/__init__.py` publishes this name from here, so it is
    # part of the package's public API rather than an unused import.
    parse_store_target,  # noqa: F401
)
from .parser import Comment, ThreadRef
from .progress import NullProgress, Progress

#: Supported regions, taken from the one table that also owns their directories,
#: so the menu can never offer a region the filesystem does not know.
AREA_CODES: dict[int, str] = paths.AREA_LABELS

DEFAULT_CUTOFF_MONTHS = 24

#: Asked once, before a search is walked, so an operator can decline one that is
#: about to take minutes. Returning `False` cancels.
ConfirmSize = Callable[["SearchSize"], bool]

#: Chooses which of the stores the search found is the one the operator meant.
SelectCandidate = Callable[[Grouping], "StoreCandidate | None"]

#: Offered the whole run once it is decided and before any of it is spent.
#: Returning `False` cancels. An unattended caller passes nothing and is asked
#: nothing — see `refresh.attempt`.
ConfirmPlan = Callable[["ScrapePlan"], bool]

#: Ceiling for a search whose page declares no hit count. Paging then falls back to
#: stopping when a page brings nothing new, and that rule has already been observed
#: failing to terminate once — the header news ticker manufactured a fresh row on
#: every page. It is fixed, but the fallback exists precisely for the day the markup
#: changes again, so it must not be able to walk a paced site without end.
MAX_UNDECLARED_SEARCH_PAGES = 20


def search_url(acode: int, word: str, page: int) -> str:
    return (
        f"https://bakusai.com/sch_thr_thread/acode={acode}/p={page}/"
        f"sch=thr_sch/sch_range=area/word={quote(word, safe='')}/"
    )


def thread_url(ref: ThreadRef, page: int) -> str:
    return (
        f"https://bakusai.com/thr_res/acode={ref.acode}/ctgid={ref.ctgid}/"
        f"bid={ref.bid}/tid={ref.tid}/p={page}/tp=1/"
    )


def months_before(when: datetime, months: int) -> datetime:
    """The same day-of-month `months` earlier, clamped to the shorter month."""
    total = when.year * 12 + (when.month - 1) - months
    year, month_index = divmod(total, 12)
    month = month_index + 1
    day = min(when.day, calendar.monthrange(year, month)[1])
    return when.replace(year=year, month=month, day=day)


class SearchCancelled(Exception):
    """The operator declined a search after being told how long it would take."""


@dataclass(frozen=True)
class SearchSize:
    """How much a search will cost, known once its first page is in hand.

    `total` is the hit count Bakusai declares, summed across the store's names;
    `pages` is what walking them costs, which is the only unit that is uniform —
    one page is one request and every request is paced the same.
    """

    total: int
    pages: int


def _fetch_search_page(acode: int, word: str, page: int, fetch: client.Fetch) -> str:
    """Fetch one search page and refuse anything that is not the page we asked for."""
    html = fetch(search_url(acode, word, page))
    if parser.is_access_error(html):
        raise client.BlockedError(f"rate-limited while searching for {word!r}")

    served = parser.parse_current_page(html)
    if served is not None and served != page:
        raise client.PinnedResponseError(
            f"asked for search page {page} of {word!r} but the server served page {served}"
        )
    return html


def _search_remaining_pages(
    acode: int, word: str, first_html: str, fetch: client.Fetch
) -> list[ThreadRef]:
    """Every hit for one word, given its already-fetched first page.

    Paging is driven by the count the page declares, because that is the only figure
    that is both known up front and correct: the pager truncates on a long search,
    and "stop when a page brings nothing new" was never safe — until the header news
    ticker was excluded from results it never terminated at all. The old behaviour is
    kept only for a page that declares no count — synthetic test pages, and whatever a
    future change of markup looks like — and it is capped, because that is the case
    where we have already been wrong about termination once.
    """
    found: list[ThreadRef] = []
    seen: set[str] = set()

    def take(html: str) -> int:
        fresh = [ref for ref in parser.parse_search_results(html) if ref.tid not in seen]
        for ref in fresh:
            seen.add(ref.tid)
            found.append(ref)
        return len(fresh)

    take(first_html)
    declared = parser.parse_result_count(first_html)
    if declared is None:
        for page in range(2, MAX_UNDECLARED_SEARCH_PAGES + 1):
            if not take(_fetch_search_page(acode, word, page, fetch)):
                break
        return found

    for page in range(2, parser.pages_for_results(declared) + 1):
        take(_fetch_search_page(acode, word, page, fetch))
    return found


def search_all_threads(
    acode: int,
    target: StoreTarget,
    fetch: client.Fetch,
    on_size: ConfirmSize | None = None,
) -> list[ThreadRef]:
    """Every search hit for this store, across all of its names.

    The names are unioned rather than tried in order: Bakusai normalises kana in
    search, so the same store appears under spellings the operator may not know —
    `ときめき胸キュン女学院` and `ときめき胸きゅん女学院` are both real.

    Each name's first page is fetched before any of them is walked, so `on_size` is
    offered the whole search's length while there is still something to decline —
    a generic word can declare thousands of hits, and the operator's fix for that is
    to type a district in front of the name, not to sit through it.
    """
    firsts = [
        (word, _fetch_search_page(acode, word, 1, fetch)) for word in target.search_names
    ]
    declared = [
        count
        for _word, html in firsts
        if (count := parser.parse_result_count(html)) is not None
    ]
    if on_size is not None and declared:
        size = SearchSize(
            total=sum(declared),
            pages=sum(parser.pages_for_results(count) for count in declared),
        )
        if not on_size(size):
            raise SearchCancelled(f"search for {target.name!r} declined at {size.pages} pages")

    found: dict[str, ThreadRef] = {}
    for word, html in firsts:
        for ref in _search_remaining_pages(acode, word, html, fetch):
            found.setdefault(ref.tid, ref)
    return list(found.values())


def is_dormant(ref: ThreadRef, cutoff: datetime | None) -> bool:
    """True when the search page shows nothing in this thread is recent enough to want.

    Stores that give each girl her own thread accumulate dozens of threads that
    went quiet years ago. Fetching one only to discard every comment costs a
    request each, and the search page already told us the last post time. When it
    did not, or the value cannot be read, the thread is fetched — the cheap check
    must never be the reason data goes missing.
    """
    if cutoff is None:
        return False
    last_post = parser.parse_comment_datetime(ref.last_post_text)
    return last_post is not None and last_post < cutoff


#: Bakusai closes a thread at this many replies and opens a numbered successor —
#: `吉原 アイドル研究生 ⑳` then `21`, `ちさと13` then `ちさと14`. Measured across the
#: 291 threads on disk: 225 sit at exactly this number and **none exceeds it**.
THREAD_REPLY_CAP = 1000


def is_capped(known_numbers: set[int] | None) -> bool:
    """True when the cache already holds the thread's final, unreachable reply.

    A thread at the cap is closed: it cannot gain a reply, so holding reply
    number `THREAD_REPLY_CAP` means holding the newest post it will ever have.

    **The test is the highest number held, not how many are held.** Counting was
    the obvious version and it was wrong: the fetch window truncates a thread, so
    a closed thread is routinely cached in part. Measured across the 295 threads
    on disk, twelve are exactly that — `中洲…` T11 holds 278 replies ending at
    #1000, `吉原 アイドル研究生` T20 holds 148 — and a count-based test skips none
    of them, which is precisely the population this check exists for.

    Comments are walked newest page first, so what is cached is a suffix; a
    suffix reaching the cap cannot be missing anything newer.

    **This check fails closed, and the two beside it fail open.** That asymmetry
    is deliberate. `is_dormant` and `has_nothing_new` are inferences from a
    timestamp the search page printed, so a date that will not parse must mean
    "fetch it" — the cheap check must never be the reason data goes missing. The
    cap is not an inference. It is arithmetic over comments already in hand, and
    making it defer to a date would reintroduce exactly the failure it removes:
    one unparseable timestamp turning 174 free skips into 174 fetched threads.
    """
    return bool(known_numbers) and max(known_numbers) >= THREAD_REPLY_CAP


def has_nothing_new(ref: ThreadRef, newest_cached: str | None) -> bool:
    """True when the search page shows no post newer than what is already cached.

    A re-run otherwise spends one request per thread just to learn that its first
    page holds nothing new, which on a store with a hundred threads is minutes of
    waiting for no data. The search page already reported each thread's last post.

    Fails open: with no cached comments, no reported last post, or a timestamp that
    cannot be read, the thread is fetched. If the search index lags behind the
    thread, the worst case is that new comments arrive one run later, never that
    they are lost — the next search will report the newer time.
    """
    if not newest_cached:
        return False
    last_post = parser.parse_comment_datetime(ref.last_post_text)
    cached = parser.parse_comment_datetime(newest_cached)
    if last_post is None or cached is None:
        return False
    return last_post <= cached


def thread_page_budget(ref: ThreadRef, known_numbers: set[int] | None = None) -> int:
    """How many pages walking this one thread is expected to cost.

    Its whole length when nothing of it is cached. Otherwise only the part above
    the newest cached reply, plus the page that straddles the boundary: pages run
    newest first, so a re-run stops as soon as it meets a comment it already has.
    Without that correction a re-scrape of a 1,000-reply thread would be budgeted
    at 20 pages to fetch the one page that holds the new replies.
    """
    whole = parser.pages_for(ref.reply_count)
    if not known_numbers or not ref.reply_count:
        return whole
    unseen = ref.reply_count - max(known_numbers)
    return min(whole, parser.pages_for(unseen) + 1)


#: Why a thread belonging to the store is not going to be fetched. Named rather
#: than merely dropped: a run that skips fifteen of a store's twenty-one threads
#: and says nothing is indistinguishable, from the outside, from one that lost
#: them. Each reason has a different remedy — a capped thread is finished, a
#: dormant one needs `--full`, one with nothing new needs only patience.
SKIP_CAPPED = "capped"
SKIP_DORMANT = "dormant"
SKIP_NOTHING_NEW = "nothing_new"


def skip_reason(
    ref: ThreadRef,
    known_numbers: set[int] | None,
    cutoff: datetime | None,
    newest_cached: str | None,
) -> str:
    """Why this thread will not be fetched, or `""` when it will be.

    The three tests gathered in one place because they share a property that the
    whole plan rests on: every one of them reads the search page and the cache
    alone, so none of them costs a request and all of them are answerable before
    the walk starts.
    """
    if is_capped(known_numbers):
        return SKIP_CAPPED
    if is_dormant(ref, cutoff):
        return SKIP_DORMANT
    if has_nothing_new(ref, newest_cached):
        return SKIP_NOTHING_NEW
    return ""


@dataclass(frozen=True)
class PlannedThread:
    """One of the store's threads and what the run intends to do with it.

    `pages` is what walking it is expected to cost, and is zero for a thread that
    will not be walked — a skip is not work that finished instantly.
    """

    ref: ThreadRef
    pages: int = 0
    skip: str = ""

    @property
    def walkable(self) -> bool:
        return not self.skip


@dataclass(frozen=True)
class ScrapePlan:
    """Everything the run has decided before it spends its first request.

    One page is one request, and every request costs the same, so `pages` is the
    run's length in the only unit that is uniform. It is available this early
    because the search page prints each thread's reply count, and because every
    skip is settled from that same page — which is what makes the plan something
    that can be shown to an operator while declining it still saves anything.

    An estimate, and an upper bound: a walk that meets the cutoff stops before a
    thread's last page, and nothing here can know where in a thread that falls.
    The bar reconciles what a thread did not need — see `progress.ScrapeProgress`.
    """

    store_name: str
    threads: tuple[PlannedThread, ...] = ()
    #: Threads that name the store but cannot be said to belong to any one of the
    #: candidates. Carried here so the omission is announced on the same screen as
    #: the plan rather than scrolling past ahead of it.
    unassigned: tuple[ThreadRef, ...] = ()

    @property
    def walkable(self) -> tuple[PlannedThread, ...]:
        return tuple(planned for planned in self.threads if planned.walkable)

    @property
    def skipped(self) -> tuple[PlannedThread, ...]:
        return tuple(planned for planned in self.threads if not planned.walkable)

    @property
    def pages(self) -> int:
        return sum(planned.pages for planned in self.walkable)


def build_plan(
    store_name: str,
    refs: Sequence[ThreadRef],
    known: dict[str, set[int]],
    newest_cached: dict[str, str],
    cutoff: datetime | None,
    unassigned: Sequence[ThreadRef] = (),
) -> ScrapePlan:
    """Decide the whole run before any of it is spent."""
    planned = []
    for ref in refs:
        skip = skip_reason(ref, known.get(ref.tid), cutoff, newest_cached.get(ref.tid))
        pages = 0 if skip else thread_page_budget(ref, known.get(ref.tid))
        planned.append(PlannedThread(ref=ref, pages=pages, skip=skip))
    return ScrapePlan(store_name, tuple(planned), tuple(unassigned))


def _fetch_thread_page(
    ref: ThreadRef, page: int, fetch: client.Fetch
) -> tuple[str, tuple[Comment, ...]]:
    """Fetch one page and refuse anything that is not the page we asked for."""
    html = fetch(thread_url(ref, page))
    if parser.is_access_error(html):
        raise client.BlockedError(f"rate-limited on tid={ref.tid} page {page}")

    served = parser.parse_current_page(html)
    if served is not None and served != page:
        raise client.PinnedResponseError(
            f"asked for page {page} of tid={ref.tid} but the server served page {served} — "
            "the response parses cleanly but is the wrong page"
        )

    comments = parser.parse_comments(html)
    if not comments:
        if parser.parse_comment_count(html) == 0:
            return html, ()  # a thread that genuinely has no replies yet
        if len(html) < parser.BLOCK_BODY_MAX_BYTES:
            raise client.BlockedError(
                f"tid={ref.tid} page {page} came back collapsed and empty — "
                "rate-limited, even though the usual error markers were absent"
            )
        raise client.ParserFailure(
            f"tid={ref.tid} page {page} returned a healthy-looking page with no comments — "
            "the selectors are probably stale"
        )
    return html, comments


def fetch_thread(
    ref: ThreadRef,
    fetch: client.Fetch,
    cutoff: datetime | None = None,
    known_numbers: set[int] | None = None,
    on_progress: Progress | None = None,
) -> tuple[ThreadRef, list[Comment]]:
    """Walk a thread from its newest page back, stopping at the cutoff or at known data.

    Returns the thread with its opening post filled in, alongside the comments. The
    opening post comes free with the first page and is often the most informative
    text there — a thread with no replies at all can still open with a real report.
    """
    known = known_numbers or set()
    report = on_progress or NullProgress()
    html, comments = _fetch_thread_page(ref, 1, fetch)
    ref = replace(ref, description=parser.parse_thread_description(html))
    total_pages = parser.parse_total_pages(html)

    collected: list[Comment] = []
    page = 1
    while True:
        reached_boundary = False
        for comment in comments:
            if comment.number in known:
                reached_boundary = True
                continue
            posted_at = parser.parse_comment_datetime(comment.datetime_text)
            if cutoff is not None and posted_at is not None and posted_at < cutoff:
                reached_boundary = True
                continue
            collected.append(comment)

        report.page(page, total_pages, f"已取得 {len(collected)} 則")

        if reached_boundary or page >= total_pages:
            break
        page += 1
        _, comments = _fetch_thread_page(ref, page, fetch)

    return ref, collected


def resolve_target(acode: int, target: StoreTarget, fetch: client.Fetch) -> StoreTarget:
    """Decide whether the operator's first word is a district or part of the store's name.

    `渋谷 マテリアル` and `TOKYO BUNNYS CLUB` are typed the same way and mean different
    things, and no rule over the words themselves tells them apart. Bakusai's search
    does: it treats a multi-word term as *all of these words*, so a district in front of
    a name narrows the result set while a word that is part of the name does not change
    it. One extra first page settles it.

    The district is then kept out of the search. Searching `渋谷 マテリアル` returns 101
    hits against `マテリアル`'s 223, and what it drops is every girl thread that names
    only the store — exactly the threads the store needs most.
    """
    if len(target.search_names) != 1 or len(target.name.split()) < 2:
        return target

    district, _, remainder = target.name.partition(" ")
    remainder = remainder.strip()
    if not remainder:
        return target

    whole = parser.parse_result_count(_fetch_search_page(acode, target.name, 1, fetch))
    without = parser.parse_result_count(_fetch_search_page(acode, remainder, 1, fetch))
    if whole is None or without is None or without <= whole:
        return target
    return StoreTarget(name=remainder, search_names=(remainder,), district=district)


def stored_anchors(acode: int) -> dict[frozenset[str], str]:
    """Every store already scraped in this region, by the identity it was scraped under.

    Read from `info.json` rather than the comments file, which reaches 19 MB — a
    re-run must not have to open every store in a region to find out which directory
    holds the one it is about to scrape.
    """
    found: dict[frozenset[str], str] = {}
    for name in paths.store_names(acode):
        info = store.load(paths.info_path(acode, name))
        recorded = (info or {}).get("store_anchor")
        if recorded:
            found[frozenset(recorded)] = name
    return found


def resolve_candidate(
    acode: int,
    grouping: Grouping,
    target: StoreTarget,
    select: SelectCandidate | None = None,
) -> StoreCandidate | None:
    """Which of the stores the search found is the one being scraped.

    The operator is asked only when nothing else settles it, in this order:

    1. **A store already scraped in this region.** Its identity was recorded the first
       time, so a re-run is unattended even when a same-named store has opened since.
    2. **The district the operator typed.** `渋谷 マテリアル` says which one already.
    3. **There being only one.** The ordinary case, and it costs no keystroke.
    """
    if not grouping.candidates:
        return None

    known = stored_anchors(acode)
    remembered = [c for c in grouping.candidates if c.key in known]
    if len(remembered) == 1:
        return remembered[0]

    if target.district:
        answering = grouping.answering_to(target.district)
        if len(answering) == 1:
            return answering[0]

    if len(grouping.candidates) == 1:
        return grouping.candidates[0]

    return select(grouping) if select is not None else None


def scrape_store(
    acode: int,
    target: StoreTarget,
    fetch: client.Fetch | None = None,
    cutoff_months: int = DEFAULT_CUTOFF_MONTHS,
    full: bool = False,
    now: datetime | None = None,
    on_progress: Progress | None = None,
    select_candidate: SelectCandidate | None = None,
    on_size: ConfirmSize | None = None,
    on_plan: ConfirmPlan | None = None,
) -> Path | None:
    """Scrape one store into its cached file. Returns the path, or `None` if not found."""
    fetch = fetch or client.create_fetch()
    reference_time = now or datetime.now()
    cutoff = None if full else months_before(reference_time, cutoff_months)

    target = resolve_target(acode, target, fetch)
    hits = search_all_threads(acode, target, fetch, on_size=on_size)
    if not hits:
        return None

    grouping = group_candidates(hits, target)
    chosen = resolve_candidate(acode, grouping, target, select_candidate)
    if chosen is None or not chosen.threads:
        return None

    # A store that renames itself keeps writing where its history already is. Its
    # directory is only named on the first scrape; afterwards it is found by identity.
    name = stored_anchors(acode).get(chosen.key, chosen.name)
    path = paths.bakusai_path(acode, name)
    info = paths.info_path(acode, name)
    existing = store.load(path)
    known = store.known_comment_numbers(existing)
    newest_cached = store.newest_comment_times(existing)

    # Skipping is decided before the walk begins, not inside it: every test reads
    # the search page alone and spends no request, so resolving them up front costs
    # nothing and is what lets the run state its own length. See `ScrapePlan`.
    plan = build_plan(
        name, chosen.threads, known, newest_cached, cutoff, grouping.unassigned
    )
    # Offered however the store was settled, not only when a menu was shown. The
    # three paths that skip the menu — one store, a typed district, a re-run — are the
    # common ones, and a plan shown only beside a menu is silent in all three.
    if on_plan is not None and not on_plan(plan):
        raise SearchCancelled(f"scrape of {name!r} declined at {plan.pages} pages")

    report = on_progress or NullProgress()
    report.start(plan.pages)

    payload = existing
    for planned in plan.walkable:
        ref = planned.ref
        report.thread(f"討論串 {ref.title}", planned.pages)
        ref, comments = fetch_thread(
            ref, fetch, cutoff, known.get(ref.tid, set()), report
        )
        # A thread with no replies is still worth keeping when it opens with a
        # report of its own; only a thread with nothing at all is skipped.
        if not comments and not ref.description and ref.tid not in known:
            continue

        # Save after every thread. A store with per-girl threads can take half an
        # hour, and saving only at the end meant an interruption threw all of it
        # away. The next run resumes: saved threads stop at page one.
        payload = store.merge(
            payload, [(ref, comments)], name, acode, completed=False
        )
        store.save(path, payload)

    if payload is None:
        return None

    payload = store.merge(payload, [], name, acode, completed=True)
    payload = store.with_store_anchor(payload, chosen.key)
    store.save(path, payload)
    store.save_info(info, payload)
    return path
