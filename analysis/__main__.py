"""Entry point for the analysis layer / 分析層進入點.

    python -m analysis      the interactive loop

A shim and nothing else. The work — selecting the evidence, assembling the
prompt, taking the answer back off the clipboard and verifying every id it
cited — lives in `analysis.cli`, which is where the decisions worth asserting
are.

There is no unattended mode. This tool never calls a model itself: it hands you
a prompt and reads back what you paste in, so a human is always part of the loop
by construction.

本層沒有無人值守模式：工具只產生提示詞並讀回你貼上的答案，不會自行呼叫模型。
"""
from __future__ import annotations

import sys
from collections.abc import Sequence

from .cli import main as interactive

USAGE = "用法：python -m analysis（互動模式，不接受任何參數）"


def main(argv: Sequence[str] | None = None) -> int:
    """Refuse arguments rather than ignore them.

    Silently dropping argv would let a stale unattended invocation look like it
    had been accepted: the loop would start asking questions, and a script would
    hang on a prompt nobody is there to answer.
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments:
        print(USAGE)
        return 2
    return interactive()


if __name__ == "__main__":
    raise SystemExit(main())
