"""Turning selected comments into prompt lines / 將挑選結果排成提示詞行.

One comment per line is the leanest shape a model can still cite from. Bodies are
emitted exactly as scraped, line breaks included: a post's structure — a report
laid out as 容姿／接客／総評 — reads to the model the way it reads to a person, and
rewriting it would be the layer inventing content it was told not to invent.
"""
from __future__ import annotations

from collections.abc import Sequence

from .select import (
    RELATION_CONTEXT,
    ROUTE_BOARD,
    ROUTE_CONFIRMED,
    ROUTE_NAME,
    ROUTE_STORE,
    Selected,
)

DIRECT_MARK = "[直接]"
BOARD_MARK = "[本板]"
OPENING_MARK = "[開串文]"
CONFIRMED_MARK = "[確認]"
CONTEXT_MARK = "[脈絡]"
STORE_MARK = "[店內資料]"

#: What each mark means, in the order a legend should list them.
MARKS: dict[str, str] = {
    STORE_MARK: "店家留言，尚未判定在描述誰；也可能與任何女生無關",
    DIRECT_MARK: "內文直接提到該名字（可能是片假名或伏字寫法）",
    BOARD_MARK: "來自她本人的專屬討論串，整串都在講她，內文不一定會寫出名字",
    OPENING_MARK: (
        "討論串的開串文（不是回覆），編號為 -0，站方不提供日期。"
        "可能來自她本人的專屬討論串，也可能是內文直接提到她；"
        "沒有人回覆的討論串就只有這一則"
    ),
    CONFIRMED_MARK: "上一輪已由模型判定確實是在講她的留言",
    CONTEXT_MARK: "與上述留言互相回覆而被帶進來的，未必是在講她，請自行判斷",
}

#: The two routes that mean the comment is here on its own account. Read last:
#: context and confirmation say more about a comment than how it was first found.
_MARKS_BY_ROUTE: dict[str, str] = {
    ROUTE_STORE: STORE_MARK,
    ROUTE_NAME: DIRECT_MARK,
    ROUTE_BOARD: BOARD_MARK,
}

#: Stands in for a comment Bakusai served without a timestamp. The column has to
#: stay, or a dated line and an undated one become the same shape and the model
#: cannot tell which field it is looking at.
UNDATED = "日期不明"


def mark_of(item: Selected) -> str:
    """The one mark that says the most about this comment to a reading model.

    The selection records three things about every comment; a prompt line has room
    for one, and this is where the three collapse into it. The order is what the
    model has to know first, not what the record thinks is most specific:

    1. **Context** — it was never claimed to be about her, which outranks every
       other thing that could be said about it.
    2. **Confirmed** — a previous pass already ruled on it, so how the first pass
       reached it is settled history.
    3. **An opening post** — the thread's premise rather than a reply, and the one
       kind of comment Bakusai serves without a date.
    4. **The route** — her name, or her board.

    How the name matched deliberately does not appear. Literal, kana and masked are
    all her name being written, the difference is evidence strength rather than
    something to read differently, and adding it here would change a pinned prompt
    to tell the model something it cannot act on.

    A route with no mark is a bug, and raising is the only way it stays one instead
    of rendering as plausible text.
    """
    if item.relation == RELATION_CONTEXT:
        return CONTEXT_MARK
    if item.route == ROUTE_CONFIRMED:
        return CONFIRMED_MARK
    # Looked up before the structural test rather than inside it, so an unknown
    # route raises instead of being hidden by an opening post that happens to
    # carry it.
    mark = _MARKS_BY_ROUTE[item.route]
    return OPENING_MARK if item.is_opening else mark


def render_line(item: Selected, with_dates: bool = True) -> str:
    """`{comment_id} {date} {mark} {body}`, with the date omittable by the caller."""
    parts = [item.comment_id]
    if with_dates:
        parts.append(item.datetime or UNDATED)
    parts.append(mark_of(item))
    parts.append(item.body)
    return " ".join(parts)


def render(selected: Sequence[Selected], with_dates: bool = True) -> str:
    """Every selected comment, one per line. An empty selection renders as empty."""
    return "\n".join(render_line(item, with_dates) for item in selected)


def legend(selected: Sequence[Selected]) -> str:
    """Explain the marks that actually appear, so the prompt never describes
    a source the sample does not contain."""
    present = {mark_of(item) for item in selected}
    return "\n".join(
        f"- `{mark}` {description}"
        for mark, description in MARKS.items()
        if mark in present
    )
