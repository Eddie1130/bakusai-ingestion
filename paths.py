"""The only place file paths are built / 唯一的路徑解析入口.

Every module that needs a location on disk asks here. Nothing else assembles a
path, because independent path guessing was a documented source of bugs in an
earlier version of this tool.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

DATA_ROOT_ENV = "BAKUSAI_DATA_ROOT"

#: Runtime state — currently just the scrape lock. Everything under here is
#: reproducible: deleting it costs time, never data. It is deliberately *not* a
#: subdirectory of the data root, so that "delete the runtime state" can never be
#: one mistyped path away from deleting a scrape.
VAR_ROOT_ENV = "BAKUSAI_VAR_ROOT"

#: Supported regions, keyed by Bakusai's own `acode`. One table so a region can
#: never exist for the menu but not for the filesystem.
AREAS: dict[int, tuple[str, str]] = {
    3: ("東京", "03_tokyo"),
    7: ("大阪", "07_osaka"),
    10: ("福岡", "10_fukuoka"),
}

#: Region code -> operator-facing label.
AREA_LABELS: dict[int, str] = {code: area[0] for code, area in AREAS.items()}

BAKUSAI_FILENAME = "bakusai.json"
INFO_FILENAME = "info.json"

VAR_DIRNAME = "var"
SCRAPE_LOCK_FILENAME = "scrape.lock"

_UNSAFE_PATH_CHARS = re.compile(r"[/\\\x00]")


def data_root() -> Path:
    """Root of the scraped-data tree. Overridable so tests never touch real data."""
    configured = os.environ.get(DATA_ROOT_ENV)
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parent / "data"


def var_root() -> Path:
    """Root of the runtime tree. Overridable so tests never touch a real lock.

    Beside the data root, never inside it. Scraped data is expensive and
    irreplaceable; everything here is neither, and keeping the two apart is what
    makes "delete it and re-run" an always-safe instruction.
    """
    configured = os.environ.get(VAR_ROOT_ENV)
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parent / VAR_DIRNAME


def scrape_lock_path() -> Path:
    """The file every scraping entry point locks, whatever process it runs in."""
    return var_root() / SCRAPE_LOCK_FILENAME


def area_dir(acode: int) -> Path:
    """Directory holding every store in one region."""
    if acode not in AREAS:
        raise ValueError(f"unknown region code: {acode!r} (known: {sorted(AREAS)})")
    return data_root() / AREAS[acode][1]


def store_names(acode: int) -> list[str]:
    """Every store already scraped in a region, in name order.

    A store counts as present only once it has a comments file. A directory
    holding nothing but metadata is an interrupted scrape with nothing to read,
    and offering it in a menu would promise data that is not there.
    """
    region = area_dir(acode)
    if not region.is_dir():
        return []
    return sorted(
        entry.name for entry in region.iterdir() if (entry / BAKUSAI_FILENAME).is_file()
    )


def safe_store_filename(store: str) -> str:
    """Reduce a store name to a single safe path component."""
    cleaned = (store or "").strip()
    if not cleaned:
        raise ValueError("store name is empty")
    cleaned = _UNSAFE_PATH_CHARS.sub("_", cleaned)
    if set(cleaned) <= {"."}:
        cleaned = "_" * len(cleaned)
    return cleaned


def store_dir(acode: int, store: str) -> Path:
    """Directory holding one store's files."""
    return area_dir(acode) / safe_store_filename(store)


def bakusai_path(acode: int, store: str) -> Path:
    """The store's scraped comments."""
    return store_dir(acode, store) / BAKUSAI_FILENAME


def info_path(acode: int, store: str) -> Path:
    """The store's metadata, kept beside its comments."""
    return store_dir(acode, store) / INFO_FILENAME
