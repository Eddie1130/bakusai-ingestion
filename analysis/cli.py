"""Interactive loop for the analysis layer / 分析層互動主迴圈.

Region, store, girl — then a prompt on the clipboard. It follows the scraping
loop's conventions deliberately: Traditional Chinese throughout, the same quit
words, and the reader injected as a callable so tests drive it without a terminal.
The clipboard writer is injected the same way, so no test shells out.

Nothing here writes into the repository or the data tree. That is the constraint
this whole layer exists to hold: an earlier version's derived-state sprawl began
with one file that seemed harmless to keep.
"""
from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

import paths
from scraping import store as store_file

from . import attribution, citations, diagnose, honorifics, prompt, select, tokens, verification
from .period import ALL_TIME, Period, parse_bound

Ask = Callable[[str], str]
Copy = Callable[[str], bool]
Paste = Callable[[], str]

QUIT_COMMANDS = frozenset({"q", "quit", "exit"})

#: Tried in order; the first one present wins. macOS, Wayland, then X11.
CLIPBOARD_WRITE_COMMANDS = (
    ("pbcopy",),
    ("wl-copy",),
    ("xclip", "-selection", "clipboard"),
)

CLIPBOARD_READ_COMMANDS = (
    ("pbpaste",),
    ("wl-paste",),
    ("xclip", "-selection", "clipboard", "-o"),
)

PROMPT_FILE_PREFIX = "bakusai_prompt_"
PROMPT_FILE_SUFFIX = ".txt"


def copy_to_clipboard(text: str) -> bool:
    """Put the prompt on the system clipboard, reporting whether it worked."""
    for command in CLIPBOARD_WRITE_COMMANDS:
        try:
            subprocess.run(
                list(command),
                input=text.encode("utf-8"),
                capture_output=True,
                check=True,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        return True
    return False


def read_clipboard() -> str:
    """Take the model's answer off the system clipboard."""
    for command in CLIPBOARD_READ_COMMANDS:
        try:
            done = subprocess.run(list(command), capture_output=True, check=True)
        except (OSError, subprocess.SubprocessError):
            continue
        return done.stdout.decode("utf-8", errors="replace")
    return ""


def prompt_area(ask: Ask = input) -> int | None:
    """Ask which region to look in. Returns `None` when the operator quits."""
    menu = "  ".join(f"{code}={label}" for code, label in sorted(paths.AREA_LABELS.items()))
    while True:
        raw = ask(f"請選擇地區 ({menu})，q 離開：").strip()
        if raw.lower() in QUIT_COMMANDS:
            return None
        if raw.isdigit() and int(raw) in paths.AREA_LABELS:
            return int(raw)
        print("  地區代碼無效，請重新輸入")


def prompt_store(names: Sequence[str], ask: Ask = input) -> str | None:
    """Ask which of the region's scraped stores to analyse."""
    listing = "\n".join(f"  [{i}] {name}" for i, name in enumerate(names, start=1))
    while True:
        print(listing)
        raw = ask(f"請選擇店家編號（1-{len(names)}），q 離開：").strip()
        if raw.lower() in QUIT_COMMANDS:
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(names):
            return names[int(raw) - 1]
        print("  編號無效，請重新輸入")


def prompt_girl(ask: Ask = input) -> str | None:
    """Ask for the girl's name, as written on the store's own roster."""
    while True:
        raw = ask("請輸入女生名字（照官網源氏名輸入），q 離開：").strip()
        if raw.lower() in QUIT_COMMANDS:
            return None
        if raw:
            return raw
        print("  名字無效，請重新輸入")


#: What the operator may type at the depth prompt, and what it means.
DEPTH_CHOICES: dict[str, int | None] = {
    "0": 0,
    "1": 1,
    "2": 2,
    "a": select.FULL_CHAIN,
}


def prompt_depth(ask: Ask = input) -> int | None:
    """Ask how far to follow the reply chains.

    One hop by default, on measurement: full closure from 199 matches for one
    measured name reaches 1,280 comments over 19 hops, and the far hops are forum
    argument carrying nothing about her. Wider is offered because a thin case
    sometimes needs it.
    """
    while True:
        raw = ask(
            "回覆鏈要展開幾層？（Enter=1 層，建議；0=不展開；2=2 層；a=整條鏈）："
        ).strip().lower()
        if not raw:
            return select.DEFAULT_DEPTH
        if raw in DEPTH_CHOICES:
            return DEPTH_CHOICES[raw]
        print("  層數無效，請重新輸入")


def deliver(text: str, copy: Copy = copy_to_clipboard) -> str:
    """Put the prompt where the operator can use it, and say where that was.

    The clipboard is the point — nobody selects 34,000 characters in a terminal —
    but a file copy makes two runs comparable without regenerating either. It goes
    to the system temporary directory, never into the project or the data tree.
    """
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix=PROMPT_FILE_PREFIX,
        suffix=PROMPT_FILE_SUFFIX,
        delete=False,
    ) as handle:
        handle.write(text)
        written = handle.name

    head = "  已複製到剪貼簿" if copy(text) else "  ⚠️ 無法寫入剪貼簿，請直接開啟下面的檔案"
    return (
        f"  提示詞共 {len(text):,} 字元\n{tokens.estimate(text).describe()}\n"
        f"{head}\n  副本：{written}"
    )


SINGLE_PASS = "single"
TWO_STEP = "two-step"
VERIFY = "verify"

#: What the operator may type at the mode prompt, and what it means.
MODE_CHOICES: dict[str, str] = {"1": SINGLE_PASS, "2": TWO_STEP, "3": VERIFY}


def prompt_mode(ask: Ask = input) -> str | None:
    """Ask what to do with this store and girl."""
    while True:
        raw = ask(
            "請選擇模式（Enter=1 單次產生報告，建議；"
            "2=兩段式，先讓模型篩留言 ID；3=驗證已完成的報告），q 離開："
        ).strip().lower()
        if raw in QUIT_COMMANDS:
            return None
        if not raw:
            return SINGLE_PASS
        if raw in MODE_CHOICES:
            return MODE_CHOICES[raw]
        print("  模式無效，請重新輸入")


def collect(what: str, ask: Ask = input, paste: Paste = read_clipboard) -> str | None:
    """Take a long answer back from the operator without routing it through the tty.

    A model's answer here is a list of a few hundred ids or a whole report —
    thousands of characters — and a terminal is the wrong pipe for that. The line
    editor re-renders the entire line on every character of a paste, canonical mode
    caps a line at a size well under what we need, and a blank line inside the text
    ends the read early. Every one of those failures is silent or looks like a hung
    terminal.

    So it comes off the clipboard, which the operator is already using to move text
    the other way, with a file path as the fallback for a machine with no clipboard
    tool. `None` means the operator gave up.

    Nothing collected here is written to disk. It is read, intersected against the
    store, used, and dropped.
    """
    while True:
        raw = ask(
            f"  {what}，然後直接按 Enter 從剪貼簿讀取；或輸入檔案路徑；q 取消："
        ).strip()
        if raw.lower() in QUIT_COMMANDS:
            return None
        if not raw:
            text = paste()
            if text.strip():
                return text
            print("  剪貼簿是空的，請先複製模型的回覆再按 Enter")
            continue
        # A path the operator typed, not a location this project owns — `paths.py`
        # resolves the data tree and has nothing to say about it.
        source = Path(raw).expanduser()
        if source.is_file():
            return source.read_text(encoding="utf-8", errors="replace")
        print("  找不到這個檔案，請確認路徑，或清空後按 Enter 改用剪貼簿")


#: How many dropped ids to list when a filter paste goes wrong. There the ids are
#: an error signal, so a sample is enough to see what happened.
DROPPED_PREVIEW = 5


def describe_dropped(found: citations.Citations) -> str:
    """Say what was thrown away, so a garbled paste is visible rather than silent."""
    if not found.unknown:
        return ""
    listed = "、".join(found.unknown[:DROPPED_PREVIEW])
    more = f" 等 {len(found.unknown)} 個" if len(found.unknown) > DROPPED_PREVIEW else ""
    return f"  ⚠️ 有 {len(found.unknown)} 個 ID 不在本輪允許的留言範圍，已捨棄：{listed}{more}"


def describe_narrowing(
    before: Sequence[select.Selected], after: Sequence[select.Selected]
) -> str:
    """Say whether the filter pass actually removed anything.

    Measured on one name it removed nothing, and the reason is not that
    re-expansion undid the filtering — dropping seeds scales the second prompt
    roughly linearly (185 of 206 seeds gives 414 comments, 103 gives 244). It is
    that there was nothing to remove: of 206 name matches exactly one is a literal
    false match, where the name's kana also spell an ordinary verb form mid-
    sentence, so a correct filter returns 454 of 457 comments. When that happens
    the extra paste bought nothing, and the operator should hear it rather than
    assume the mode did something.
    """
    kept = {item.comment_id for item in after}
    started = {item.comment_id for item in before}
    if kept == started:
        return "  ⚠️ 篩選後的證據與單次版完全相同，這一輪兩段式沒有縮小任何範圍"
    return (
        f"  與單次版相比：篩掉 {len(started - kept)} 則，"
        f"第二輪共 {len(kept)} 則（單次版 {len(started)} 則）"
    )


def describe_honorifics(rate: honorifics.HonorificRate) -> str:
    """Report the honorific rate, and warn when it says the name is a common word.

    Never a block. The lowest real stage name measured sits at 6.5%, close enough
    to the line that suppressing on it would eventually be wrong, and a genuinely
    obscure girl with a handful of mentions and no honorifics would be exactly the
    case a heuristic gets wrong.
    """
    if rate.occurrences == 0:
        return ""
    line = (
        f"  名字後接敬稱：{rate.rate:.1%}"
        f"（{rate.occurrences} 次出現中 {rate.with_honorific} 次）"
    )
    if not rate.is_suspicious:
        return line
    return (
        f"{line}\n"
        "  ⚠️ 比例偏低，這通常代表比對到的是普通詞彙而不是源氏名"
        "（例如兩拍的假名名字會比對到「〜かもね」這類句尾）。\n"
        "     請確認源氏名寫法；提示詞仍會照常產生。"
    )


def load_store(acode: int, store_name: str) -> dict | None:
    return store_file.load(paths.bakusai_path(acode, store_name))


def _describe_selection(
    selected: Sequence[select.Selected],
    payload: dict | None = None,
    girl: str = "",
) -> str:
    """What was chosen, over what period, and how well it is attributed.

    The confidence line is what tells the operator whether a large sample is large
    because she is discussed or large because her name is a common word — a count
    alone cannot, and the count is what used to be printed.
    """
    span = select.date_span(selected)
    period = f"{span[0]} ～ {span[1]}" if span else "日期不明"
    line = f"  已挑出 {len(selected)} 則留言（{period}）"
    confidence = attribution.describe(attribution.confidences(payload, girl, selected))
    return "\n".join(part for part in (line, confidence) if part)


def _gather(
    acode: int, store_name: str, girl: str, depth: int | None, period: Period = ALL_TIME
) -> tuple[dict, tuple[select.Selected, ...]] | str:
    """The store and her comments, or the message explaining why there are none.

    Both report modes open the same way; returning the message rather than
    printing it keeps who-prints-what in one place. Nothing was found is a
    conclusion the store file can be read for rather than guessed at — see
    `analysis/diagnose.py`.
    """
    payload = load_store(acode, store_name)
    if payload is None:
        return f"  「{store_name}」尚未抓取，請先執行抓取工具"
    payload = period.apply(payload)
    selected = select.select(payload, girl, depth)
    if not selected:
        if period != ALL_TIME:
            return f"  所選期間（{period.label}）找不到與「{girl}」相關的留言"
        return diagnose.explain_empty(payload, girl)
    return payload, selected


def analyse_once(
    acode: int,
    store_name: str,
    girl: str,
    copy: Copy = copy_to_clipboard,
    depth: int | None = select.DEFAULT_DEPTH,
    period: Period = ALL_TIME,
) -> str:
    """Build one girl's prompt and return the message to show the operator."""
    gathered = _gather(acode, store_name, girl, depth, period)
    if isinstance(gathered, str):
        return gathered
    payload, selected = gathered

    lines = [
        _describe_selection(selected, payload, girl),
        describe_honorifics(honorifics.honorific_rate(payload, girl)),
        deliver(prompt.build(store_name, girl, selected, period=period), copy),
    ]
    return "\n".join(line for line in lines if line)


def analyse_two_step(
    acode: int,
    store_name: str,
    girl: str,
    ask: Ask = input,
    copy: Copy = copy_to_clipboard,
    depth: int | None = select.DEFAULT_DEPTH,
    paste: Paste = read_clipboard,
    period: Period = ALL_TIME,
) -> str:
    """Filter first, then report over what the model confirmed.

    Two pastes rather than one. The second prompt reasons over concentrated
    evidence, re-expanded one hop so the credibility context that filtering would
    otherwise have removed comes back.

    Unlike its siblings this prints as it goes, because the operator has to see the
    first prompt land and act on it before there is anything to ask him for.
    """
    gathered = _gather(acode, store_name, girl, depth, period)
    if isinstance(gathered, str):
        return gathered
    payload, selected = gathered

    warning = describe_honorifics(honorifics.honorific_rate(payload, girl))
    print(_describe_selection(selected, payload, girl))
    if warning:
        print(warning)
    print(deliver(prompt.build_filter(store_name, girl, selected, period=period), copy))

    collected = collect("請複製模型回覆的留言 ID", ask, paste)
    if collected is None:
        return "  已取消"

    sent_ids = {item.comment_id for item in selected}
    sent_payload = {"comments": [
        item for key, item in store_file.comments_by_id(payload).items() if key in sent_ids
    ]}
    found = citations.cited(sent_payload, collected)
    read = f"  讀到 {len(collected)} 個字元、{found.total} 個 ID"
    if not found.known:
        return "\n".join(
            line
            for line in [read, "  沒有讀到任何本輪送出的留言 ID，請確認複製的內容", describe_dropped(found)]
            if line
        )

    confirmed = select.from_ids(payload, found.known, select.DEFAULT_DEPTH)
    lines = [
        read,
        f"  模型確認 {len(found.known)} 則，展開回覆脈絡後共 {len(confirmed)} 則",
        describe_dropped(found),
        describe_narrowing(selected, confirmed),
        deliver(prompt.build(store_name, girl, confirmed, period=period), copy),
    ]
    return "\n".join(line for line in lines if line)


def verify_report(
    acode: int, store_name: str, ask: Ask = input, paste: Paste = read_clipboard,
    period: Period = ALL_TIME,
) -> str:
    """Check what a finished report cited, and print the comments so it can be read.

    Requiring inline citations suppresses invention, because a model cannot easily
    fabricate a claim it must anchor to a concrete id. It does not prevent
    misattribution: a real id can be cited for a claim it does not support. So the
    machine answers the question it can answer — does this id exist — and the cited
    comments are printed for the operator to answer the one it cannot.
    """
    payload = load_store(acode, store_name)
    if payload is None:
        return f"  「{store_name}」尚未抓取，請先執行抓取工具"

    collected = collect("請複製整份完成的報告", ask, paste)
    if collected is None:
        return "  已取消"

    return (f"  驗證範圍：{period.label}；區間外 ID 亦視為不在範圍\n"
            + verification.render(period.apply(payload), collected, scoped=period != ALL_TIME))


NAME = "name"
FEATURES = "features"
ROSTER = "roster"


def prompt_target(ask: Ask = input) -> str | None:
    while True:
        raw = ask(
            "分析目標（Enter=1 已知名字；2=按外貌／服裝／紋身等特徵尋人；"
            "3=整理全店女生；4=驗證已完成的報告），q 離開："
        ).strip().lower()
        if raw in QUIT_COMMANDS:
            return None
        if raw in {"", "1", "2", "3", "4"}:
            return {"": NAME, "1": NAME, "2": FEATURES, "3": ROSTER, "4": VERIFY}[raw]
        print("  選項無效，請重新輸入")


def prompt_period(ask: Ask = input) -> Period | None:
    print("  時間以留言發表日期為準（店家當地時間），含首尾日。")
    print("  格式：YYYY、YYYY-MM、YYYY-MM-DD、now；兩欄留空＝全部已爬取資料。")
    print("  指定任一日期界線時，日期不明留言及區間外回覆一律排除。")
    while True:
        start = ask("開始日期（Enter=最早），q 離開：").strip()
        if start.lower() in QUIT_COMMANDS:
            return None
        end = ask("結束日期（Enter=最新），q 離開：").strip()
        if end.lower() in QUIT_COMMANDS:
            return None
        try:
            return Period(parse_bound(start), parse_bound(end, end=True))
        except (ValueError, OverflowError) as exc:
            print(f"  日期無效：{exc}，請重新輸入")


def analyse_discovery(
    acode: int, store_name: str, *, features: str | None = None,
    period: Period = ALL_TIME, copy: Copy = copy_to_clipboard,
) -> str:
    payload = load_store(acode, store_name)
    if payload is None:
        return f"  「{store_name}」尚未抓取，請先執行抓取工具"
    scoped = period.apply(payload)
    selected = select.all_comments(scoped)
    if not selected:
        return "  所選期間沒有可用留言，未產生提示詞"
    by_id = store_file.comments_by_id(scoped)
    thread_ids = {str(by_id[item.comment_id].get("thread_index")) for item in selected}
    threads = [thread for thread in scoped.get("threads", [])
               if str(thread.get("index")) in thread_ids]
    text = prompt.build_discovery(
        store_name, selected, features=features, period=period, threads=threads,
    )
    return "\n".join([
        f"  時間範圍：{period.label}",
        f"  已納入 {len(selected)} / {len(store_file.comments_by_id(payload))} 則留言，未按名字或特徵字面預篩。",
        deliver(text, copy),
    ])


def _run_mode(
    mode: str, acode: int, store_name: str, ask: Ask, copy: Copy, paste: Paste,
    period: Period = ALL_TIME,
) -> str | None:
    """Drive a name workflow or verification within the same strict period."""
    if mode == VERIFY:
        return verify_report(acode, store_name, ask, paste, period)
    girl = prompt_girl(ask)
    if girl is None:
        return None
    depth = prompt_depth(ask)
    if mode == TWO_STEP:
        return analyse_two_step(acode, store_name, girl, ask, copy, depth, paste, period)
    return analyse_once(acode, store_name, girl, copy, depth, period)


def main(
    ask: Ask = input,
    copy: Copy = copy_to_clipboard,
    paste: Paste = read_clipboard,
) -> int:
    """Run the interactive loop until the operator quits."""
    print("爆サイ 女生留言分析工具")
    while True:
        acode = prompt_area(ask)
        if acode is None:
            break
        names = paths.store_names(acode)
        if not names:
            print(f"  {paths.AREA_LABELS[acode]} 還沒有抓過任何店家，請先執行抓取工具")
            continue
        store_name = prompt_store(names, ask)
        if store_name is None:
            break
        target = prompt_target(ask)
        if target is None:
            break
        period = prompt_period(ask)
        if period is None:
            break
        if target == VERIFY:
            message = verify_report(acode, store_name, ask, paste, period)
        elif target == NAME:
            mode = prompt_mode(ask)
            if mode is None:
                break
            message = _run_mode(mode, acode, store_name, ask, copy, paste, period)
        else:
            features = None
            if target == FEATURES:
                while not features:
                    features = ask("請描述外貌、服裝、紋身或任何特點，q 離開：").strip()
                    if features.lower() in QUIT_COMMANDS:
                        return 0
                    if not features:
                        print("  請至少輸入一項特徵")
            message = analyse_discovery(
                acode, store_name, features=features, period=period, copy=copy
            )
        if message is None:
            break
        print(message)
    print("結束")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
