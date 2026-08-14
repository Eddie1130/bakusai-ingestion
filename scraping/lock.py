"""One scrape at a time, across processes / 跨行程的抓取互斥鎖.

`client.RateLimiter` paces requests inside one process. Two processes halve the
interval, and the interval is not a guess: 2.0 s survived 682 consecutive
requests and a field run held it with no block at all, while 1.6 s settles into
ten pages, a block, a 45 s recovery, ten pages again — 6.02 s per page over 143
pages. Two scrapers at 2.0 s each are a scraper at 1.0 s, well past that cliff.

Nothing but the operator's habit of opening a single terminal prevented this, and
anything that scrapes unattended removes the habit. So the lock lives on the
filesystem, which is the only thing two unrelated processes share, and every
entry point that scrapes takes it.

**It refuses; it does not wait.** Blocking here would mean a command hanging for
the half hour the other scrape has left, which reads as a crash. Queueing is the
caller's business; the CLI's answer is to come back later.

`flock` is released by the kernel when the descriptor closes or the process dies,
so a killed scrape leaves a stale *file* but never a stale *lock*. That is the
whole reason for choosing it over a pid file, which cannot tell the two apart.
"""
from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import paths


class ScrapeInProgress(RuntimeError):
    """Another process holds the scrape lock."""


def _holder(handle: int) -> str:
    """Whatever the holder recorded about itself, for the refusal message.

    Advisory only. The lock is held by `flock`, not by this text, so a garbled or
    empty file changes nothing about correctness — it only makes the message
    vaguer, which is why every failure here is swallowed.
    """
    try:
        os.lseek(handle, 0, os.SEEK_SET)
        return os.read(handle, 64).decode("utf-8", errors="replace").strip()
    except OSError:
        return ""


@contextmanager
def scrape_lock() -> Iterator[Path]:
    """Hold the scrape lock for the duration of the block.

    Raises `ScrapeInProgress` immediately if another process holds it. The path
    comes from `paths.py` and is not a parameter: an entry point that could
    supply its own would be locking a different file from everything else, which
    is indistinguishable from not locking at all.
    """
    target = paths.scrape_lock_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = os.open(target, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            held_by = _holder(handle)
            where = f"（{held_by}）" if held_by else ""
            raise ScrapeInProgress(f"另一個行程正在抓取{where}") from exc

        # Recorded after acquiring, so what a refused process reads is always a
        # live holder rather than the last one to have finished.
        try:
            os.ftruncate(handle, 0)
            os.write(handle, f"pid {os.getpid()}\n".encode())
        except OSError:
            pass
        yield target
    finally:
        os.close(handle)
