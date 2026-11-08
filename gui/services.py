"""GUI use cases, sharing selection and citation rules with the CLI."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, replace
import json
import re
import secrets
import threading

import paths
from analysis import (
    attribution,
    batching,
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
from .templates import TEMPLATE_NAMES, validate_overrides
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


def selected_threads(
    payload: dict, selected: tuple[select.Selected, ...]
) -> list[dict]:
    by_id = store.comments_by_id(payload)
    indexes = {by_id[item.comment_id].get("thread_index") for item in selected}
    return [
        thread
        for thread in payload.get("threads", [])
        if thread.get("index") in indexes
    ]


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
    filter_progress: FilterProgress | None = None
    analysis_mode: str = "name"


@dataclass(frozen=True)
class BatchAnswer:
    ids: tuple[str, ...]
    report: str = ""


@dataclass
class FilterProgress:
    plan: batching.BatchPlan
    answers: list[BatchAnswer | None]
    revision: int = 0


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
            if session is not None:
                self._sessions.move_to_end(key)
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
        elif mode == "features":
            method = text_field(data, "feature_analysis", 20) or "direct"
            if method not in {"direct", "filter"}:
                raise ValueError("請選擇直接分析，或先篩留言再分析")
            base = AnalysisSession(
                acode,
                name,
                payload,
                scoped,
                period,
                (),
                girl,
                "full_filter",
                templates,
                analysis_mode="features",
            )
            return self._start_batch_analysis(base, data, "features_" + method)
        elif mode == "name":
            if type(data.get("two_step", False)) is not bool:
                raise ValueError("兩段式選項無效")
            strategy = text_field(data, "filter_strategy", 20) or "quick"
            if strategy not in {"quick", "full"}:
                raise ValueError("篩選方式無效，請選擇快速或完整篩選")
            if data.get("two_step") and strategy == "full":
                base = AnalysisSession(
                    acode,
                    name,
                    payload,
                    scoped,
                    period,
                    (),
                    girl,
                    "full_filter",
                    templates,
                )
                return self._start_batch_analysis(base, data, "name")
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

    def _start_batch_analysis(
        self, base: AnalysisSession, data: dict, purpose: str
    ) -> dict:
        frozen = {key: prompt.load_template(key) for key in TEMPLATE_NAMES}
        frozen.update(base.templates)
        if purpose == "features_direct":
            spec = prompt.discovery_specification(
                base.name, features=True, overrides=frozen
            )
        else:
            template = (
                "features_filter_spec.md"
                if purpose == "features_filter"
                else "full_filter_spec.md"
            )
            spec = prompt.specification(template, base.name, base.girl, frozen)
        plan = batching.BatchPlan(
            base.name,
            base.girl,
            base.scoped,
            base.period,
            spec,
            data.get("batch_comments", batching.DEFAULT_BATCH_COMMENTS),
            purpose,
        )
        if purpose == "features_direct" and len(plan.batches) == 1:
            text = prompt.build_discovery(
                base.name,
                plan.selected,
                features=base.girl,
                period=base.period,
                threads=selected_threads(base.scoped, plan.selected),
                overrides=frozen,
            )
            return self._remember(
                replace(base, selected=plan.selected, stage="report", templates=frozen),
                text,
                ["已納入期間內全部留言，直接產生候選人分析提示詞，未依字面特徵預篩。"],
            )
        progress = FilterProgress(plan, [None] * len(plan.batches))
        session = replace(
            base, selected=plan.selected, templates=frozen, filter_progress=progress
        )
        key = self._remember(session, "", [])["session"]
        return self._filter_response(key, session, 0)

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
        if session.stage == "full_filter":
            return self._refine_batch(data, session)
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
            analysis_mode=session.analysis_mode,
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

    def _batch_index(self, value: object, progress: FilterProgress) -> int:
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 <= value < len(progress.plan.batches)
        ):
            raise ValueError("批次編號無效，請重新選擇批次")
        return value

    def _filter_response(
        self, key: str, session: AnalysisSession, index: int, dropped=()
    ) -> dict:
        progress = session.filter_progress
        if progress is None:
            raise ValueError("找不到完整篩選進度，請重新產生")
        with self._lock:
            answers = list(progress.answers)
            revision = progress.revision
        plan = progress.plan
        batch = plan.batches[index]
        text = plan.text(batch, index, len(plan.batches))
        estimate = tokens.estimate(text)
        confirmed = set().union(
            *(set(answer.ids if answer else ()) for answer in answers)
        )
        direct = plan.purpose == "features_direct"
        completed = sum(answer is not None for answer in answers)
        notes = ["所有期間內留言都會作為主要留言出現一次，不做名字或字面特徵預篩。"]
        if direct:
            uncited = [
                str(i + 1)
                for i, answer in enumerate(answers)
                if answer is not None and not answer.ids
            ]
            if uncited:
                notes.append(
                    "第 "
                    + "、".join(uncited)
                    + " 批分析沒有引用 ID；若有候選人說法請補上引用，整合時會標示無法核對。"
                )
        if batch.omitted:
            notes.append(
                f"本批有 {batch.omitted} 則鄰近或直接回覆脈絡因留言數量上限未附入；未截斷任何主要留言。"
            )
        if dropped:
            notes.append("已捨棄未提供於本批的 ID：" + "、".join(dropped))
        return {
            "session": key,
            "stage": "full_filter",
            "prompt": text,
            "analysis_mode": session.analysis_mode,
            "output_kind": "reports" if direct else "ids",
            "target": session.girl,
            "store": session.name,
            "acode": session.acode,
            "start": str(session.period.start or ""),
            "end": str(session.period.end or ""),
            "count": len(batch.primary) + len(batch.context),
            "characters": len(text),
            "tokens": {"lower": estimate.lower, "upper": estimate.upper},
            "period": session.period.label,
            "span": select.date_span((*batch.primary, *batch.context)),
            "notes": notes,
            "filter": {
                "current": index,
                "total": len(plan.batches),
                "completed": completed,
                "total_comments": len(plan.selected),
                "confirmed_count": len(confirmed),
                "reviewed_count": sum(
                    len(batch.primary)
                    for batch, answer in zip(plan.batches, answers)
                    if answer is not None
                ),
                "total_tokens": {
                    "lower": sum(e.lower for e in plan.estimates),
                    "upper": sum(e.upper for e in plan.estimates),
                },
                "all_done": completed == len(plan.batches),
                "revision": revision,
                "comment_limit": plan.comment_limit,
                "primary_ids": [c.comment_id for c in batch.primary],
                "context_ids": [c.comment_id for c in batch.context],
                "context_omitted": batch.omitted,
                "saved_answer": (
                    answers[index].report
                    if direct
                    else json.dumps(answers[index].ids, ensure_ascii=False)
                )
                if answers[index] is not None
                else "",
                "batches": [
                    {
                        "index": i,
                        "primary_count": len(b.primary),
                        "tokens": {
                            "lower": plan.estimates[i].lower,
                            "upper": plan.estimates[i].upper,
                        },
                        "context_count": len(b.context),
                        "complete": answers[i] is not None,
                        "confirmed_count": len(answers[i].ids) if answers[i] else 0,
                        "missing_citations": direct
                        and answers[i] is not None
                        and not answers[i].ids,
                    }
                    for i, b in enumerate(plan.batches)
                ],
            },
        }

    def filter_batch(self, data: dict) -> dict:
        session = self._session(data)
        if session.stage != "full_filter" or session.filter_progress is None:
            raise ValueError("這不是完整篩選，請重新產生提示詞")
        index = self._batch_index(data.get("batch_index", 0), session.filter_progress)
        return self._filter_response(text_field(data, "session", 100), session, index)

    def _refine_batch(self, data: dict, session: AnalysisSession) -> dict:
        progress = session.filter_progress
        if progress is None:
            raise ValueError("找不到完整篩選進度")
        index = self._batch_index(data.get("batch_index"), progress)
        raw = text_field(data, "answer", 4_000_000)
        batch = progress.plan.batches[index]
        allowed = {c.comment_id for c in (*batch.primary, *batch.context)}
        direct = progress.plan.purpose == "features_direct"
        if direct:
            if not raw:
                raise ValueError("請貼上本批的暫時分析；沒有相關人選時也請明確說明")
            known = citations.parse_ids(raw)
            unknown = [key for key in known if key not in allowed]
            if unknown:
                raise ValueError(
                    "本批分析引用了未提供的 ID："
                    + "、".join(unknown)
                    + "。請修正後再確認。"
                )
            dropped = ()
            answer = BatchAnswer(known, raw)
        else:
            fenced = re.fullmatch(
                r"```(?:json)?\s*(.*?)\s*```", raw, flags=re.IGNORECASE | re.DOTALL
            )
            try:
                parsed = json.loads(fenced.group(1) if fenced else raw)
            except (ValueError, TypeError) as exc:
                raise ValueError(
                    '請貼上 JSON ID 陣列，例如 ["T1-101"]；沒有相關留言時填 []'
                ) from exc
            if not isinstance(parsed, list) or any(
                not isinstance(key, str) or citations.COMMENT_ID.fullmatch(key) is None
                for key in parsed
            ):
                raise ValueError(
                    "回覆必須是留言 ID 字串的 JSON 陣列；沒有相關留言時填 []"
                )
            ids = tuple(dict.fromkeys(parsed))
            known = tuple(key for key in ids if key in allowed)
            dropped = tuple(key for key in ids if key not in allowed)
            if ids and not known:
                raise ValueError(
                    "這些 ID 都不在本批提供的留言中，請確認貼回了正確批次；無相關留言時填 []"
                )
            answer = BatchAnswer(known)
        revision = data.get("filter_revision")
        with self._lock:
            if (
                isinstance(revision, bool)
                or not isinstance(revision, int)
                or revision != progress.revision
            ):
                raise ValueError("批次進度已更新，請重新載入本批後再貼回結果")
            progress.answers[index] = answer
            progress.revision += 1
            next_index = next(
                (i for i, answer in enumerate(progress.answers) if answer is None),
                index,
            )
        return self._filter_response(
            text_field(data, "session", 100), session, next_index, dropped
        )

    def full_filter_report(self, data: dict) -> dict:
        session = self._session(data)
        progress = session.filter_progress
        if session.stage != "full_filter" or progress is None:
            raise ValueError("這不是完整篩選，請重新產生提示詞")
        with self._lock:
            if any(answer is None for answer in progress.answers):
                raise ValueError("請先完成所有批次，才能產生第二輪報告提示詞")
            revision = data.get("filter_revision", progress.revision)
            if type(revision) is not int or revision != progress.revision:
                raise ValueError("批次進度已更新，請重新載入後再產生報告")
            ids = tuple(
                dict.fromkeys(
                    key
                    for answer in progress.answers
                    for key in (answer.ids if answer else ())
                )
            )
            reports = tuple(
                answer.report for answer in progress.answers if answer is not None
            )
        direct = progress.plan.purpose == "features_direct"
        if not ids and not direct:
            raise ValueError(
                "完整篩選已完成，但沒有確認相關留言；可回到各批修正結果，或調整日期與分析條件重新分析"
            )
        selected = select.from_ids(session.scoped, ids, select.DEFAULT_DEPTH)
        next_session = AnalysisSession(
            session.acode,
            session.name,
            session.payload,
            session.scoped,
            session.period,
            selected,
            session.girl,
            "report",
            session.templates,
            analysis_mode=session.analysis_mode,
        )
        if direct:
            text = prompt.build_feature_merge(
                session.name,
                session.girl,
                selected,
                reports,
                session.period,
                session.templates,
            )
        elif session.analysis_mode == "features":
            text = prompt.build_discovery(
                session.name,
                selected,
                features=session.girl,
                period=session.period,
                threads=selected_threads(session.scoped, selected),
                overrides=session.templates,
            )
        else:
            text = prompt.build(
                session.name,
                session.girl,
                selected,
                period=session.period,
                overrides=session.templates,
            )
        result = self._remember(
            next_session,
            text,
            [
                f"完整篩選共 {len(progress.plan.batches)} 批、{len(progress.plan.selected)} 則主要留言；"
                f"去重後引用證據 {len(ids)} 則，展開一層回覆脈絡後共 {len(selected)} 則。"
            ],
        )
        if tokens.estimate(text).upper > max(
            estimate.upper for estimate in progress.plan.estimates
        ):
            result["warning"] = (
                "第二輪報告提示詞比第一輪最長的一批還長。未刪除任何已確認留言，請確認模型能容納全文。"
            )
        if direct:
            result["notes"].append(
                "整合保留全部批次分析，只附上它們實際引用的原文及一層回覆脈絡；未引用的留言不再次附入。整合階段可能超過第一輪每批留言上限。"
            )
            if not ids:
                result["warning"] = (
                    "各批結果都沒有引用 ID，沒有可附入的原文證據；整合時只能標示無法核對，不代表已確認任何人選。"
                )
        return result

    def verify(self, data: dict) -> dict:
        report = text_field(data, "report", 4_000_000)
        if not report:
            raise ValueError("請先貼上模型報告")
        acode, name = location(data)
        if data.get("session"):
            session = self._session(data)
            if session.stage in {"selection", "full_filter"}:
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
