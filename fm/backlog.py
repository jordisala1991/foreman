"""The backlog: one `backlog.json` in today's foreman format (schema 5: tasks with id XX-n, title, status, detail,
acceptance {goal, criteria, verdict}, result, priority; decisions), plus new keys. Its only writer, under the file lock.

Statuses, and who moves a task between them (DESIGN.md has the picture):
  triage       filed by a health check or a source; waits for the owner's «go» (→ backlog or ready)
  backlog      to plan (it has acceptance criteria): the loop launches a planning agent
  refining     a planning agent works on it; it ends with `fm ready` (the plan, or «trivial»)
  ready        the loop launches the task's responsible agent
  in progress  the responsible agent works on it; it ends with `fm done`
  in review    checked (tests, diff, risk) and reviewed; the owner accepts, or the autonomy does
  done | canceled | duplicate

Keys added to a task: `agent` {name, session, short, kind: planning|responsible, started, nudged}, `branch`, `worktree`,
`sessions` (every session id), `session_kinds` (sid → planning|responsible|review), `report` {summary, review, confused, at},
`check` {tests, files, lines, risk, reasons, at}, `rework` (times it went back to its agent), `plan_file`, `source`
(dedup key of a finding or signal), `blocked` (why the loop leaves it alone), `parent` (of a subtask).
A question to the owner is a decision tied to its task and agent: {id: "XX-1 Q1", task, agent, title, text, kind, action?}.
"""

import contextlib
import re
from collections.abc import Generator, Mapping
from datetime import date
from typing import Any, NotRequired, TypedDict, Unpack

from . import util
from .config import Project

STATUSES = ("triage", "backlog", "refining", "ready", "in progress", "in review", "done", "canceled", "duplicate")
CLOSED = ("done", "canceled", "duplicate")
RUNNING = ("refining", "in progress")
PRIORITIES = ("urgent", "high", "medium", "low", "none")
Task = dict[str, Any]
Data = dict[str, Any]


class BacklogError(Exception):
    pass


class Agent(TypedDict, total=False):
    """A task's `agent`: the session working on it."""

    name: str
    short: str
    session: str
    cwd: str


class Acceptance(TypedDict, total=False):
    goal: str
    criteria: str
    verdict: str


class Report(TypedDict, total=False):
    summary: str
    review: str
    confused: str


class Reviewer(TypedDict, total=False):
    """A task's `reviewer`: the newest review launched (`reviewers` keeps every launch, same shape)."""

    round: int
    tip: str


class Review(TypedDict):
    """A task's `review`: a reviewer's verdict."""

    round: int
    tip: str
    open: int
    returned: NotRequired[bool]


def review_round(i: Mapping[str, Any]) -> int:
    """The round the next review is: reviews since the owner last asked for a fresh one (`review_from`), plus one."""
    return len(i.get("reviews") or []) - int(i.get("review_from", 0)) + 1


class Check(TypedDict, total=False):
    head: str
    risk: str


def empty() -> Data:
    return {"schema_version": 5, "updated": "", "tasks": [], "decisions": [], "decisions_resolved": []}


def load(p: Project) -> Data:
    d: Data = util.read_json(p.backlog_file, None) or empty()
    if d.get("schema_version") != 5:
        raise BacklogError(f"{p.backlog_file}: schema {d.get('schema_version')!r}, expected 5")
    for k, v in empty().items():
        d.setdefault(k, v)
    return d


@contextlib.contextmanager
def edit(p: Project) -> Generator[Data, None, None]:
    with util.file_lock(p.backlog_file):
        d = load(p)
        yield d
        d["updated"] = date.today().isoformat()
        util.write_json(p.backlog_file, d)


def tasks(d: Data) -> list[Task]:
    """Every task."""
    return list(d["tasks"])


def get(d: Data, tid: str) -> Task:
    for i in tasks(d):
        if i["id"] == tid:
            return i
    raise BacklogError(f"no task {tid}")


def new_id(p: Project, d: Data) -> str:
    prefix = p.get("project", "prefix")
    pat = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
    nums = [int(m.group(1)) for i in tasks(d) if (m := pat.match(i["id"]))]
    return f"{prefix}-{max(nums, default=0) + 1}"


def event(p: Project, tid: str | None, text: str) -> None:
    util.append_jsonl(p.state / "events.jsonl", {"time": util.now(), "task": tid, "text": text})


class NewTask(TypedDict, total=False):
    """The optional fields of a new task."""

    detail: str
    criteria: str
    goal: str
    source: str | None
    priority: str
    parent: str
    blocked_by: list[str]
    model: str
    effort: str


class Question(TypedDict, total=False):
    kind: str
    action: str


def add(p: Project, title: str, *, status: str = "backlog", **f: Unpack[NewTask]) -> str:
    detail, criteria, goal = f.get("detail", ""), f.get("criteria", ""), f.get("goal", "")
    source, priority = f.get("source"), f.get("priority", "none")
    if status not in ("triage", "backlog", "ready"):
        raise BacklogError("a new task starts in triage, backlog or ready")
    if not criteria:
        status = "triage"  # only a task with acceptance criteria can be let in
    with edit(p) as d:
        if source and any(i.get("source") == source and i["status"] not in CLOSED for i in tasks(d)):
            return ""
        tid = new_id(p, d)
        task: Task = {
            "id": tid,
            "title": title,
            "status": status,
            "blocked": "",
            "detail": detail,
            "priority": priority,
            "created": util.now(),
        }
        if criteria or goal:
            task["acceptance"] = {"goal": goal, "criteria": criteria}
        if source:
            task["source"] = source
        if parent := f.get("parent"):
            task["parent"] = parent
        if ids := f.get("blocked_by"):
            task["blocked_by"] = ids
        for k in ("model", "effort"):
            if v := f.get(k):
                task[k] = v
        d["tasks"].append(task)
    event(p, tid, f"filed ({status}): {title}")
    return tid


def set_status(p: Project, tid: str, status: str, why: str = "", **keys: Any) -> Task:
    if status not in STATUSES:
        raise BacklogError(f"bad status {status!r}")
    with edit(p) as d:
        i = get(d, tid)
        if status == "canceled" and i["status"] in CLOSED:  # closed meanwhile (merged): keep it
            return i
        i.update(keys, status=status)
        if status in CLOSED:
            i["closed"] = date.today().isoformat()
    event(p, tid, f"→ {status}" + (f": {why}" if why else ""))
    return i


def update(p: Project, tid: str, **keys: Any) -> Task:
    with edit(p) as d:
        i = get(d, tid)
        i.update(keys)
    return i


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


def mark_delivered(p: Project, qid: str) -> None:
    with edit(p) as d:
        for q in d["decisions_resolved"]:
            if q["id"] == qid:
                q["delivered"] = True
