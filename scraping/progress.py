"""Progress display for a long scrape / 抓取進度顯示.

A scrape of one store can run half an hour, so the operator's question is "how
much longer", not "is it alive". tqdm answers that once it is given the right
unit of work; deciding that unit is what this module is for.

**A page is the unit, because a page is a request and every request costs the
same ~2s.** Threads do not: measured across the 97 threads in `data/`, one spans
between 1 and 20 pages, so a bar counting threads would treat 2 seconds of work
and 40 seconds of work as the same tick. The page budget is known before any
request is spent — the search page prints each thread's 「レス数」 — which is what
makes an estimate possible at all rather than merely a spinner.
"""
from __future__ import annotations

from collections.abc import Callable
from types import TracebackType
from typing import Any, Protocol

from tqdm import tqdm

from . import client

#: Builds the underlying bar. Injected so tests need no terminal.
BarFactory = Callable[..., Any]

#: Long Japanese thread titles otherwise wrap the line and hide the bar itself.
MAX_LABEL_CHARS = 40

#: Below this, a duration reads better in seconds than as "約 1 分鐘".
MINUTES_ABOVE_SECONDS = 90


class Progress(Protocol):
    """What a scrape reports as it runs.

    `start` gives the run's whole page budget, `thread` the share of it the next
    thread is expected to want, and `page` one fetched page. A thread is settled
    against its budget when the next one starts, so no "finished" event is needed.
    """

    def start(self, pages: int) -> None: ...

    def thread(self, label: str, budget: int) -> None: ...

    def page(self, page: int, of: int, note: str = "") -> None: ...


class NullProgress:
    """Accepts every report and shows nothing.

    Lets the scraper call the same methods whether or not anyone is watching,
    instead of guarding each call site with a `None` check.
    """

    def start(self, pages: int) -> None:
        pass

    def thread(self, label: str, budget: int) -> None:
        pass

    def page(self, page: int, of: int, note: str = "") -> None:
        pass

    def close(self) -> None:
        pass


def shorten(label: str) -> str:
    """Cut a label to something that still leaves room for the bar."""
    if len(label) <= MAX_LABEL_CHARS:
        return label
    return label[: MAX_LABEL_CHARS - 1] + "…"


def describe_duration(seconds: float) -> str:
    """How long, in the coarsest unit that still says something useful."""
    if seconds < MINUTES_ABOVE_SECONDS:
        return f"約 {round(seconds)} 秒"
    return f"約 {round(seconds / 60)} 分鐘"


def describe_wait(pages: int) -> str:
    """How long this many pages take at the paced request rate.

    The pace is the one thing about a scrape's duration that is known before it
    starts — every page is one request and every request is held to the same
    interval — so a run can state its own length before spending anything.
    """
    return describe_duration(pages * client.MIN_REQUEST_INTERVAL_SECONDS)


def describe_page(label: str, page: int, of: int, note: str) -> str:
    """What the run is doing right now, as one line.

    Shared by both renderers so a terminal and a phone describe the same moment
    the same way. A one-page thread needs no page counter, and most threads are
    one page.
    """
    parts = [label]
    if of > 1:
        parts.append(f"第 {page}/{of} 頁")
    if note:
        parts.append(note)
    return " · ".join(part for part in parts if part)


class PageBudget:
    """How many pages a run owes and how many it has spent.

    The arithmetic of "how much longer", with no display in it. It lives apart
    from any one renderer because a run may be watched from more than one place,
    and an estimate that differs between them would mean one of them is lying.
    Every renderer asks this object.

    The up-front budget is an estimate, and it is corrected in both directions:

    - **A thread's own page reports the real count.** The search page's 「レス数」
      can be stale, so page one's figure replaces it and the total moves with it.
    - **An early stop hands its unused pages back.** The walk ends at the cutoff
      or at cached comments, usually well before a thread's last page, so a total
      that kept those pages would leave the estimate reading long for the rest of
      the run.

    Nothing is ever credited that was not fetched, and the total never falls below
    what has already been done — an interrupted run stops where it got to instead
    of rounding itself up to done.
    """

    def __init__(self) -> None:
        self.total = 0
        self.done = 0
        self._budget = 0
        self._used = 0

    def start(self, pages: int) -> None:
        self.total = max(pages, 0)

    def thread(self, budget: int) -> None:
        """Begin a thread expected to want `budget` pages, settling the last one."""
        self.settle()
        self._budget = max(budget, 1)
        self._used = 0

    def page(self, of: int) -> None:
        """One fetched page, from a thread now known to hold `of` of them."""
        self._used += 1
        self._rebudget(max(of, 1))
        self.done += 1

    def settle(self) -> None:
        """Return the pages the thread that just ended never needed."""
        unused = self._budget - self._used
        if unused > 0:
            self.total = max(self.total - unused, self.done)
        self._budget = 0
        self._used = 0

    @property
    def remaining(self) -> int:
        return max(self.total - self.done, 0)

    @property
    def fraction(self) -> float:
        """How much of the run is done, as 0.0–1.0. Zero pages means zero done."""
        return self.done / self.total if self.total > 0 else 0.0

    def _rebudget(self, pages: int) -> None:
        """Move the total by however much this thread's real length differs."""
        if pages == self._budget:
            return
        self.total = max(self.total + pages - self._budget, self.done)
        self._budget = pages


class ScrapeProgress:
    """One tqdm bar counting fetched pages against the run's page budget.

    The counting is `PageBudget`'s; this is the display. The bar's total is
    mirrored from it after every event, so the two cannot disagree.
    """

    def __init__(self, make_bar: BarFactory = tqdm) -> None:
        self._make_bar = make_bar
        self._bar: Any | None = None
        self._label = ""
        self._pages = PageBudget()

    def start(self, pages: int) -> None:
        if pages <= 0:
            return  # nothing to fetch: a bar would only claim otherwise
        self._pages.start(pages)
        self._bar = self._make_bar(
            total=pages,
            unit="頁",
            # The overall average, not a recent-window one. Cached pages come back
            # no faster, but a thread that ends early returns budget in a burst,
            # and a windowed rate would read that burst as the run's speed.
            smoothing=0,
            dynamic_ncols=True,
        )

    def thread(self, label: str, budget: int) -> None:
        if self._bar is None:
            return
        self._pages.thread(budget)
        self._bar.total = self._pages.total
        self._label = label
        self._bar.set_description_str(shorten(label), refresh=True)

    def page(self, page: int, of: int, note: str = "") -> None:
        if self._bar is None:
            return
        self._pages.page(of)
        self._bar.total = self._pages.total
        self._bar.update(1)
        self._bar.set_description_str(shorten(describe_page(self._label, page, of, note)), refresh=True)

    def close(self) -> None:
        if self._bar is None:
            return
        self._pages.settle()
        self._bar.total = self._pages.total
        self._bar.close()
        self._bar = None

    def __enter__(self) -> "ScrapeProgress":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is None:
            self.close()
            return
        # An interrupted run settles nothing: the pages it never reached are not
        # savings, and reporting them as such would claim the run had shrunk.
        if self._bar is not None:
            self._bar.close()
            self._bar = None
