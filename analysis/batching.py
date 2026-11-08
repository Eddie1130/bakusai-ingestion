"""Count-bounded full-scope batches; every scoped comment is primary once."""

from __future__ import annotations

from dataclasses import dataclass, replace

from . import prompt, select, tokens
from .period import Period

DEFAULT_BATCH_COMMENTS = 200
MIN_BATCH_COMMENTS = 1
MAX_BATCH_COMMENTS = 2_000


@dataclass(frozen=True)
class FilterBatch:
    primary: tuple[select.Selected, ...]
    context: tuple[select.Selected, ...]
    omitted: int


class BatchPlan:
    def __init__(
        self,
        store: str,
        girl: str,
        scoped: dict,
        period: Period,
        spec: str,
        comment_limit: int,
        purpose: str = "name",
    ) -> None:
        if (
            isinstance(comment_limit, bool)
            or not isinstance(comment_limit, int)
            or not MIN_BATCH_COMMENTS <= comment_limit <= MAX_BATCH_COMMENTS
        ):
            raise ValueError("每批留言數量上限須為 1～2,000 的整數")
        self.store, self.girl, self.scoped, self.period = store, girl, scoped, period
        self.spec, self.comment_limit = spec, comment_limit
        self.purpose = purpose
        self.selected = select.all_comments(scoped)
        if not self.selected:
            raise ValueError("所選期間沒有可用留言，請調整日期或先更新資料")
        self._positions = {c.comment_id: i for i, c in enumerate(self.selected)}
        self._by_id = {c.comment_id: c for c in self.selected}
        # Reuse the selector's graph once, rather than rebuilding the whole
        # store for every batch (especially important with a one-comment cap).
        self._neighbors = select._reply_graph(scoped)
        self._thread_ends = {
            c.comment_id.split("-", 1)[0]: i + 1 for i, c in enumerate(self.selected)
        }
        self.batches = self._partition()
        self.estimates = tuple(
            tokens.estimate(self.text(batch, i, len(self.batches)))
            for i, batch in enumerate(self.batches)
        )

    def text(self, batch: FilterBatch, index: int, total: int) -> str:
        return prompt.build_filter_batch(
            self.store,
            self.girl,
            batch.primary,
            batch.context,
            self.spec,
            self.period,
            self.scoped.get("threads", []),
            index=index,
            total=total,
            omitted=batch.omitted,
            purpose=self.purpose,
        )

    def _partition(self) -> tuple[FilterBatch, ...]:
        if self.purpose != "name" and len(self.selected) <= self.comment_limit:
            return (FilterBatch(self.selected, (), 0),)
        # Reserve about one fifth for overlap. If a complete thread fits within
        # the total limit, keep it together instead of splitting just to reserve
        # context that might not be needed.
        reserve = max(1, self.comment_limit // 5) if self.comment_limit > 1 else 0
        primary_limit = self.comment_limit - reserve
        batches = []
        start = 0
        while start < len(self.selected):
            first_thread = self.selected[start].comment_id.split("-", 1)[0]
            thread_end = self._thread_ends[first_thread]
            end = min(start + primary_limit, thread_end)
            if thread_end - start <= self.comment_limit:
                end = thread_end
            primary = self.selected[start:end]
            core = {c.comment_id for c in primary}
            direct = set().union(*(self._neighbors.get(key, ()) for key in core)) - core
            adjacent = set()
            for position in (start - 1, end):
                if 0 <= position < len(self.selected):
                    neighbor = self.selected[position]
                    if neighbor.comment_id.split("-", 1)[0] in {
                        c.comment_id.split("-", 1)[0] for c in primary
                    }:
                        adjacent.add(neighbor.comment_id)
            candidates = sorted(direct, key=self._positions.__getitem__) + sorted(
                adjacent - direct - core, key=self._positions.__getitem__
            )
            capacity = self.comment_limit - len(primary)
            context = [
                replace(self._by_id[key], route=select.ROUTE_REPLY, match=None)
                if key in direct
                else self._by_id[key]
                for key in candidates[:capacity]
            ]
            context.sort(key=lambda c: self._positions[c.comment_id])
            batches.append(
                FilterBatch(primary, tuple(context), len(candidates) - len(context))
            )
            start = end
        return tuple(batches)
