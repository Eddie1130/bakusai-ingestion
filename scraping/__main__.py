"""Entry points for the scraping layer / 爬取層進入點.

    python -m scraping                  the interactive loop
    python -m scraping refresh <店名>    re-scrape one store, unattended

A shim and nothing else: it resolves a name to a store, hands the work to
`refresh.attempt`, prints what came back, and turns the outcome into an exit
code. Every decision worth asserting is on the other side of that call.
"""
from __future__ import annotations

import sys
from collections.abc import Sequence

from . import cli, refresh, stores
from .progress import Progress, ScrapeProgress

USAGE = "用法：python -m scraping [refresh <店名>]"

REFRESH = "refresh"


def ambiguous(found: Sequence[stores.ScrapedStore]) -> str:
    """More than one store matched. Naming them is the whole answer."""
    listing = "\n".join(f"  {option.label}" for option in found)
    return f"找到 {len(found)} 家符合的店，請打得更完整一點：\n{listing}"


def refresh_once(typed: str, report: Progress | None = None) -> tuple[str, int]:
    """Re-scrape the one store this name means, or explain why it cannot."""
    found = stores.match(typed)
    if not found:
        return refresh.NOT_SCRAPED.format(name=typed), 1
    if len(found) > 1:
        return ambiguous(found), 1

    outcome = refresh.attempt(found[0], report or ScrapeProgress())
    return outcome.message, 0 if outcome.succeeded else 1


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        return cli.main()
    if arguments[0] != REFRESH or len(arguments) < 2:
        print(USAGE)
        return 2

    # The name is every remaining word, because a store name may be several
    # (`refresh 吉原 アイドル研究生`) and joining is the only reading that keeps
    # such a name reachable from the command line.
    message, code = refresh_once(" ".join(arguments[1:]))
    print(message)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
