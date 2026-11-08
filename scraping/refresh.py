"""Re-scraping a store already on disk / 重新抓取已存在的店家.

The simplest scraping there is, and the only one that needs nobody present. A
store already on disk recorded its identity the first time it was scraped, so
`info.json` settles region, district and target without a menu, without a
candidate list, and without a single question. That is what makes it unattended,
and unattended is what makes it callable by something other than a person — a
terminal one-liner today, a background ingestion loop later.

**Every failure is an outcome, not an exception.** A caller that has just spent
thirty minutes of someone's evening must not lose the run to a raised error, and
a caller analysing a roster must be able to carry on to the next store. The
`succeeded` flag is what decides whether anything built on this data may be
cached: a refresh that was blocked leaves data that is still complete and valid
— merely older — so an answer from it is worth giving and not worth keeping.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import paths

from . import client, failures, progress, store as store_file, stores
from .candidates import StoreTarget
from .lock import ScrapeInProgress, scrape_lock
from .scraper import DEFAULT_CUTOFF_MONTHS, ConfirmPlan, SearchCancelled, scrape_store

#: Another process — a terminal, another job — is scraping. Two scrapers 2.0 s
#: apart are one scraper at 1.0 s, which is past the measured block cliff.
ANOTHER_SCRAPE = "另一個行程正在抓取，等它結束再試一次"

CANCELLED = "已取消。已經抓到的討論串都存好了，下次會從那裡接著抓"

NOT_SCRAPED = "「{name}」尚未抓過，沒有東西可以重抓。"

#: The search came back with nothing belonging to this store — which for a store
#: already on disk is a symptom, not an answer.
NOTHING_FOUND = (
    "重新抓取「{name}」時，搜尋沒有回傳任何屬於它的討論串。\n"
    "店名可能已經改掉，或這次搜尋被限流了。"
)

#: Asked before the walk starts and after every page. Returning true stops the
#: run at the next checkpoint; a reporter with no way to be cancelled says false.
Cancel = Callable[[], bool]


class Cancelled(Exception):
    """A caller stopped the run from inside its own progress reporter."""


def never() -> bool:
    return False


@dataclass(frozen=True)
class Outcome:
    """What a refresh attempt did, for a caller that has to carry on either way."""

    succeeded: bool
    message: str
    cancelled: bool = False


def attempt(
    target: stores.ScrapedStore,
    report: progress.Progress,
    *,
    cancelled: Cancel = never,
    fetch: client.Fetch | None = None,
    cutoff_months: int = DEFAULT_CUTOFF_MONTHS,
    now: datetime | None = None,
    on_plan: ConfirmPlan | None = None,
) -> Outcome:
    """Re-scrape one store, turning every way it can fail into an outcome.

    The file lock is taken here rather than by the caller, and at the last
    possible moment: a job queued behind a half-hour scrape should get its turn
    afterwards rather than having been refused when it was submitted.
    """
    try:
        with scrape_lock():
            path = scrape_store(
                target.acode,
                StoreTarget(target.name, (target.name,)),
                fetch,
                cutoff_months=cutoff_months,
                now=now,
                on_progress=report,
                existing_store=target.name,
                on_plan=on_plan,
                # No menu and no exclusion report: an unattended run has nobody to
                # ask, and a re-run never needs to. `on_size` is not a question
                # here either — it is the only checkpoint the search phase offers,
                # and a cancel arriving in the first seconds would otherwise wait
                # out a seventeen-page search.
                on_size=lambda _size: not cancelled(),
            )
    except ScrapeInProgress as exc:
        return Outcome(False, f"{ANOTHER_SCRAPE}\n{failures.DETAIL_LABEL}：{exc}")
    except (Cancelled, SearchCancelled):
        return Outcome(False, CANCELLED, cancelled=True)
    except client.ScrapeError as exc:
        return Outcome(False, failures.describe(exc))
    if path is None:
        return Outcome(False, NOTHING_FOUND.format(name=target.name))
    return Outcome(True, describe(target))


def describe(target: stores.ScrapedStore) -> str:
    """What the refresh found, once it is over."""
    payload = store_file.load(paths.bakusai_path(target.acode, target.name))
    if payload is None:
        return f"抓完了，但讀不回「{target.name}」的檔案"
    # No completeness warning here: a run that reached this point wrote
    # `last_run_completed`, and one that did not raised instead. A partial file is
    # flagged where it is still true tomorrow — the store listing.
    return (
        f"✅ 已更新「{target.name}」\n"
        f"{len(payload.get('threads', []))} 串"
        f"・{payload.get('total_comment_count', 0):,} 則留言"
    )
