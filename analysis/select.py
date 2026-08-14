"""Choosing the comments that concern one girl / 挑選與一位女生相關的留言.

No model is consulted here, and that is the load-bearing decision of this whole
layer. The girl's name is typed by the operator rather than discovered from the
text, which turns attribution from open-vocabulary extraction into closed-set
string matching. An earlier version put an LLM in this path and paid for it in
phase caches, rotating API keys, and derived state files that drifted apart.

Judging whether a match is really about her is deferred to the one strong model
that already reads the evidence. **The one exception runs the argument backwards**:
`morphology.py` may remove a match it can prove is a piece of grammar rather than
a name, for the short kana names where the collision is otherwise unmanageable. It
is a dictionary rather than a model — same text, same answer, forever — and it can
only ever subtract. Nothing here decides that a match *is* her.

**Three separate things are recorded about every selection, on three axes.** The
route it arrived by, the form her name took if the route read a name at all, and
whether the comment is a thread's opening post. They were one string until they
had to be persisted, and one string cannot answer any of the three: an opening
post overwrote the route that reached it, so a thread that is hers and a post that
writes her name became indistinguishable, and the form of the match was computed
inside one regex alternation and then thrown away. Nothing downstream could weigh
a masked mention against a literal one, because nothing downstream was told.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass

from scraping.parser import GIRL_SCOPE, normalise_kana
from scraping.store import comments_by_id, is_opening_post

from . import morphology

#: How the comment was reached, most specific first. A comment reachable two ways
#: keeps the first route in this order: naming her says more than sitting on her
#: board. This is the audit trail — `relation` says the comment counts as evidence,
#: this says why anyone believed that.
ROUTE_NAME = "name"
ROUTE_BOARD = "board"
ROUTE_REPLY = "reply"
ROUTE_CONFIRMED = "confirmed"

#: Why the comment is in the selection at all, which is the only part of this a
#: ranking layer has any business reading. Derived from the route rather than
#: stored beside it: a reply neighbour is the only thing that is ever context, so
#: storing the same fact twice would only create two fields that can disagree.
RELATION_DIRECT = "direct"
RELATION_CONTEXT = "context"

#: Which form the name took, when the route went through the name at all —
#: strongest first, and the order is the strength. A name written exactly as typed
#: is a stronger claim than the same name in the other kana, which is stronger than
#: a name with a character masked out. All three were previously collapsed into
#: "it matched", which is the one thing a confidence cannot be computed from.
MATCH_LITERAL = "literal"
MATCH_KANA = "kana"
MATCH_MASK = "mask"

#: Ask for the whole connected chain rather than a fixed number of hops.
FULL_CHAIN = None

#: One hop, measured. Transitive closure from 199 seeds for one name reaches 1,280
#: comments across 19 hops, and sampling hops two through four returns forum
#: meta-argument and abuse carrying nothing about her. False-positive seeds expand
#: with exactly the same force as real ones: a two-mora kana name whose every match
#: is really the sentence ending 「〜かもね」 drags in 868 comments at full closure
#: against 254 at one hop.
DEFAULT_DEPTH = 1

#: Characters people substitute into a name, measured in `03_tokyo/アイドル研究生`
#: rather than guessed. `◯○〇` carry the bulk of it; `●` and `☆` mask names too,
#: appearing mid-name in real posts. Deliberately excluded: `◎` and `★`, which
#: appear only as ratings — 「プレイ◎」, 「プレイ★1」 — and each produced a false
#: positive and no true one when tested against ten known stage names; `×` and `✕`,
#: which mark an NG rather than a name; `♡`, decorative; and `＊`/`*`, which never
#: occur inside a word at all.
MASK_CHARACTERS = "◯○〇●☆"

#: Below this length a name is matched exactly as typed — no kana folding, no mask
#: expansion. Both cost precision that only a longer name can afford, and both
#: were measured on a two-mora name in `アイドル研究生`: masking one of two positions
#: leaves a single literal character and takes 121 matches to 371, while folding
#: onto katakana lands the name inside the store's own name and takes it to 368, of
#: which 247 are 「アイドル」.
MIN_VARIANT_LENGTH = 3


@dataclass(frozen=True)
class Selected:
    """One comment chosen for the prompt, and every axis of why it was chosen."""

    comment_id: str
    datetime: str
    body: str
    route: str = ROUTE_NAME
    #: `None` whenever the route never read her name — her own board and reply
    #: context reach a comment without it being written at all, and a match kind
    #: invented for those would be a fabrication at the exact point this subsystem
    #: exists to be trusted about.
    match: str | None = None
    #: Structural, and true independently of the route. An opening post used to
    #: replace the route instead of accompanying it.
    is_opening: bool = False

    @property
    def relation(self) -> str:
        """Whether this is evidence about her, or context that makes it readable.

        Read off the route rather than stored: reply expansion never displaces a
        route already settled, so a reply neighbour is exactly the set of comments
        that were not claimed to be about her, and the two can never disagree.
        """
        return RELATION_CONTEXT if self.route == ROUTE_REPLY else RELATION_DIRECT


@dataclass(frozen=True)
class Origin:
    """How one comment got in, before there is a comment to attach it to."""

    route: str
    match: str | None = None


@dataclass(frozen=True)
class Matcher:
    """How one girl's name is recognised in a comment body.

    Whether the text is kana-folded travels with the pattern because the two have
    to agree: a pattern built from folded text finds nothing in unfolded text.

    `pattern` alone decides membership, exactly as it always has. `literal` and
    `folded` are narrower readings of the same name used only to say which form a
    body used, so classification cannot select a comment matching never would.
    """

    pattern: re.Pattern[str]
    folds: bool
    #: The name exactly as typed, run against the body exactly as scraped.
    literal: re.Pattern[str]
    #: The name kana-folded, run against the folded body. `None` for a name too
    #: short to be folded at all.
    folded: re.Pattern[str] | None = None

    def prepare(self, body: str) -> str:
        """The text this pattern can actually be run against."""
        return normalise_kana(body) if self.folds else body

    def matches(self, body: str) -> bool:
        return bool(self.pattern.search(self.prepare(body)))

    def kind(self, body: str) -> str | None:
        """The strongest form the name took here, or `None` when it is absent.

        Checked in order, because the order is the evidence strength and a body
        can hold more than one form: a line carrying both the katakana spelling
        and the roster's own hiragana spelling of a name is literal evidence, not
        kana evidence. The fallback is deliberately `matches` rather than a mask
        pattern of its own — membership and its explanation then come from one
        pattern, so a selected comment always has a kind and a kind never implies
        a comment that was not selected.
        """
        if self.literal.search(body):
            return MATCH_LITERAL
        if self.folded is not None and self.folded.search(normalise_kana(body)):
            return MATCH_KANA
        return MATCH_MASK if self.matches(body) else None

    def occurrences(self, body: str) -> Iterator[tuple[str, int]]:
        """The prepared text and the end of each match, for counting what follows."""
        text = self.prepare(body)
        for found in self.pattern.finditer(text):
            yield text, found.end()


def name_matcher(name: str) -> Matcher:
    """Build the matcher for a name, widening it only where the name can afford it.

    A name of three characters or more is folded onto katakana, so a name typed in
    hiragana finds its katakana spelling and the reverse, and is expanded so any
    one position may be masked. A shorter name gets neither, because both widen it
    past the point of usefulness — see `MIN_VARIANT_LENGTH`.

    Folding is the scraper's own, so both layers see through the difference the
    same way Bakusai's search does.
    """
    typed = name.strip()
    literal = re.compile(re.escape(typed))
    if len(typed) < MIN_VARIANT_LENGTH:
        return Matcher(literal, folds=False, literal=literal)

    folded = normalise_kana(typed)
    masks = f"[{re.escape(MASK_CHARACTERS)}]"
    alternatives = [re.escape(folded)] + [
        re.escape(folded[:position]) + masks + re.escape(folded[position + 1 :])
        for position in range(len(folded))
    ]
    return Matcher(
        re.compile("|".join(alternatives)),
        folds=True,
        literal=literal,
        folded=re.compile(re.escape(folded)),
    )


def _thread_order(payload: dict) -> dict[str, int]:
    """Where each thread sits in the file, which is already sequence order.

    Read rather than re-derived from the comment id: `scraping/store.py` owns the
    file format, and a second module decoding the id string is how the reference
    project ended up with two ideas of what a comment id meant.
    """
    return {
        str(thread.get("index", "")): position
        for position, thread in enumerate(payload.get("threads", []))
    }


def _order_key(comment: dict, order: dict[str, int]) -> tuple[int, int]:
    index = str(comment.get("thread_index", ""))
    return (order.get(index, len(order)), int(comment.get("comment_number", 0)))


def _as_selected(comment: dict, origin: Origin) -> Selected:
    return Selected(
        comment_id=comment["comment_id"],
        datetime=comment.get("datetime", "") or "",
        body=comment.get("body", "") or "",
        route=origin.route,
        match=origin.match,
        is_opening=is_opening_post(comment),
    )


def _girl_board_threads(payload: dict, matcher: Matcher) -> set[str]:
    """Threads that are hers: girl-scope boards whose title carries her name.

    Scope and title are read from the stored file rather than re-derived — the
    scraper already recorded which board a thread came from, and the board is the
    authority on what a thread is for, not the wording of its title.

    Most girls have no such thread. In the measured store one of twenty-one is
    girl-scope, so this is an additional source and never a replacement for
    name matching.
    """
    return {
        str(thread.get("index", ""))
        for thread in payload.get("threads", [])
        if thread.get("scope") == GIRL_SCOPE
        and matcher.matches(thread.get("thread_title") or "")
    }


def _origin_of(comment: dict, matcher: Matcher, her_threads: set[str]) -> Origin | None:
    """Why this comment is about her, or `None` when it is not.

    An opening post is reached by the same two routes as a reply — it names her, or
    the thread is hers — and being one is then recorded beside the route rather
    than in place of it. Including one that names her on a store-wide thread is
    deliberate: it is a body carrying her name, which is the whole of the direct
    rule, and refusing it would discard the fullest account on the page for having
    been written first.
    """
    kind = matcher.kind(comment.get("body") or "")
    if kind is not None:
        return Origin(ROUTE_NAME, kind)
    if str(comment.get("thread_index", "")) in her_threads:
        return Origin(ROUTE_BOARD)
    return None


def _reply_graph(payload: dict) -> dict[str, set[str]]:
    """Who answers whom, in both directions.

    Built from the stored `reply` field rather than re-parsed from bodies. The
    scraper already resolved each thread-local `>>NNN` into a full comment id, and
    that resolution was verified against the bodies with zero mismatches — so
    parsing the text again would be a second implementation of a settled question.

    A reference to a comment the store does not hold is dropped, which is normal:
    the fetch window cuts a thread somewhere, and the comment above the cut is
    simply not there. So is a comment answering itself.

    `>>0` is Bakusai's own reference to the thread header, so it now resolves onto
    the opening post instead of dangling — a reply to the opening post pulls it in
    as context, which is what it means. `>>1` is unaffected: it is the first reply,
    and the opening post is numbered zero precisely so the two cannot collide.
    """
    known = comments_by_id(payload).keys()
    graph: dict[str, set[str]] = {}
    for comment in payload.get("comments", []):
        answering = comment["comment_id"]
        for answered in comment.get("reply") or []:
            if answered not in known or answered == answering:
                continue
            graph.setdefault(answering, set()).add(answered)
            graph.setdefault(answered, set()).add(answering)
    return graph


def _neighbours(
    seeds: Iterable[str], graph: dict[str, set[str]], depth: int | None
) -> set[str]:
    """Everything within `depth` hops of a seed; `FULL_CHAIN` for the whole chain.

    Both directions matter and for different reasons: the comment a match answers
    gives the context, and the comments answering a match give other people's
    verdicts — which is where roughly half the conclusions are written, as bare
    replies with the name only in the comment above.

    Already-reached comments are never revisited, so a cycle or a self-reference
    terminates rather than spinning.
    """
    reached = set(seeds)
    frontier = reached
    hops = 0
    while frontier and (depth is None or hops < depth):
        frontier = {n for comment in frontier for n in graph.get(comment, ())} - reached
        reached |= frontier
        hops += 1
    return reached - set(seeds)


def select(
    payload: dict | None, name: str, depth: int | None = DEFAULT_DEPTH
) -> tuple[Selected, ...]:
    """Comments about the girl, oldest first within each thread.

    Two routes in — her name in the body, and her own board, where nobody has to
    write her name because the whole thread is about her — then the reply
    neighbours of everything they reached. Both routes read opening posts as well
    as replies, so a thread nobody ever answered still contributes what it opened
    with. Origins are settled before expansion, so a comment reachable more than
    one way appears once and keeps the most specific route.

    `depth` is hops of reply expansion: `0` for none, `FULL_CHAIN` for the whole
    connected chain.

    A store file holds each thread newest-first, which is the order a forum page
    is read in but the wrong one for a prompt: a reply would arrive before the
    comment it answers, and the reply chains are where the verdicts live.
    """
    wanted = (name or "").strip()
    if not payload or not wanted:
        return ()

    matcher = name_matcher(wanted)
    her_threads = _girl_board_threads(payload, matcher)
    by_id = comments_by_id(payload)

    origins = {
        comment_id: origin
        for comment_id, comment in by_id.items()
        if (origin := _origin_of(comment, matcher, her_threads)) is not None
    }
    origins = _without_grammar(wanted, matcher, by_id, origins)
    return _emit(payload, by_id, _with_context(payload, origins, depth))


def _occurrences(matcher: Matcher, body: str) -> list[tuple[int, int]]:
    """Where the name sits in the body, as offsets into the body itself.

    The search runs on folded text and the offsets are handed back against the raw
    body, which is sound only because folding is a per-character map — and which
    matters, because a run of full-width katakana standing in for ordinary kana is
    not Japanese, and a parser reading it says so. Below `MIN_VARIANT_LENGTH`, where the filter runs, nothing is folded at
    all; the check is here for the day the gate widens.
    """
    folded = matcher.prepare(body)
    if len(folded) != len(body):
        raise ValueError("folding changed a length; offsets can no longer be reused")
    return [(found.start(), found.end()) for found in matcher.pattern.finditer(folded)]


def _without_grammar(
    name: str, matcher: Matcher, by_id: dict[str, dict], origins: dict[str, Origin]
) -> dict[str, Origin]:
    """Drop the comments whose every occurrence of the name is provably grammar.

    **Only below `MIN_VARIANT_LENGTH`, which is where the damage is.** A short kana
    name is the one that collides with ordinary Japanese — the same finding that
    makes short names matched literally rather than by variant — and measuring
    the filter over longer names found it removing real mentions of one such name
    at 12%, because Sudachi's reading of an unregistered stage name moves with the
    sentence around it. Narrow enough to be safe is worth more here than wide
    enough to be thorough.

    **Only the name route.** Her own board and reply context were never claimed on
    the strength of the characters, so there is nothing about them to disprove.

    **Before expansion, deliberately.** A false seed expands with exactly the same
    force as a real one: the two-mora name above reaches 868 comments at full
    closure without one of them being about a girl, and every one of those arrived
    through a seed this removes.
    """
    if len(name) >= MIN_VARIANT_LENGTH:
        return origins
    return {
        comment_id: origin
        for comment_id, origin in origins.items()
        if origin.route != ROUTE_NAME
        or not morphology.is_grammar(
            body := by_id[comment_id].get("body") or "",
            _occurrences(matcher, body),
        )
    }


def from_ids(
    payload: dict | None,
    comment_ids: Iterable[str],
    depth: int | None = DEFAULT_DEPTH,
) -> tuple[Selected, ...]:
    """The comments a model confirmed are about her, plus their neighbours.

    The second half of the two-step mode. Ids the store does not hold are dropped
    here as well as being reported to the operator, so a hallucinated id cannot
    reach the prompt by any route.

    Re-expanding restores what filtering removed: the accusations of shilling and
    the rebuttals that say how much a glowing report is worth. The model was asked
    which comments are *about* her, not which comments are worth reading around
    her, and those are different questions.
    """
    if not payload:
        return ()
    by_id = comments_by_id(payload)
    origins = {
        comment_id: Origin(ROUTE_CONFIRMED)
        for comment_id in dict.fromkeys(comment_ids)
        if comment_id in by_id
    }
    return _emit(payload, by_id, _with_context(payload, origins, depth))


def _with_context(
    payload: dict, origins: dict[str, Origin], depth: int | None
) -> dict[str, Origin]:
    """Add the reply neighbours of everything chosen, without displacing a route."""
    if not origins or depth == 0:
        return origins
    context = _neighbours(origins, _reply_graph(payload), depth)
    return origins | {comment_id: Origin(ROUTE_REPLY) for comment_id in context}


def _emit(
    payload: dict, by_id: dict[str, dict], origins: dict[str, Origin]
) -> tuple[Selected, ...]:
    order = _thread_order(payload)
    chosen = [(by_id[comment_id], origin) for comment_id, origin in origins.items()]
    return tuple(
        _as_selected(comment, origin)
        for comment, origin in sorted(chosen, key=lambda pair: _order_key(pair[0], order))
    )


def date_span(selected: Sequence[Selected]) -> tuple[str, str] | None:
    """First and last date in the selection, or `None` when it carries no dates.

    Stated in the prompt so the model knows what period it is reasoning over —
    the sample is never filtered by date, because a girl who was ranked first in
    March and drew complaints in May is answered by the change itself.
    """
    stamps = sorted(item.datetime for item in selected if item.datetime)
    return (stamps[0], stamps[-1]) if stamps else None
