"""Render citation verification / 呈現引用驗證結果.

Every caller receives a finished report, pasted back by hand. Citation checking is
a pure function over that text and one store payload, so the wording lives here
rather than drifting between callers.
"""
from __future__ import annotations

from . import citations


VERIFY_SAMPLE = 10
RATE_LIMITATION = "捏造率只能界定捏造；真實 ID 是否錯誤歸屬到不支持的說法，仍須人工核對。"


def describe_unknown(found: citations.Citations) -> str:
    """Name every unknown id because each one is a finding, not a sample."""
    if not found.unknown:
        return "  引用的 ID 全部存在於這家店"
    return f"  ⚠️ 不存在的 ID（{len(found.unknown)} 個）：" + "、".join(found.unknown)


def render(payload: dict | None, report: str, sample_count: int = VERIFY_SAMPLE) -> str:
    """Check every cited id and show an even sample for semantic spot-checking."""
    found = citations.cited(payload, report)
    if found.total == 0:
        return (
            f"  讀到 {len(report)} 個字元\n"
            "  這份報告沒有引用任何留言 ID，無法驗證（這不是滿分，是無從檢查）"
        )

    sample = citations.spread(found.known, sample_count)
    lines = [
        f"  引用 {found.total} 個 ID，其中 {len(found.unknown)} 個不存在"
        f"　捏造率 {found.fabrication_rate:.1%}",
        f"  {RATE_LIMITATION}",
        describe_unknown(found),
        f"  以下等距抽出 {len(sample)} / {len(found.known)} 則被引用的留言原文"
        "（涵蓋報告全文，不是只有開頭），請自行確認內容是否真的支持報告的說法：",
    ]
    for comment_id, when, body in citations.bodies_of(payload, sample):
        lines.append(f"  {comment_id} {when or '日期不明'}\n    {body}")
    return "\n".join(lines)
