"""Agents are Claude Code sessions of their own (`claude --bg -n NAME …`), so the owner can open any of them and talk
to it (`claude attach SHORT`, the agent view, or the Claude app). Adapted from foreman's sessionlaunch.py: the same
measured command (bypassPermissions, `worktree.bgIsolation: none`), plus the guard hook and our environment in
`--settings`.

`resume` continues a session that ended its turn (an owner's answer, a rejected integration, a nudge) under the same
id: `claude --bg --resume SID MESSAGE`. With any other flag (name, model, settings…) claude starts a copy under a new
id instead, so a resume carries none; if it starts a copy anyway, `resume` returns the copy's ids."""

import contextlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import proc, token, util
from .config import Project

BACKGROUNDED = re.compile(r"backgrounded\s*·\s*([0-9a-f]{6,})", re.I)
ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "bin"
STOP_SETTLE_S = 3.0  # `claude stop` returns before the process is gone; a resume right after it still copies (0 s: copy, 2 s: ok)


class SessionError(Exception):
    pass


def settings(p: Project, iid: str, agent: str) -> dict[str, Any]:
    env = {
        "FM_HOME": str(p.home),
        "FM_ISSUE": iid,
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

    iid: str
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
        json.dumps(settings(p, agent.iid, agent.name), separators=(",", ":")),
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


def resume_command(p: Project, sid: str, message: str) -> list[str]:
    """No flag besides the message: the session keeps its saved options and its id."""
    return [p.get("agents", "claude"), "--bg", "--resume", sid, message]


def _find(p: Project, short: str) -> str | None:
    return next((sid for sid, row in listing(p).items() if row.get("id") == short or sid.startswith(short)), None)


def start(p: Project, agent: Agent, prompt: str, cwd: Path) -> tuple[str, str | None]:
    """(short id, full session id or None)."""
    short = _run(command(p, agent, prompt), cwd)
    return short, _find(p, short)


def resume(p: Project, sid: str, message: str, cwd: Path) -> tuple[str, str | None]:
    """(short id, full session id or None) of the session that got the message: `sid`'s own, or a copy's."""
    row_state = state(listing(p), sid)  # fresh: the tick's listing may be old, and the owner may be in the session now
    if busy({sid: {"state": row_state}}, sid):
        raise SessionError(f"session {sid[:8]} is {row_state}: not resuming it")
    if row_state == "done":  # idle but its process is alive: claude would start a copy; it wakes in place only stopped
        with contextlib.suppress(proc.ProcError):  # best effort: the resume says if the session cannot be reached
            stop(p, sid[:8])
        time.sleep(STOP_SETTLE_S)
    short = _run(resume_command(p, sid, message), cwd)
    return short, sid if sid.startswith(short) else _find(p, short)


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


def busy(rows: dict[str, dict[str, Any]], sid: str | None) -> bool:
    """Not idle: working, or any other live state Claude Code reports (e.g. blocked). Resuming a session that is not
    idle starts a copy of it, so messages wait for `done` / `stopped` / gone."""
    return state(rows, sid) not in ("done", "stopped", "gone")


def stop(p: Project, short: str) -> None:
    proc.run([p.get("agents", "claude"), "stop", short], timeout=30)
