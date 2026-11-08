"""Strict comment-date scoping, applied before selection and reply expansion."""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date

from scraping.parser import parse_comment_datetime


def parse_bound(raw: str, *, end: bool = False) -> date | None:
    """Accept a year, month or date; upper bounds include the entire period."""
    value = raw.strip().lower()
    if not value:
        return None
    if value in {"now", "today", "現在", "今天"}:
        return date.today()
    if not re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?", value):
        raise ValueError("請用 YYYY、YYYY-MM、YYYY-MM-DD 或 now")
    parts = [int(part) for part in value.split("-")]
    year = parts[0]
    month = parts[1] if len(parts) > 1 else (12 if end else 1)
    day = parts[2] if len(parts) > 2 else (
        calendar.monthrange(year, month)[1] if end else 1
    )
    return date(year, month, day)


@dataclass(frozen=True)
class Period:
    start: date | None = None
    end: date | None = None

    def __post_init__(self) -> None:
        if self.start and self.end and self.start > self.end:
            raise ValueError("開始日期不可晚於結束日期")

    @property
    def label(self) -> str:
        if self.start is None and self.end is None:
            return "全部已爬取時間（包含日期不明留言）"
        return f"{self.start or '最早'} ～ {self.end or '最新'}（含首尾日；排除日期不明留言）"

    def apply(self, payload: dict) -> dict:
        if self.start is None and self.end is None:
            return payload
        comments = []
        for comment in payload.get("comments", []):
            stamp = parse_comment_datetime(comment.get("datetime"))
            if stamp is None:
                continue
            posted = stamp.date()
            if (self.start is None or posted >= self.start) and (
                self.end is None or posted <= self.end
            ):
                comments.append(comment)
        # Opening posts are synthesized from thread descriptions by
        # comments_by_id(). They have no date, so remove that source as well.
        threads = [{**thread, "description": ""} for thread in payload.get("threads", [])]
        return {**payload, "comments": comments, "threads": threads}


ALL_TIME = Period()
