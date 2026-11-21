"""Questions to the owner, and the messages and notes filed for a task's agent: decisions tied to a task (see backlog.py)."""

from typing import Any, TypedDict

from ... import util
from .backlog import RUNNING, Agent, Data, Task


class Question(TypedDict, total=False):
    kind: str
    action: str


def open_questions(d: Data, tid: str | None = None) -> list[dict[str, Any]]:
    return [q for q in d["decisions"] if q.get("task") and (tid is None or q["task"] == tid)]


def message(d: dict[str, Any], i: dict[str, Any], tag: str, kinds: tuple[str, ...], **fields: Any) -> None:
    """Files a message for the task's agent among the resolved decisions, id `<task> <tag><n>` (n counts the task's
    entries of `kinds`); the loop delivers it (loop.deliver_answers)."""
    n = sum(1 for q in d["decisions_resolved"] if q.get("task") == i["id"] and q.get("kind") in kinds)
    agent: Agent = i.get("agent") or {}
    d["decisions_resolved"].append(
        {
            "id": f"{i['id']} {tag}{n + 1}",
            "task": i["id"],
            "agent": agent.get("name"),
            "resolved": util.now()[:10],
            "delivered": False,
            "answered_at": util.now(),
            "title": i["title"],
            **fields,
        }
    )


def undelivered(d: Data) -> list[dict[str, Any]]:
    return [q for q in d["decisions_resolved"] if q.get("delivered") is False]


def holds_slot(d: Data, i: Task) -> bool:
    """A task whose planning or responsible agent counts against the slots: in `RUNNING`, not blocked, and not waiting
    for the owner (an open question or permission with no answer on its way)."""
    waiting = open_questions(d, i["id"]) and not any(q.get("task") == i["id"] for q in undelivered(d))
    return i["status"] in RUNNING and not i.get("blocked") and not waiting
