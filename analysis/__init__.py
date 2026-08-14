"""Turning a store file and a girl's name into a pasteable prompt / 分析層.

No eager imports. `python -m analysis.cli` would otherwise import the package,
import `cli` into it, and then import `cli` a second time as `__main__` — which
Python warns about and which leaves two copies of the module's constants around.

    analysis.select       which comments concern this girl, decided without a model
    analysis.morphology   drops a match it can prove is grammar, not a name
    analysis.honorifics   whether the name is a stage name at all
    analysis.attribution  how sure the selection is that a comment is about her
    analysis.diagnose     why a selection came back empty
    analysis.render       those comments as prompt lines
    analysis.prompt       the whole text the operator pastes
    analysis.citations    comment ids coming back from a model
    analysis.verification how many of those ids were invented
    analysis.cli          the interactive loop

Run it with `python -m analysis`.
"""
from __future__ import annotations
