"""The backlog: one `backlog.json` in today's foreman format (schema 5: issues with id XX-n, title, status, detail,
acceptance {claim, criterion, verdict}, result, priority, relations; decisions for the owner), with the keys the new
model needs. This module is its only writer; every write is under the file lock and atomic.

Statuses, and who moves an issue between them (DESIGN.md has the picture):
  triage       filed by a health check or a source; waits for the owner's «go» (→ backlog or ready)
  backlog      to define: the loop launches a defining agent
  refining     a defining agent works on it; it ends with `fm ready` (goal + done criterion)
  ready        the loop launches the task's responsible agent
  in progress  the responsible agent works on it; it ends with `fm done`
  in review    in the integration queue: checked (tests, diff, risk); the owner accepts, or the autonomy level does
  done | canceled | duplicate

Keys added to an issue: `agent` {name, session, short, kind: define|work, started, nudged}, `branch`, `worktree`,
`report` {summary, review, confused, at}, `check` {tests, files, lines, risk, reasons, at}, `rework` (times it went
back to its agent), `source` (the dedup key of a finding or signal), `blocked` (why the loop leaves it alone).
A question to the owner is a decision tied to its issue and agent: {id: "XX-1 Q1", issue, agent, title, text,
kind: question|permission, action?}; its answer goes back to that agent, never to a coordinator.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Iterator
from datetime import date
from typing import Any

from . import util
from .config import Project

STATUSES = ("triage", "backlog", "refining", "ready", "in progress", "in review", "done", "canceled", "duplicate")
CLOSED = ("done", "canceled", "duplicate")
RUNNING = ("refining", "in progress")
PRIORITIES = ("urgent", "high", "medium", "low", "none")
EMPTY = {"schema_version": 5, "updated": "", "projects": [], "issues": [], "archive": [], "decisions": [],
         "decisions_resolved": []}

Issue = dict[str, Any]
Data = dict[str, Any]


class BacklogError(Exception):
    pass


def load(p: Project) -> Data:
    d = util.read_json(p.backlog_file, None) or {k: (list(v) if isinstance(v, list) else v) for k, v in EMPTY.items()}
    if d.get("schema_version") != 5:
        raise BacklogError(f"{p.backlog_file}: schema {d.get('schema_version')!r}, expected 5")
    for k, v in EMPTY.items():
        d.setdefault(k, list(v) if isinstance(v, list) else v)
    return d


@contextlib.contextmanager
def edit(p: Project) -> Iterator[Data]:
    with util.file_lock(p.backlog_file):
        d = load(p)
        yield d
        d["updated"] = date.today().isoformat()
        util.write_json(p.backlog_file, d)


def issues(d: Data) -> list[Issue]:
    """Every issue: the product's own and those inside its projects (the format allows both)."""
    return list(d["issues"]) + [i for pr in d["projects"] for i in pr.get("issues", [])]


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


def add(p: Project, title: str, *, detail: str = "", status: str = "backlog", criterion: str = "",
        claim: str = "", source: str | None = None, priority: str = "none") -> str:
    if status not in ("triage", "backlog", "ready"):
        raise BacklogError("a new issue starts in triage, backlog or ready")
    if status == "ready" and not criterion:
        raise BacklogError("a ready issue needs a done criterion (--criterion)")
    with edit(p) as d:
        if source and any(i.get("source") == source and i["status"] not in CLOSED for i in issues(d)):
            return ""
        iid = new_id(p, d)
        issue: Issue = {"id": iid, "title": title, "status": status, "blocked": "", "plan": [], "subs": [],
                        "detail": detail, "priority": priority, "created": util.now()}
        if criterion or claim:
            issue["acceptance"] = {"claim": claim, "criterion": criterion}
        if source:
            issue["source"] = source
        d["issues"].append(issue)
    event(p, iid, f"filed ({status}): {title}")
    return iid


def set_status(p: Project, iid: str, status: str, why: str = "", **keys: Any) -> Issue:
    if status not in STATUSES:
        raise BacklogError(f"bad status {status!r}")
    with edit(p) as d:
        i = get(d, iid)
        i["status"] = status
        i.update(keys)
        if status in CLOSED:
            i["closed"] = date.today().isoformat()
    event(p, iid, f"→ {status}" + (f": {why}" if why else ""))
    return i


def update(p: Project, iid: str, **keys: Any) -> Issue:
    with edit(p) as d:
        i = get(d, iid)
        i.update(keys)
    return i


def lesson(p: Project, iid: str, who: str, text: str) -> None:
    """What misled an agent, kept in the project's LESSONS.md for the next agents (the owner prunes it)."""
    t = " ".join((text or "").split())
    if not t or re.match(r"(?i)(none|nothing|n/a|-)\b", t):
        return
    f = ensure_lessons(p)
    with util.file_lock(f):
        util.write_text_atomic(f, f.read_text(encoding="utf-8") + f"- {date.today().isoformat()} {iid} ({who}): {t}\n")


def ensure_lessons(p: Project) -> Any:
    if not p.lessons_file.exists():
        util.write_text_atomic(p.lessons_file, "# Lessons\n\nWhat misled earlier agents in this project, in their "
                               "words. The owner prunes it; agents read it before starting.\n\n")
    return p.lessons_file


def open_questions(d: Data, iid: str | None = None) -> list[dict[str, Any]]:
    return [q for q in d["decisions"] if q.get("issue") and (iid is None or q["issue"] == iid)]


def ask(p: Project, iid: str, agent: str, text: str, kind: str = "question", action: str = "") -> str:
    with edit(p) as d:
        i = get(d, iid)
        n = sum(1 for q in d["decisions"] + d["decisions_resolved"] if q.get("issue") == iid) + 1
        qid = f"{iid} Q{n}"
        q = {"id": qid, "issue": iid, "agent": agent, "title": i["title"], "text": text, "options": [],
             "date": date.today().isoformat(), "asked": util.now(), "kind": kind}
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
        q.update({"answer": text, "resolved": date.today().isoformat(), "by": f"{by}, {util.now()}",
                  "free": True, "delivered": False})
        d["decisions_resolved"].append(q)
    event(p, q["issue"], f"owner answered {qid}: {text}")
    return q


def note(p: Project, iid: str, text: str, source: str) -> None:
    """A note from outside fm for the issue's agent: delivered like an owner's answer (loop.deliver_answers)."""
    with edit(p) as d:
        i = get(d, iid)
        n = sum(1 for q in d["decisions_resolved"] if q.get("issue") == iid and q.get("kind") == "note")
        d["decisions_resolved"].append({"id": f"{iid} N{n + 1}", "issue": iid, "kind": "note", "agent": (i.get(
            "agent") or {}).get("name"), "text": "signal", "answer": text, "source": source,
            "resolved": util.now()[:10], "delivered": False, "options": [], "title": i["title"]})
    event(p, iid, f"note from {source}: {text}")


def undelivered(d: Data) -> list[dict[str, Any]]:
    return [q for q in d["decisions_resolved"] if q.get("delivered") is False]


def mark_delivered(p: Project, qid: str) -> None:
    with edit(p) as d:
        for q in d["decisions_resolved"]:
            if q["id"] == qid:
                q["delivered"] = True
