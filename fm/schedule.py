"""Recurring entries, the I/O around `recurring`: each tick files a task for every entry that is due. `state/recurring.json`
holds when fm first saw each entry, where its count or period starts until a task of its own closes."""

from datetime import datetime
from typing import Any

from . import util
from .core.project.config import Project
from .core.tasks import backlog, recurring


def _context(p: Project) -> tuple[list[backlog.Task], dict[str, str]]:
    return backlog.tasks(backlog.load(p)), recurring.closed_at(util.read_jsonl(p.state / "events.jsonl"))


def run(p: Project, now: datetime | None = None) -> list[str]:
    """Files one task for each due entry; the ids filed."""
    entries: dict[str, dict[str, Any]] = p.cfg["recurring"]
    if not entries:
        (p.state / "recurring.json").unlink(missing_ok=True)  # the last entry was removed: nothing stays
        return []
    now = now or datetime.now()
    with util.locked_json(p.state / "recurring.json", {}) as seen:
        for name in [n for n in seen if n not in entries]:
            del seen[name]  # a removed entry stops, and a re-added one starts afresh
        for name in entries:
            seen.setdefault(name, now.strftime(recurring.STAMP))
        tasks, closed = _context(p)
        due = [(e, st) for e in entries.values() if (st := recurring.status(e, tasks, closed, seen[e["name"]], now)).due]
    filed = [backlog.add(p, e["title"], status="triage" if e["triage"] else "backlog", **_fields(e, st, now)) for e, st in due]
    return [tid for tid in filed if tid]


def _fields(e: dict[str, Any], st: recurring.Status, now: datetime) -> dict[str, Any]:
    detail = "\n\n".join(t for t in (e["detail"], recurring.covers(e, st, now)) if t)
    return {
        "goal": e["goal"],
        "criteria": e["criteria"],
        "model": e["model"],
        "effort": e["effort"],
        "type": e["type"],
        "source": recurring.SOURCE + e["name"],
        "detail": detail,
    }


def rows(p: Project, now: datetime | None = None) -> list[dict[str, Any]]:
    """Each entry as health and the panel show it. Reads only."""
    entries: dict[str, dict[str, Any]] = p.cfg["recurring"]
    now = now or datetime.now()
    seen = util.read_json(p.state / "recurring.json", {})
    tasks, closed = _context(p)
    out: list[dict[str, Any]] = []
    for e in entries.values():
        st = recurring.status(e, tasks, closed, seen.get(e["name"], now.strftime(recurring.STAMP)), now)
        trigger = f"every {e['every_merges']} merges" if e["every_merges"] else f"every {e['every']}"
        out.append(
            {
                "name": e["name"],
                "title": e["title"],
                "trigger": trigger,
                "open": st.open,
                "left": st.left if e["every_merges"] else None,
                "next": st.next if e["every"] and not st.open else None,
            }
        )
    return out


def line(r: dict[str, Any]) -> str:
    """One entry for `fm health`."""
    head = f"recurring {r['name']}: {r['trigger']}"
    if r["open"]:
        return f"{head}; waits for {r['open']} to close"
    return f"{head}; {r['left']} merges left" if r["left"] is not None else f"{head}; next due {r['next']}"
