"""The panel's read side: what each JSON view shows (projects, state, task, system)."""

from datetime import date, timedelta
from typing import Any
from urllib.parse import quote

from . import backlog, costs, evidence, health, lock, metrics, pulls, sessions, util
from .config import Project, registered

RECENT_DAYS = 7  # closed tasks the tasks screen still shows
Obj = dict[str, Any]


# ---------- the projects ----------


def projects() -> list[Project]:
    out: list[Project] = []
    for home in registered():
        try:
            out.append(Project.load(home))
        except Exception:  # a broken fm.toml leaves the others served
            continue
    return out


def project(q: dict[str, list[str]]) -> Project:
    ps = projects()
    name = q.get("p", [""])[0]
    p = next((x for x in ps if x.name == name), None) if name else (ps[0] if ps else None)
    if p is None:
        raise KeyError(f"no project {name!r}" if name else "no project registered (fm init)")
    return p


def loop_beat(p: Project) -> Obj:
    hb = util.read_json(p.state / "loop.json", {})
    return {"time": hb.get("time"), "alive": not health.loop_dead(p)}


def paused(p: Project) -> str | None:
    f = p.state / "paused"
    return f.read_text().strip() or "yes" if f.exists() else None


def needs(d: Obj) -> int:
    """What waits for the owner: open questions, the queue, triage, blocked tasks."""
    waiting = [i for i in backlog.tasks(d) if i["status"] in ("in review", "triage") or (i.get("blocked") and i["status"] not in backlog.CLOSED)]
    return len(d["decisions"]) + len(waiting)


def summary(p: Project) -> Obj:
    try:
        d = backlog.load(p)
        return {
            "name": p.name,
            "paused": paused(p),
            "loop": loop_beat(p),
            "needs": needs(d),
            "running": sum(1 for i in backlog.tasks(d) if i["status"] in backlog.RUNNING),
        }
    except Exception as e:
        return {"name": p.name, "broken": str(e)}


def with_session(p: Project, i: Obj, rows: dict[str, Any]) -> Obj:
    a: backlog.Agent = i.get("agent") or {}
    return {**i, "agent_state": sessions.state(rows, a.get("session")) if a else None}


def state(p: Project) -> Obj:
    d = backlog.load(p)
    since = (date.today() - timedelta(days=RECENT_DAYS)).isoformat()
    rows = sessions.listing(p)
    shown = [with_session(p, i, rows) for i in backlog.tasks(d) if i["status"] not in backlog.CLOSED or (i.get("closed") or "") >= since]
    events = util.read_jsonl(p.state / "events.jsonl")[-20:][::-1]
    return {"name": p.name, "paused": paused(p), "loop": loop_beat(p), "tasks": shown, "questions": d["decisions"], "events": events}


def task(p: Project, tid: str) -> Obj:
    d = backlog.load(p)
    i = backlog.get(d, tid)
    sids = [s for s in i.get("sessions", []) if s]
    c = costs.of_sessions(sids)
    shown = with_session(p, i, sessions.listing(p))
    if i.get("report"):
        check: Obj = i.get("check") or {}
        text = pulls.description(p, i, check.get("head", ""), check.get("at", ""))
        urls = {n: f"/evidence?p={quote(p.name)}&id={quote(tid)}&name={quote(n)}" for n in pulls.evidence_files(p, tid, text)}
        shown["description"] = evidence.relink(text, urls)
    return {
        "task": shown,
        "events": [e for e in util.read_jsonl(p.state / "events.jsonl") if e.get("task") == tid][::-1],
        "questions": [q for q in d["decisions"] + d["decisions_resolved"] if q.get("task") == tid],
        "cost": {"usd": c["usd"], "minutes": c["minutes"]},
    }


def system(p: Project) -> Obj:
    found = health.findings(p, sessions.listing(p), outside=True, probe=False)
    rows = metrics.per_task(p)
    locks = [r.status() for r in lock.declared(p.cfg).values()]
    return {
        "name": p.name,
        "paused": paused(p),
        "loop": loop_beat(p),
        "findings": found,
        "metrics": rows,
        "total": round(sum(r["usd"] for r in rows), 2),
        "locks": locks,
    }
