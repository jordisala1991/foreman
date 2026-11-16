"""Which tasks a task waits for (pure): `blocked_by` lists their ids; absent when empty."""

from . import backlog


def ids(raw: list[str]) -> list[str]:
    """The ids given on the command line, without repeats; `none` alone clears the list."""
    return [] if raw == ["none"] else list(dict.fromkeys(raw))


def _status(tasks: list[backlog.Task], x: str) -> str:
    return next((i["status"] for i in tasks if i["id"] == x), "")


def pending(i: backlog.Task, tasks: list[backlog.Task]) -> list[str]:
    """The blockers not done yet (an id with no task counts as not done); none once the task has started or closed."""
    if i["status"] not in ("triage", "backlog", "ready"):
        return []
    return [x for x in i.get("blocked_by", []) if _status(tasks, x) != "done"]


def dead(i: backlog.Task, tasks: list[backlog.Task]) -> list[str]:
    """The blockers that closed without being done."""
    return [x for x in i.get("blocked_by", []) if _status(tasks, x) in backlog.CLOSED and _status(tasks, x) != "done"]


def label(i: backlog.Task, tasks: list[backlog.Task]) -> str:
    return f"waits for {', '.join(w)}" if (w := pending(i, tasks)) else ""


def _reaches(tasks: list[backlog.Task], start: str, goal: str) -> bool:
    """Whether `goal` is reachable from `start` by following `blocked_by`."""
    seen: set[str] = set()
    todo = [start]
    while todo:
        x = todo.pop()
        if x == goal:
            return True
        if x not in seen:
            seen.add(x)
            todo += [w for i in tasks if i["id"] == x for w in i.get("blocked_by", [])]
    return False


def refusal(tasks: list[backlog.Task], tid: str, new: list[str]) -> str:
    """Why `tid` (empty for a task being filed) cannot wait for `new` ("" if it can)."""
    for x in new:
        if not _status(tasks, x):
            return f"no task {x}"
        if x == tid:
            return f"{tid} cannot wait for itself"
        if tid and _reaches(tasks, x, tid):
            return f"{tid} cannot wait for {x}: {x} already waits for {tid}, directly or through other tasks"
    return ""
