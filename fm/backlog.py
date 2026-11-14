"""The backlog: one `backlog.json` in today's foreman format (schema 5: issues with id XX-n, title, status, detail,
acceptance {claim, criteria, verdict}, result, priority; decisions), plus new keys. Its only writer, under the file lock.

Statuses, and who moves an issue between them (DESIGN.md has the picture):
  triage       filed by a health check or a source; waits for the owner's «go» (→ backlog or ready)
  backlog      to plan (it has acceptance criteria): the loop launches a planning agent
  refining     a planning agent works on it; it ends with `fm ready` (the plan, or «trivial»)
  ready        the loop launches the task's responsible agent
  in progress  the responsible agent works on it; it ends with `fm done`
  in review    in the integration queue: checked (tests, diff, risk); the owner accepts, or the autonomy level does; then done | canceled | duplicate

Keys added to an issue: `agent` {name, session, short, kind: define|work, started, nudged}, `branch`, `worktree`,
`sessions` (every session id), `session_kinds` (sid → define|work|review), `report` {summary, review, confused, at},
`check` {tests, files, lines, risk, reasons, at}, `rework` (times it went back to its agent), `plan_file`, `plan_approved` {by, at},
`source` (dedup key of a finding or signal), `blocked` (why the loop leaves it alone), `parent` (of a subtask).
A question to the owner is a decision tied to its issue and agent: {id: "XX-1 Q1", issue, agent, title, text, kind, action?}.
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
Issue = dict[str, Any]
Data = dict[str, Any]


class BacklogError(Exception):
    pass


class Agent(TypedDict, total=False):
    """An issue's `agent`: the session working on it."""

    name: str
    short: str
    session: str
    cwd: str


class Acceptance(TypedDict, total=False):
    claim: str
    criteria: str
    verdict: str


class Report(TypedDict, total=False):
    summary: str
    review: str
    confused: str


class Reviewer(TypedDict, total=False):
    """An issue's `reviewer`: the newest review launched (`reviewers` keeps every launch, same shape)."""

    round: int
    tip: str


class Review(TypedDict):
    """An issue's `review`: a reviewer's verdict."""

    round: int
    tip: str
    open: NotRequired[int]  # verdicts from before it carry high/medium/low instead: see open_findings
    sent_back: NotRequired[bool]


def open_findings(v: Mapping[str, Any]) -> int:
    """Findings to fix. Verdicts from before the single count carried H/M/L: their H and M were the ones to fix."""
    return int(v["open"]) if "open" in v else int(v.get("high", 0)) + int(v.get("medium", 0))


def review_round(i: Mapping[str, Any]) -> int:
    """The round the next review is: reviews since the owner last asked for a fresh one (`review_from`), plus one."""
    return len(i.get("reviews") or []) - int(i.get("review_from", 0)) + 1


class Check(TypedDict, total=False):
    head: str
    risk: str


def empty() -> Data:
    return {"schema_version": 5, "updated": "", "issues": [], "decisions": [], "decisions_resolved": []}


def load(p: Project) -> Data:
    d: Data = util.read_json(p.backlog_file, None) or empty()
    if d.get("schema_version") != 5:
        raise BacklogError(f"{p.backlog_file}: schema {d.get('schema_version')!r}, expected 5")
    for k, v in empty().items():
        d.setdefault(k, v)
    for i in d["issues"]:  # tasks stored before the rename carry `criterion`
        acceptance = i.get("acceptance")
        if acceptance and "criterion" in acceptance:
            acceptance.setdefault("criteria", acceptance.pop("criterion"))
    return d


@contextlib.contextmanager
def edit(p: Project) -> Generator[Data, None, None]:
    with util.file_lock(p.backlog_file):
        d = load(p)
        yield d
        d["updated"] = date.today().isoformat()
        util.write_json(p.backlog_file, d)


def issues(d: Data) -> list[Issue]:
    """Every issue."""
    return list(d["issues"])


def get(d: Data, iid: str) -> Issue:
    for i in issues(d):
        if i["id"] == iid:
            return i
    raise BacklogError(f"no issue {iid}")


def new_id(p: Project, d: Data) -> str:
    prefix = p.get("project", "prefix")
    pat = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
    nums = [int(m.group(1)) for i in issues(d) if (m := pat.match(i["id"]))]
    return f"{prefix}-{max(nums, default=0) + 1}"


def event(p: Project, iid: str | None, text: str) -> None:
    util.append_jsonl(p.state / "events.jsonl", {"time": util.now(), "issue": iid, "text": text})


class NewIssue(TypedDict, total=False):
    """The optional fields of a new issue."""

    detail: str
    criteria: str
    claim: str
    source: str | None
    priority: str
    parent: str


class Question(TypedDict, total=False):
    kind: str
    action: str


def add(p: Project, title: str, *, status: str = "backlog", **f: Unpack[NewIssue]) -> str:
    detail, criteria, claim = f.get("detail", ""), f.get("criteria", ""), f.get("claim", "")
    source, priority = f.get("source"), f.get("priority", "none")
    if status not in ("triage", "backlog", "ready"):
        raise BacklogError("a new issue starts in triage, backlog or ready")
    if not criteria:
        status = "triage"  # only a task with acceptance criteria can be let in
    with edit(p) as d:
        if source and any(i.get("source") == source and i["status"] not in CLOSED for i in issues(d)):
            return ""
        iid = new_id(p, d)
        issue: Issue = {
            "id": iid,
            "title": title,
            "status": status,
            "blocked": "",
            "detail": detail,
            "priority": priority,
            "created": util.now(),
        }
        if criteria or claim:
            issue["acceptance"] = {"claim": claim, "criteria": criteria}
        if source:
            issue["source"] = source
        if parent := f.get("parent"):
            issue["parent"] = parent
        d["issues"].append(issue)
    event(p, iid, f"filed ({status}): {title}")
    return iid


def set_status(p: Project, iid: str, status: str, why: str = "", **keys: Any) -> Issue:
    if status not in STATUSES:
        raise BacklogError(f"bad status {status!r}")
    with edit(p) as d:
        i = get(d, iid)
        if status == "canceled" and i["status"] in CLOSED:  # closed meanwhile (merged): keep it
            return i
        i.update(keys, status=status)
        if status in CLOSED:
            i["closed"] = date.today().isoformat()
    event(p, iid, f"→ {status}" + (f": {why}" if why else ""))
    return i


def update(p: Project, iid: str, **keys: Any) -> Issue:
    with edit(p) as d:
        i = get(d, iid)
        i.update(keys)
    return i


def open_questions(d: Data, iid: str | None = None) -> list[dict[str, Any]]:
    return [q for q in d["decisions"] if q.get("issue") and (iid is None or q["issue"] == iid)]


def ask(p: Project, iid: str, agent: str, text: str, **f: Unpack[Question]) -> str:
    kind, action = f.get("kind", "question"), f.get("action", "")
    with edit(p) as d:
        i = get(d, iid)
        n = sum(1 for q in d["decisions"] + d["decisions_resolved"] if q.get("issue") == iid) + 1
        qid = f"{iid} Q{n}"
        q: dict[str, Any] = {
            "id": qid,
            "issue": iid,
            "agent": agent,
            "title": i["title"],
            "text": text,
            "asked": util.now(),
            "kind": kind,
        }
        if action:
            q["action"] = action
        d["decisions"].append(q)
    event(p, iid, f"asks the owner ({kind}): {text}")
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
    event(p, q["issue"], f"owner answered {qid}: {text}")
    return q


def message(d: dict[str, Any], i: dict[str, Any], tag: str, kinds: tuple[str, ...], **fields: Any) -> None:
    """Files a message for the issue's agent among the resolved decisions, id `<issue> <tag><n>` (n counts the issue's
    entries of `kinds`); the loop delivers it (loop.deliver_answers)."""
    n = sum(1 for q in d["decisions_resolved"] if q.get("issue") == i["id"] and q.get("kind") in kinds)
    agent: Agent = i.get("agent") or {}
    d["decisions_resolved"].append(
        {
            "id": f"{i['id']} {tag}{n + 1}",
            "issue": i["id"],
            "agent": agent.get("name"),
            "resolved": util.now()[:10],
            "delivered": False,
            "answered_at": util.now(),
            "title": i["title"],
            **fields,
        }
    )


def note(p: Project, iid: str, text: str, source: str) -> None:
    """A note from outside fm for the issue's agent: delivered like an owner's answer (loop.deliver_answers)."""
    with edit(p) as d:
        message(d, get(d, iid), "N", ("note",), kind="note", text="signal", answer=text, source=source)
    event(p, iid, f"note from {source}: {text}")


def undelivered(d: Data) -> list[dict[str, Any]]:
    return [q for q in d["decisions_resolved"] if q.get("delivered") is False]


def mark_delivered(p: Project, qid: str) -> None:
    with edit(p) as d:
        for q in d["decisions_resolved"]:
            if q["id"] == qid:
                q["delivered"] = True
