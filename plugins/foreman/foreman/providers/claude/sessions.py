"""Agents are Claude Code sessions of their own (`claude --bg -n TITLE …`), so the owner can open any of them and talk
to it (`claude attach SHORT`, Claude Code's agent view, or the Claude app). Adapted from foreman's sessionlaunch.py: the same
measured command (bypassPermissions, `worktree.bgIsolation: none`), plus the guard hook and our environment in
`--settings`. The runner port's adapter.

`send` continues a session that ended its turn (an owner's answer, a rejection, a nudge) under the same
id: `claude --bg --resume SID MESSAGE`. With any other flag (name, model, settings…) claude starts a copy under a new
id instead, so a resume carries none. `relaunch` is the one resume with flags, on purpose: the copy runs today's
`--settings` and becomes the task's session."""

import json
import re
import time
from pathlib import Path
from typing import Any

from ... import proc, util
from ...core.project.config import Project
from ..runner import Agent, RunnerError, SessionRef, settings
from . import login
from .rows import ClaudeRows

BACKGROUNDED = re.compile(r"backgrounded\s*·\s*([0-9a-f]{6,})", re.I)
STOP_SETTLE_S = 3.0  # after the process is gone, a resume right away still copies (0 s: copy, 2 s: ok)
STOP_WAIT_TRIES = 15


def command(p: Project, agent: Agent, prompt: str, resume: str = "") -> list[str]:
    """With `resume`, a copy of that session (its conversation) started with today's flags: see `Claude.relaunch`."""
    return [
        p.get("agents", "claude"),
        "--bg",
        *(["--resume", resume] if resume else []),
        "-n",
        agent.title,
        "--model",
        agent.model,
        "--effort",
        agent.effort,
        "--permission-mode",
        "bypassPermissions",
        "--settings",
        json.dumps(settings(p, agent.tid, agent.name), separators=(",", ":")),
        prompt,
    ]


def _run(cmd: list[str], cwd: Path) -> str:
    try:
        r = proc.run(cmd, cwd=cwd, timeout=120, env=login.child_env())
    except proc.ProcError as e:
        raise RunnerError(f"claude did not start: {e}") from e
    m = BACKGROUNDED.search(r.out + r.err)
    if r.code != 0 or not m:
        raise RunnerError(" ".join((r.err or r.out).split())[:300] or f"exit {r.code}")
    return m.group(1)


class Claude:
    def __init__(self, p: Project) -> None:
        self.p = p

    def start(self, agent: Agent, brief: str, cwd: Path) -> SessionRef:
        short = _run(command(self.p, agent, brief), cwd)
        rows = self.rows()
        return SessionRef(short, rows.find(short) if rows else None)

    def send(self, ref: SessionRef, message: str, cwd: Path) -> SessionRef:
        """Wakes the session with a message. claude continues a session under its id only if it is stopped (process gone)
        and no flag is given; otherwise it starts a copy. So: stop it, wait until it is gone, resume bare; a copy is an error."""
        sid = self._quiet(ref)
        short = _run([self.p.get("agents", "claude"), "--bg", "--resume", sid, message], cwd)
        if not sid.startswith(short):
            raise RunnerError(f"claude started a copy ({short}) instead of resuming {sid[:8]}")
        return ref

    def relaunch(self, ref: SessionRef, agent: Agent, message: str, cwd: Path) -> SessionRef:
        """Stops the session and starts a copy of it with today's flags (`--settings`: PATH, guard, env): the same
        conversation under a new id, run by the foreman installed now."""
        sid = self._quiet(ref)
        short = _run(command(self.p, agent, message, resume=sid), cwd)
        if sid.startswith(short):
            raise RunnerError(f"claude kept session {sid[:8]} instead of starting a copy with today's settings")
        rows = self.rows()
        return SessionRef(short, rows.find(short) if rows else None)

    def _quiet(self, ref: SessionRef) -> str:
        """Stops an idle session and waits until its process is gone and settled; its full id."""
        sid = ref.session
        if not sid:
            raise RunnerError("no session to resume")
        rows = self._listing()
        if rows.busy(ref):  # fresh: the tick's listing may be old, and the owner may be in the session now
            raise RunnerError(f"session {sid[:8]} is {rows.raw(ref)}: not resuming it")
        try:
            self.stop(SessionRef(sid[:8], sid))
        except RunnerError as e:
            raise RunnerError(f"claude stop failed: {e}") from e
        for _ in range(STOP_WAIT_TRIES):
            if not self._listing().alive(ref):
                break
            time.sleep(1)
        else:
            raise RunnerError(f"session {sid[:8]} did not stop: not resuming it")
        time.sleep(STOP_SETTLE_S)
        return sid

    def _listing(self) -> ClaudeRows:
        rows = self.rows()
        if rows is None or not rows.has_sessions():
            raise RunnerError("claude agents is unreadable: not resuming")
        return rows

    def rows(self) -> ClaudeRows | None:
        """Every background session Claude Code knows (done ones included), by full session id; None when unreadable (an
        empty listing is a listing with no session)."""
        try:
            listed = util.json_list(proc.run_json([self.p.get("agents", "claude"), "agents", "--json", "--all"], timeout=30, env=login.child_env()))
        except proc.ProcError:
            return None
        found: dict[str, dict[str, Any]] = {}
        for item in listed:
            row = util.json_object(item)
            if row and row.get("sessionId"):
                found[row["sessionId"]] = row
        return ClaudeRows(found)

    def stop(self, ref: SessionRef) -> None:
        try:
            proc.run([self.p.get("agents", "claude"), "stop", ref.short], timeout=30, env=login.child_env())
        except proc.ProcError as e:
            raise RunnerError(str(e)) from e
