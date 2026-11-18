"""The panel's read side: what each JSON view shows (projects, state, task, system)."""

from datetime import date, timedelta
from typing import Any
from urllib.parse import quote

from ... import launcher, util
from ...core.oversight import costs
from ...core.project import registry
from ...core.project.config import Project
from ...core.review import evidence
from ...core.tasks import backlog, recurring, taskrepo, tasktypes
from ...services import lock
from ...services.claude import sessions
from ...services.delivery import pulls
from ...steps import metrics, schedule
from ...steps.health import checks, health

RECENT_DAYS = 7  # closed tasks the tasks screen still shows
Obj = dict[str, Any]


# ---------- the projects ----------


def projects() -> list[Project]:
    return registry.loaded()


def project(q: dict[str, list[str]]) -> Project:
    ps = projects()
    name = q.get("p", [""])[0]
    p = next((x for x in ps if x.name == name), None) if name else (ps[0] if ps else None)
    if p is None:
        raise KeyError(f"no project {name!r}" if name else "no project registered (foreman init)")
    return p


def loop_beat(p: Project) -> Obj:
    hb = checks.heartbeat(p)
    return {"time": hb.get("time"), "alive": not checks.loop_dead(p)}


def needs(d: Obj) -> int:
    """What waits for the owner: open questions, the queue, triage, blocked tasks."""
    waiting = [i for i in backlog.tasks(d) if i["status"] in ("in review", "triage") or (i.get("blocked") and i["status"] not in backlog.CLOSED)]
    return len(d["decisions"]) + len(waiting)


def summary(p: Project) -> Obj:
    try:
        d = backlog.load(p)
        return {
            "name": p.name,
            "paused": p.paused or None,
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
    all_tasks = backlog.tasks(d)
    recent = [i for i in all_tasks if i["status"] not in backlog.CLOSED or (i.get("closed") or "") >= since]
    shown = [recurring.shown(taskrepo.shown(tasktypes.shown(with_session(p, i, rows), all_tasks), all_tasks, p.several)) for i in recent]
    events = util.read_jsonl(p.state / "events.jsonl")[-20:][::-1]
    repos: Obj = {"repos": p.repo_keys} if p.several else {}
    return {"name": p.name, "paused": p.paused or None, "loop": loop_beat(p), "tasks": shown, "questions": d["decisions"], "events": events, **repos}


def task(p: Project, tid: str) -> Obj:
    d = backlog.load(p)
    i = backlog.get(d, tid)
    sids = [s for s in i.get("sessions", []) if s]
    c = costs.of_sessions(sids)
    shown = recurring.shown(taskrepo.shown(tasktypes.shown(with_session(p, i, sessions.listing(p)), backlog.tasks(d)), backlog.tasks(d), p.several))
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
    found = health.findings(p, sessions.listing(p), outside=True)
    rows = metrics.per_task(p)
    locks = [r.status() for r in lock.declared(p.cfg).values()]
    entries = schedule.rows(p)
    return {
        "name": p.name,
        "paused": p.paused or None,
        "in_flight": backlog.in_flight(backlog.load(p)),
        "loop": loop_beat(p),
        "findings": found,
        "metrics": rows,
        "total": round(sum(r["usd"] for r in rows), 2),
        "locks": locks,
        **({"recurring": entries} if entries else {}),
        "versions": versions(p),
    }


def versions(p: Project) -> Obj:
    try:
        now: str | None = launcher.installed().version
    except launcher.LauncherError:
        now = None
    return {"loop": checks.heartbeat(p).get("version"), "panel": launcher.ROOT.name, "installed": now}
