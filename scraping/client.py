"""The only place the network is touched / 唯一與網路互動的模組.

Two measured facts shape this module:

* Requests too close together get the client blocked, and a block arrives as
  HTTP 200 with a collapsed body — status codes carry no signal.
* A client that sends only User-Agent and Referer gets a response that looks
  perfectly healthy but has its page parameter silently ignored. Browser-equivalent
  headers are a correctness requirement, not politeness.
"""
from __future__ import annotations

import os
import random
import time
from collections.abc import Callable
from dataclasses import dataclass

import requests

from .parser import is_access_error

#: The floor, and the interval with the most measurement behind it: 2.0 s survived
#: 682 consecutive requests from a cold limiter, and a field run holds it with no
#: block at all — 2.01 s per page end to end, meaning the limiter never widened.
#:
#: 1.6 s was the floor before this and does not hold: at 1.6 s a run settles into
#: roughly ten pages, a block, a 45 s recovery, ten pages again, which measured
#: 6.02 s per page. The cliff between the two is narrow and unexplained, so this
#: value is a measurement, not a derivation — do not shade it downward by reasoning.
MIN_REQUEST_INTERVAL_SECONDS = 2.0
#: Where backing off stops. Past this the run is slower than re-running it later.
MAX_REQUEST_INTERVAL_SECONDS = 15.0
REQUEST_TIMEOUT_SECONDS = 20
BLOCK_RECOVERY_SECONDS = 45
MAX_ATTEMPTS = 3

#: Multiplicative on the way up, additive on the way down. A block is evidence the
#: current pace is wrong and warrants a large correction; a run of successes is
#: only weak evidence there is room, and warrants a small one.
BLOCK_BACKOFF_FACTOR = 1.75
NARROW_AFTER_SUCCESSES = 40
NARROW_STEP_SECONDS = 0.4

#: Lets an operator start a run at a pace measured on the day, without an edit.
INTERVAL_ENV_VAR = "BAKUSAI_REQUEST_INTERVAL"

@dataclass(frozen=True)
class BrowserIdentity:
    """A User-Agent and the client hints that must agree with it.

    These three travel together because a mismatch between them is a bot signal:
    a Windows User-Agent sent with `sec-ch-ua-platform: "macOS"` describes a browser
    that does not exist. Keeping them in one object makes the mismatch unwritable.
    """

    user_agent: str
    ua_brands: str
    platform: str


BROWSER_IDENTITIES = (
    BrowserIdentity(
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
        ua_brands='"Chromium";v="146", "Google Chrome";v="146", "Not?A_Brand";v="24"',
        platform='"macOS"',
    ),
    BrowserIdentity(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
        ua_brands='"Chromium";v="146", "Google Chrome";v="146", "Not?A_Brand";v="24"',
        platform='"Windows"',
    ),
)

#: The Sec-Fetch and client-hint group is what makes pagination work. Removing it
#: silently pins the client to a single page. The identity-dependent hints
#: (`sec-ch-ua`, `sec-ch-ua-platform`, `User-Agent`) are added per client, not here.
BROWSER_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "sec-ch-ua-mobile": "?0",
    "Connection": "keep-alive",
}

Fetch = Callable[[str], str]


class ScrapeError(Exception):
    """Base class for scraping failures."""


class BlockedError(ScrapeError):
    """Bakusai rate-limited us. Wait and retry; the data is fine."""


class PinnedResponseError(ScrapeError):
    """The server served a page other than the one requested.

    The response parses cleanly into real comments, which is exactly what makes
    this dangerous: persisting it would fill the store with the same page repeated.
    """


class ParserFailure(ScrapeError):
    """A healthy-looking response yielded no comments — the selectors are stale."""


def configured_interval() -> float:
    """The starting interval, from the environment, never below the floor.

    A bad value pauses nothing and warns nobody by design: this is read at the
    start of a scrape that may run for an hour, and a typo in an env var is not
    worth losing that run over. The floor is what a fallback costs.
    """
    raw = os.environ.get(INTERVAL_ENV_VAR)
    if raw is None:
        return MIN_REQUEST_INTERVAL_SECONDS
    try:
        return max(float(raw), MIN_REQUEST_INTERVAL_SECONDS)
    except ValueError:
        return MIN_REQUEST_INTERVAL_SECONDS


class RateLimiter:
    """Keeps at least `interval` seconds between requests, and adjusts it.

    Spacing alone cannot hold a pace against a site that meters a budget over a
    window: any fixed interval faster than the budget allows keeps the window
    permanently full, and a fixed recovery drains only part of it. So a block
    widens the interval and a long clean run narrows it, which converges on
    whatever the site is actually allowing today without needing to know it.
    """

    def __init__(
        self,
        interval: float = MIN_REQUEST_INTERVAL_SECONDS,
        ceiling: float = MAX_REQUEST_INTERVAL_SECONDS,
    ) -> None:
        # Deliberately not clamped here. The floor belongs at the boundary where
        # an operator supplies a value — `configured_interval` — and clamping it
        # again in the constructor buys nothing (no entry point passes an
        # interval) while making the limiter impossible to exercise without
        # sleeping through it for real.
        self._base = interval
        self._interval = interval
        self._ceiling = ceiling
        self._successes = 0
        self._last_request_at: float | None = None

    @property
    def interval(self) -> float:
        return self._interval

    def wait(self) -> None:
        if self._last_request_at is not None:
            elapsed = time.monotonic() - self._last_request_at
            remaining = self._interval - elapsed
            if remaining > 0:
                time.sleep(remaining)
        self._last_request_at = time.monotonic()

    def widen(self) -> None:
        """Record a block. The current pace is too fast; back off hard."""
        self._successes = 0
        self._interval = min(self._interval * BLOCK_BACKOFF_FACTOR, self._ceiling)

    def narrow(self) -> None:
        """Record a clean fetch, and step back down after enough of them."""
        self._successes += 1
        if self._successes < NARROW_AFTER_SUCCESSES:
            return
        self._successes = 0
        self._interval = max(self._interval - NARROW_STEP_SECONDS, self._base)


class HttpClient:
    """Fetches pages politely, with browser-equivalent headers."""

    def __init__(
        self,
        interval: float | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self._session = session or requests.Session()
        self._limiter = RateLimiter(configured_interval() if interval is None else interval)
        self._referer = "https://bakusai.com/"
        #: Chosen once. One session is one browser: a cookie jar whose User-Agent
        #: changes between requests describes nothing that can exist.
        self._identity = random.choice(BROWSER_IDENTITIES)

    @property
    def interval(self) -> float:
        """The pace the client has settled on, for callers that report progress."""
        return self._limiter.interval

    def _headers(self) -> dict[str, str]:
        headers = dict(BROWSER_HEADERS)
        headers["User-Agent"] = self._identity.user_agent
        headers["sec-ch-ua"] = self._identity.ua_brands
        headers["sec-ch-ua-platform"] = self._identity.platform
        headers["Referer"] = self._referer
        return headers

    def get(self, url: str) -> str:
        """Fetch a page, riding out a rate-limit block rather than failing the run."""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self._limiter.wait()
            try:
                response = self._session.get(
                    url, headers=self._headers(), timeout=REQUEST_TIMEOUT_SECONDS
                )
                response.raise_for_status()
            except requests.RequestException as exc:
                if attempt == MAX_ATTEMPTS:
                    raise ScrapeError(f"request failed after {MAX_ATTEMPTS} attempts: {exc}") from exc
                time.sleep(random.uniform(1.0, 2.0))
                continue

            if is_access_error(response.text):
                # Widen before the recovery sleep, so a block that never clears
                # still leaves the next client-level retry a slower pace to use.
                self._limiter.widen()
                if attempt == MAX_ATTEMPTS:
                    raise BlockedError(
                        f"still rate-limited after {MAX_ATTEMPTS} attempts: {url}"
                    )
                time.sleep(BLOCK_RECOVERY_SECONDS)
                continue

            self._limiter.narrow()
            self._referer = url
            return response.text

        raise BlockedError(f"exhausted attempts fetching {url}")


def create_fetch(interval: float | None = None) -> Fetch:
    """Build the fetch callable the scraper depends on (seam 1).

    One client per scrape, so the pace it learns is carried across every page of
    the run rather than reset at each thread.
    """
    return HttpClient(interval).get
