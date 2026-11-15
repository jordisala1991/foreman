"""Task types (pure): change, bug, mechanical, definition. A type only changes the instructions of the task's agents."""

from collections.abc import Mapping
from typing import Any

from . import backlog, subtasks

TYPES = ("change", "bug", "mechanical", "definition")
DEFAULT = "change"

PARAGRAPHS = {
    "bug": "First reproduce the bug with a test that fails, and name its cause in the report.",
    "mechanical": "No design decisions: the diff is the proof.",
    "definition": "The PR changes only documents: the definition goes in DESIGN.md and the drafts of the subtasks go in the report, without filing them.",
}


def of(i: backlog.Task, tasks: list[backlog.Task]) -> str:
    """The task's type; empty for a parent, which has none of its own."""
    return "" if subtasks.of(tasks, i["id"]) else i.get("type") or DEFAULT


def shown(i: backlog.Task, tasks: list[backlog.Task]) -> backlog.Task:
    """A copy of the task with its type as it is read: the default if none, no type for a parent."""
    t = of(i, tasks)
    out = {k: v for k, v in i.items() if k != "type"}
    return {**out, "type": t} if t else out


def section(i: Mapping[str, Any]) -> str:
    """The agents' paragraph for this task's type; empty for change, so its instructions stay as they were."""
    t = i.get("type") or DEFAULT
    return f"# Task type: {t}\n{PARAGRAPHS[t]}\n\n" if t in PARAGRAPHS else ""
