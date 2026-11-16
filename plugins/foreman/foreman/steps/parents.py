"""The parent agent: a session per let-in parent task, with no worktree, that follows its subtasks with the owner. The
parent keeps status backlog while it lives; it is told when a subtask is merged or canceled (`notified` lists those
already told), and closes only by its own `foreman done` after the owner's yes."""

from typing import Any

from .. import util
from ..core.project.config import Project
from ..core.tasks import backlog, blockers, questions, subtasks
from ..services.agents.brief import agent_name, brief
from ..services.agents.launch import record_session
from ..services.claude import sessions


def launch(p: Project, i: dict[str, Any]) -> None:
    tid = i["id"]
    name = agent_name(p, tid, "parent")
    model, effort = p.get("agents", "planning_model"), p.get("agents", "planning_effort")
    try:
        title = util.session_title(tid, "parent", i["title"])
        short, sid = sessions.start(p, sessions.Agent(tid, name, model, effort, title), brief(p, i, "parent"), p.repo)
    except sessions.SessionError as e:
        backlog.update(p, tid, blocked=f"could not launch its agent: {e}")
        backlog.event(p, tid, f"launch failed: {e}")
        return
    agent = {"name": name, "short": short, "session": sid, "kind": "parent", "model": model, "effort": effort, "started": util.now(), "cwd": str(p.repo)}
    with backlog.edit(p) as d:
        it = backlog.get(d, tid)
        still = it["status"] == "backlog" and not it.get("blocked")
        if still:
            it["agent"] = agent
            it["notified"] = [s["id"] for s in subtasks.of(backlog.tasks(d), tid) if s["status"] in backlog.CLOSED]
            if sid:
                record_session(it, sid, "parent")
    if not still:  # the owner cancelled or changed it while the session started
        sessions.stop(p, short)
        backlog.event(p, tid, f"launch of {name} undone: the task changed meanwhile")
        return
    backlog.event(p, tid, f"launched {name} (parent, {model}, {effort}, session {short})")


def notify(p: Project) -> None:
    """Files a message for each parent agent per subtask merged or canceled since it was last told."""
    told: list[tuple[str, str]] = []
    with backlog.edit(p) as d:
        tasks = backlog.tasks(d)
        for i in tasks:
            agent: dict[str, str] = i.get("agent") or {}
            if i["status"] in backlog.CLOSED or agent.get("kind") != "parent":
                continue
            subs = subtasks.of(tasks, i["id"])
            for s in subs:
                if s["status"] in backlog.CLOSED and s["id"] not in i.get("notified", []):
                    questions.message(d, i, "S", ("subtask",), kind="subtask", text="subtask", answer=subtasks.notice(s, subs, i["id"]))
                    i["notified"] = [*i.get("notified", []), s["id"]]
                    told.append((i["id"], s["id"]))
    for tid, sid in told:
        backlog.event(p, tid, f"its agent is told {sid} closed")


def run(p: Project, rows: dict[str, Any]) -> None:
    d = backlog.load(p)
    for i in backlog.tasks(d):
        a: dict[str, Any] = i.get("agent") or {}
        if i["status"] in backlog.CLOSED and a.get("kind") == "parent" and sessions.state(rows, a.get("session")) not in ("stopped", "gone"):
            sessions.stop_live(p, i["id"], a, rows)
    notify(p)
    if (p.state / "paused").exists():
        return
    tasks = backlog.tasks(backlog.load(p))
    for i in tasks:
        if i["status"] == "backlog" and not i.get("blocked") and not i.get("agent") and subtasks.of(tasks, i["id"]) and not blockers.pending(i, tasks):
            launch(p, i)


def finish(p: Project, tid: str, summary: str) -> str:
    """Closes a parent from its agent's `foreman done`: done if any subtask is done, else canceled; refused while one is open."""
    subs = subtasks.of(backlog.tasks(backlog.load(p)), tid)
    status = subtasks.outcome(subs)
    if not status:
        open_ = ", ".join(s["id"] for s in subs if s["status"] not in backlog.CLOSED)
        raise backlog.BacklogError(f"{tid} still has open subtasks: {open_}; it closes once every one is done or canceled")
    backlog.set_status(p, tid, status, summary)
    return status
