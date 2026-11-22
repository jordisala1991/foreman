"""Chains (pure): a chain parent is filed by a lens entry and stores `chain`; its steps are its subtasks (DESIGN.md
`## Improvement chains`)."""

from collections.abc import Mapping
from typing import Any, TypedDict

from . import backlog, subtasks


def is_parent(tasks: list[backlog.Task], i: backlog.Task) -> bool:
    """A parent: it has subtasks, or it is a chain parent, which is one before its first step."""
    return bool(i.get("chain")) or bool(subtasks.of(tasks, i["id"]))


def outcome(i: backlog.Task, subs: list[backlog.Task]) -> str:
    """How the parent closes on its agent's `foreman done`: a chain with no step is done; else as its subtasks say."""
    return "done" if i.get("chain") and not subs else subtasks.outcome(subs)


class Progress(TypedDict):
    """Where a chain stands: `step` steps filed of `cap`, `open` the id of the step not closed ("" if none)."""

    lens: str
    step: int
    cap: int
    open: str


def progress(tasks: list[backlog.Task], i: backlog.Task) -> Progress | None:
    """Where the chain of parent `i` stands (None if `i` is no chain parent)."""
    if not i.get("chain"):
        return None
    steps = subtasks.of(tasks, i["id"])
    open_ = next((s["id"] for s in steps if s["status"] not in backlog.CLOSED), "")
    return Progress(lens=i["chain"]["lens"], step=len(steps), cap=i["chain"]["max_steps"], open=open_)


def standing(tasks: list[backlog.Task], parent: backlog.Task, missed: list[str]) -> str:
    """The brief section for a parent launched anew: the session before it ended, where the chain and the subtasks stand,
    and the notices the old session did not receive."""
    lines = ["# Where it stands", "The session that worked on this task before you ended; you replace it."]
    if (prog := progress(tasks, parent)) is not None:
        lines.append(f"Chain step {prog['step']} of {prog['cap']}" + (f" ({prog['open']} open)" if prog["open"] else ""))
    lines += [f"- {s['id']} {s['status']}: {s['title']}" for s in subtasks.of(tasks, parent["id"])]
    if missed:
        lines += ["Notices you missed:", *(f"- {m}" for m in missed)]
    lines.append(f"This is a snapshot: run `foreman show {parent['id']}` before you act.")
    return "\n".join(lines) + "\n\n"


def mark(d: backlog.Data, tid: str) -> int:
    """How far the parent of `tid` has come: its subtasks filed and closed (chain steps), its own questions to the owner
    (`asked`) and the owner's answers to them. A session that ends with the same mark made no progress."""
    subs = subtasks.of(backlog.tasks(d), tid)
    closed = [s for s in subs if s["status"] in backlog.CLOSED]
    asked = [q for q in d["decisions"] + d["decisions_resolved"] if q.get("task") == tid and "asked" in q]
    answered = [q for q in d["decisions_resolved"] if q.get("task") == tid and "asked" in q]
    return len(subs) + len(closed) + len(asked) + len(answered)


def stalled(old: Mapping[str, Any], mark: int, epoch: str) -> list[str]:
    """The sessions of a parent that ended in a row without progress, once its session in `old` has ended: none for a
    first launch or after progress (`mark` grew); the same as before when the runner restarted (its `epoch` changed),
    which ends every session; else the ones before and `old`'s own."""
    if not old:
        return []
    prior: list[str] = old.get("ended", [])
    if mark > old.get("mark", mark):
        return []
    if old.get("epoch") and epoch != old["epoch"]:
        return prior
    return [*prior, old.get("short") or str(old.get("session") or "")[:8]]


def _parent(tasks: list[backlog.Task], pid: str) -> backlog.Task | None:
    return next((i for i in tasks if i["id"] == pid and i.get("chain")), None)


def refusal(tasks: list[backlog.Task], pid: str) -> str:
    """Why a step cannot be filed under the chain parent `pid` ("" if it can, or if `pid` is no chain): the brakes of
    DESIGN.md `### Brakes`. A canceled step ends the chain, one step is open at a time, at most `max_steps` steps."""
    parent = _parent(tasks, pid)
    if parent is None:
        return ""
    steps = subtasks.of(tasks, pid)
    if canceled := next((s["id"] for s in steps if s["status"] == "canceled"), ""):
        return f"{canceled} was canceled: a chain ends after a canceled step; write the owner's reason in History and close the chain with foreman done"
    if open_ := next((s["id"] for s in steps if s["status"] not in backlog.CLOSED), ""):
        return f"{open_} is still open: file the next step once it is merged"
    if len(steps) >= parent["chain"]["max_steps"]:
        return f"{pid} has {len(steps)} steps, the cap of its chain: ask the owner to renew"
    return ""


class StepFields(TypedDict):
    """What a step takes from its chain parent."""

    goal: str
    criteria: str
    model: str
    effort: str
    type: str


def step_fields(tasks: list[backlog.Task], pid: str) -> StepFields | None:
    """What a step takes from its chain parent, whoever files it (None for no chain)."""
    parent = _parent(tasks, pid)
    if parent is None:
        return None
    acceptance: dict[str, str] = parent.get("acceptance") or {}
    return StepFields(
        goal=acceptance.get("goal", ""),
        criteria=acceptance.get("criteria", ""),
        model=parent.get("model", ""),
        effort=parent.get("effort", ""),
        type=parent.get("type", ""),
    )


def notice(sub: backlog.Task, subs: list[backlog.Task], parent: backlog.Task) -> str:
    """The message the chain agent gets when one of its steps closes: file the next step or close; after a canceled step,
    close (filing is refused then, `refusal`)."""
    pid = parent["id"]
    how = {"done": "merged", "canceled": "canceled"}.get(sub["status"], f"closed as {sub['status']}")
    text = f"Step {sub['id']} «{sub['title']}» was {how}. Steps of {pid}: " + ", ".join(f"{s['id']} {s['status']}" for s in subs) + "."
    close = 'close the chain (foreman done --summary "…")'
    if sub["status"] != "canceled":
        return text + f' Read the lens report, rewrite its Frontier, then file the next step (foreman add "title" --parent {pid} --detail "…") or {close}.'
    return text + f" A chain ends after a canceled step: write the owner's reason in History of the lens report, then {close}."
