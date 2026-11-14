"""The mechanic: a helper session that diagnoses each triage task health filed. Its only write is `fm diagnose`."""

from typing import Any

from . import agents, backlog, diagnosis, proc, sessions, util
from .agents import idle_s
from .config import Project
from .talk import NUDGE_IDLE_S

TIMEOUT_S = 1800


def missing_why(m: dict[str, Any], rows: dict[str, Any]) -> str:
    if (util.age_s(m.get("started")) or 0) > TIMEOUT_S:
        return "ran longer than 30 minutes"
    if rows and m.get("session") and not sessions.busy(rows, m["session"]) and idle_s(m) > NUDGE_IDLE_S:
        return "ended without writing a diagnosis"
    return ""


def note_missing(p: Project, tid: str, why: str) -> None:
    diagnosis.record(p, tid, "Diagnosis missing", f"The mechanic {why}: the diagnosis is missing.", missing=why)
    backlog.event(p, tid, f"mechanic: diagnosis missing ({why})")


def launch(p: Project, i: dict[str, Any]) -> None:
    tid = i["id"]
    name = f"{tid.lower()}-mechanic"
    model, effort = p.get("agents", "planning_model"), p.get("agents", "planning_effort")
    out = p.home / "diagnoses" / f"{tid}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    brief = agents.brief(p, i, "mechanic", {"state": str(p.state), "diagnosis": str(out)})
    try:
        short, sid = sessions.start(p, sessions.Agent(tid, name, model, effort), brief, p.repo)
    except sessions.SessionError as e:
        note_missing(p, tid, f"could not launch: {e}")
        return
    m = {"name": name, "short": short, "session": sid, "kind": "mechanic", "model": model, "effort": effort, "started": util.now(), "cwd": str(p.repo)}
    with backlog.edit(p) as d:
        it = backlog.get(d, tid)
        still = it["status"] == "triage"
        if still:
            it["mechanic"] = m
            if sid:
                agents.record_session(it, sid, "mechanic")
    if not still:
        try:
            sessions.stop(p, short)
        except proc.ProcError as e:
            backlog.event(p, tid, f"could not stop session {short}: {e}")
        return
    backlog.event(p, tid, f"launched {name} (mechanic, {model}, session {short})")


def run(p: Project, rows: dict[str, Any]) -> None:
    for i in backlog.tasks(backlog.load(p)):
        if agents.diagnosing(i) and (why := missing_why(i["mechanic"], rows)):
            note_missing(p, i["id"], why)
    d = backlog.load(p)
    for i in backlog.tasks(d):
        if i.get("diagnosis") and i.get("mechanic") and sessions.state(rows, i["mechanic"].get("session")) not in ("stopped", "gone"):
            sessions.stop_live(p, i["id"], i["mechanic"], rows)
    if (p.state / "paused").exists():
        return
    free = int(p.get("agents", "slots")) - agents.running(d)
    health = (i for i in backlog.tasks(d) if i["status"] == "triage" and str(i.get("source", "")).startswith("health:"))
    todo = [i for i in health if not i.get("mechanic") and not i.get("diagnosis")]
    for i in todo[: max(0, free)]:
        launch(p, i)
