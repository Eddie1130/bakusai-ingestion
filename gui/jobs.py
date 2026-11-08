"""One background scrape with explicit browser-side decisions and cancellation."""

from __future__ import annotations

from copy import deepcopy
import logging
import secrets
import threading
import time

from scraping import client, failures, scraper
from scraping.cli import SEARCH_CONFIRM_PAGES, render_plan, render_plan_detail
from scraping.lock import ScrapeInProgress, scrape_lock
from scraping.progress import PageBudget, describe_duration

from .services import load_payload, location, text_field

logger = logging.getLogger(__name__)
DECISION_TIMEOUT_SECONDS = 30 * 60


class JobCancelled(Exception):
    """A browser cancelled at a safe checkpoint."""


class ScrapeJob:
    def __init__(self, data: dict) -> None:
        self.acode, self.name = location(data)
        self.existing = data.get("existing", False)
        self.full = data.get("full", False)
        if type(self.existing) is not bool or type(self.full) is not bool:
            raise ValueError("抓取選項無效")
        if self.existing:
            load_payload(self.acode, self.name)
        self.target = scraper.parse_store_target(self.name)
        if self.target is None:
            raise ValueError("請輸入有效店名")
        self.id = secrets.token_urlsafe(18)
        self._condition = threading.Condition()
        self._cancelled = False
        self._answer = None
        self._budget = PageBudget()
        self._started = time.monotonic()
        self._state = {
            "id": self.id,
            "status": "running",
            "phase": "搜尋店家",
            "message": "正在搜尋討論串；搜尋會連線至 Bakusai。",
            "decision": None,
            "done": 0,
            "total": 0,
            "store": self.name,
            "acode": self.acode,
        }
        self.thread_handle = threading.Thread(
            target=self._run, daemon=True, name="bakusai-scrape"
        )

    def snapshot(self) -> dict:
        with self._condition:
            return deepcopy(self._state)

    def _update(self, **values) -> None:
        with self._condition:
            self._state.update(values)

    def check_cancelled(self) -> None:
        with self._condition:
            if self._cancelled:
                raise JobCancelled()

    def cancel(self) -> None:
        with self._condition:
            if self._state["status"] in {"done", "error", "cancelled"}:
                return
            self._cancelled = True
            self._state["message"] = (
                "正在停止，請稍候目前的網路請求結束。已完成的討論串會保留。"
            )
            self._condition.notify_all()

    def answer(self, data: dict) -> None:
        decision_id = text_field(data, "decision", 100)
        with self._condition:
            decision = self._state["decision"]
            if self._cancelled or decision is None or decision["id"] != decision_id:
                raise ValueError("這個確認步驟已結束，請重新整理抓取狀態")
            value = data.get("value")
            if decision["kind"] == "candidate":
                if (
                    isinstance(value, bool)
                    or not isinstance(value, int)
                    or not 0 <= value < len(decision["options"])
                ):
                    raise ValueError("請選擇有效候選店家")
            elif type(value) is not bool:
                raise ValueError("請確認是否繼續")
            if self._answer is not None:
                raise ValueError("已收到選擇，請稍候")
            self._answer = value
            self._condition.notify_all()

    def _ask(self, kind: str, title: str, **details):
        with self._condition:
            self._answer = None
            self._state.update(
                status="waiting",
                decision={
                    "id": secrets.token_urlsafe(16),
                    "kind": kind,
                    "title": title,
                    **details,
                },
            )
            ready = self._condition.wait_for(
                lambda: self._answer is not None or self._cancelled,
                timeout=DECISION_TIMEOUT_SECONDS,
            )
            if not ready:
                self._cancelled = True
            self.check_cancelled()
            answer = self._answer
            self._state.update(status="running", decision=None)
            return answer

    def _on_size(self, size: scraper.SearchSize) -> bool:
        self.check_cancelled()
        self._update(
            message=f"找到 {size.total:,} 件，約 {size.pages} 頁搜尋結果。正在辨識店家。"
        )
        if size.pages > SEARCH_CONFIRM_PAGES:
            return self._ask(
                "search",
                "搜尋範圍較大，要繼續嗎？",
                summary=f"約 {size.pages} 頁。建議加上小區縮小範圍。",
            )
        return True

    def _choose(self, grouping):
        self.check_cancelled()
        options = [
            {
                "name": c.name,
                "count": len(c.threads),
                "boards": list(c.board_labels),
                "threads": [t.title for t in c.threads],
            }
            for c in grouping.candidates
        ]
        choice = self._ask(
            "candidate", "找到同名店家，請選擇你要的店家", options=options
        )
        return grouping.candidates[choice]

    def _on_plan(self, plan: scraper.ScrapePlan) -> bool:
        self.check_cancelled()
        return self._ask(
            "plan",
            "確認這次的抓取計畫",
            summary=render_plan(plan).strip(),
            detail=render_plan_detail(plan).strip(),
        )

    def start(self, pages: int) -> None:
        self.check_cancelled()
        self._budget.start(pages)
        self._started = time.monotonic()
        self._update(phase="抓取留言", total=pages, done=0, message="開始抓取留言")

    def thread(self, label: str, budget: int) -> None:
        self.check_cancelled()
        self._budget.thread(budget)
        self._update(phase="抓取留言", message=label, total=self._budget.total)

    def page(self, page: int, of: int, note: str = "") -> None:
        self._budget.page(of)
        done, total = self._budget.done, self._budget.total
        elapsed = time.monotonic() - self._started
        remaining = (total - done) * elapsed / max(done, 1)
        self._update(
            done=done,
            total=total,
            message=f"第 {page}/{of} 頁 · {note} · 預估剩餘 {describe_duration(remaining)}",
        )
        # Cancellation happens before the next request, allowing a finished thread
        # to be saved by the existing scraper before stopping.

    def _run(self) -> None:
        try:
            with scrape_lock():
                fetch = client.create_fetch()

                def checked_fetch(url: str) -> str:
                    self.check_cancelled()
                    return fetch(url)

                result = scraper.scrape_store(
                    self.acode,
                    self.target,
                    fetch=checked_fetch,
                    full=self.full,
                    on_progress=self,
                    on_size=self._on_size,
                    on_plan=self._on_plan,
                    select_candidate=self._choose,
                    existing_store=self.name if self.existing else None,
                )
            if result is None:
                self._update(
                    status="error",
                    decision=None,
                    message="找不到符合這家店身分的討論串。請確認店名與地區，或稍後再試。",
                )
            else:
                self._update(
                    status="done",
                    decision=None,
                    store=result.parent.name,
                    message="資料已更新，可以開始分析。",
                )
        except (JobCancelled, scraper.SearchCancelled):
            self._update(
                status="cancelled",
                decision=None,
                message="已停止。已完成的討論串已保留，下次更新會接續處理。",
            )
        except ScrapeInProgress:
            self._update(
                status="error",
                decision=None,
                message="另一個行程正在抓取，請等它完成後再試。",
            )
        except client.ScrapeError as exc:
            self._update(status="error", decision=None, message=failures.describe(exc))
        except Exception:
            logger.exception("GUI scrape failed")
            self._update(
                status="error",
                decision=None,
                message="抓取未完成。已儲存的資料仍保留；請查看終端機訊息後重試。",
            )


class Jobs:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._job: ScrapeJob | None = None

    def start(self, data: dict) -> dict:
        with self._lock:
            if self._job and self._job.thread_handle.is_alive():
                raise ValueError("已有抓取工作進行中，請先完成或停止它")
            job = ScrapeJob(data)
            self._job = job
            job.thread_handle.start()
            return job.snapshot()

    def current(self) -> dict:
        with self._lock:
            return self._job.snapshot() if self._job else {"status": "idle"}

    def respond(self, data: dict) -> dict:
        with self._lock:
            if not self._job or text_field(data, "id", 100) != self._job.id:
                raise ValueError("找不到這個抓取工作")
            if data.get("action") == "cancel":
                self._job.cancel()
            elif data.get("action") == "answer":
                self._job.answer(data)
            else:
                raise ValueError("抓取操作無效")
            return self._job.snapshot()

    def close(self) -> None:
        with self._lock:
            if self._job:
                self._job.cancel()
