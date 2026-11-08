"""Explicit comment selection for free-form analysis; no reply expansion."""

from __future__ import annotations

from collections import Counter
import re

from analysis import select
from scraping import store
from scraping.parser import parse_comment_datetime


def thread_options(payload: dict) -> list[dict]:
    counts = Counter(
        c.get("thread_index") for c in store.comments_by_id(payload).values()
    )
    return [
        {
            "id": t["index"],
            "title": t.get("thread_title", ""),
            "count": counts[t["index"]],
        }
        for t in payload.get("threads", [])
    ]


def id_list(value: object, label: str) -> set[str]:
    if (
        not isinstance(value, list)
        or len(value) > 1_000_000
        or any(
            not isinstance(item, str) or not re.fullmatch(r"T\d{1,12}-\d{1,12}", item)
            for item in value
        )
    ):
        raise ValueError(f"{label}格式無效")
    return set(value)


def number_ranges(raw: object, threads: set[str]) -> dict[str, list[tuple[int, int]]]:
    if not isinstance(raw, str) or len(raw) > 20_000:
        raise ValueError("留言編號範圍格式無效")
    raw = re.sub(r"\s*(?:~|～|\.\.|至)\s*", "~", raw.strip())
    pieces = re.split(r"[\s,，、;；]+", raw) if raw else []
    if len(pieces) > 256:
        raise ValueError("一次最多指定 256 組留言編號範圍")
    result: dict[str, list[tuple[int, int]]] = {}
    for piece in pieces:
        match = re.fullmatch(
            r"(?:(T\d{1,12})-)?(\d{1,12})(?:~(?:(T\d{1,12})-)?(\d{1,12}))?", piece
        )
        if match is None:
            raise ValueError("編號請用 T1-100、T1-100～T1-200，多組以逗號分隔")
        thread, first, end_thread, last = match.groups()
        if thread is None:
            if len(threads) != 1:
                raise ValueError(
                    "只填留言編號時，請先選擇單一討論串；或輸入完整 ID，例如 T1-100"
                )
            thread = next(iter(threads))
        if end_thread is not None and end_thread != thread:
            raise ValueError("一組編號範圍必須屬於同一討論串")
        start, end = int(first), int(last or first)
        if start > end:
            raise ValueError("留言編號範圍的起點不可大於終點")
        result.setdefault(thread, []).append((start, end))
    return result


def choose(payload: dict, data: dict) -> tuple[tuple[select.Selected, ...], int]:
    available = {t["index"] for t in payload.get("threads", [])}
    raw_threads = data.get("threads")
    if raw_threads is None:
        threads = available
    elif not isinstance(raw_threads, list) or any(
        not isinstance(t, str) or t not in available for t in raw_threads
    ):
        raise ValueError("請選擇這家店的有效討論串")
    else:
        threads = set(raw_threads)
    ranges = number_ranges(data.get("ranges", ""), threads)
    limit = data.get("limit", 0)
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 0 <= limit <= 1_000_000
    ):
        raise ValueError("留言數量上限必須為 0～1,000,000 的整數；0 表示全部")
    order = data.get("order", "newest")
    if order not in ("newest", "oldest"):
        raise ValueError("留言排序方式無效")
    comments = [
        c
        for c in store.comments_by_id(payload).values()
        if c.get("thread_index") in threads
        and (
            not ranges
            or any(
                low <= c.get("comment_number", -1) <= high
                for low, high in ranges.get(c.get("thread_index"), [])
            )
        )
    ]
    # Unknown dates always come after dated comments, in either direction.
    stamps = {
        c["comment_id"]: parse_comment_datetime(c.get("datetime")) for c in comments
    }
    dated = [c for c in comments if stamps[c["comment_id"]] is not None]
    unknown = [c for c in comments if stamps[c["comment_id"]] is None]
    dated.sort(
        key=lambda c: (stamps[c["comment_id"]], c["thread_index"], c["comment_number"]),
        reverse=order == "newest",
    )
    unknown.sort(
        key=lambda c: (c["thread_index"], c["comment_number"]),
        reverse=order == "newest",
    )
    ordered = dated + unknown
    matched = len(ordered)
    if limit:
        ordered = ordered[:limit]
    chosen = {c.comment_id: c for c in select.all_comments(payload)}
    return tuple(chosen[c["comment_id"]] for c in ordered), matched
