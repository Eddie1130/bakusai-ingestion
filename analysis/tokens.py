"""Offline prompt-size estimates when the destination model is unspecified."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TokenEstimate:
    lower: int
    upper: int

    def describe(self) -> str:
        return (
            f"  預估輸入 tokens：約 {self.lower:,}～{self.upper:,}"
            "（UTF-8 位元組粗估，非模型 tokenizer 計數）\n"
            "  僅估算本次複製全文；不含對話歷史、系統指令或模型輸出。"
            "實際用量依模型而異，也可能超出此參考範圍。"
        )


def estimate(text: str) -> TokenEstimate:
    """Use 2–4 UTF-8 bytes per token as a deliberately rough sizing range.

    Bytes account for Japanese and Chinese taking more space than ASCII, unlike
    dividing every language's character count by four. These are planning
    heuristics, not calibrated bounds for any model or a context-fit guarantee.
    """
    size = len(text.encode("utf-8"))
    return TokenEstimate(lower=(size + 3) // 4, upper=(size + 1) // 2)
