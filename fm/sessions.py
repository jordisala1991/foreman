"""Agents are Claude Code sessions of their own (`claude --bg -n NAME …`), so the owner can open any of them and talk
to it (`claude attach SHORT`, Claude Code's agent view, or the Claude app). Adapted from foreman's sessionlaunch.py: the same
measured command (bypassPermissions, `worktree.bgIsolation: none`), plus the guard hook and our environment in
`--settings`.

`resume` continues a session that ended its turn (an owner's answer, a rejection, a nudge) under the same
id: `claude --bg --resume SID MESSAGE`. With any other flag (name, model, settings…) claude starts a copy under a new
id instead, so a resume carries none."""

import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import backlog, proc, token, util
from .config import Project

BACKGROUNDED = re.compile(r"backgrounded\s*·\s*([0-9a-f]{6,})", re.I)
ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "bin"
STOP_SETTLE_S = 3.0  # after the process is gone, a resume right away still copies (0 s: copy, 2 s: ok)
STOP_WAIT_TRIES = 15


class SessionError(Exception):
    pass


def settings(p: Project, tid: str, agent: str) -> dict[str, Any]:
    env = {
        "FM_HOME": str(p.home),
        "FM_TASK": tid,
        "FM_AGENT": agent,
        "PATH": f"{BIN}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    guard = f"{sys.executable} {BIN / 'fm'} guard"
    s: dict[str, Any] = {
        "worktree": {"bgIsolation": "none"},
        "env": env,
        "hooks": {"PreToolUse": [{"matcher": "Bash|Edit|Write|MultiEdit|NotebookEdit", "hooks": [{"type": "command", "command": guard}]}]},
    }
    settings: dict[str, Any] = p.get("agents", "settings") or {}
    for k, v in settings.items():  # the project's extra settings, e.g. enabledPlugins
        s[k] = {**s[k], **v} if isinstance(v, dict) and isinstance(s.get(k), dict) else v
    return s


@dataclass(frozen=True)
class Agent:
    """Who a session is for: its task, its name, its model."""

    tid: str
    name: str
    model: str
    effort: str


def command(p: Project, agent: Agent, prompt: str) -> list[str]:
    return [
        p.get("agents", "claude"),
        "--bg",
        "-n",
        agent.name,
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
        r = proc.run(cmd, cwd=cwd, timeout=120, env=token.child_env(cmd[0]))
    except proc.ProcError as e:
        raise SessionError(f"claude did not start: {e}") from e
    m = BACKGROUNDED.search(r.out + r.err)
    if r.code != 0 or not m:
        raise SessionError(" ".join((r.err or r.out).split())[:300] or f"exit {r.code}")
    return m.group(1)


def find(rows: dict[str, Any], short: str) -> str | None:
    """The full session id of a short one, in a listing."""
    return next((sid for sid, row in rows.items() if row.get("id") == short or sid.startswith(short)), None)


def start(p: Project, agent: Agent, prompt: str, cwd: Path) -> tuple[str, str | None]:
    """(short id, full session id or None)."""
    short = _run(command(p, agent, prompt), cwd)
    return short, find(listing(p), short)


def resume(p: Project, sid: str, message: str, cwd: Path) -> None:
    """Wakes `sid` with a message. claude continues a session under its id only if it is stopped (process gone) and no
    flag is given; otherwise it starts a copy. So: stop it, wait until it is gone, resume bare; a copy is an error."""
    if not listing(p):
        raise SessionError("claude agents is unreadable: not resuming")
    if busy(listing(p), sid):  # fresh: the tick's listing may be old, and the owner may be in the session now
        raise SessionError(f"session {sid[:8]} is {state(listing(p), sid)}: not resuming it")
    try:
        stop(p, sid[:8])
    except proc.ProcError as e:
        raise SessionError(f"claude stop failed: {e}") from e
    for _ in range(STOP_WAIT_TRIES):
        rows = listing(p)
        if not rows:
            raise SessionError("claude agents is unreadable: not resuming")
        if not alive(rows, sid):
            break
        time.sleep(1)
    else:
        raise SessionError(f"session {sid[:8]} did not stop: not resuming it")
    time.sleep(STOP_SETTLE_S)
    short = _run([p.get("agents", "claude"), "--bg", "--resume", sid, message], cwd)
    if not sid.startswith(short):
        raise SessionError(f"claude started a copy ({short}) instead of resuming {sid[:8]}")


def listing(p: Project) -> dict[str, dict[str, Any]]:
    """Every background session Claude Code knows (done ones included), by full session id; {} when unreadable."""
    try:
        rows = util.json_list(proc.run_json([p.get("agents", "claude"), "agents", "--json", "--all"], timeout=30))
    except proc.ProcError:
        return {}
    found: dict[str, dict[str, Any]] = {}
    for item in rows:
        row = util.json_object(item)
        if row and row.get("sessionId"):
            found[row["sessionId"]] = row
    return found


def state(rows: dict[str, dict[str, Any]], sid: str | None) -> str:
    """working | done (ended its turn, waiting) | stopped | gone."""
    row = rows.get(sid or "")
    return str(row.get("state") or "gone") if row else "gone"


def alive(rows: dict[str, dict[str, Any]], sid: str | None) -> bool:
    """Its process still runs. claude keeps the state of a stopped session (`done`) and drops its `pid`."""
    row = rows.get(sid or "")
    return bool(row and row.get("pid") and row.get("state") != "stopped")


def busy(rows: dict[str, dict[str, Any]], sid: str | None) -> bool:
    """Not idle: working, or any other live state Claude Code reports. Idle whatever the state says: status `idle` (an
    agent that asked the owner and ended its turn shows `blocked`/`idle`, and a hunter `working`/`idle`), or a
    `blocked` row with neither status nor process (a session whose process went away). Resuming a session that is not
    idle starts a copy of it, so messages wait until it is idle."""
    s = state(rows, sid)
    if s in ("done", "stopped", "gone"):
        return False
    row = rows[sid or ""]
    return row.get("status") != "idle" and not (s == "blocked" and not row.get("status") and not row.get("pid"))


def stop(p: Project, short: str) -> None:
    proc.run([p.get("agents", "claude"), "stop", short], timeout=30)


def stop_live(p: Project, tid: str, agent: dict[str, Any] | None, rows: dict[str, dict[str, Any]]) -> None:
    """Best-effort: stops a recorded agent's session unless Claude Code shows it stopped. A failure is noted on the
    task, never raised: cleanup must not block a merge, a cancel or a review."""
    a = agent or {}
    if a.get("short") and state(rows, a.get("session")) != "stopped":
        try:
            stop(p, a["short"])
        except proc.ProcError as e:
            backlog.event(p, tid, f"could not stop session {a['short']}: {e}")
