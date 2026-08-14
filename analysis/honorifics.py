"""Is this name a stage name at all? / 這個名字真的是源氏名嗎.

Short kana stage names collide with ordinary Japanese. Searching one two-mora
name in the measured store returns 105 comments and not one is about a girl —
every match is really the sentence ending 「〜かもね」. Without this check the
operator finds that out only after pasting 22,000 characters into a chat window
and being told there is nothing to report.

The answer is a warning, never a block. See `HONORIFIC_THRESHOLD`.
"""
from __future__ import annotations

from dataclasses import dataclass

from scraping.parser import normalise_kana

from . import morphology
from .select import name_matcher


#: Owned by `morphology.py` and re-exported here, because the grammar filter needs
#: the same set and sits below this module in the import order. One list, because
#: two copies of a vocabulary drift and the drift would be silent: this module
#: would warn about a name the filter had already decided was addressed.
HONORIFICS = morphology.HONORIFICS

#: Also ported: a trailing 「と」 disqualifies the honorific. 「◯◯さんと」 reads as
#: "with ◯◯" rather than as addressing her, and 「ちゃんと」 alone means
#: "properly" — both are the name sitting next to an ordinary word.
NOT_AN_ADDRESS_AFTER = "と"

#: Below this share of occurrences the matches look like a common word rather than
#: a stage name. Measured: twelve known stage names ran 6.5%–41.7%, while common
#: words and collisions (かもね, ところ, わかる, なった) ran 0%–0.7%. The
#: threshold sits in the middle of that gap, and it warns rather than blocks —
#: the lowest real name measured was 6.5%, close enough that a block would
#: eventually suppress a genuinely obscure girl.
HONORIFIC_THRESHOLD = 0.03

_HONORIFIC_LOOKAHEAD = morphology.HONORIFIC_LOOKAHEAD


@dataclass(frozen=True)
class HonorificRate:
    """How often the name is addressed rather than merely written."""

    occurrences: int
    with_honorific: int

    @property
    def rate(self) -> float:
        return self.with_honorific / self.occurrences if self.occurrences else 0.0

    @property
    def is_suspicious(self) -> bool:
        """A name nobody writes is not a low rate — it is no evidence either way."""
        return self.occurrences > 0 and self.rate < HONORIFIC_THRESHOLD


def _is_addressed(text: str, position: int) -> bool:
    """Whether an honorific, not swallowed by 「と」, follows this occurrence."""
    tail = normalise_kana(text[position : position + _HONORIFIC_LOOKAHEAD])
    excluded = normalise_kana(NOT_AN_ADDRESS_AFTER)
    for honorific in HONORIFICS:
        folded = normalise_kana(honorific)
        if tail.startswith(folded):
            return not tail[len(folded) :].startswith(excluded)
    return False


def addresses(name: str, body: str) -> bool:
    """Whether an honorific follows the name anywhere in this one comment.

    The per-comment half of `honorific_rate`, sharing its rule rather than
    restating it: the lookahead, the honorific set and the 「と」 exclusion are all
    one implementation, so a comment cannot be read as addressing her by one
    caller and not by another.
    """
    matcher = name_matcher((name or "").strip())
    return any(
        _is_addressed(text, position) for text, position in matcher.occurrences(body or "")
    )


def honorific_rate(payload: dict | None, name: str) -> HonorificRate:
    """How often this name is addressed across the whole store.

    Short kana stage names collide with ordinary Japanese — one two-mora name
    returns 105 comments in the measured store and not one is about a girl; every
    match is really the sentence ending 「〜かもね」. The share of occurrences an
    honorific follows separates the two cases cleanly, and it is cheap: the
    operator learns the name is wrong before pasting 22,000 characters for
    nothing.
    """
    wanted = (name or "").strip()
    if not payload or not wanted:
        return HonorificRate(0, 0)

    matcher = name_matcher(wanted)
    occurrences = addressed = 0
    # Replies only, deliberately: this reads the file rather than
    # `store.comments_by_id`, which also offers each thread's opening post. The
    # threshold was measured over replies, and one post per thread — twenty-one
    # against 18,954 comments in the measured store — cannot move a rate. Adding
    # them would re-open a measured number to buy nothing.
    for comment in payload.get("comments", []):
        for text, position in matcher.occurrences(comment.get("body") or ""):
            occurrences += 1
            addressed += _is_addressed(text, position)
    return HonorificRate(occurrences, addressed)
