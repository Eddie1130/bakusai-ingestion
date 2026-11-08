"""GUI use cases, sharing selection and citation rules with the CLI."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import secrets
import threading

import paths
from analysis import (
    attribution,
    citations,
    diagnose,
    honorifics,
    prompt,
    select,
    tokens,
)
from analysis.cli import describe_honorifics
from analysis.period import Period, parse_bound
from scraping import store
from .templates import validate_overrides
from . import custom_selection


def text_field(data: dict, key: str, maximum: int = 1000) -> str:
    value = data.get(key, "")
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError(f"「{key}」格式不正確或文字過長")
    return value.strip()


def location(data: dict) -> tuple[int, str]:
    acode = data.get("acode")
    if (
        isinstance(acode, bool)
        or not isinstance(acode, int)
        or acode not in paths.AREAS
    ):
        raise ValueError("請選擇有效地區")
    name = text_field(data, "store", 200)
    if not name or name != paths.safe_store_filename(name):
        raise ValueError("請選擇有效店家")
    return acode, name


def period_from(data: dict) -> Period:
    try:
        return Period(
            parse_bound(text_field(data, "start", 20)),
            parse_bound(text_field(data, "end", 20), end=True),
        )
    except (ValueError, OverflowError) as exc:
        raise ValueError(
            "日期無效，請使用 YYYY、YYYY-MM、YYYY-MM-DD；開始不可晚於結束"
        ) from exc


def load_payload(acode: int, name: str) -> dict:
    payload = store.load(paths.bakusai_path(acode, name))
    if not isinstance(payload, dict):
        raise ValueError("找不到店家資料，請先新增店家並抓取留言")
    return payload


def store_list() -> dict:
    regions = [
        {"acode": code, "label": label} for code, label in paths.AREA_LABELS.items()
    ]
    items = []
    for code in paths.AREAS:
        for name in paths.store_names(code):
            try:
                info_path = paths.info_path(code, name)
                info = store.load(info_path)
                # Partial scrapes only update bakusai.json. The identity summary
                # is written at successful completion, so it may describe an
                # older, complete run while the current data is incomplete.
                if (
                    info is None
                    or paths.bakusai_path(code, name).stat().st_mtime_ns
                    >= info_path.stat().st_mtime_ns
                ):
                    payload = load_payload(code, name)
                    meta = {
                        **payload.get("scrape_meta", {}),
                        "total_comment_count": payload.get("total_comment_count", 0),
                        "first_comment": payload.get("first_comment"),
                        "last_comment": payload.get("last_comment"),
                    }
                else:
                    meta = info.get("bakusai", {})
                items.append(
                    {
                        "acode": code,
                        "name": name,
                        "region": paths.AREA_LABELS[code],
                        "count": meta.get("total_comment_count", 0),
                        "updated": meta.get("last_scraped_at"),
                        "first": meta.get("first_comment"),
                        "last": meta.get("last_comment"),
                        "complete": meta.get("last_run_completed", True),
                        "error": "",
                    }
                )
            except (OSError, ValueError, TypeError, AttributeError):
                items.append(
                    {
                        "acode": code,
                        "name": name,
                        "region": paths.AREA_LABELS[code],
                        "count": 0,
                        "updated": None,
                        "first": None,
                        "last": None,
                        "complete": False,
                        "error": "資料讀取失敗，請檢查店家檔案",
                    }
                )
    return {"regions": regions, "stores": items}


def comment_view(comment: dict, threads: dict) -> dict:
    return {
        "id": comment["comment_id"],
        "date": comment.get("datetime") or "日期不明",
        "body": comment.get("body") or "",
        "thread": threads.get(comment.get("thread_index"), ""),
    }


def browse(data: dict) -> dict:
    acode, name = location(data)
    payload = period_from(data).apply(load_payload(acode, name))
    query = text_field(data, "query", 200).casefold()
    offset = data.get("offset", 0)
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("留言頁碼無效")
    threads = {
        t["index"]: t.get("thread_title", "") for t in payload.get("threads", [])
    }
    comments = list(store.comments_by_id(payload).values())
    comments.sort(
        key=lambda c: (c.get("datetime") or "", c["comment_id"]), reverse=True
    )
    if query:
        comments = [
            c
            for c in comments
            if query
            in (
                c["comment_id"]
                + " "
                + (c.get("body") or "")
                + " "
                + threads.get(c.get("thread_index"), "")
            ).casefold()
        ]
    return {
        "total": len(comments),
        "offset": offset,
        "comments": [comment_view(c, threads) for c in comments[offset : offset + 40]],
    }


def custom_threads(data: dict) -> dict:
    acode, name = location(data)
    scoped = period_from(data).apply(load_payload(acode, name))
    return {"threads": custom_selection.thread_options(scoped)}


@dataclass(frozen=True)
class AnalysisSession:
    acode: int
    name: str
    payload: dict
    scoped: dict
    period: Period
    selected: tuple[select.Selected, ...]
    girl: str
    stage: str
    templates: dict[str, str]


class Workspace:
    """Bounded, in-memory snapshots; reports and prompts are never persisted."""

    def __init__(self) -> None:
        self._sessions: OrderedDict[str, AnalysisSession] = OrderedDict()
        self._lock = threading.Lock()

    def _remember(self, session: AnalysisSession, text: str, notes: list[str]) -> dict:
        key = secrets.token_urlsafe(24)
        with self._lock:
            self._sessions[key] = session
            while len(self._sessions) > 8:
                self._sessions.popitem(last=False)
        estimate = tokens.estimate(text)
        span = select.date_span(session.selected)
        return {
            "session": key,
            "prompt": text,
            "count": len(session.selected),
            "characters": len(text),
            "tokens": {"lower": estimate.lower, "upper": estimate.upper},
            "period": session.period.label,
            "span": span,
            "notes": notes,
            "stage": session.stage,
        }

    def _session(self, data: dict) -> AnalysisSession:
        key = text_field(data, "session", 100)
        with self._lock:
            session = self._sessions.get(key)
        if session is None:
            raise ValueError("這份提示詞的暫存已過期，請重新產生提示詞")
        if location(data) != (session.acode, session.name):
            raise ValueError("提示詞與目前店家不同，請重新產生")
        return session

    def generate(self, data: dict) -> dict:
        templates = validate_overrides(data.get("templates", {}))
        acode, name = location(data)
        mode = text_field(data, "mode", 20)
        if mode not in {"name", "features", "roster", "other"}:
            raise ValueError("請選擇分析方式")
        if mode == "other":
            snapshot = self._session(
                {**data, "session": data.get("selection_session", "")}
            )
            if snapshot.stage != "selection" or snapshot.period != period_from(data):
                raise ValueError("留言範圍已變更，請重新預覽留言")
            payload, scoped, period = snapshot.payload, snapshot.scoped, snapshot.period
        else:
            payload = load_payload(acode, name)
            period = period_from(data)
            scoped = period.apply(payload)
        girl = text_field(data, "target", 3000)
        if mode in {"name", "features"} and not girl:
            raise ValueError("請輸入名字或特徵描述")
        notes = []
        stage = "report"
        if mode == "other":
            ids = custom_selection.id_list(data.get("comment_ids", []), "勾選的留言 ID")
            allowed = {c.comment_id for c in snapshot.selected}
            if not ids <= allowed:
                raise ValueError("勾選的留言不在預覽範圍，請重新預覽")
            choice = data.get("selection_mode", "exclude")
            if choice not in ("include", "exclude"):
                raise ValueError("留言勾選方式無效")
            selected = tuple(
                c
                for c in snapshot.selected
                if (c.comment_id in ids) == (choice == "include")
            )
            if not selected:
                raise ValueError("請至少選擇一則留言")
            instructions = text_field(data, "instructions", 50_000)
            if not instructions:
                raise ValueError("請輸入這次要交給模型的分析要求")
            text = prompt.build_custom(name, selected, instructions, period)
            notes = [
                f"從預覽的 {len(snapshot.selected)} 則中選取 {len(selected)} 則；未自動展開回覆鏈。"
            ]
        elif mode == "name":
            depth = data.get("depth", 1)
            if isinstance(depth, bool) or depth not in (0, 1, 2, "all"):
                raise ValueError("回覆脈絡層數無效")
            selected = select.select(scoped, girl, None if depth == "all" else depth)
            if not selected:
                raise ValueError(diagnose.explain_empty(scoped, girl))
            if type(data.get("two_step", False)) is not bool:
                raise ValueError("兩段式選項無效")
            stage = "filter" if data.get("two_step") else "report"
            builder = prompt.build_filter if stage == "filter" else prompt.build
            text = builder(name, girl, selected, period=period, overrides=templates)
            notes = [
                attribution.describe(attribution.confidences(scoped, girl, selected)),
                describe_honorifics(honorifics.honorific_rate(scoped, girl)),
            ]
        else:
            selected = select.all_comments(scoped)
            if not selected:
                raise ValueError("所選期間沒有可用留言，請調整日期或先更新資料")
            by_id = store.comments_by_id(scoped)
            indexes = {by_id[c.comment_id].get("thread_index") for c in selected}
            threads = [
                t for t in scoped.get("threads", []) if t.get("index") in indexes
            ]
            text = prompt.build_discovery(
                name,
                selected,
                features=girl if mode == "features" else None,
                period=period,
                threads=threads,
                overrides=templates,
            )
            notes = ["已納入期間內全部留言，未依名字或特徵字面預篩。"]
        session = AnalysisSession(
            acode,
            name,
            payload,
            scoped,
            period,
            tuple(selected),
            girl,
            stage,
            templates,
        )
        return self._remember(session, text, [n.strip() for n in notes if n])

    def custom_preview(self, data: dict) -> dict:
        acode, name = location(data)
        payload = load_payload(acode, name)
        period = period_from(data)
        scoped = period.apply(payload)
        selected, matched = custom_selection.choose(scoped, data)
        snapshot = AnalysisSession(
            acode, name, payload, scoped, period, selected, "", "selection", {}
        )
        remembered = self._remember(snapshot, "", [])
        return {
            "session": remembered["session"],
            "count": len(selected),
            "matched": matched,
            **self._selection_page(snapshot, 0),
        }

    def _selection_page(self, snapshot: AnalysisSession, offset: int) -> dict:
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("留言頁碼無效")
        by_id = store.comments_by_id(snapshot.scoped)
        threads = {
            t["index"]: t.get("thread_title", "")
            for t in snapshot.scoped.get("threads", [])
        }
        return {
            "count": len(snapshot.selected),
            "offset": offset,
            "comments": [
                comment_view(by_id[c.comment_id], threads)
                for c in snapshot.selected[offset : offset + 40]
            ],
        }

    def custom_page(self, data: dict) -> dict:
        snapshot = self._session(data)
        if snapshot.stage != "selection":
            raise ValueError("這不是留言預覽，請重新選擇留言")
        return self._selection_page(snapshot, data.get("offset", 0))

    def refine(self, data: dict) -> dict:
        session = self._session(data)
        if session.stage != "filter":
            raise ValueError("請先產生兩段式的第一輪提示詞")
        answer = text_field(data, "answer", 4_000_000)
        sent = {item.comment_id for item in session.selected}
        found = citations.parse_ids(answer)
        known = [key for key in found if key in sent]
        dropped = [key for key in found if key not in sent]
        if not known:
            raise ValueError("沒有找到本輪送出的留言 ID，請貼上模型的篩選結果")
        selected = select.from_ids(session.scoped, known, select.DEFAULT_DEPTH)
        next_session = AnalysisSession(
            session.acode,
            session.name,
            session.payload,
            session.scoped,
            session.period,
            tuple(selected),
            session.girl,
            "report",
            session.templates,
        )
        text = prompt.build(
            session.name,
            session.girl,
            selected,
            period=session.period,
            overrides=session.templates,
        )
        notes = [f"模型確認 {len(known)} 則，展開一層回覆脈絡後共 {len(selected)} 則。"]
        if dropped:
            notes.append("已捨棄未送出的引用：" + "、".join(dropped))
        return self._remember(next_session, text, notes)

    def verify(self, data: dict) -> dict:
        report = text_field(data, "report", 4_000_000)
        if not report:
            raise ValueError("請先貼上模型報告")
        acode, name = location(data)
        if data.get("session"):
            session = self._session(data)
            if session.stage == "selection":
                raise ValueError("請先產生提示詞，再核對報告引用")
            payload, scoped, period = session.payload, session.scoped, session.period
            allowed = {c.comment_id for c in session.selected}
            scope_label = "當次提示詞實際送出的留言"
        else:
            payload = load_payload(acode, name)
            period = period_from(data)
            scoped = period.apply(payload)
            allowed = set(store.comments_by_id(scoped))
            scope_label = period.label
        full = store.comments_by_id(payload)
        in_period = store.comments_by_id(scoped)
        threads = {
            t["index"]: t.get("thread_title", "") for t in payload.get("threads", [])
        }
        result = []
        for key in citations.parse_ids(report):
            status = (
                "missing"
                if key not in full
                else "outside_period"
                if key not in in_period
                else "not_sent"
                if key not in allowed
                else "known"
            )
            comment = full.get(key, {"comment_id": key})
            result.append({**comment_view(comment, threads), "status": status})
        return {
            "scope": scope_label,
            "citations": result,
            "total": len(result),
            "known": sum(c["status"] == "known" for c in result),
            "missing": sum(c["status"] == "missing" for c in result),
        }
