"""The panel's read side: what each JSON view shows (projects, state, issue, system), with a few seconds of memo for the slow parts."""

import threading
import time
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

from . import backlog, costs, health, lock, metrics, pulls, sessions, util
from .config import Project, registered

RECENT_DAYS = 7  # closed issues the tasks screen still shows
Obj = dict[str, Any]

# ---------- what is slow, kept a few seconds ----------

_memo: dict[Any, tuple[float, Any]] = {}
_memo_lock = threading.Lock()


def memo(key: Any, ttl: float, fn: Callable[[], Any]) -> Any:
    with _memo_lock:
        hit = _memo.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
    value = fn()
    with _memo_lock:
        _memo[key] = (time.time(), value)
    return value


def forget() -> None:
    with _memo_lock:
        _memo.clear()


def session_rows(p: Project) -> dict[str, Any]:
    """`claude agents` (a subprocess): every 10 s at most."""
    return memo(("sessions", p.get("agents", "claude")), 10, lambda: sessions.listing(p))


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
    age = time.time() - float(hb.get("epoch") or 0)
    return {"time": hb.get("time"), "alive": age < 3 * int(p.get("health", "loop_seconds"))}


def paused(p: Project) -> str | None:
    f = p.state / "paused"
    return f.read_text().strip() or "yes" if f.exists() else None


def needs(d: Obj) -> int:
    """What waits for the owner: open questions, the queue, triage, blocked issues."""
    waiting = [i for i in backlog.issues(d) if i["status"] in ("in review", "triage") or (i.get("blocked") and i["status"] not in backlog.CLOSED)]
    return len(d["decisions"]) + len(waiting)


def summary(p: Project) -> Obj:
    try:
        d = backlog.load(p)
        return {
            "name": p.name,
            "paused": paused(p),
            "loop": loop_beat(p),
            "needs": needs(d),
            "running": sum(1 for i in backlog.issues(d) if i["status"] in backlog.RUNNING),
        }
    except Exception as e:
        return {"name": p.name, "broken": str(e)}


def with_session(p: Project, i: Obj, rows: dict[str, Any]) -> Obj:
    a: backlog.Agent = i.get("agent") or {}
    return {**i, "agent_state": sessions.state(rows, a.get("session")) if a else None}


def state(p: Project) -> Obj:
    d = backlog.load(p)
    since = (date.today() - timedelta(days=RECENT_DAYS)).isoformat()
    rows = session_rows(p)
    shown = [with_session(p, i, rows) for i in backlog.issues(d) if i["status"] not in backlog.CLOSED or (i.get("closed") or "") >= since]
    events = util.read_jsonl(p.state / "events.jsonl")[-20:][::-1]
    return {"name": p.name, "paused": paused(p), "loop": loop_beat(p), "issues": shown, "questions": d["decisions"], "events": events}


def issue(p: Project, iid: str) -> Obj:
    d = backlog.load(p)
    i = backlog.get(d, iid)
    sids = [s for s in i.get("sessions", []) if s]
    c = memo(("cost", tuple(sids)), 30, lambda: costs.of_sessions(sids))
    shown = with_session(p, i, session_rows(p))
    if i.get("report"):
        check: Obj = i.get("check") or {}
        shown["description"] = pulls.description(p, i, check.get("head", ""), check.get("at", ""))
    return {
        "issue": shown,
        "events": [e for e in util.read_jsonl(p.state / "events.jsonl") if e.get("issue") == iid][::-1],
        "questions": [q for q in d["decisions"] + d["decisions_resolved"] if q.get("issue") == iid],
        "cost": {"usd": c["usd"], "minutes": c["minutes"]},
    }


def system(p: Project) -> Obj:
    found = memo(("health", str(p.home)), 60, lambda: health.findings(p, session_rows(p), outside=True, probe=False))
    rows = memo(("metrics", str(p.home)), 30, lambda: metrics.per_issue(p))
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
