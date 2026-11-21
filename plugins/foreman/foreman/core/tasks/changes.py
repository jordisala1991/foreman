"""The changes the backlog's adapters make to its data (pure: each mutates the `Data` it is given): filing, a move, a
question and its answer, a note, what misled an agent. One place for their rules, whatever holds the backlog."""

from datetime import date
from typing import Any

from ... import util
from .backlog import CLOSED, STATUSES, BacklogError, Data, NewTask, Task, get, tasks
from .questions import Question, message


def filed(title: str, status: str, f: NewTask) -> Task:
    """A new task with no id nor creation time yet (the adapter gives them); a task with no criteria starts in triage."""
    criteria, goal = f.get("criteria", ""), f.get("goal", "")
    if status not in ("triage", "backlog", "ready"):
        raise BacklogError("a new task starts in triage, backlog or ready")
    if not criteria:
        status = "triage"  # only a task with acceptance criteria can be let in
    task: Task = {"title": title, "status": status, "blocked": "", "detail": f.get("detail", ""), "priority": f.get("priority", "none")}
    if criteria or goal:
        task["acceptance"] = {"goal": goal, "criteria": criteria}
    if source := f.get("source"):
        task["source"] = source
    if parent := f.get("parent"):
        task["parent"] = parent
    if ids := f.get("blocked_by"):
        task["blocked_by"] = ids
    for k in ("model", "effort", "type", "repo", "chain"):
        if v := f.get(k):
            task[k] = v
    return task


def known(d: Data, source: str | None) -> bool:
    """An open task already has this source (the dedup key of a finding)."""
    return bool(source) and any(i.get("source") == source and i["status"] not in CLOSED for i in tasks(d))


def moved(d: Data, tid: str, status: str, keys: dict[str, Any]) -> bool:
    """Moves the task to `status` with `keys`; False when it was not moved (canceling a task closed meanwhile keeps it)."""
    if status not in STATUSES:
        raise BacklogError(f"bad status {status!r}")
    i = get(d, tid)
    if status == "canceled" and i["status"] in CLOSED:  # closed meanwhile (merged): keep it
        return False
    i.update(keys, status=status)
    if status in CLOSED:
        i["closed"] = date.today().isoformat()
    return True


def misled_text(text: str) -> str:
    """What misled an agent, as kept: one line; empty for «none» and the like, which is not kept."""
    t = " ".join(text.split())
    return "" if t.lower().rstrip(".") in ("", "none", "nothing", "n/a", "-") else t


def misled(d: Data, tid: str, role: str, text: str) -> None:
    if t := misled_text(text):
        texts = get(d, tid).setdefault("misled", {}).setdefault(role, [])
        if t not in texts:
            texts.append(t)


def asked(d: Data, tid: str, agent: str, text: str, f: Question) -> str:
    """Files a question to the owner for the task's agent; its id."""
    kind, action = f.get("kind", "question"), f.get("action", "")
    i = get(d, tid)
    n = sum(1 for q in d["decisions"] + d["decisions_resolved"] if q.get("task") == tid) + 1
    qid = f"{tid} Q{n}"
    q: dict[str, Any] = {"id": qid, "task": tid, "agent": agent, "title": i["title"], "text": text, "asked": util.now(), "kind": kind}
    if action:
        q["action"] = action
    d["decisions"].append(q)
    return qid


def answered(d: Data, qid: str, text: str, by: str) -> dict[str, Any]:
    q = next((q for q in d["decisions"] if q["id"] == qid), None)
    if q is None:
        raise BacklogError(f"no open question {qid}")
    d["decisions"].remove(q)
    q.update({"answer": text, "resolved": date.today().isoformat(), "by": f"{by}, {util.now()}", "delivered": False})
    q["answered_at"] = util.now()
    d["decisions_resolved"].append(q)
    return q


def noted(d: Data, tid: str, text: str, source: str) -> None:
    message(d, get(d, tid), "N", ("note",), kind="note", text="signal", answer=text, source=source)


def delivered(d: Data, qid: str) -> None:
    for q in d["decisions_resolved"]:
        if q["id"] == qid:
            q["delivered"] = True
