"""The parent agent: a session per let-in parent task, with no worktree, that follows its subtasks with the owner. The
parent keeps status backlog while it lives, and closes only by its own `foreman done` after the owner's yes. It runs only
when something for it happens: a wakeup is a message of the task not yet delivered (a subtask closed, the owner's answer,
an owner edit); the loop resumes its idle session with it, or starts a new session told where it stands when the old one
ended. Its run is one turn (steps/recover.py ends it done once the session idles). A chain parent (filed by a lens entry,
`core/tasks/chains.py`) gets its agent before any subtask, and its agent closes it without the owner's yes."""

from typing import Any

from ..core.project.config import Project
from ..core.tasks import backlog, blockers, chains, questions, runs, subtasks
from ..providers import registry
from ..providers.runner import Rows, State, ref_of, stop_live
from ..services.agents.launch import ended, launch_parent


def wake(p: Project) -> None:
    """A wakeup for each parent agent per subtask merged or canceled that no message of it names yet."""
    told: list[tuple[str, str]] = []
    with registry.backlog(p).edit() as d:
        tasks = backlog.tasks(d)
        for i in tasks:
            agent: dict[str, str] = i.get("agent") or {}
            if i["status"] in backlog.CLOSED or agent.get("kind") != "parent":
                continue
            subs = subtasks.of(tasks, i["id"])
            for s in subtasks.unwoken(tasks, i["id"], d["decisions_resolved"]):
                text = chains.notice(s, subs, i) if i.get("chain") else subtasks.notice(s, subs, i["id"])
                questions.message(d, i, "S", ("subtask",), kind="subtask", text="subtask", answer=text, subtask=s["id"])
                told.append((i["id"], s["id"]))
    for tid, sid in told:
        backlog.event(p, tid, f"its agent is told {sid} closed")


def run(p: Project, rows: Rows) -> None:
    d = registry.backlog(p).load()
    for i in backlog.tasks(d):
        a: dict[str, Any] = i.get("agent") or {}
        if i["status"] in backlog.CLOSED and a.get("kind") == "parent" and rows.state(ref_of(a)) not in (State.ENDED, State.GONE):
            stop_live(p, i["id"], ref_of(a), registry.runner(p), rows)
    wake(p)
    if p.paused:
        return
    d = registry.backlog(p).load()
    tasks, pending = backlog.tasks(d), {q["task"] for q in questions.undelivered(d)}
    for i in tasks:
        due = not i.get("agent") or (ended(i["agent"], rows) and i["id"] in pending)  # first launch, or a wakeup its ended session cannot take
        if i["status"] == "backlog" and not i.get("blocked") and due and chains.is_parent(tasks, i) and not blockers.pending(i, tasks):
            launch_parent(p, i)


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
