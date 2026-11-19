"""The parent agent: a session per let-in parent task, with no worktree, that follows its subtasks with the owner. The
parent keeps status backlog while it lives; it is told when a subtask is merged or canceled (`notified` lists those
already told), and closes only by its own `foreman done` after the owner's yes. A chain parent (filed by a lens entry,
`core/tasks/chains.py`) gets its agent before any subtask, and its agent closes it without the owner's yes."""

from typing import Any

from .. import util
from ..core.project.config import Project
from ..core.tasks import backlog, blockers, chains, questions, subtasks
from ..providers import registry
from ..providers.runner import Agent, Rows, RunnerError, State, ref_of, stop_live
from ..services.agents.brief import agent_name, brief, lens_file
from ..services.agents.launch import record_session


def launch(p: Project, i: dict[str, Any]) -> None:
    tid = i["id"]
    name = agent_name(p, tid, "parent")
    model, effort = p.get("agents", "planning_model"), p.get("agents", "planning_effort")
    runner = registry.runner(p)
    try:
        title = util.session_title(tid, "parent", i["title"])
        ref = runner.start(Agent(tid, name, model, effort, title), _brief(p, i), p.repo)
    except RunnerError as e:
        backlog.update(p, tid, blocked=f"could not launch its agent: {e}")
        backlog.event(p, tid, f"launch failed: {e}")
        return
    agent = {"name": name, **ref.fields(), "kind": "parent", "model": model, "effort": effort, "started": util.now(), "cwd": str(p.repo)}
    with backlog.edit(p) as d:
        it = backlog.get(d, tid)
        still = it["status"] == "backlog" and not it.get("blocked")
        if still:
            it["agent"] = agent
            it["notified"] = [s["id"] for s in subtasks.of(backlog.tasks(d), tid) if s["status"] in backlog.CLOSED]
            record_session(it, ref, "parent")
    if not still:  # the owner cancelled or changed it while the session started
        runner.stop(ref)
        backlog.event(p, tid, f"launch of {name} undone: the task changed meanwhile")
        return
    backlog.event(p, tid, f"launched {name} (parent, {model}, {effort}, session {ref.short})")


def _brief(p: Project, i: dict[str, Any]) -> str:
    """A chain parent's agent reads `chain.md`, told its lens report, cap and budget; any other parent's reads `parent.md`."""
    c: backlog.Chain | None = i.get("chain")
    if not c:
        return brief(p, i, "parent")
    extra = {"lens": c["lens"], "lens_report": str(lens_file(p, c["lens"])), "max_steps": c["max_steps"], "max_lines": c["max_lines"]}
    return brief(p, i, "chain", extra)


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
                    text = chains.notice(s, subs, i) if i.get("chain") else subtasks.notice(s, subs, i["id"])
                    questions.message(d, i, "S", ("subtask",), kind="subtask", text="subtask", answer=text)
                    i["notified"] = [*i.get("notified", []), s["id"]]
                    told.append((i["id"], s["id"]))
    for tid, sid in told:
        backlog.event(p, tid, f"its agent is told {sid} closed")


def run(p: Project, rows: Rows) -> None:
    d = backlog.load(p)
    for i in backlog.tasks(d):
        a: dict[str, Any] = i.get("agent") or {}
        if i["status"] in backlog.CLOSED and a.get("kind") == "parent" and rows.state(ref_of(a)) not in (State.ENDED, State.GONE):
            stop_live(p, i["id"], ref_of(a), registry.runner(p), rows)
    notify(p)
    if p.paused:
        return
    tasks = backlog.tasks(backlog.load(p))
    for i in tasks:
        if i["status"] == "backlog" and not i.get("blocked") and not i.get("agent") and chains.is_parent(tasks, i) and not blockers.pending(i, tasks):
            launch(p, i)


def finish(p: Project, tid: str, summary: str) -> str:
    """Closes a parent from its agent's `foreman done`: done if any subtask is done, else canceled; refused while one is open.
    A chain parent with no step closes as done."""
    tasks = backlog.tasks(backlog.load(p))
    subs = subtasks.of(tasks, tid)
    status = chains.outcome(next(i for i in tasks if i["id"] == tid), subs)
    if not status:
        open_ = ", ".join(s["id"] for s in subs if s["status"] not in backlog.CLOSED)
        raise backlog.BacklogError(f"{tid} still has open subtasks: {open_}; it closes once every one is done or canceled")
    backlog.set_status(p, tid, status, summary)
    return status
