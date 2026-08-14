"""Assembling the text the operator pastes / 組出使用者要貼上的提示詞.

The prompt is the product of this layer, so it is assembled from Markdown
templates that live beside this module rather than from Python string constants:
revising the wording is then a text edit with a readable diff, and a test can pin
the assembled result so a change is deliberate rather than incidental.
"""
from __future__ import annotations

from collections.abc import Sequence
from importlib import resources
from string import Template

from . import render
from .select import Selected, date_span

TEMPLATE_PACKAGE = "analysis"
TEMPLATE_DIR = "templates"

REPORT_SPEC = "report_spec.md"
DIMENSIONS_SPEC = "dimensions.md"

#: The first pass of the two-step mode. It sits beside the report specification on
#: purpose: the difference between what the two ask for should be visible at a
#: glance, because that difference is the whole idea of the mode.
FILTER_SPEC = "filter_spec.md"

#: `>>528` inside a body is thread-local. The stored ids carry the thread, so the
#: model has to be told how one maps onto the other or it cannot follow a chain.
REPLY_CONVENTION = (
    "內文中的 `>>528` 指同一討論串內編號 528 的留言，"
    "也就是本文中前綴相同、編號 528 的那個留言 ID。"
)


def load_template(name: str) -> str:
    """Read one Markdown template out of the package."""
    return (
        resources.files(TEMPLATE_PACKAGE)
        .joinpath(TEMPLATE_DIR, name)
        .read_text(encoding="utf-8")
    )


def specification(name: str, store: str, girl: str) -> str:
    """One template, addressed to this store and this girl."""
    return Template(load_template(name)).safe_substitute(
        store=store, girl=girl, dimensions=load_template(DIMENSIONS_SPEC).strip()
    )


def report_specification(store: str, girl: str) -> str:
    """What to write about her."""
    return specification(REPORT_SPEC, store, girl)


def filter_specification(store: str, girl: str) -> str:
    """Which of these comments are about her — ids only, no assessment."""
    return specification(FILTER_SPEC, store, girl)


def _preamble(store: str, girl: str, selected: Sequence[Selected]) -> str:
    span = date_span(selected)
    lines = [
        "# 爆サイ 留言分析請求",
        "",
        f"- 店家：{store}",
        f"- 對象：{girl}",
        f"- 留言則數：{len(selected)}",
        f"- 日期範圍：{span[0]} ～ {span[1]}" if span else "- 日期範圍：不明",
        "",
        "留言以每則一行的形式列在最後，格式為 `留言ID 日期 來源標記 內文`。",
        "內文保留原本的換行，換行之後的文字仍屬於上一則留言。",
        REPLY_CONVENTION,
    ]
    marks = render.legend(selected)
    if marks:
        lines += ["", "來源標記：", marks]
    return "\n".join(lines)


def _assemble(
    store: str, girl: str, selected: Sequence[Selected], spec: str, with_dates: bool
) -> str:
    return "\n\n".join(
        [
            _preamble(store, girl, selected),
            spec.strip(),
            "## 留言\n\n" + render.render(selected, with_dates),
        ]
    ) + "\n"


def build(
    store: str, girl: str, selected: Sequence[Selected], with_dates: bool = True
) -> str:
    """The whole prompt: what the sample is, what to write, then the evidence."""
    return _assemble(
        store, girl, selected, report_specification(store, girl), with_dates
    )


def build_filter(
    store: str, girl: str, selected: Sequence[Selected], with_dates: bool = True
) -> str:
    """The first pass of the two-step mode: which of these are about her.

    Not about saving tokens — the evidence fits a context window several times
    over. About concentration: of 457 comments selected for one measured girl,
    roughly 250 are reply context, some of it flame-war residue, and that dilutes
    a model's attention over the comments that actually carry evidence even when
    it all fits.
    """
    return _assemble(
        store, girl, selected, filter_specification(store, girl), with_dates
    )
