"""A task's repo (pure): shown only with several repos, and what Go and add refuse without one."""

from . import backlog, subtasks


def of(i: backlog.Task, tasks: list[backlog.Task], several: bool) -> str:
    """The task's repo key; `-` for a task with none or with subtasks; empty with one repo, where none is shown."""
    if not several:
        return ""
    return "-" if subtasks.of(tasks, i["id"]) or not i.get("repo") else i["repo"]


def shown(i: backlog.Task, tasks: list[backlog.Task], several: bool) -> backlog.Task:
    """A copy of the task with its repo as it is read: none with one repo, `-` where it has none."""
    r = of(i, tasks, several)
    out = {k: v for k, v in i.items() if k != "repo"}
    return {**out, "repo": r} if r else out


def go_refusal(i: backlog.Task, tasks: list[backlog.Task], keys: list[str]) -> str:
    """Why Go cannot let the task in: several repos and it has no repo (a task with subtasks needs none); else empty."""
    if len(keys) < 2 or i.get("repo") or subtasks.of(tasks, i["id"]):
        return ""
    return f"{i['id']}: choose its repo first (foreman edit {i['id']} --repo {'|'.join(keys)})"


def add_refusal(keys: list[str], repo: str, status: str, criteria: str) -> str:
    """Why a new task cannot be filed: several repos, criteria, to be let in (backlog) and no repo; else empty."""
    if len(keys) < 2 or repo or status != "backlog" or not criteria:
        return ""
    return f"choose its repo first (--repo {'|'.join(keys)}, or --triage)"
