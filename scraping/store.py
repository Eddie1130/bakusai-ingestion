"""Reading, merging and writing a store's comments / 讀取、合併、寫入店家留言.

Threads are identified solely by their Bakusai thread id. A thread's sequence
number is assigned the first time it is seen and then persisted, never recomputed,
because comment ids are the citation primitive the rest of the pipeline depends on
and a renumbered thread would silently redirect every reference to it.
"""
from __future__ import annotations

import json
import os
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from pathlib import Path

from .parser import (
    Comment,
    ThreadRef,
    board_scope,
    parse_comment_datetime,
    reply_references,
)

SCHEMA_VERSION = "2.0"

#: The comment number an opening post is offered under. Bakusai numbers the thread
#: header `#0` and its replies from 1, so this is the site's own convention rather
#: than an invention, it cannot collide with a real comment, and it sorts ahead of
#: every reply under the oldest-first ordering the analysis layer applies.
OPENING_POST_NUMBER = 0

FetchedThread = tuple[ThreadRef, Sequence[Comment]]


#: How `scrape_meta.last_scraped_at` is written. This module owns the file
#: format, so it owns reading it back too — the format literal previously
#: appeared in three layers, which is how a stored field and its readers drift.
SCRAPE_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime(SCRAPE_TIME_FORMAT)


def scraped_at(payload: dict | None) -> datetime | None:
    """When this store was last fetched, or `None` when the file cannot say.

    Unreadable and absent are deliberately the same answer. Anything that acts on
    staleness has to treat "I do not know" as "not known to be fresh"; papering
    over the difference would make an unreadable stamp look like a recent one.
    """
    raw = ((payload or {}).get("scrape_meta") or {}).get("last_scraped_at")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return datetime.strptime(raw.strip(), SCRAPE_TIME_FORMAT).replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


def run_completed(payload: dict | None) -> bool:
    """Whether the run that produced this file finished.

    A long scrape saves after every thread, so a file can legitimately exist
    while still being partial — and `last_scraped_at` is stamped on those partial
    saves too. Anything reading the timestamp to decide freshness must read this
    beside it, or a scrape interrupted at thread two looks perfectly fresh.
    """
    return bool(((payload or {}).get("scrape_meta") or {}).get("last_run_completed", True))


def _sequence_of(index: str) -> int:
    digits = "".join(ch for ch in index if ch.isdigit())
    return int(digits) if digits else 0


def _tid_sort_key(tid: str) -> tuple[int, object]:
    return (0, int(tid)) if tid.isdigit() else (1, tid)


def load(path: Path) -> dict | None:
    """Read a store file, or `None` when it does not exist yet."""
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save(path: Path, payload: dict, indent: int | None = None) -> Path:
    """Write a store file atomically, creating its directory if needed.

    Comment files are written without indentation. Nothing reads them by eye —
    they feed later processing stages — and pretty-printing 2,855 comments costs
    a third of the file for nothing. Use `jq .` when a human does need to look.

    The write goes to a temporary file and is then renamed over the target, so a
    run interrupted mid-write leaves the previous file intact rather than a
    truncated one. This matters because a long scrape saves after every thread.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    separators = None if indent else (",", ":")
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=indent, separators=separators),
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return path


def save_info(path: Path, payload: dict) -> Path:
    """Write the store's identity and provenance beside its comments."""
    info = {
        "store_name": payload["store_name"],
        "acode": payload["acode"],
        # Carried here as well as in the comments file so a re-run can find which
        # directory holds a store without opening every store's comments — the
        # largest is 19 MB, and a region can hold any number of them.
        "store_anchor": sorted(store_anchor(payload) or ()),
        "boards": sorted(
            {(thread["ctgid"], thread["bid"]) for thread in payload.get("threads", [])}
        ),
        "bakusai": {
            **payload.get("scrape_meta", {}),
            "total_comment_count": payload.get("total_comment_count", 0),
            "first_comment": payload.get("first_comment"),
            "last_comment": payload.get("last_comment"),
        },
    }
    # Small, and the one file worth glancing at by eye — keep it readable.
    return save(path, info, indent=2)


def is_opening_post(comment: dict) -> bool:
    """Whether this record is a thread's opening post rather than a reply."""
    return comment.get("comment_number") == OPENING_POST_NUMBER


def opening_posts(payload: dict | None) -> list[dict]:
    """Each thread's opening post, shaped like a comment so it can be cited.

    The opening post is stored on the thread, not among the comments, because it
    is not a reply and the page gives it no number and no timestamp. But it is
    text about the store, frequently the fullest account on the page, and a thread
    nobody ever answered has nothing else. Reaching the analysis layer means
    carrying an id, so it is offered as comment zero of its thread.

    A thread with no opening post yields nothing. That is the ordinary case for a
    busy thread and is not a failure.
    """
    return [
        {
            "comment_id": f"{thread['index']}-{OPENING_POST_NUMBER}",
            "thread_index": thread["index"],
            "comment_number": OPENING_POST_NUMBER,
            "datetime": "",
            "body": thread["description"],
            "reply": [],
        }
        for thread in (payload or {}).get("threads", [])
        if (thread.get("description") or "").strip()
    ]


def comments_by_id(payload: dict | None) -> dict[str, dict]:
    """Every citable post in the file — the replies and the opening posts — by id.

    Anything that works comment-by-comment needs this, and it is the one shape of
    the file that callers would otherwise each rebuild for themselves — which is
    how an earlier version of this tool ended up with several ideas of what a comment id
    meant. The format is owned here, so the index into it is too, and that is why
    opening posts are folded in here rather than by each caller in turn: an id the
    selector can emit but the citation check cannot resolve would report a report's
    own evidence as fabricated.

    This is a view, not a record. Opening posts are never written into `comments`,
    where the next `merge` would read them back as replies.
    """
    posts = list((payload or {}).get("comments", [])) + opening_posts(payload)
    return {post["comment_id"]: post for post in posts}


def known_comment_numbers(payload: dict | None) -> dict[str, set[int]]:
    """Comment numbers already cached, keyed by thread id.

    The scraper uses this to stop walking a thread as soon as it meets what it has.
    """
    meta_by_tid, comments_by_index = _existing_state(payload)
    return {
        tid: set(comments_by_index.get(meta["index"], {}))
        for tid, meta in meta_by_tid.items()
    }


def newest_comment_times(payload: dict | None) -> dict[str, str]:
    """The newest cached comment's timestamp per thread id.

    Compared against the last post time the search page reports, this says whether
    a thread has anything new — without spending a request to find out.
    """
    meta_by_tid, comments_by_index = _existing_state(payload)
    newest: dict[str, str] = {}
    for tid, meta in meta_by_tid.items():
        stamps = [
            comment.get("datetime", "")
            for comment in comments_by_index.get(meta["index"], {}).values()
            if comment.get("datetime")
        ]
        if stamps:
            newest[tid] = max(stamps)
    return newest


def store_anchor(payload: dict | None) -> frozenset[str] | None:
    """What this store's own thread titles say around its name, or `None` if unrecorded.

    This is the store's identity, and recording it is what makes a re-run unattended:
    membership is recomputed from a fresh search every time, so a new volume joins on
    its own and a same-named store opening later is excluded on its own, without the
    operator being asked a second time.
    """
    if not payload:
        return None
    recorded = payload.get("scrape_meta", {}).get("store_anchor")
    return frozenset(recorded) if recorded is not None else None


def with_store_anchor(payload: dict, key: Iterable[str]) -> dict:
    """Record which store this file holds, so later runs need not ask again."""
    return {
        **payload,
        "scrape_meta": {**payload.get("scrape_meta", {}), "store_anchor": sorted(key)},
    }


def count_since(payload: dict | None, cutoff: datetime | None) -> int:
    """How many cached comments are newer than `cutoff`.

    Judging whether a store has enough evidence has to look at *recent* activity;
    a dormant store can hold thousands of comments and still say nothing useful
    about how it is run today.
    """
    if not payload:
        return 0
    comments = payload.get("comments", [])
    if cutoff is None:
        return len(comments)
    recent = 0
    for comment in comments:
        posted_at = parse_comment_datetime(comment.get("datetime"))
        if posted_at is None or posted_at >= cutoff:
            recent += 1
    return recent


def _existing_state(payload: dict | None) -> tuple[dict[str, dict], dict[str, dict[int, dict]]]:
    """Thread metadata keyed by thread id, and comments keyed by index then number."""
    meta_by_tid: dict[str, dict] = {}
    comments_by_index: dict[str, dict[int, dict]] = {}
    if not payload:
        return meta_by_tid, comments_by_index

    for thread in payload.get("threads", []):
        meta_by_tid[str(thread["tid"])] = dict(thread)
        comments_by_index.setdefault(thread["index"], {})
    for comment in payload.get("comments", []):
        index = comment.get("thread_index", "")
        comments_by_index.setdefault(index, {})[int(comment["comment_number"])] = dict(comment)
    return meta_by_tid, comments_by_index


def _with_sequences(
    meta_by_tid: dict[str, dict], fetched: Iterable[FetchedThread]
) -> dict[str, dict]:
    """Return the thread table extended with any unseen thread, oldest thread first.

    Sequence numbers already in the table are never touched, because comment ids
    are built from them and downstream references must stay valid forever.
    """
    extended = dict(meta_by_tid)
    next_sequence = max((_sequence_of(m["index"]) for m in extended.values()), default=0) + 1
    unseen = sorted(
        (ref for ref, _ in fetched if str(ref.tid) not in extended),
        key=lambda ref: _tid_sort_key(str(ref.tid)),
    )
    for ref in unseen:
        extended[str(ref.tid)] = {
            "index": f"T{next_sequence}",
            "tid": str(ref.tid),
            "thread_title": ref.title,
            "ctgid": ref.ctgid,
            "bid": ref.bid,
            "board_label": ref.board_label,
            "scope": board_scope(ref.board_label),
            "description": ref.description,
            # A thread first seen in search results has never had a page fetched.
            "description_checked": False,
            "count": 0,
        }
        next_sequence += 1
    return extended


def _comment_record(index: str, comment: Comment) -> dict:
    return {
        "comment_id": f"{index}-{comment.number}",
        "thread_index": index,
        "comment_number": comment.number,
        "datetime": comment.datetime_text,
        "body": comment.body,
        "reply": [f"{index}-{n}" for n in reply_references(comment.body)],
    }


def merge(
    existing: dict | None,
    fetched: Sequence[FetchedThread],
    store_name: str,
    acode: int,
    now: str | None = None,
    completed: bool = True,
) -> dict:
    """Fold newly fetched threads into the cached store file.

    `completed` records whether the run that produced this state finished. A long
    scrape saves after every thread, so a file can legitimately exist while still
    being partial; without the flag there is no way to tell that apart from a
    store that simply has few threads.
    """
    timestamp = now or _utc_now()
    existing_meta, comments_by_index = _existing_state(existing)
    meta_by_tid = _with_sequences(existing_meta, fetched)

    for ref, comments in fetched:
        meta = meta_by_tid[str(ref.tid)] = {
            **meta_by_tid[str(ref.tid)],
            "thread_title": ref.title,
            "ctgid": ref.ctgid,
            "bid": ref.bid,
            "board_label": ref.board_label,
            "scope": board_scope(ref.board_label),
            "description": ref.description,
            # Everything reaching `merge` has had page one fetched and parsed, so
            # an empty description here is an answer rather than a gap.
            "description_checked": True,
        }
        bucket = comments_by_index.setdefault(meta["index"], {})
        for comment in comments:
            bucket[comment.number] = _comment_record(meta["index"], comment)

    threads: list[dict] = []
    all_comments: list[dict] = []
    for meta in sorted(meta_by_tid.values(), key=lambda m: _sequence_of(m["index"])):
        bucket = comments_by_index.get(meta["index"], {})
        ordered = [bucket[number] for number in sorted(bucket, reverse=True)]
        threads.append({**meta, "count": len(ordered)})
        all_comments.extend(ordered)

    timestamps = sorted(c["datetime"] for c in all_comments if c.get("datetime"))
    first_scraped = (existing or {}).get("scrape_meta", {}).get("first_scraped_at") or timestamp

    return {
        "schema_version": SCHEMA_VERSION,
        "store_name": store_name,
        "acode": acode,
        "scrape_meta": {
            "first_scraped_at": first_scraped,
            "last_scraped_at": timestamp,
            "last_run_completed": completed,
        },
        "total_comment_count": len(all_comments),
        "first_comment": timestamps[0] if timestamps else None,
        "last_comment": timestamps[-1] if timestamps else None,
        "threads": threads,
        "comments": all_comments,
    }
