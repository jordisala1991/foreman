"""Tasks and subtasks (pure): a subtask names its parent in `parent`; the parent stores no list."""

from . import backlog


def of(issues: list[backlog.Issue], pid: str) -> list[backlog.Issue]:
    return [i for i in issues if i.get("parent") == pid]


def ordered(issues: list[backlog.Issue]) -> list[backlog.Issue]:
    """Each task in file order, followed by its subtasks."""
    ids = {i["id"] for i in issues}
    out: list[backlog.Issue] = []
    for i in issues:
        if i.get("parent") not in ids:
            out += [i, *of(issues, i["id"])]
    return out


def refusal(issues: list[backlog.Issue], pid: str) -> str:
    """Why a subtask cannot be filed under `pid` ("" if it can)."""
    parent = next((i for i in issues if i["id"] == pid), None)
    if parent is None:
        return f"no issue {pid}"
    if parent.get("parent"):
        return f"{pid} is itself a subtask of {parent['parent']}: only one level"
    if parent["status"] not in ("triage", "backlog"):
        return f"{pid} is {parent['status']}: a task is split before it starts (triage or backlog)"
    return ""


def outcome(subs: list[backlog.Issue]) -> str:
    """How the parent closes once its subtasks have: "" while any is open or there are none."""
    if not subs or any(s["status"] not in backlog.CLOSED for s in subs):
        return ""
    return "done" if any(s["status"] == "done" for s in subs) else "canceled"
