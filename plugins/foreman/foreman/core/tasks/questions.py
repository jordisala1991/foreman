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


def tasks_waiting(d: Data) -> set[str]:
    """Tasks whose agent is not waiting for the owner but for the loop: an open question or a message not yet delivered."""
    return {q["task"] for q in open_questions(d)} | {q["task"] for q in undelivered(d)}


def holds_slot(d: Data, i: Task) -> bool:
    """A task whose planning or responsible agent counts against the slots: in `RUNNING`, not blocked, and not waiting
    for the owner (an open question or permission with no answer on its way)."""
    waiting = open_questions(d, i["id"]) and not any(q.get("task") == i["id"] for q in undelivered(d))
    return i["status"] in RUNNING and not i.get("blocked") and not waiting


def is_yes(answer: str) -> bool:
    """The owner's yes to a proposal: the word alone, case, spaces and a final `.` or `!` ignored."""
    return answer.strip().rstrip(".!").strip().lower() == "yes"


def message_for(i: dict[str, Any], q: dict[str, Any]) -> str:
    """What an agent is told about a delivered answer, by its kind."""
    agent: Agent = i.get("agent") or {}
    if q.get("kind") in ("conflict", "edit", "failing", "review", "subtask"):
        return q["answer"]
    elif q.get("kind") == "rejection":
        return f"The owner rejected task {i['id']}: «{q['answer']}». Fix it, review again if you change much, and run `foreman done` again."
    elif q.get("kind") == "note":
        return f"A note from outside fm for task {i['id']} ({q.get('source')}): «{q['answer']}»."
    elif q.get("kind") == "permission":
        return f"The owner answered your request {q['id']} ({q.get('action')}): «{q['answer']}». " + (
            "It is allowed once: run it again." if q.get("allowed") else "It is not allowed."
        )
    elif q.get("kind") == "unblock":
        return f"The owner cleared the block on task {i['id']}: carry on with the task."
    elif q.get("kind") == "stopped":
        return (
            f"The owner answered about task {i['id']}, where you ended your turn with no question, no block and no open subtask: "
            f"«{q['answer']}». Carry on with it, then end with a foreman ask, a foreman block, an open subtask or, only on his yes, foreman done."
        )
    elif agent.get("kind") == "parent":
        if is_yes(q["answer"]):
            return (
                f"The owner answered yes to your question {q['id']}: «{q['text']}». "
                "If it proposed an action, run exactly that action and no other, then end your turn."
            )
        return (
            f"The owner replied to your question {q['id']}: «{q['answer']}». It is not a yes to a proposed command: run no proposed command because of it. "
            "If the question was a point to decide, carry on with his answer; "
            "if you still need his yes for an action, propose it again with `foreman ask`."
        )
    else:
        return f"The owner answered your question {q['id']}: «{q['answer']}». Carry on with the task."
