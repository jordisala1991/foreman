"""Relaunching a task's agent on the foreman installed now: its idle session is stopped and a copy of it (same
conversation, new id) starts with today's `--settings` (PATH, guard, environment); the copy becomes the task's session.
A parent agent whose session ended is launched anew, told where it stands."""

from pathlib import Path
from typing import Any

from ... import util
from ...core.project.config import Project
from ...core.tasks import backlog, blockers
from ...providers import registry
from ...providers.runner import Agent, Rows, RunnerError, ref_of, unreadable
from .launch import ended, launch_parent, record_session
from .running import session_lock

MESSAGE = (
    "foreman restarted this session on its current build: your conversation is kept. Carry on where you left off; "
    "if you were waiting for the owner or for a subtask, end your turn."
)


def relaunch(p: Project, tid: str) -> tuple[str, str]:
    """(old short id, new short id). Every refusal is a one-line `RunnerError`, raised before anything is stopped."""
    with session_lock(p, tid):
        return _relaunch(p, tid)


def _relaunch(p: Project, tid: str) -> tuple[str, str]:
    runner = registry.runner(p)
    rows = runner.rows()
    if rows is None:
        raise RunnerError(unreadable(p))
    task = backlog.get(registry.backlog(p).load(), tid)
    live = next(((r, ref) for r in ("agent", "mechanic") if (ref := ref_of(task.get(r))) and rows.alive(ref)), None)
    if live is None:
        return _launch_anew(p, tid, task, rows)
    role, ref = live
    a: dict[str, Any] = task[role]
    if rows.busy(ref):
        raise RunnerError(f"{tid}'s agent {a['name']} is {rows.state(ref).value}: relaunch it when it is idle")
    agent = Agent(tid, a["name"], a["model"], a["effort"], util.session_title(tid, a["kind"], task["title"]))
    try:
        new = runner.relaunch(ref, agent, MESSAGE, Path(a.get("cwd") or p.repo))
    except RunnerError as e:
        backlog.event(p, tid, f"relaunch of {a['name']} failed: {e}")
        raise
    with registry.backlog(p).edit() as d:
        it = backlog.get(d, tid)
        now: dict[str, Any] = it.get(role) or {}
        still = it["status"] == task["status"] and now.get("session") == a["session"]
        if still:
            it[role] = {**it[role], **new.fields(), "started": util.now(), "nudged": None}
            record_session(it, new, a["kind"])
    if not still:  # cancelled, merged or relaunched meanwhile: the copy has no task to work for
        runner.stop(new)
        backlog.event(p, tid, f"relaunch of {a['name']} undone: the task changed meanwhile (copy {new.short} stopped)")
        raise RunnerError(f"{tid} changed while its agent restarted: the copy {new.short} is stopped")
    backlog.event(p, tid, f"relaunched {a['name']} on the current foreman: session {a['short']} → {new.short}")
    return a["short"], new.short


def _launch_anew(p: Project, tid: str, task: dict[str, Any], rows: Rows) -> tuple[str, str]:
    """A parent whose session ended has no copy to restart: a new session replaces it. Any other task has no agent to relaunch."""
    old: dict[str, Any] = task.get("agent") or {}
    if task["status"] in backlog.CLOSED or not ended(old, rows):
        raise RunnerError(f"{tid} has no live agent")
    if task.get("blocked"):
        raise RunnerError(f"{tid} is blocked: {task['blocked']}; unblock it first")
    if waits := blockers.pending(task, backlog.tasks(registry.backlog(p).load())):
        raise RunnerError(f"{tid} waits for {', '.join(waits)} to be done")
    if launch_parent(p, task) is None:
        raise RunnerError(f"{tid}'s parent agent could not be launched: see its events")
    return old["short"], backlog.get(registry.backlog(p).load(), tid)["agent"]["short"]
