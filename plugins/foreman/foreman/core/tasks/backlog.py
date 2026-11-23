"""The backlog: one `backlog.json` in today's foreman format (schema 5: tasks with id XX-n, title, status, detail,
acceptance {goal, criteria, verdict}, result, priority; decisions), plus new keys. Read and written through the backlog port
(`providers/backlog.py`); here, its vocabulary and the pure functions on its data.

Statuses, and who moves a task between them (DESIGN.md has the picture):
  triage       filed by a health check or a source; waits for the owner's «go» (→ backlog or ready)
  backlog      to plan (it has acceptance criteria): the loop launches a planning agent
  refining     a planning agent works on it; it ends with `foreman ready` (the plan, or «trivial»)
  ready        the loop launches the task's responsible agent
  in progress  the responsible agent works on it; it ends with `foreman done`
  in review    checked (tests, diff, risk) and reviewed; the owner accepts, or the autonomy does
  done | canceled | duplicate

Keys added to a task: `agent` {name, session, short, kind: planning|responsible, started, terminal, pane, waiting_seen}, `branch`, `worktree`,
`sessions` (every session id), `session_kinds` (sid → planning|responsible|review), `session_tabs` (sid → [terminal, pane]),
`report` {summary, review, confused, at}, `check` {tests, files, lines, risk, reasons, at}, `rework` (times it went back to its agent),
`plan_file`, `source`
(dedup key of a finding or signal), `blocked` (why the loop leaves it alone), `parent` (of a subtask), `misled` {planning|
responsible|hunter|reviewer: [what misled that agent]}, `repo` (the `[repos.K]` key of the repo it changes; several repos only).
A question to the owner is a decision tied to its task and agent: {id: "XX-1 Q1", task, agent, title, text, kind, action?}.
"""

import re
from typing import Any, Literal, NamedTuple, TypedDict, get_args

from ... import util
from ..project.config import Project

STATUSES = ("triage", "backlog", "refining", "ready", "in progress", "in review", "done", "canceled", "duplicate")
CLOSED = ("done", "canceled", "duplicate")
RUNNING = ("refining", "in progress")
PRIORITIES = ("urgent", "high", "medium", "low", "none")
Task = dict[str, Any]
Data = dict[str, Any]
RetryField = Literal["retry", "pr", "check_retry"]  # the prefixes of the `<prefix>_error` and `<prefix>_count` keys `loop.guarded` keeps
RETRY_FIELDS: tuple[RetryField, ...] = get_args(RetryField)


def unblocked() -> dict[str, Any]:
    """The keys an unblock sets: the block gone and every `loop.guarded` failure count back to nothing."""
    return {"blocked": "", **{f"{f}_error": "" for f in RETRY_FIELDS}, **{f"{f}_count": 0 for f in RETRY_FIELDS}}


class BacklogError(Exception):
    pass


class NoSnapshot(BacklogError):
    """A view of the Board before the loop has read it."""


class Local(NamedTuple):
    """A task's keys that only the machine running it knows."""

    worktree: str = ""
    branch: str = ""


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


def id_number(prefix: str, tid: str) -> int | None:
    """The number of a task id `PREFIX-<digits>`; None for any other id."""
    m = re.fullmatch(rf"{re.escape(prefix)}-(\d+)", tid)
    return int(m[1]) if m else None


def new_id(p: Project, d: Data) -> str:
    prefix = p.get("project", "prefix")
    nums = [n for i in tasks(d) if (n := id_number(prefix, i["id"])) is not None]
    return f"{prefix}-{max(nums, default=0) + 1}"


STATUS_KINDS = {"in review": "built"}  # the kind of the event a status move writes; any other move is `other`
KINDS = ("planned", "built", "rejected", "conflict", "failing", "review", "session", "accepted", "merged", "other")


def event(p: Project, tid: str | None, text: str, kind: str = "other") -> None:
    util.append_jsonl(p.state / "events.jsonl", {"time": util.now(), "task": tid, "text": text, "kind": kind})


class Event(TypedDict):
    """One step of a task's path: when, which kind (one of `KINDS`) and its text."""

    at: str
    kind: str
    text: str


def history(p: Project, tid: str) -> list[Event]:
    """The events of a task in time order; a row without a kind (written before kinds) is `other`, never dropped."""
    return [
        {"at": str(r["time"]), "kind": r["kind"] if r.get("kind") in KINDS else "other", "text": str(r["text"])}
        for r in util.read_jsonl(p.state / "events.jsonl")
        if r.get("task") == tid
    ]


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
