"""What to tell the operator when a scrape fails / 抓取失敗的說法.

**One wording per failure, for every entry point.** The interactive loop and the
unattended refresh report the same five failures, and them drifting apart is how a
message that says "已中止以免存入錯誤資料" in one place comes to say something
weaker in the other — while the condition, and what the operator should do about
it, is identical.

Every message is Traditional Chinese and every one of them carries the exception's
own text on a separate labelled line. A rate-limit block and a broken selector
both read as "抓取失敗" without it, and those two have opposite remedies: wait, or
fix the code.
"""
from __future__ import annotations

from . import client

DETAIL_LABEL = "技術細節"

BLOCKED = "被爆サイ限流了，請稍候約 30 秒再試"
PINNED = "伺服器回傳了錯誤的頁面，已中止以免存入錯誤資料"
PARSER_FAILED = "頁面解析失敗，選擇器可能已失效，需要修正程式"
SCRAPE_FAILED = "抓取失敗"


def reason(
    exc: client.ScrapeError,
    *,
    blocked: str = BLOCKED,
    fallback: str = SCRAPE_FAILED,
) -> str:
    """The plain-language half of the message, without the technical detail.

    Ordered from the most specific subclass outwards; `ScrapeError` is the base of
    all three, so it has to be tested last or it would answer for everything.
    """
    if isinstance(exc, client.BlockedError):
        return blocked
    if isinstance(exc, client.PinnedResponseError):
        return PINNED
    if isinstance(exc, client.ParserFailure):
        return PARSER_FAILED
    return fallback


def describe(
    exc: client.ScrapeError,
    *,
    blocked: str = BLOCKED,
    fallback: str = SCRAPE_FAILED,
) -> str:
    """The whole message: what happened, then what the exception said."""
    return f"{reason(exc, blocked=blocked, fallback=fallback)}\n{DETAIL_LABEL}：{exc}"


def indented(text: str, prefix: str = "  ") -> str:
    """The same message, laid out for a terminal rather than for a chat."""
    return "\n".join(f"{prefix}{line}" for line in text.split("\n"))
