"""The loop's review step of a branch in review: what it sees (`seen_of`, the facts decide.review_step needs) and doing
what that step said (`apply_review`). The decisions themselves are in foreman/core/rules/decide.py."""

from pathlib import Path
from typing import Any

from ..core.project.config import Project
from ..core.review import reviews
from ..core.rules import decide
from ..core.tasks import backlog, runs
from ..providers import registry
from ..providers.runner import Rows, ref_of
from ..services import scratch
from ..services.agents.launch import Scope, launch_reviewer
from ..services.agents.running import idle_s, running
from ..services.delivery import git
from .talk import NUDGE_IDLE_S, return_to_agent


def seen_of(p: Project, i: dict[str, Any], head: str, rows: Rows) -> decide.Seen:
    """What the loop sees of this branch's review: tips, a free slot, the hunter's file, the sessions, why a review would
    run (`review_for`) and how the head follows the verdict's tip (a merge of main, clean or resolved)."""
    v: dict[str, Any] = i.get("review") or {}
    h: dict[str, Any] = i.get("hunter") or {}
    r: dict[str, Any] = i.get("reviewer") or {}
    tip = reviews.verdict_tip(v)
    merge, auto = git.merge_of(p, tip, head) if tip and tip != head else ("", "")
    return decide.Seen(
        head=head,
        verdict_tip=tip,
        hunter_tip=h.get("tip", ""),
        reviewer_tip=r.get("tip", ""),
        free=running(registry.backlog(p).load()) < int(p.get("agents", "slots")),
        hunter_file=Path(h.get("file", "")).exists(),
        hunter_working=working(h, rows),
        hunter_ended=ended(h, rows),
        reviewer_ended=ended(r, rows),
        review_for=i.get("review_for", ""),
        merge=merge,
        auto=auto,
    )


def apply_review(p: Project, i: dict[str, Any], head: str, step: decide.Step) -> None:
    """Does what `decide.review_step` said: launch the hunter or the reviewer, carry the verdict to this tip, block, or
    send the findings back once."""
    v: dict[str, Any] = i.get("review") or {}
    if step.do in ("hunt", "review"):
        launch_reviewer(p, i, head, "hunter" if step.do == "hunt" else "reviewer", Scope(step.text, step.base))
    elif step.do == "hold":
        registry.backlog(p).update(i["id"], review={**v, "holds": head})
        backlog.event(p, i["id"], f"new tip {head[:9]} follows reviewed {v['tip'][:9]}: no review runs, its verdict holds")
    elif step.do == "block":
        block_review(p, i["id"], step.text)
        runs.end(p, i["id"], ("hunter", "review"), "failed", step.text)
    elif step.do == "back":
        return_to_agent(p, i["id"], "review", step.text)
        registry.backlog(p).update(i["id"], review={**v, "returned": True})


def working(a: dict[str, Any], rows: Rows) -> bool:
    return bool(a.get("session")) and rows.busy(ref_of(a))


def ended(a: dict[str, Any], rows: Rows) -> bool:
    return bool(a.get("session")) and not rows.busy(ref_of(a)) and idle_s(a) > NUDGE_IDLE_S


def block_review(p: Project, tid: str, why: str) -> None:
    registry.backlog(p).update(tid, blocked=why)
    backlog.event(p, tid, f"blocked: {why}")


def sweep_scratch(p: Project) -> None:
    """Removes every scratch folder but those of the hunter and reviewer of the round a task in review is in."""
    keep: set[Path] = set()
    for i in backlog.tasks(registry.backlog(p).load()):
        for role in ("hunter", "reviewer"):
            a: dict[str, Any] = i.get(role) or {}
            if i["status"] == "in review" and a.get("round") == reviews.review_round(i):
                keep.add(scratch.folder(p, i["id"], a["round"], a["tip"], role))
    scratch.sweep(p, keep)
