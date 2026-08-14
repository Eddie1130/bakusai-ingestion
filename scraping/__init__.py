"""Bakusai scraping package / 爆サイ 爬取套件.

Public API only. Implementation lives in the submodules:

* `client`     — the only place the network is touched
* `parser`     — pure functions over markup
* `candidates` — grouping search hits into the store they belong to
* `scraper`    — search, pagination, cutoff, incremental fetching
* `store`      — reading, merging and writing a store's file
* `stores`     — the index of what is on disk, and which store a name means
* `progress`   — what a long run reports while it runs
* `failures`   — what a run could not fetch, and why
* `lock`       — one scrape at a time, across processes
* `refresh`    — re-scraping a known store, unattended
* `cli`        — the interactive loop
"""
from __future__ import annotations

from .client import (
    BlockedError,
    ParserFailure,
    PinnedResponseError,
    ScrapeError,
    create_fetch,
)
from .parser import Comment, ThreadRef
from .scraper import AREA_CODES, StoreTarget, parse_store_target, scrape_store

__all__ = [
    "AREA_CODES",
    "BlockedError",
    "Comment",
    "ParserFailure",
    "PinnedResponseError",
    "ScrapeError",
    "StoreTarget",
    "ThreadRef",
    "create_fetch",
    "parse_store_target",
    "scrape_store",
]
