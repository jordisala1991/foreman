"""Tasks and subtasks (pure): a subtask names its parent in `parent`; the parent stores no list."""

from . import backlog


def of(tasks: list[backlog.Task], pid: str) -> list[backlog.Task]:
    return [i for i in tasks if i.get("parent") == pid]


def ordered(tasks: list[backlog.Task]) -> list[backlog.Task]:
    """Each task in file order, followed by its subtasks."""
    ids = {i["id"] for i in tasks}
    out: list[backlog.Task] = []
    for i in tasks:
        if i.get("parent") not in ids:
            out += [i, *of(tasks, i["id"])]
    return out


def refusal(tasks: list[backlog.Task], pid: str) -> str:
    """Why a subtask cannot be filed under `pid` ("" if it can)."""
    parent = next((i for i in tasks if i["id"] == pid), None)
    if parent is None:
        return f"no task {pid}"
    if parent.get("parent"):
        return f"{pid} is itself a subtask of {parent['parent']}: only one level"
    if parent["status"] not in ("triage", "backlog"):
        return f"{pid} is {parent['status']}: a task is split before it starts (triage or backlog)"
    return ""


def outcome(subs: list[backlog.Task]) -> str:
    """How the parent closes once its subtasks have: "" while any is open or there are none."""
    if not subs or any(s["status"] not in backlog.CLOSED for s in subs):
        return ""
    return "done" if any(s["status"] == "done" for s in subs) else "canceled"
