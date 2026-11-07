"""`foreman launch --session`: an agent started as a Claude Code session of its own (`claude --bg`) instead of
through the orchestrator's Agent tool, so it outlives the orchestrator's session.

The command is the one measured on Claude Code 2.1.317:
run in the agent's worktree, `--agent foreman:TYPE`, `--model`, `-n NAME`,
`--permission-mode bypassPermissions` (the guard decides, not Claude Code's questions) and `--settings` with
`worktree.bgIsolation: none` (foreman made the worktree) and `env.FOREMAN_LAUNCH` (the launch token: the lasting mark
by which the guard knows the session is an agent, identity.LAUNCH_ENV). The prompt is the launch line, then the brief.
Remote Control is not turned on: the owner wants it only on demand. `--bg` prints `backgrounded · SHORTID · NAME`,
and `claude agents --json` gives that session's full id, which is tied to the launch (identity.link)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from . import identity, util

if TYPE_CHECKING:
    from .config import Project

BACKGROUNDED = re.compile(r"backgrounded\s*·\s*([0-9a-f]{6,})", re.I)
TIMEOUT_S = 120


class SessionError(Exception):
    """The session could not be started (the message is for the one who launched it)."""


def enabled(project: Project) -> bool:
    return project.section("agents").get("sessions") is True


def agent_arg(agent_type: str | None) -> str | None:
    """The `--agent` value: a bare type is foreman's own (`dev` → `foreman:dev`), a qualified one stays as it is."""
    if not agent_type:
        return None
    return agent_type if ":" in agent_type else f"foreman:{agent_type}"


def settings(token: str) -> str:
    return json.dumps({"worktree": {"bgIsolation": "none"}, "env": {identity.LAUNCH_ENV: token}}, separators=(",", ":"))


def command(claude: str, name: str, token: str, prompt: str, agent_type: str | None, model: str | None) -> list[str]:
    cmd = [claude, "--bg", "-n", name]
    agent = agent_arg(agent_type)
    if agent:
        cmd += ["--agent", agent]
    if model:
        cmd += ["--model", model]
    return cmd + ["--permission-mode", "bypassPermissions", "--settings", settings(token), prompt]


def find_claude() -> str:
    exe = shutil.which("claude")
    if not exe:
        raise SessionError("no `claude` command in PATH: it starts the session")
    return exe


def full_session_id(claude: str, short: str, name: str, cwd: str) -> str | None:
    """The full session id of the background session `short` (or, failing that, named `name`), from `claude agents
    --json`; None when it cannot be read."""
    try:
        r = subprocess.run(
            [claude, "agents", "--json"], cwd=cwd, capture_output=True, text=True, timeout=30, check=False
        )
        rows = json.loads(r.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        sid = row.get("sessionId")
        if isinstance(sid, str) and (row.get("id") == short or sid.startswith(short)):
            return sid
    named = [r_ for r_ in rows if isinstance(r_, dict) and r_.get("name") == name and r_.get("sessionId")]
    return named[0]["sessionId"] if len(named) == 1 else None


def start(
    project: Project,
    token: str,
    rec: Mapping[str, Any],
    prompt: str,
    cwd: str,
) -> tuple[str, str | None]:
    """Starts the session and ties it to launch `token`; (short id, full session id or None). Raises SessionError."""
    claude = find_claude()
    cmd = command(claude, rec["name"], token, prompt, rec.get("type"), rec.get("model"))
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        raise SessionError(f"`claude --bg` did not start: {e}") from e
    m = BACKGROUNDED.search(r.stdout + r.stderr)
    if r.returncode != 0 or not m:
        why = " ".join((r.stderr or r.stdout).split())[:300] or f"exit {r.returncode}"
        raise SessionError(f"`claude --bg` did not start the session: {why}")
    short = m.group(1)
    sid = full_session_id(claude, short, rec["name"], cwd)
    if sid:  # the id is the complement; the token in the session's environment is the lasting mark
        from . import agents as agents_mod

        f = agents_mod.agents_file(project)
        with util.locked_json(f, {}) as d:
            identity.link(project, d, sid, token, "launch")
            util.write_json(f, d)
    return short, sid
