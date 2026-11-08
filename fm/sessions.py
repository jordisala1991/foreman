"""Agents are Claude Code sessions of their own (`claude --bg -n NAME …`), so the owner can open any of them and talk
to it (`claude attach SHORT`, the agent view, or the Claude app). Adapted from foreman's sessionlaunch.py: the same
measured command (bypassPermissions, `worktree.bgIsolation: none`), plus the guard hook and our environment in
`--settings`.

`resume` continues a session that ended its turn (an owner's answer, a rejected integration, a nudge) under the same
id: `claude --bg --resume SID … MESSAGE`."""

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import util
from .config import Project

BACKGROUNDED = re.compile(r"backgrounded\s*·\s*([0-9a-f]{6,})", re.I)
ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "bin"


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


def command(p: Project, agent: Agent, prompt: str, resume: str | None = None) -> list[str]:
    cmd = [p.get("agents", "claude"), "--bg", "-n", agent.name]
    if resume:
        cmd += ["--resume", resume]
    return cmd + [
        "--model",
        agent.model,
        "--permission-mode",
        "bypassPermissions",
        "--settings",
        json.dumps(settings(p, agent.iid, agent.name), separators=(",", ":")),
        prompt,
    ]


def _run(cmd: list[str], cwd: Path) -> str:
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=120, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        raise SessionError(f"claude did not start: {e}") from e
    m = BACKGROUNDED.search(r.stdout + r.stderr)
    if r.returncode != 0 or not m:
        raise SessionError(" ".join((r.stderr or r.stdout).split())[:300] or f"exit {r.returncode}")
    return m.group(1)


def start(p: Project, agent: Agent, prompt: str, cwd: Path) -> tuple[str, str | None]:
    """(short id, full session id or None)."""
    short = _run(command(p, agent, prompt), cwd)
    sid = next((sid for sid, row in listing(p).items() if row.get("id") == short or sid.startswith(short)), None)
    return short, sid


def resume(p: Project, agent: Agent, sid: str, message: str, cwd: Path) -> str:
    return _run(command(p, agent, message, resume=sid), cwd)


def listing(p: Project) -> dict[str, dict[str, Any]]:
    """Every background session Claude Code knows (done ones included), by full session id; {} when unreadable."""
    try:
        r = subprocess.run([p.get("agents", "claude"), "agents", "--json", "--all"], capture_output=True, text=True, timeout=30, check=False)
        rows = util.json_list(json.loads(r.stdout))
    except (OSError, subprocess.SubprocessError, ValueError):
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
    subprocess.run([p.get("agents", "claude"), "stop", short], capture_output=True, timeout=30, check=False)
