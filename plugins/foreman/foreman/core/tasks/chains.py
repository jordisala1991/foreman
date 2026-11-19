"""Chains (pure): a chain parent is filed by a lens entry and stores `chain`; its steps are its subtasks (DESIGN.md
`## Improvement chains`)."""

from typing import TypedDict

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
