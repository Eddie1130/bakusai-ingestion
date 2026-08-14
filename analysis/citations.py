"""Comment ids that came back from a model / 從模型回覆中取回留言 ID.

A model's answer is untrusted text. Ids are pulled out by pattern and intersected
with the ids the store actually holds, so a hallucinated id vanishes, a truncated
list simply yields fewer, and a garbled paste is visible rather than silent.

Shared by the two-step mode, which asks a model which comments are relevant, and
the verification mode, which checks what a finished report cited.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from scraping.store import comments_by_id

#: A comment id wherever it appears — bare, in JSON, in a list, or behind the `#`
#: an earlier version prefixed them with. A leading prefix is not captured, so
#: `#T1-101` and `T1-101` are the same id.
COMMENT_ID = re.compile(r"T\d+-\d+")


@dataclass(frozen=True)
class Citations:
    """What a model's text referred to, split by whether the store holds it."""

    known: tuple[str, ...]
    unknown: tuple[str, ...]

    @property
    def total(self) -> int:
        return len(self.known) + len(self.unknown)

    @property
    def fabrication_rate(self) -> float:
        """Share of cited ids that do not exist. Zero citations is not a perfect
        score, and the caller has to check `total` to tell the two apart."""
        return len(self.unknown) / self.total if self.total else 0.0


def parse_ids(text: str) -> tuple[str, ...]:
    """Every comment id in the text, in order, without repeats."""
    return tuple(dict.fromkeys(COMMENT_ID.findall(text or "")))


def cited(payload: dict | None, text: str) -> Citations:
    """Split the ids in `text` by whether the store holds them."""
    known_ids = comments_by_id(payload)
    found = parse_ids(text)
    return Citations(
        known=tuple(i for i in found if i in known_ids),
        unknown=tuple(i for i in found if i not in known_ids),
    )


def spread(ids: Sequence[str], count: int) -> tuple[str, ...]:
    """An evenly spaced sample across `ids`, in the order they were cited.

    Taking the first N is the wrong sample for a spot-check: a report cites in the
    order it argues, so the head is all one section. Measured on a real two-step
    report, eight of the first ten citations were the employment-status paragraph
    — a genuine sample of ten would have covered attitude, service and risk too.

    Evenly spaced rather than random, so two runs over the same report check the
    same comments and a finding can be repeated.
    """
    if count <= 0 or not ids:
        return ()
    if len(ids) <= count:
        return tuple(ids)
    if count == 1:
        return (ids[0],)
    step = (len(ids) - 1) / (count - 1)
    return tuple(ids[round(position * step)] for position in range(count))


def bodies_of(payload: dict | None, ids: Iterable[str]) -> tuple[tuple[str, str, str], ...]:
    """(id, date, body) for each id, so the operator can read what was cited."""
    by_id = comments_by_id(payload)
    return tuple(
        (
            comment_id,
            by_id[comment_id].get("datetime", "") or "",
            by_id[comment_id].get("body", "") or "",
        )
        for comment_id in ids
        if comment_id in by_id
    )
