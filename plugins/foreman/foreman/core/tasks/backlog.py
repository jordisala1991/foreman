"""The backlog: one `backlog.json` in today's foreman format (schema 5: tasks with id XX-n, title, status, detail,
acceptance {goal, criteria, verdict}, result, priority; decisions), plus new keys. Its only writer, under the file lock.

Statuses, and who moves a task between them (DESIGN.md has the picture):
  triage       filed by a health check or a source; waits for the owner's «go» (→ backlog or ready)
  backlog      to plan (it has acceptance criteria): the loop launches a planning agent
  refining     a planning agent works on it; it ends with `foreman ready` (the plan, or «trivial»)
  ready        the loop launches the task's responsible agent
  in progress  the responsible agent works on it; it ends with `foreman done`
  in review    checked (tests, diff, risk) and reviewed; the owner accepts, or the autonomy does
  done | canceled | duplicate

Keys added to a task: `agent` {name, session, short, kind: planning|responsible, started, nudged, terminal, pane, waiting_seen}, `branch`, `worktree`,
`sessions` (every session id), `session_kinds` (sid → planning|responsible|review), `session_tabs` (sid → [terminal, pane]),
`report` {summary, review, confused, at}, `check` {tests, files, lines, risk, reasons, at}, `rework` (times it went back to its agent),
`plan_file`, `source`
(dedup key of a finding or signal), `blocked` (why the loop leaves it alone), `parent` (of a subtask), `misled` {planning|
responsible|hunter|reviewer: [what misled that agent]}, `repo` (the `[repos.K]` key of the repo it changes; several repos only).
A question to the owner is a decision tied to its task and agent: {id: "XX-1 Q1", task, agent, title, text, kind, action?}.
"""

import contextlib
import re
from collections.abc import Generator
from datetime import date
from typing import Any, TypedDict, Unpack

from ... import util
from ..project.config import Project

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
    started: str
    terminal: str
    pane: str
    dispatch: str  # orca: the worker's dispatch id and its coordinator tab
    coordinator: str


class Acceptance(TypedDict, total=False):
    goal: str
    criteria: str
    verdict: str


class Report(TypedDict, total=False):
    summary: str
    review: str
    confused: str


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


def record_misled(p: Project, tid: str, role: str, text: str) -> None:
    """What misled an agent (its `--misled` text) is kept on the task under its role, once; «none» is not kept."""
    t = " ".join(text.split())
    if t.lower().rstrip(".") in ("", "none", "nothing", "n/a", "-"):
        return
    with edit(p) as d:
        texts = get(d, tid).setdefault("misled", {}).setdefault(role, [])
        if t not in texts:
            texts.append(t)


def tasks(d: Data) -> list[Task]:
    """Every task."""
    return list(d["tasks"])


def in_flight(d: Data) -> int:
    """Tasks a pause leaves working: an agent on them, or in review."""
    return sum(1 for i in tasks(d) if i["status"] in (*RUNNING, "in review"))


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


class Chain(TypedDict):
    """What makes a parent a chain: its lens, the cap of steps and the size budget of each step."""

    lens: str
    max_steps: int
    max_lines: int


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
    type: str
    repo: str
    chain: Chain


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
        for k in ("model", "effort", "type", "repo", "chain"):
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
