"""HTML and text parsing / HTML 與文字解析.

Pure functions over markup. Nothing here touches the network or the filesystem,
so every function is directly testable against captured fixtures.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime

from bs4 import BeautifulSoup

COMMENTS_PER_PAGE = 50
COMMENT_DATETIME_FORMAT = "%Y/%m/%d %H:%M"

#: Results a full search page holds. Measured: all three captured search pages carry
#: exactly this many, and dividing the declared hit count by it reproduces the page
#: count each of them advertises.
SEARCH_RESULTS_PER_PAGE = 30

#: A blocked thread page measured about 64–70 KB against a healthy 190–230 KB.
#: Used only as a secondary signal, for the day the error markers change wording:
#: a small page with no comments is a block, a full-size one is a stale selector.
BLOCK_BODY_MAX_BYTES = 100_000

#: Markers Bakusai serves on a rate-limit block. The response is HTTP 200, so
#: these strings are the only reliable signal.
ACCESS_ERROR_MARKERS = (
    "アクセスエラー",
    "ERROR_CODE:thr_res",
    "ika error",
    "前のページへは[戻るボタン]",
)

_ARTICLE_SELECTOR = "#res_list div.article, div.article, article.article, li.article"
_DATE_SELECTOR = (
    'span[itemprop="commentTime"], time[itemprop="commentTime"], span.resdate, span.date'
)
_BODY_SELECTOR = "div.resbody, p.resbody"
#: The opening post, and only the opening post. Two alternatives were carried here
#: alongside it — `dl#thr_top dd.body` and `div[itemprop="articlebody"]` — and both
#: are gone. On a girl-scope thread all three match the same element, so they added
#: nothing; on a store-scope thread, which has no `#threadBody` at all, they matched
#: the shop panel below and reported an advert as somebody's opening post.
_THREAD_BODY_SELECTOR = "#threadBody"

#: Bakusai's own class for the shop panel it injects above a store-scope thread, in
#: the slot a girl-scope thread uses for its opening post. The panel is an advert —
#: 基本情報, phone number, opening hours, uncleaned pachinko fields, and
#: 「体験談はまだありません」 — byte-identical on every thread of the store, and
#: written by nobody. Selecting `#threadBody` alone already excludes it, because the
#: panel contains none; this is checked as well so a board that nests the two cannot
#: quietly reintroduce the advert as evidence.
BOARD_INFO_PANEL_CLASS = "fuzokulink"

#: Bakusai's generated keyword cloud — 「…への投稿から店舗を AI 解析」 followed by
#: 出禁／NG／フェラ／デブ and so on — appended *inside* `#threadBody`, after whatever
#: the poster actually wrote. So this one cannot be excluded by choosing a different
#: element; it has to be removed from the element before the text is read, or a
#: machine's word list is handed to a model as somebody's account of the shop.
BOARD_KEYWORD_PANEL_CLASS = "chart_wrapper"
#: The element wrapping the search hits themselves. Every search page also links a
#: news headline from its header ticker, with a `bid` and a `tid` and an anchor
#: indistinguishable from a result's. Scoping to this container is what tells them
#: apart; nothing about the link itself does.
_SEARCH_RESULTS_CONTAINER = "div.srcResultList"
#: `<span title="HIT件数 37件">37件</span>`, inside the results heading.
_RESULT_COUNT_SELECTOR = "div.resultsTitle"
_RESULT_COUNT = re.compile(r"([0-9,]+)\s*件")
_THREAD_HREF = re.compile(
    r"/thr_res/acode=(\d+)/ctgid=(\d+)/bid=(\d+)/tid=(\d+)/"
)
_LAST_POST = re.compile(r"最新レス投稿日時[：:]\s*(\d{4}/\d{2}/\d{2} \d{2}:\d{2})")
_BOARD_LABEL = re.compile(r"\S*掲示板\s+(\S+)")
_REPLY_COUNT = re.compile(r"レス数\s*(\d+)")

#: What a board is for, read from the suffix of its name. The prefix carries the
#: region and trade and varies freely — `福岡風俗・お店`, `東京デリヘル・お店`,
#: `西東京風俗・個人` — but the suffix after `・` is stable site-wide.
STORE_SCOPE = "store"
GIRL_SCOPE = "girl"
GENERAL_SCOPE = "general"
UNKNOWN_SCOPE = "unknown"

#: The suffixes that classify a board, and what each means. Public because the board
#: family is the same name with the suffix taken off — two readings of one table that
#: must never drift apart.
SCOPE_BY_SUFFIX = {
    "お店": STORE_SCOPE,
    "個人": GIRL_SCOPE,
    "総合": GENERAL_SCOPE,
}
_COMMENT_NUMBER = re.compile(r"(\d+)")
_REPLY_REFERENCE = re.compile(r">>(\d+)")


@dataclass(frozen=True)
class Comment:
    """One reply in a thread."""

    number: int
    datetime_text: str
    body: str


@dataclass(frozen=True)
class ThreadRef:
    """Everything needed to rebuild a thread's URL, recovered from search results.

    `last_post_text` is the thread's most recent post time as the search page
    reports it. It lets a caller skip a thread that cannot hold anything recent
    enough to want, without spending a request to find that out. Empty when the
    search page did not say.

    `board_label` is the board's name, e.g. `東京デリヘル・お店`. Its suffix says
    whether the thread discusses the store as a whole or one girl — the distinction
    the whole product rests on. See `board_scope`.

    `description` is the thread's opening post, filled in once the thread is
    fetched. Search results do not carry it.

    `reply_count` is the thread's declared 「レス数」, also from the search page. It
    is how much fetching the thread will cost — one request per `COMMENTS_PER_PAGE`
    replies — and knowing it before any request is what lets a run estimate its own
    length. 0 when the search page did not say.
    """

    acode: int
    ctgid: int
    bid: int
    tid: str
    title: str
    last_post_text: str = ""
    board_label: str = ""
    description: str = ""
    reply_count: int = 0


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html or "", "html.parser")


def parse_comments(html: str) -> tuple[Comment, ...]:
    """Extract every reply on a thread page, skipping the thread header (#0)."""
    soup = _soup(html)
    comments: list[Comment] = []
    for article in soup.select(_ARTICLE_SELECTOR):
        number_el = article.select_one("span.resnumb a")
        if number_el is None:
            continue
        match = _COMMENT_NUMBER.search(number_el.get_text(strip=True))
        if match is None:
            continue
        number = int(match.group(1))
        if number == 0:
            continue
        date_el = article.select_one(_DATE_SELECTOR)
        body_el = article.select_one(_BODY_SELECTOR)
        comments.append(
            Comment(
                number=number,
                datetime_text=date_el.get_text(strip=True) if date_el else "",
                # Join with newlines: `<br/>` separates lines in a post, and
                # dropping it glues Japanese sentences together with no space to
                # mark the boundary, which destroys meaning for later analysis.
                body=body_el.get_text("\n", strip=True) if body_el else "",
            )
        )
    return tuple(comments)


def _digits(text: str | None) -> int:
    """The digits in `text` read as an integer, or 0 when there are none."""
    return int(re.sub(r"[^\d]", "", text or "") or "0")


def parse_thread_description(html: str) -> str:
    """The thread's opening post, which the replies are answering.

    It is not a reply and carries no comment number, so it is not in
    `parse_comments`, but it is often the most informative text on the page — a
    thread with no replies at all can still open with a first-hand report. Skipping
    it would throw that away.

    Two things Bakusai injects are removed first, because neither was written by
    anybody: the shop panel that stands where a store-scope thread's opening post
    would be, and the AI keyword cloud appended inside the post itself. A thread
    left with nothing after that had no opening post, and `""` says so.
    """
    element = _soup(html).select_one(_THREAD_BODY_SELECTOR)
    if element is None or element.find_parent(class_=BOARD_INFO_PANEL_CLASS) is not None:
        return ""
    for panel in element.select(f".{BOARD_KEYWORD_PANEL_CLASS}"):
        panel.decompose()
    return element.get_text("\n", strip=True)


def parse_comment_count(html: str) -> int:
    """How many replies the thread declares it holds. 0 means a genuinely empty thread."""
    count_el = _soup(html).select_one("div.res-count")
    if count_el is not None:
        return _digits(count_el.get_text(strip=True))
    numbers = [c.number for c in parse_comments(html)]
    return max(numbers) if numbers else 0


def pages_for(reply_count: int) -> int:
    """How many pages a thread of this many replies spans. Never fewer than one."""
    return max(1, -(-max(reply_count, 0) // COMMENTS_PER_PAGE))


def parse_total_pages(html: str) -> int:
    """How many pages the thread spans, from its reply count."""
    return pages_for(parse_comment_count(html))


def parse_current_page(html: str) -> int | None:
    """The page number the server says it served. `None` when the page has no pager.

    Comparing this against the page that was requested is the only way to catch a
    pinned response, which is otherwise indistinguishable from healthy data.
    """
    element = _soup(html).select_one("span.paging_number")
    if element is None:
        return None
    return _digits(element.get_text(strip=True)) or None


def is_access_error(html: str) -> bool:
    """True when the response is a rate-limit block rather than content."""
    return any(marker in (html or "") for marker in ACCESS_ERROR_MARKERS)


def parse_search_results(html: str) -> tuple[ThreadRef, ...]:
    """Thread references from a region-wide search page.

    Region search crosses boards, so the category and board of each result must be
    read from its own link rather than assumed.

    Only the results container is read. The page header carries a news ticker whose
    links are thread links in every respect, and its headline rotates from page to
    page — read as results they become stores nobody searched for, and they keep any
    "this page brought nothing new" rule alive forever. A page with no container at
    all is read whole, which is what synthetic test pages rely on.
    """
    soup = _soup(html)
    results = soup.select_one(_SEARCH_RESULTS_CONTAINER) or soup
    by_tid: dict[str, ThreadRef] = {}
    for anchor in results.select('a[href*="thr_res"][href*="tid="]'):
        match = _THREAD_HREF.search(anchor.get("href", ""))
        if match is None:
            continue
        acode, ctgid, bid, tid = match.groups()

        title = _anchor_title(anchor)
        existing = by_tid.get(tid)
        if existing is not None and not (title and not existing.title):
            # A thread can appear more than once on a page — as a thumbnail and
            # again as text. Keep whichever occurrence actually names it.
            continue

        by_tid[tid] = ThreadRef(
            acode=int(acode),
            ctgid=int(ctgid),
            bid=int(bid),
            tid=tid,
            title=title,
            last_post_text=_search_field(anchor, _LAST_POST),
            board_label=_search_field(anchor, _BOARD_LABEL),
            reply_count=int(_search_field(anchor, _REPLY_COUNT) or 0),
        )
    return tuple(by_tid.values())


def parse_result_count(html: str) -> int | None:
    """How many hits the search declares it found. `None` when the page does not say.

    This is what makes a search's length knowable before walking it. The pager
    cannot serve: a long search truncates it — 504 results print `1 2 3 … 15 …` —
    so its highest visible number is not the last page.
    """
    heading = _soup(html).select_one(_RESULT_COUNT_SELECTOR)
    if heading is None:
        return None
    match = _RESULT_COUNT.search(heading.get_text(" ", strip=True))
    return _digits(match.group(1)) if match else None


def pages_for_results(count: int) -> int:
    """How many search pages this many hits span. Never fewer than one."""
    return max(1, -(-max(count, 0) // SEARCH_RESULTS_PER_PAGE))


def _anchor_title(anchor) -> str:
    """The thread title, falling back to a thumbnail's alt text.

    Image-led results wrap the link around an `<img>` and carry the title only in
    its `alt`. Reading just the link text leaves such a thread nameless, which both
    blanks it in the operator's board menu and stops it counting as a title match.
    """
    text = anchor.get_text(strip=True)
    if text:
        return text
    image = anchor.find("img")
    return (image.get("alt") or "").strip() if image is not None else ""


def _search_field(anchor, pattern: re.Pattern[str]) -> str:
    """Pull one value out of the text the search page prints beside a result."""
    entry = anchor.find_parent("li")
    if entry is None:
        return ""
    match = pattern.search(entry.get_text(" ", strip=True))
    return match.group(1) if match else ""


def normalise_kana(text: str) -> str:
    """Fold hiragana onto katakana, the way Bakusai's own search does.

    A store is written both ways — `ときめき胸きゅん女学院` and
    `ときめき胸キュン女学院` are one shop — and search returns both spellings, so
    matching has to see through the difference too.
    """
    return "".join(
        chr(ord(character) + 0x60) if "ぁ" <= character <= "ゖ" else character
        for character in text or ""
    )


def normalise_spacing(text: str) -> str:
    """Fold width and kana, keeping word boundaries as single spaces.

    What separates this from `normalise_for_match` is that a title still has to be
    splittable into words after folding — the tokens either side of a store's name
    are what tell one branch of a chain from another.
    """
    return normalise_kana(" ".join(unicodedata.normalize("NFKC", text or "").split()))


def normalise_for_match(text: str) -> str:
    """Fold a title or a store name to the form Bakusai's own search compares.

    The site normalises more than kana. Searching `E+アイドルスクール` returns
    `E＋ アイドルスクール りら` and prints the term back as `E アイドルスクール`: the
    fullwidth ＋ folds onto the halfwidth one, and the sign is treated as a break
    rather than a character. A matcher that only folds kana discards a thread the
    site has just handed it, which is silent loss of exactly the data that was asked
    for.

    Kept apart from `normalise_kana`, which the analysis layer's girl-name matcher
    depends on. Widening that would widen girl-name matching too — measured, and
    rejected: a short kana name collides with ordinary Japanese, so a looser
    matcher buys thread coverage by paying in false attributions.
    """
    return normalise_spacing(text).replace(" ", "")


def title_mentions_store(title: str, store: str) -> bool:
    """True when the title names the store at all, however it is written.

    Deliberately blind to word boundaries: `中洲ときめき胸キュン女学院` and
    `渋谷マテリアル` are the store behind a district, and a boundary rule drops both.
    Telling those from `個室ボックス金太郎`, which is another business, is not this
    function's job — see `candidates.group_candidates`, which offers them as separate
    stores under their own names. What this rejects is the thread that names the store
    nowhere in its title, which region search returns because it reads comment bodies.
    """
    if not title or not store:
        return False
    return normalise_for_match(store) in normalise_for_match(title)


def board_scope(board_label: str) -> str:
    """Whether a board discusses stores, individual girls, or the trade generally.

    Bakusai names boards `{region}{trade}・{scope}`. The prefix varies by region and
    trade — 福岡風俗, 東京デリヘル, 西東京風俗 — but the suffix is stable site-wide,
    so it, not the thread title, is what tells store-level talk from girl-level talk.

    Read as a tail rather than as the whole partition, because a trade can be written
    into the same segment: `大阪遊郭・新地お店` partitions to `新地お店`, which no
    exact table can hold, and every 新地 store lives on a board named that way.
    """
    label = board_label or ""
    for suffix, scope in SCOPE_BY_SUFFIX.items():
        if label.endswith(suffix):
            return scope
    return UNKNOWN_SCOPE


def parse_comment_datetime(text: str | None) -> datetime | None:
    """Read Bakusai's absolute timestamp. `None` when it cannot be read.

    Callers treat `None` as "keep this comment", so an unfamiliar format degrades
    into fetching too much rather than silently truncating a thread.
    """
    if not text:
        return None
    try:
        return datetime.strptime(text[:16], COMMENT_DATETIME_FORMAT)
    except ValueError:
        return None


def reply_references(body: str) -> tuple[int, ...]:
    """Comment numbers this body replies to."""
    return tuple(int(n) for n in _REPLY_REFERENCE.findall(body or ""))
