"""`foreman list`'s rows (pure): each task with the facts `list` prints, shared by its text and its `--json`."""

from typing import TypedDict

from . import backlog, blockers, chains, recurring, subtasks, taskrepo, tasktypes


class TaskRow(TypedDict):
    """One task as `list` shows it; a fact the task lacks is "" (`priority` "none"; `chain` None for a task that is no chain parent).

    `closed` is the stored date, None while the task has none; `rework` the owner's rejections, 0 when none."""

    id: str
    status: str
    type: str
    title: str
    agent: str
    repo: str
    parent: str
    priority: str
    model: str
    effort: str
    blocked: str
    waiting: str
    recurring: str
    chain: chains.Progress | None
    closed: str | None
    rework: int


def rows(tasks: list[backlog.Task], closed: bool, several: bool) -> list[TaskRow]:
    """Each task in file order with its subtasks after it, the closed ones only under `closed`."""
    return [_row(i, tasks, several) for i in subtasks.ordered(tasks) if closed or i["status"] not in backlog.CLOSED]


def _row(i: backlog.Task, tasks: list[backlog.Task], several: bool) -> TaskRow:
    agent: backlog.Agent = i.get("agent") or {}
    return TaskRow(
        id=i["id"],
        status=i["status"],
        type=tasktypes.of(i, tasks),
        title=i["title"],
        agent=agent.get("name", ""),
        repo=taskrepo.of(i, tasks, several),
        parent=i.get("parent") or "",
        priority=i.get("priority", "none"),
        model=i.get("model") or "",
        effort=i.get("effort") or "",
        blocked=i.get("blocked") or "",
        waiting=blockers.label(i, tasks),
        recurring=recurring.name_of(i),
        chain=chains.progress(tasks, i),
        closed=i.get("closed") or None,
        rework=i.get("rework", 0),
    )
