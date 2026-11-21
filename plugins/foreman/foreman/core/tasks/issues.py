"""An issue of the Board's repo as fm's task (DESIGN.md «Every key of a task»). Pure: the adapter reads GitHub's answer into
`Issue`, typed at its border, and these functions make the task dict the port hands out."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime

from .backlog import CLOSED, STATUSES, BacklogError, Task
from .ids import task_id

CLOSED_AS = {"COMPLETED": "done", "NOT_PLANNED": "canceled", "DUPLICATE": "duplicate"}  # other closed reasons: closed undone
LEGACY_MARK = re.compile(r"<!-- foreman:legacy-id=[^>]*-->")


@dataclass(frozen=True)
class Linked:
    """An issue another one points at (a blocker), with what the read brings of it."""

    number: int
    title: str
    state: str  # OPEN | CLOSED
    reason: str  # COMPLETED | NOT_PLANNED | DUPLICATE | ""
    created: str  # ISO, UTC
    closed: str  # ISO, UTC; "" while open


@dataclass(frozen=True)
class Issue:
    number: int
    title: str
    body: str
    state: str
    reason: str
    created: str
    closed: str
    parent: int | None
    blockers: tuple[Linked, ...]
    fields: dict[str, str | int]  # the Board fields set on its card, by name
    carded: bool  # it has a card on the project's Board


def body_parts(body: str) -> tuple[str, str, str]:
    """(goal, criteria, detail) of an issue body: `## Goal`, `## Acceptance criteria` (one `- ` bullet per line) and
    `## Detail` (to the end); a body with none of them is all detail. The legacy-id marker line is dropped."""
    lines = body.replace("\r\n", "\n").split("\n")
    if lines and LEGACY_MARK.fullmatch(lines[0].strip()):
        lines = lines[1:]
    heads = {"## Goal": "goal", "## Acceptance criteria": "criteria", "## Detail": "detail"}
    if not any(ln.rstrip() in heads for ln in lines):
        return "", "", "\n".join(lines).strip()
    parts: dict[str, list[str]] = {"goal": [], "criteria": [], "detail": []}
    at = ""
    for ln in lines:
        if at != "detail" and (ln.rstrip() in heads or ln.startswith("## ")):
            at = heads.get(ln.rstrip(), "")
            continue
        if at:
            parts[at].append(ln)
    criteria = [ln.removeprefix("- ") for ln in "\n".join(parts["criteria"]).strip().split("\n")]
    return "\n".join(parts["goal"]).strip(), "\n".join(criteria).strip(), "\n".join(parts["detail"]).strip()


def _local(iso: str, fmt: str) -> str:
    return datetime.fromisoformat(iso).astimezone().strftime(fmt)


def _closed(iso: str) -> str:
    """The local date it closed; today when GitHub gives none (an open issue, or a closed one with no `closedAt`)."""
    return _local(iso, "%Y-%m-%d") if iso else date.today().isoformat()


def _closed_status(reason: str) -> str:
    return CLOSED_AS.get(reason, "canceled")


def _status(i: Issue) -> str:
    name = i.fields.get("Status")
    if name is None:
        return "triage" if i.state == "OPEN" else _closed_status(i.reason)
    if str(name).lower() not in STATUSES:
        raise BacklogError(f"#{i.number}: Status {name!r} is not one of fm's")
    return str(name).lower()


def to_task(i: Issue, prefix: str, legacy: Mapping[str, int]) -> Task:
    """The task the issue and its card hold; a key is set only when the issue has it, as the local file keeps them."""
    f = i.fields
    goal, criteria, detail = body_parts(i.body)
    t: Task = {
        "id": task_id(prefix, i.number, legacy),
        "title": i.title,
        "status": _status(i),
        "blocked": str(f.get("Blocked", "")),
        "detail": detail,
        "priority": str(f.get("Priority", "none")),
        "created": _local(i.created, "%Y-%m-%d %H:%M:%S"),
    }
    if t["status"] in CLOSED:  # also a card at a closed Status on an open issue, so the recent list shows it
        t["closed"] = _closed(i.closed)
    if goal or criteria:
        t["acceptance"] = {"goal": goal, "criteria": criteria}
    for key, name in (("model", "Model"), ("effort", "Effort"), ("type", "Task type"), ("repo", "Repo key"), ("source", "Source")):
        if name in f:
            t[key] = str(f[name])
    if i.parent is not None:
        t["parent"] = task_id(prefix, i.parent, legacy)
    if i.blockers:
        t["blocked_by"] = [task_id(prefix, b.number, legacy) for b in i.blockers]
    if "Lens" in f:
        t["chain"] = {"lens": str(f["Lens"]), "max_steps": int(f.get("Max steps", 0)), "max_lines": int(f.get("Max lines", 0))}
    return t


def stub(b: Linked, prefix: str, legacy: Mapping[str, int]) -> Task:
    """A closed blocker as a task, so a task's blockers read as done or closed undone."""
    return {
        "id": task_id(prefix, b.number, legacy),
        "title": b.title,
        "status": _closed_status(b.reason),
        "detail": "",
        "priority": "none",
        "blocked": "",
        "created": _local(b.created, "%Y-%m-%d %H:%M:%S"),
        "closed": _closed(b.closed),
    }
