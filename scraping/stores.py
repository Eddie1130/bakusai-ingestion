"""Finding a scraped store from a name / 模糊比對已抓店家.

The index of what is on disk, and the one place that decides which store a typed
name means. The region is never part of the name, so a store is found across
every region at once; ten stores on disk are nationally unique, and when two are
not, the caller is told rather than guessed at.

**The folding is the scraper's own.** `normalise_for_match` is what decides
whether a thread title names a store, so reusing it means this index and the
scraper see through exactly the same differences — width, kana, spacing — and
cannot drift apart into two rules that disagree. Case folding is added on top,
because that is about what someone typed rather than about Bakusai.
"""
from __future__ import annotations

from dataclasses import dataclass

import paths
from .parser import normalise_for_match


@dataclass(frozen=True)
class ScrapedStore:
    """A store already on disk, and the region holding it."""

    acode: int
    name: str

    @property
    def label(self) -> str:
        return f"{paths.AREA_LABELS.get(self.acode, self.acode)}／{self.name}"


def _folded(text: str) -> str:
    return normalise_for_match(text).casefold()


def scraped() -> tuple[ScrapedStore, ...]:
    """Every store on disk, region by region, in a stable order."""
    return tuple(
        ScrapedStore(acode, name)
        for acode in sorted(paths.AREA_LABELS)
        for name in paths.store_names(acode)
    )


def match(typed: str) -> tuple[ScrapedStore, ...]:
    """Stores whose name contains what was typed, however either is written.

    An exact match wins alone. Without that rule a store whose full name is a
    prefix of another — `巣鴨 マテリアル` inside `巣鴨 マテリアル 別館` — could never
    be reached, because typing it in full would stay ambiguous forever.
    """
    wanted = _folded(typed)
    if not wanted:
        return ()
    candidates = scraped()
    exact = tuple(store for store in candidates if _folded(store.name) == wanted)
    if exact:
        return exact
    return tuple(store for store in candidates if wanted in _folded(store.name))
