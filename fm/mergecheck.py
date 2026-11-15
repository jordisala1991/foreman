"""The merged check (FN-113): just before an accepted branch merges, the test command runs on it merged with main as
main is at that moment, so two branches that pass alone cannot break main together. It runs only at the merge step
(fm/loop.py, `gate_merge`), one at a time; the result goes in the task's `check`, with `merged_with` = main's commit."""

from typing import Any

from . import delivery, pulls, util
from .core.project.config import Project
from .core.tasks import backlog


def merged_check(p: Project, tid: str) -> None:
    """Runs the tests on the branch merged with main and stores them in `check`. Nothing is stored on a conflict (the
    merge step returns it, as today) or when the branch moved meanwhile (its new head is checked first)."""
    i = backlog.get(backlog.load(p), tid)
    head = pulls.head_of(p, i["branch"])
    main_head = delivery.git(p.repo, "rev-parse", p.get("project", "main")).out.strip()
    if delivery.git(p.repo, "merge-tree", "--write-tree", main_head, head, check=False).code != 0:
        return
    tests, out = delivery.tests_at(p, main_head, tid.lower(), merge=head)
    c: dict[str, Any] = backlog.get(backlog.load(p), tid).get("check") or {}
    if c.get("head") != head:
        return
    new: dict[str, Any] = {**c, "tests": tests, "tests_output": out, "main": main_head, "merged_with": main_head, "at": util.now()}
    if tests == "fail":
        why = f"tests fail merged with main {main_head[:9]}, not on the branch alone [tests]"
        new = {**new, "risk": "high", "reasons": [why, *c.get("reasons", [])][:3]}
    backlog.update(p, tid, check=new)
    backlog.event(p, tid, f"checked merged with main {main_head[:9]}: tests {tests}")
