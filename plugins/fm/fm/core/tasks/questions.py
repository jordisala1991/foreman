"""Questions to the owner, and the messages and notes filed for a task's agent: decisions tied to a task (see backlog.py)."""

from datetime import date
from typing import Any, TypedDict, Unpack

from ... import util
from ..project.config import Project
from .backlog import RUNNING, Agent, BacklogError, Data, Task, edit, event, get


class Question(TypedDict, total=False):
    kind: str
    action: str


def open_questions(d: Data, tid: str | None = None) -> list[dict[str, Any]]:
    return [q for q in d["decisions"] if q.get("task") and (tid is None or q["task"] == tid)]


def ask(p: Project, tid: str, agent: str, text: str, **f: Unpack[Question]) -> str:
    kind, action = f.get("kind", "question"), f.get("action", "")
    with edit(p) as d:
        i = get(d, tid)
        n = sum(1 for q in d["decisions"] + d["decisions_resolved"] if q.get("task") == tid) + 1
        qid = f"{tid} Q{n}"
        q: dict[str, Any] = {
            "id": qid,
            "task": tid,
            "agent": agent,
            "title": i["title"],
            "text": text,
            "asked": util.now(),
            "kind": kind,
        }
        if action:
            q["action"] = action
        d["decisions"].append(q)
    event(p, tid, f"asks the owner ({kind}): {text}")
    return qid


def answer(p: Project, qid: str, text: str, by: str = "owner (panel)") -> dict[str, Any]:
    with edit(p) as d:
        q = next((q for q in d["decisions"] if q["id"] == qid), None)
        if q is None:
            raise BacklogError(f"no open question {qid}")
        d["decisions"].remove(q)
        q.update({"answer": text, "resolved": date.today().isoformat(), "by": f"{by}, {util.now()}", "delivered": False})
        q["answered_at"] = util.now()
        d["decisions_resolved"].append(q)
    event(p, q["task"], f"owner answered {qid}: {text}")
    return q


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


def note(p: Project, tid: str, text: str, source: str) -> None:
    """A note from outside fm for the task's agent: delivered like an owner's answer (loop.deliver_answers)."""
    with edit(p) as d:
        message(d, get(d, tid), "N", ("note",), kind="note", text="signal", answer=text, source=source)
    event(p, tid, f"note from {source}: {text}")


def undelivered(d: Data) -> list[dict[str, Any]]:
    return [q for q in d["decisions_resolved"] if q.get("delivered") is False]


def holds_slot(d: Data, i: Task) -> bool:
    """A task whose planning or responsible agent counts against the slots: in `RUNNING`, not blocked, and not waiting
    for the owner (an open question or permission with no answer on its way)."""
    waiting = open_questions(d, i["id"]) and not any(q.get("task") == i["id"] for q in undelivered(d))
    return i["status"] in RUNNING and not i.get("blocked") and not waiting


def mark_delivered(p: Project, qid: str) -> None:
    with edit(p) as d:
        for q in d["decisions_resolved"]:
            if q["id"] == qid:
                q["delivered"] = True
