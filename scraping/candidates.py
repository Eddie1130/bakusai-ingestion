"""Grouping search hits into candidate stores / 把搜尋結果分成候選店家.

A board is not a store. `東京デリヘル・お店` carries `渋谷 マテリアル`,
`下北沢 マテリアル` and `町田 マテリアルガールズ` side by side, so no choice of board
separates them; and one store routinely spans several boards — `TOKYO BUNNYS CLUB` sits
on `吉原ソープ・お店` and `東京風俗・お店` at once. What identifies a store is what its
own thread titles say around its name.

Pure functions over already-parsed search results. Nothing here fetches or writes.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from . import parser
from .parser import ThreadRef

_ALIAS_SPEC = re.compile(r"(.+?)[\s　]*[（(]([^（）()]+)[）)]")
_ALIAS_SEPARATORS = re.compile(r"[/／|｜]")


@dataclass(frozen=True)
class StoreTarget:
    """A store to look up, plus any alternative names it goes by on Bakusai.

    `district` is the word the operator typed ahead of the name, when they typed one.
    It never reaches Bakusai — searching `渋谷 マテリアル` narrows the site's own search
    to threads carrying both words, which drops the girl threads that name only the
    store. It is a filter over the candidates instead.
    """

    name: str
    search_names: tuple[str, ...]
    district: str = ""


def parse_store_target(spec: str) -> StoreTarget | None:
    """Read an operator's input, supporting the `嵐(錦)` alias syntax."""
    cleaned = (spec or "").strip().lstrip("﻿")
    if not cleaned or cleaned.startswith("#"):
        return None

    match = _ALIAS_SPEC.fullmatch(cleaned)
    if match is None:
        return StoreTarget(cleaned, (cleaned,))

    name = match.group(1).strip()
    if not name:
        return None
    aliases = [a.strip() for a in _ALIAS_SEPARATORS.split(match.group(2)) if a.strip()]

    search_names: list[str] = []
    for candidate in [name, *aliases]:
        if candidate not in search_names:
            search_names.append(candidate)
    return StoreTarget(name, tuple(search_names))

#: Where a store's identity may be drawn from, best first. Only the highest tier that
#: yields anything is used: a name with real 店 boards must not also grow identities out
#: of the unrelated same-named chain sitting on boards Bakusai never classified.
ANCHOR_SCOPE_ORDER = (
    parser.STORE_SCOPE,
    parser.GIRL_SCOPE,
    parser.GENERAL_SCOPE,
    parser.UNKNOWN_SCOPE,
)

#: A circled numeral is never part of a word, so it is a volume marker wherever it
#: trails — `金太郎 泉大津店⑨` is glued and still a volume.
_CIRCLED = "①-⓿㉑-㉟㊱-㊿"
#: Bare digits are only a volume when they stand alone. `ときめき胸キュン女学院 ちさと14`
#: is a girl's fourteenth thread, not the store's — the digits belong to her name.
_VOLUME_MARKER = re.compile(rf"(?:\s+[0-9０-９]+|[{_CIRCLED}]+)\s*$")


def strip_volume_marker(title: str) -> str:
    """A thread title without the volume its store has reached.

    `渋谷 マテリアル 元祖清楚系デリヘル 74` and `… 73` are one store, and the number is
    the only thing between them.
    """
    stripped = (title or "").strip()
    while True:
        shorter = _VOLUME_MARKER.sub("", stripped).strip()
        if shorter == stripped:
            return stripped
        stripped = shorter


def board_family(board_label: str) -> str:
    """The board's region and trade, with its scope suffix removed.

    `東京デリヘル・お店` and `東京デリヘル・個人` are one family — a store's own board
    and the board its girls are discussed on. A board Bakusai gave no scope suffix is
    its own family, so unclassified boards never pool into one.
    """
    label = (board_label or "").strip()
    if parser.board_scope(label) == parser.UNKNOWN_SCOPE:
        return label
    for suffix in parser.SCOPE_BY_SUFFIX:
        if label.endswith(suffix):
            return label[: -len(suffix)].rstrip("・")
    return label


def _name_pattern(name: str) -> re.Pattern[str] | None:
    """Match a store name in a folded title, tolerating spaces inside it.

    Bakusai's search treats a sign as a break — it returns `E＋ アイドルスクール りら`
    for `E+アイドルスクール` — so the same store is written with and without a space in
    the middle of its name, and both have to reduce to the same store.
    """
    folded = parser.normalise_for_match(name)
    if not folded:
        return None
    return re.compile(r"\s*".join(re.escape(character) for character in folded))


def title_tokens(title: str, search_names: Sequence[str]) -> frozenset[str]:
    """What a thread title says about the store, apart from the store's own name.

    This is the whole identity signal. Everything a title carries around the name is
    kept — a district before it (`渋谷 マテリアル …`, `中洲ときめき胸キュン女学院`,
    glued or not), a branch after it (`E+アイドルスクール 池袋店`), a subtitle
    (`… 元祖清楚系デリヘル`) — because which of those a store uses is not knowable in
    advance, and picking one of them was measurably wrong: keying on the prefix alone
    merges all five `E+アイドルスクール` branches when the operator searches
    `アイドルスクール` rather than `E+アイドルスクール`, and keying on a branch suffix
    ending in `店` splits `E+アイドルスクール 錦糸町`, which has no `店`, away from its
    own chain.
    """
    folded = parser.normalise_spacing(strip_volume_marker(title))
    for name in search_names:
        pattern = _name_pattern(name)
        if pattern is None:
            continue
        found = pattern.search(folded)
        if found is not None:
            folded = f"{folded[: found.start()]} {folded[found.end():]}"
            break
    return frozenset(token for token in folded.split() if token)


@dataclass(frozen=True)
class StoreCandidate:
    """One store the search found, and the threads that belong to it."""

    #: What this store's own threads say around its name. Empty is legitimate — a
    #: store whose titles carry nothing but its name — and such a store is then held
    #: together by its board family rather than by its key.
    key: frozenset[str]
    #: The store's own title, volume removed. Operator-facing, and its directory name.
    name: str
    threads: tuple[ThreadRef, ...]
    #: Board families the store's own threads sit on. A store can span more than one.
    families: frozenset[str]

    @property
    def latest_post(self) -> str:
        return max((thread.last_post_text for thread in self.threads), default="")

    @property
    def board_labels(self) -> tuple[str, ...]:
        seen: list[str] = []
        for thread in self.threads:
            if thread.board_label and thread.board_label not in seen:
                seen.append(thread.board_label)
        return tuple(seen)

    def answers_to(self, word: str) -> bool:
        """Whether an operator typing `word` meant this store.

        Containment either way, because the operator types the district and the title
        may carry more than that — `池袋` has to find `池袋店`.
        """
        typed = parser.normalise_for_match(word)
        if not typed:
            return False
        return any(
            typed in token or token in typed
            for token in (parser.normalise_for_match(t) for t in self.key)
        )


@dataclass(frozen=True)
class Grouping:
    """Every store the search found, plus the threads that belong to none of them."""

    candidates: tuple[StoreCandidate, ...]
    #: Threads naming the store whose owner cannot be decided — they answer to two
    #: stores at once, or to none and their board family holds more than one. They are
    #: left out rather than guessed: attributing another store's girls to this one is
    #: not visible downstream, whereas a stated omission is.
    unassigned: tuple[ThreadRef, ...]

    def answering_to(self, word: str) -> tuple[StoreCandidate, ...]:
        return tuple(candidate for candidate in self.candidates if candidate.answers_to(word))

    def with_key(self, key: Iterable[str]) -> StoreCandidate | None:
        wanted = frozenset(key)
        return next((c for c in self.candidates if c.key == wanted), None)


def _anchor_threads(refs: Sequence[ThreadRef]) -> list[ThreadRef]:
    """The threads a store's identity may be built from, from the best tier present."""
    by_scope: dict[str, list[ThreadRef]] = {}
    for ref in refs:
        by_scope.setdefault(parser.board_scope(ref.board_label), []).append(ref)
    for scope in ANCHOR_SCOPE_ORDER:
        if by_scope.get(scope):
            return by_scope[scope]
    return []


def _newest_first(threads: Iterable[ThreadRef]) -> tuple[ThreadRef, ...]:
    return tuple(sorted(threads, key=lambda t: t.last_post_text, reverse=True))


def group_candidates(refs: Sequence[ThreadRef], target: StoreTarget) -> Grouping:
    """Split search hits into the stores they belong to.

    Threads whose own title never names the store are dropped without comment: region
    search matches comment bodies too, so every busy board returns other shops' threads
    that merely mention this one.
    """
    named = [ref for ref in refs if _mentions(ref, target)]
    tokens = {ref.tid: title_tokens(ref.title, target.search_names) for ref in named}

    anchors: dict[frozenset[str], list[ThreadRef]] = {}
    for ref in _anchor_threads(named):
        anchors.setdefault(tokens[ref.tid], []).append(ref)
    if not anchors:
        return Grouping((), ())

    families = {
        key: frozenset(board_family(ref.board_label) for ref in threads)
        for key, threads in anchors.items()
    }
    members: dict[frozenset[str], list[ThreadRef]] = {key: list(v) for key, v in anchors.items()}
    claimed = {ref.tid for threads in anchors.values() for ref in threads}

    unassigned: list[ThreadRef] = []
    for ref in named:
        if ref.tid in claimed:
            continue
        owner = _owner(tokens[ref.tid], board_family(ref.board_label), families)
        if owner is None:
            unassigned.append(ref)
        else:
            members[owner].append(ref)

    candidates = [
        StoreCandidate(
            key=key,
            name=strip_volume_marker(_newest_first(anchors[key])[0].title),
            threads=_newest_first(threads),
            families=families[key],
        )
        for key, threads in members.items()
    ]
    candidates.sort(key=lambda c: (len(c.threads), c.latest_post), reverse=True)
    return Grouping(tuple(candidates), _newest_first(unassigned))


def _mentions(ref: ThreadRef, target: StoreTarget) -> bool:
    return any(parser.title_mentions_store(ref.title, name) for name in target.search_names)


def _owner(
    tokens: frozenset[str],
    family: str,
    families: dict[frozenset[str], frozenset[str]],
) -> frozenset[str] | None:
    """Which store a thread belongs to, or `None` when that cannot be decided.

    A shared token decides it — `渋谷 デリヘル マテリアル リサコ` names 渋谷 and so does
    the store. Failing that, a board family holding exactly one store decides it, which
    is what keeps threads carrying no district at all (`マテリアル ユア`) and threads
    whose district is spelled differently from the store's own (`新大久保 …` against a
    store whose boards say `大久保`) with their store. Two candidate owners, or none in a
    family holding several, is a coin toss and is refused.
    """
    shared = [key for key in families if key & tokens]
    if len(shared) == 1:
        return shared[0]
    if shared:
        return None
    in_family = [key for key, where in families.items() if family in where]
    return in_family[0] if len(in_family) == 1 else None
