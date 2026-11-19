"""The mechanic: a helper session that diagnoses each triage task health filed. Its only write is `foreman diagnose`."""

from typing import Any

from .. import util
from ..core.oversight import diagnosis
from ..core.project.config import Project
from ..core.tasks import backlog
from ..providers import registry
from ..providers.runner import Agent, Rows, RunnerError, State, ref_of, stop_live
from ..services.agents.brief import brief
from ..services.agents.launch import record_session
from ..services.agents.running import diagnosing, idle_s, running
from .talk import NUDGE_IDLE_S

TIMEOUT_S = 1800


def missing_why(m: dict[str, Any], rows: Rows) -> str:
    if (util.age_s(m.get("started")) or 0) > TIMEOUT_S:
        return "ran longer than 30 minutes"
    if rows.has_sessions() and m.get("session") and not rows.busy(ref_of(m)) and idle_s(m) > NUDGE_IDLE_S:
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
    text = brief(p, i, "mechanic", {"state": str(p.state), "diagnosis": str(out)})
    runner = registry.runner(p)
    try:
        ref = runner.start(Agent(tid, name, model, effort, util.session_title(tid, "mechanic", i["title"])), text, p.repo)
    except RunnerError as e:
        note_missing(p, tid, f"could not launch: {e}")
        return
    m = {"name": name, **ref.fields(), "kind": "mechanic", "model": model, "effort": effort, "started": util.now(), "cwd": str(p.repo)}
    with backlog.edit(p) as d:
        it = backlog.get(d, tid)
        still = it["status"] == "triage"
        if still:
            it["mechanic"] = m
            record_session(it, ref, "mechanic")
    if not still:
        try:
            runner.stop(ref)
        except RunnerError as e:
            backlog.event(p, tid, f"could not stop session {ref.short}: {e}")
        return
    backlog.event(p, tid, f"launched {name} (mechanic, {model}, session {ref.short})")


def run(p: Project, rows: Rows) -> None:
    for i in backlog.tasks(backlog.load(p)):
        if diagnosing(i) and (why := missing_why(i["mechanic"], rows)):
            note_missing(p, i["id"], why)
    d = backlog.load(p)
    for i in backlog.tasks(d):
        if i.get("diagnosis") and i.get("mechanic") and rows.state(ref_of(i["mechanic"])) not in (State.ENDED, State.GONE):
            stop_live(p, i["id"], ref_of(i["mechanic"]), registry.runner(p), rows)
    if p.paused:
        return
    free = int(p.get("agents", "slots")) - running(d)
    health = (i for i in backlog.tasks(d) if i["status"] == "triage" and str(i.get("source", "")).startswith("health:"))
    todo = [i for i in health if not i.get("mechanic") and not i.get("diagnosis")]
    for i in todo[: max(0, free)]:
        launch(p, i)
