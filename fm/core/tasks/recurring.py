"""Recurring entries (pure): when each `[[recurring]]` entry is due (DESIGN.md `## Recurring tasks`). An entry files a
task, with source `recurring:NAME`, every N merged tasks or every fixed interval of time. The clock (`now`) and the
closing times are arguments: the I/O is in `schedule`."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..project.recurring_table import interval
from . import backlog, subtasks
from .backlog import Task

SOURCE = "recurring:"
STAMP = "%Y-%m-%d %H:%M:%S"
CLOSING = ("→ done", "→ canceled", "→ duplicate")


def name_of(i: Task) -> str:
    """The name of the entry that filed the task; empty when none did."""
    source = i.get("source") or ""
    return source.removeprefix(SOURCE) if source.startswith(SOURCE) else ""


def shown(i: Task) -> Task:
    """A copy of the task with `recurring`, the entry's name, when an entry filed it."""
    return {**i, "recurring": name_of(i)} if name_of(i) else i


def closed_at(events: list[dict[str, Any]]) -> dict[str, str]:
    """Each task's closing time: that of its last `→ done`, `→ canceled` or `→ duplicate` event."""
    return {e["task"]: e["time"] for e in events if str(e.get("text", "")).startswith(CLOSING)}


@dataclass(frozen=True)
class Status:
    open: str  # the entry's task that is not closed yet; "" if none
    since: str  # where the count or the period starts
    merged: list[Task]  # every_merges: the merged tasks since then
    left: int  # every_merges: merges still to wait for
    next: str  # every: when it is due
    due: bool


def status(entry: dict[str, Any], tasks: list[Task], closed: dict[str, str], seen: str, now: datetime) -> Status:
    """Where an entry stands. Its count or period starts when fm first saw it (`seen`) or when its last task closed."""
    mine = [i for i in tasks if i.get("source") == SOURCE + entry["name"]]
    open_ = next((i["id"] for i in mine if i["status"] not in backlog.CLOSED), "")
    since = max([seen, *(closed[i["id"]] for i in mine if i["id"] in closed)])
    if (span := interval(entry["every"])) is not None:
        due = (datetime.strptime(since, STAMP) + span).strftime(STAMP)
        return Status(open_, since, [], 0, due, not open_ and now.strftime(STAMP) >= due)
    merged = [i for i in tasks if i["status"] == "done" and not name_of(i) and not subtasks.of(tasks, i["id"]) and closed.get(i["id"], "") > since]
    merged.sort(key=lambda i: closed[i["id"]])
    left = max(0, entry["every_merges"] - len(merged))
    return Status(open_, since, merged, left, "", not open_ and left == 0)


def covers(entry: dict[str, Any], st: Status, now: datetime) -> str:
    """The last lines of the filed task's detail: what it covers."""
    if entry["every"]:
        return f"Covers the period from {st.since} to {now.strftime(STAMP)}."
    lines = "\n".join(f"- {i['id']}: {i['title']}" for i in st.merged)
    return f"Covers the tasks merged since {st.since}:\n{lines}"
