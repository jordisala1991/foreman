"""Which tasks a task waits for (pure): `blocked_by` lists their ids; absent when empty."""

from . import backlog


def ids(raw: list[str]) -> list[str]:
    """The ids given on the command line, without repeats; `none` alone clears the list."""
    return [] if raw == ["none"] else list(dict.fromkeys(raw))


def _status(issues: list[backlog.Issue], x: str) -> str:
    return next((i["status"] for i in issues if i["id"] == x), "")


def pending(i: backlog.Issue, issues: list[backlog.Issue]) -> list[str]:
    """The blockers not done yet (an id with no issue counts as not done)."""
    return [x for x in i.get("blocked_by", []) if _status(issues, x) != "done"]


def dead(i: backlog.Issue, issues: list[backlog.Issue]) -> list[str]:
    """The blockers that closed without being done."""
    return [x for x in i.get("blocked_by", []) if _status(issues, x) in backlog.CLOSED and _status(issues, x) != "done"]


def label(i: backlog.Issue, issues: list[backlog.Issue]) -> str:
    return f"waits for {', '.join(w)}" if (w := pending(i, issues)) else ""


def _reaches(issues: list[backlog.Issue], start: str, goal: str) -> bool:
    """Whether `goal` is reachable from `start` by following `blocked_by`."""
    seen: set[str] = set()
    todo = [start]
    while todo:
        x = todo.pop()
        if x == goal:
            return True
        if x not in seen:
            seen.add(x)
            todo += [w for i in issues if i["id"] == x for w in i.get("blocked_by", [])]
    return False


def refusal(issues: list[backlog.Issue], iid: str, new: list[str]) -> str:
    """Why `iid` (empty for a task being filed) cannot wait for `new` ("" if it can)."""
    for x in new:
        if not _status(issues, x):
            return f"no issue {x}"
        if x == iid:
            return f"{iid} cannot wait for itself"
        if iid and _reaches(issues, x, iid):
            return f"{iid} cannot wait for {x}: {x} already waits for {iid}, directly or through other tasks"
    return ""
