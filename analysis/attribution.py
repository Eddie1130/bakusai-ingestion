"""How sure the selection is that a comment is about her / 歸屬的確信程度.

One question, per comment: **is this comment about this girl?** Not whether what
it says is true, not whether it is useful — only whether the attribution holds.

**It is computed, not asked for.** A model returning `0.83` is a number with no
calibration behind it, no way to audit it, and no guarantee of returning the same
value twice; it would nonetheless be sorted on. Everything here is a rule over
facts the selection already recorded, so a level can always be answered for — "her
name is written plainly and nobody addresses her with it anywhere in this store"
is reviewable in a way a decimal never is.

**Levels, not a score.** Three rungs and a floor, because that is the resolution
the evidence supports. Only one boundary has a measurement behind it — the
honorific rate, where twelve known stage names ran 6.5%–41.7% and known collisions
0%–0.7%. The rest is ordering what the selection knows, and a decimal would dress
that ordering up as precision it does not have.

It is written as a mapping from comment id, so a caller can filter the evidence
down to the rungs it trusts without re-deriving anything.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from . import honorifics, select

#: Somebody is talking to her, or a model already ruled that they were.
HIGH = "high"
#: Her name is written plainly, or the whole thread is hers.
MEDIUM = "medium"
#: The match needed widening to find, or the name is one the store never addresses.
LOW = "low"
#: Reply context. Not a weak claim — no claim at all. A level reading as "low"
#: would invite a reader to treat background as thin evidence about her, and
#: thin evidence is what this subsystem exists not to produce.
UNCLAIMED = "unclaimed"

#: Strongest first. The order is the API: a caller filtering evidence takes a
#: prefix of this, and `tally` reports in it.
LEVELS = (HIGH, MEDIUM, LOW, UNCLAIMED)

_LABELS = {HIGH: "高", MEDIUM: "中", LOW: "低", UNCLAIMED: "僅脈絡"}


def _level(item: select.Selected, *, addressed: bool, suspect: bool) -> str:
    """The rung this one comment sits on. First rule that applies wins.

    Deliberately not consulted: whether the comment is a thread's opening post.
    It is a structural fact about where the text sits, and no reasoning was
    available for why it should move an attribution up or down — and inventing a
    rule is exactly how a level stops being reviewable.
    """
    if item.route == select.ROUTE_REPLY:
        return UNCLAIMED
    if item.route == select.ROUTE_CONFIRMED:
        # A model was asked this exact question about this exact comment. It can
        # still be wrong about whether the comment *supports* anything — that is
        # what the fabrication rate is for — but not about who it names.
        return HIGH
    if item.route == select.ROUTE_BOARD:
        # Every comment on her thread is about her, and the thread was claimed by
        # matching its title: a 「ナナ」 matcher accepts a 「ナナセ」 board, which is
        # why this is not the top rung. See `diagnose.py`.
        return MEDIUM
    if addressed:
        return HIGH
    if suspect:
        # The store never addresses this name, so a plain match of it carries the
        # weight of a word rather than of a person.
        return LOW
    return MEDIUM if item.match == select.MATCH_LITERAL else LOW


def confidences(
    payload: dict | None, name: str, selected: Sequence[select.Selected]
) -> dict[str, str]:
    """A level for every selected comment, keyed by comment id.

    The store-wide honorific rate is read once rather than per comment: it is a
    fact about the name, and computing it inside the loop would be the same answer
    bought nineteen thousand times.
    """
    if not selected:
        return {}
    suspect = honorifics.honorific_rate(payload, name).is_suspicious
    return {
        item.comment_id: _level(
            item,
            addressed=item.route == select.ROUTE_NAME
            and honorifics.addresses(name, item.body),
            suspect=suspect,
        )
        for item in selected
    }


def tally(confidences: Mapping[str, str]) -> dict[str, int]:
    """How many comments sit on each rung, in `LEVELS` order and always complete.

    A missing key would make the zero cases invisible, and "no high-confidence
    evidence at all" is the finding a reader most needs to see.
    """
    counted = {level: 0 for level in LEVELS}
    for level in confidences.values():
        counted[level] += 1
    return counted


def describe(confidences: Mapping[str, str]) -> str:
    """The one line an operator reads before spending a paste on this selection."""
    if not confidences:
        return ""
    counted = tally(confidences)
    parts = " ".join(
        f"{_LABELS[level]} {counted[level]}" for level in LEVELS if counted[level]
    )
    return f"  歸屬確信度：{parts}"
