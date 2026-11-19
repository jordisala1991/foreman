"""Relaunching a task's agent on the foreman installed now: its idle session is stopped and a copy of it (same
conversation, new id) starts with today's `--settings` (PATH, guard, environment); the copy becomes the task's session."""

from pathlib import Path
from typing import Any

from ... import util
from ...core.project.config import Project
from ...core.tasks import backlog
from ..claude import sessions
from .launch import record_session

MESSAGE = (
    "foreman restarted this session on its current build: your conversation is kept. Carry on where you left off; "
    "if you were waiting for the owner or for a subtask, end your turn."
)


def relaunch(p: Project, tid: str) -> tuple[str, str]:
    """(old short id, new short id). Every refusal is a one-line `SessionError`, raised before anything is stopped."""
    rows = sessions.read(p)
    if rows is None:
        raise sessions.SessionError("claude agents is unreadable")
    task = backlog.get(backlog.load(p), tid)
    records: dict[str, dict[str, Any]] = {r: task.get(r) or {} for r in ("agent", "mechanic")}
    role = next((r for r, a in records.items() if sessions.alive(rows, a.get("session"))), None)
    if role is None:
        raise sessions.SessionError(f"{tid} has no live agent")
    a = records[role]
    if sessions.busy(rows, a["session"]):
        raise sessions.SessionError(f"{tid}'s agent {a['name']} is {sessions.state(rows, a['session'])}: relaunch it when it is idle")
    agent = sessions.Agent(tid, a["name"], a["model"], a["effort"], util.session_title(tid, a["kind"], task["title"]))
    try:
        new, sid = sessions.relaunch(p, agent, a["session"], MESSAGE, Path(a.get("cwd") or p.repo))
    except sessions.SessionError as e:
        backlog.event(p, tid, f"relaunch of {a['name']} failed: {e}")
        raise
    with backlog.edit(p) as d:
        it = backlog.get(d, tid)
        it[role] = {**it[role], "short": new, "session": sid, "started": util.now(), "nudged": None}
        if sid:
            record_session(it, sid, a["kind"])
    backlog.event(p, tid, f"relaunched {a['name']} on the current foreman: session {a['short']} → {new}")
    return a["short"], new
