"""The parent agent: a session per let-in parent task, with no worktree, that follows its subtasks with the owner. The
parent keeps status backlog while it lives; it is told when a subtask is merged or canceled (`notified` lists those
already told), and closes only by its own `foreman done` after the owner's yes. A parent whose session ended gets a new
one on the next pass, told where it stands. A chain parent (filed by a lens entry, `core/tasks/chains.py`) gets its agent
before any subtask, and its agent closes it without the owner's yes."""

from typing import Any

from ..core.project.config import Project
from ..core.tasks import backlog, blockers, chains, questions, runs, subtasks
from ..providers import registry
from ..providers.runner import Rows, State, ref_of, stop_live
from ..services.agents.launch import ended, launch_parent


def notify(p: Project) -> None:
    """Files a message for each parent agent per subtask merged or canceled since it was last told."""
    told: list[tuple[str, str]] = []
    with registry.backlog(p).edit() as d:
        tasks = backlog.tasks(d)
        for i in tasks:
            agent: dict[str, str] = i.get("agent") or {}
            if i["status"] in backlog.CLOSED or agent.get("kind") != "parent":
                continue
            subs = subtasks.of(tasks, i["id"])
            for s in subs:
                if s["status"] in backlog.CLOSED and s["id"] not in i.get("notified", []):
                    text = chains.notice(s, subs, i) if i.get("chain") else subtasks.notice(s, subs, i["id"])
                    questions.message(d, i, "S", ("subtask",), kind="subtask", text="subtask", answer=text)
                    i["notified"] = [*i.get("notified", []), s["id"]]
                    told.append((i["id"], s["id"]))
    for tid, sid in told:
        backlog.event(p, tid, f"its agent is told {sid} closed")


def run(p: Project, rows: Rows) -> None:
    d = registry.backlog(p).load()
    for i in backlog.tasks(d):
        a: dict[str, Any] = i.get("agent") or {}
        if i["status"] in backlog.CLOSED and a.get("kind") == "parent" and rows.state(ref_of(a)) not in (State.ENDED, State.GONE):
            stop_live(p, i["id"], ref_of(a), registry.runner(p), rows)
    notify(p)
    if p.paused:
        return
    tasks = backlog.tasks(registry.backlog(p).load())
    for i in tasks:
        gone = not i.get("agent") or ended(i["agent"], rows)
        if i["status"] == "backlog" and not i.get("blocked") and gone and chains.is_parent(tasks, i) and not blockers.pending(i, tasks):
            launch_parent(p, i, rows.epoch())


def finish(p: Project, tid: str, summary: str) -> str:
    """Closes a parent from its agent's `foreman done`: done if any subtask is done, else canceled; refused while one is open.
    A chain parent with no step closes as done."""
    tasks = backlog.tasks(registry.backlog(p).load())
    subs = subtasks.of(tasks, tid)
    status = chains.outcome(next(i for i in tasks if i["id"] == tid), subs)
    if not status:
        open_ = ", ".join(s["id"] for s in subs if s["status"] not in backlog.CLOSED)
        raise backlog.BacklogError(f"{tid} still has open subtasks: {open_}; it closes once every one is done or canceled")
    registry.backlog(p).set_status(tid, status, summary)
    runs.end(p, tid, ("parent", "chain"), "done")
    return status
