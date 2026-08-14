"""Why nothing was selected / 一則留言都沒選到時的判讀.

An empty selection has at least three causes, and the message this layer used to
print named only the one that blames the operator — check the spelling. Measured
against `03_tokyo/アイドル研究生`: one girl's name returns nothing from 18,954
comments spanning 2024/08/05 to 2026/08/04, scraped the same day, and every
variant the matcher builds returns nothing either. The name was right. She had
started about two weeks earlier, and nobody had written about her yet.

So this module states the facts that separate the causes instead of picking one.
They are already in the store file and cost nothing to read: how many comments it
holds, the window they span, and when it was last fetched. A stale store cannot
contain a girl who started last week, which is the only cause the operator can act
on immediately, so that is the one this says out loud.

Nothing here widens matching. Loosening `select.py`'s measured precision to guess
at an intended name would trade real matches for a hint. The one guess offered is
drawn from girl-scope thread titles — a handful of store-authored strings, not
free text — and it is offered as a question, never applied.
"""
from __future__ import annotations

from datetime import datetime, timezone
from difflib import SequenceMatcher

from scraping import store

from scraping.parser import GIRL_SCOPE, normalise_kana, parse_comment_datetime

from .select import MIN_VARIANT_LENGTH, name_matcher

#: Past this many days a scrape can no longer speak about a girl who has just
#: started. The measured case is a two-week gap between her first shift and the
#: question, so the window that matters is small; a week is inside it, and the
#: cost of being wrong is one incremental re-scrape, which stops at the comments
#: already cached and is cheap. When the store is younger than this the message
#: says so, which rules the cause out rather than leaving it hanging.
STALE_AFTER_DAYS = 7

#: How far a girl-scope board title may be from the typed name and still be worth
#: showing. One character: a substitution, a dakuten, a dropped kana. Two would
#: reach unrelated names of the same length.
MAX_TYPO_DISTANCE = 1

#: Reading the stamp belongs to the module that writes it — see
#: `scraping/store.py`, which owns the file format.
_scraped_at = store.scraped_at


def _days_since(when: datetime | None) -> int | None:
    return max((datetime.now(timezone.utc) - when).days, 0) if when else None


def _scrape_age(payload: dict) -> int | None:
    """Days since this store was fetched, or `None` when the file does not say."""
    return _days_since(_scraped_at(payload))


def _newest_comment_age(payload: dict) -> int | None:
    """Days since the newest cached comment — an upper bound on the scrape age.

    A scrape cannot predate the comments it collected, so a file with no recorded
    scrape time but a comment from yesterday was fetched yesterday at the earliest.
    That is enough to rule staleness out; it can never establish it, because a
    store with nothing new to say looks the same as one nobody has fetched.

    Bakusai prints wall-clock JST with no zone, so this is up to nine hours out,
    which cannot move a count of days by more than one.
    """
    when = parse_comment_datetime(payload.get("last_comment"))
    return _days_since(when.replace(tzinfo=timezone.utc) if when else None)


def _as_date(stamp: str) -> str:
    """The scrape timestamp as a date, written the way comment dates are."""
    return stamp[:10].replace("-", "/")


def _as_day(stamp: str) -> str:
    """A comment timestamp without its time. The window is a span of days."""
    return stamp[:10]


def _ago(days: int | None) -> str:
    if days is None:
        return "時間不明"
    return "今天" if days == 0 else f"{days} 天前"


def _describe_coverage(payload: dict) -> str:
    """What this store is able to answer at all, and how fresh that is."""
    total = payload.get("total_comment_count") or len(payload.get("comments", []))
    first, last = payload.get("first_comment"), payload.get("last_comment")
    window = (
        f"涵蓋 {_as_day(first)} ～ {_as_day(last)}" if first and last else "沒有任何有日期的留言"
    )
    scraped = payload.get("scrape_meta", {}).get("last_scraped_at")
    when = (
        f"{_as_date(scraped)}（{_ago(_scrape_age(payload))}）"
        if scraped
        else "不明（這個檔案沒有記錄抓取時間）"
    )
    return f"  這家店目前有 {total:,} 則留言，{window}；最後抓取：{when}"


def _describe_action(payload: dict) -> str:
    """The one cause the operator can act on now, or the fact that it is ruled out."""
    age = _scrape_age(payload)
    bound = _newest_comment_age(payload)
    if age is None and bound is not None and bound <= STALE_AFTER_DAYS:
        age = bound
    if age is not None and age <= STALE_AFTER_DAYS:
        freshness = "今天抓的" if age == 0 else f" {age} 天前抓的"
        return (
            f"  這份資料是{freshness}，重新抓取幾乎不會多出東西，"
            "所以請往第 1、2 種想：先確認源氏名的寫法，或她本來就還太新。"
        )
    if age is not None:
        reason = f"距離上次抓取已 {age} 天"
    elif bound is not None:
        reason = f"這個檔案沒有記錄抓取時間，最新的留言是 {bound} 天前的"
    else:
        reason = "這個檔案沒有記錄抓取時間，也沒有任何有日期的留言"
    return (
        f"  ⚠️ {reason}，第 3 種最有可能：她入店之後的留言不會在這份資料裡。\n"
        "     請先重新抓取這家店，再查一次。"
    )


def _girl_board_titles(payload: dict) -> list[str]:
    """Titles of the boards that belong to one girl rather than to the store.

    Scope is read from the file, exactly as `select.py` reads it: the board a
    thread came from is the authority on what the thread is for.
    """
    return [
        thread.get("thread_title") or ""
        for thread in payload.get("threads", [])
        if thread.get("scope") == GIRL_SCOPE
    ]


def _matching_characters(name: str, window: str) -> int:
    blocks = SequenceMatcher(None, name, window).get_matching_blocks()
    return sum(block.size for block in blocks)


def _closeness(name: str, title: str) -> int:
    """Characters of the name found, in order, in the closest window of the title.

    A window the length of the name, so a title that merely contains a lot of kana
    cannot accumulate a score. Titles are short and girl-scope boards are rare —
    one thread in twenty-one in the measured store — so scanning them all is free.
    """
    width = len(name)
    windows = range(len(title) - width + 1)
    return max((_matching_characters(name, title[at : at + width]) for at in windows), default=0)


def _describe_board(payload: dict, girl: str) -> str:
    """Her own board, when the store has one that is bare or nearly her name.

    A title the matcher accepts, reaching here, means one thing only: that board
    holds nothing at all, because any comment on it would have been selected as
    hers. The spelling is therefore not the problem — the opposite of what the old
    message asserted. It is stated as a title that matches rather than as her
    board, because the matcher's acceptance is a substring test and 「ナナ」 accepts
    a 「ナナセ」 board; who the board belongs to is exactly the question this cannot
    answer.
    """
    titles = _girl_board_titles(payload)
    matcher = name_matcher(girl)
    matched = [title for title in titles if matcher.matches(title)]
    if matched:
        return (
            f"  ⚠️ 有一個標題對得上的個人板「{matched[0]}」，但板上一則留言都沒有 —— "
            "名字寫法沒問題，只是還沒有人在上面寫過東西。"
        )
    if len(girl) < MIN_VARIANT_LENGTH:
        return ""
    folded = normalise_kana(girl)
    scored = [(_closeness(folded, normalise_kana(title)), title) for title in titles]
    best, title = max(scored, key=lambda pair: pair[0], default=(0, ""))
    if best < len(folded) - MAX_TYPO_DISTANCE:
        return ""
    return f"  個人板標題裡最接近的是「{title}」，請確認源氏名是不是這個寫法。"


def explain_empty(payload: dict, girl: str) -> str:
    """Say why there are no comments, without asserting a cause nothing checked."""
    wanted = (girl or "").strip()
    lines = [
        f"  找不到提到「{wanted}」的留言，但這不代表名字打錯了。",
        _describe_coverage(payload),
        "  可能的原因有三種，光看這裡分不出來：",
        "   1. 源氏名的寫法和留言裡的不一樣（漢字／片假名／伏字）",
        "   2. 她才剛入店，還沒有人寫到她",
        "   3. 這家店抓得太舊，她入店之後的留言還沒進來",
        _describe_action(payload),
        _describe_board(payload, wanted),
    ]
    return "\n".join(line for line in lines if line)
