"""Proving a match is *not* a name / 證明這個 match 不是名字.

A short kana stage name is indistinguishable from ordinary Japanese by string
matching alone: one two-mora name returns 106 comments in `アイドル研究生` and every
one of them is really the sentence ending 「〜かもね」. An earlier version answered
this with an LLM. This answers it with a dictionary.

**The question is deliberately the negative one.** Nothing here is asked whether a
match *is* a girl's name, because nothing local can know that — an unregistered
stage name is an unknown word, and an unknown word is exactly what a dictionary
cannot rule on. It is asked whether the characters that matched are, beyond
argument, a piece of grammar that happens to spell her name. Silence means keep.

**Sudachi rather than GiNZA, and a tokeniser rather than a parser.** GiNZA's NER
would only ever say "keep", which is already the default, and its verdict moves
with the model version — a statistical judgement inside the selection would make
the selection, and therefore the cache key, drift under a dependency upgrade.
Sudachi is a dictionary lookup: same text, same answer, forever.

**Measured before it was written.** Across 15 stores and the 13 short girl names
that have their own boards on disk — 750 name-route matches — this drops 110 and
not one of them is a real mention.
"""
from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache

#: Parts of speech that cannot be a stage name on their own, in Sudachi's own
#: vocabulary. Everything absent from this set — every noun, verb, adjective,
#: prefix, suffix, and every unknown word, which is what an unregistered stage
#: name becomes — means keep. The asymmetry is the whole design.
CLOSED_CLASS = frozenset({"助詞", "助動詞", "接続詞", "感動詞", "補助記号", "空白"})

#: Ported from an earlier version's extraction core, which arrived at this set
#: over a lot of real text — a guessed pair of ちゃん/さん would miss the rest.
#: It lives here rather than in `honorifics.py` because both that module and this
#: one need it, and this one is the only one of the two that nothing else imports.
HONORIFICS = (
    "ちゃん",
    "さん",
    "くん",
    "たん",
    "さま",
    "様",
    "君",
    "嬢",
    "chan",
    "Chan",
    "CHAN",
)

#: Long enough for the longest honorific, and no longer: a suffix further away
#: than this is addressing something else.
HONORIFIC_LOOKAHEAD = 8


@lru_cache(maxsize=1)
def _tokenizer():
    """Sudachi's tokeniser, or `None` when it is not installed.

    The dictionary is tens of megabytes and takes hundredths of a second to open,
    so it is opened once per process and never again. Absence is not an error:
    this module only ever removes comments, so an install without it selects more
    rather than selecting wrongly.
    """
    try:
        from sudachipy import dictionary, tokenizer
    except ImportError:  # pragma: no cover - exercised by monkeypatching
        return None
    return dictionary.Dictionary().create(), tokenizer.Tokenizer.SplitMode.C


def _covering(body: str, start: int, end: int) -> list[tuple[str, int, int]]:
    """The tokens overlapping `[start, end)`, as `(part of speech, from, to)`."""
    tokens, mode = _tokenizer()
    covering, position = [], 0
    for token in tokens.tokenize(body, mode):
        first, last = position, position + len(token.surface())
        position = last
        if first < end and last > start:
            covering.append((token.part_of_speech()[0], first, last))
    return covering


def _is_grammar(body: str, start: int, end: int) -> bool:
    """Whether this one occurrence is grammar rather than a name.

    Four things must all hold, and each of the last three closes a false negative
    that was found by measurement rather than reasoned about in advance:

    1. **Every token it touches is closed-class.** A single noun, verb or unknown
       word anywhere under the span means keep — 「あいす」 keeps 「あい」.
    2. **It starts where a token starts.** 「ルックス{ですず}レベル」 tokenises as
       です＋ず and would otherwise take a real 「すず」 with it; a span beginning
       inside a token is an artefact of where the tokeniser cut, not a finding.
    3. **It is not a whole token by itself.** A name Sudachi happens to know as a
       particle is still the only thing in its own cell.
    4. **No honorific follows.** 「もねちゃん」 and 「そうかもね」 tokenise the same
       way — も＋ね — and the suffix is the only thing that separates them.
    """
    covering = _covering(body, start, end)
    if not covering:
        return False
    if any(part not in CLOSED_CLASS for part, _, _ in covering):
        return False
    if covering[0][1] != start:
        return False
    if len(covering) == 1 and covering[0][2] == end:
        return False
    tail = body[end : end + HONORIFIC_LOOKAHEAD]
    return not any(tail.startswith(honorific) for honorific in HONORIFICS)


def is_grammar(body: str, spans: Sequence[tuple[int, int]]) -> bool:
    """Whether **every** occurrence in this comment is grammar rather than a name.

    All of them, because one real mention is what the comment is evidence of and
    the rest is noise around it. A comment with no occurrence at all is not a
    finding of grammar — it cannot arrive here through `select`, and answering yes
    would discard a comment on no evidence whatsoever.

    `spans` are offsets found in kana-folded text and read against the raw body.
    That is sound only because folding is a per-character map that never changes a
    length, and it matters: a run of full-width katakana standing in for ordinary
    kana is not Japanese, and Sudachi calls it an interjection. A span that does not fit the body means the guarantee has
    been broken somewhere and is raised rather than analysed.
    """
    if not spans or _tokenizer() is None:
        return False
    for start, end in spans:
        if not 0 <= start < end <= len(body):
            raise ValueError(f"span ({start}, {end}) does not fit a body of {len(body)}")
    return all(_is_grammar(body, start, end) for start, end in spans)
