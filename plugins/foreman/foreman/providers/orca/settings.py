"""What an Orca chat carries: the agent's settings in its folder's `.claude/settings.local.json`, which the chat reads
(FN-218 F2), and the claude session id, which only a SessionStart hook sees (F6): the hook writes it to a file of
foreman's state, outside every repo. One agent per folder: two agents sharing a file swap their task live (F3)."""

import hashlib
import json
import shlex
import sys
import time
from pathlib import Path

from ...core.project.config import Project
from ..runner import Agent, RunnerError, settings

HOOK = "import json,sys;open(sys.argv[1],'w').write(json.load(sys.stdin)['session_id'])"


def session_file(p: Project, folder: Path) -> Path:
    return p.state / "orca-sessions" / f"{hashlib.sha1(str(folder.resolve()).encode()).hexdigest()[:16]}.id"


def carry(p: Project, agent: Agent, folder: Path) -> None:
    """Writes the agent's settings (env, the guard hook, the plugin off) and the session hook into the folder; never
    into a repo's main checkout, where it would reach every worktree (F3)."""
    if folder.resolve() in {r.resolve() for r in p.repos}:
        raise RunnerError(f"never a settings file in a main checkout: {folder}")
    s = settings(p, agent.tid, agent.name)
    hook = shlex.join([sys.executable, "-c", HOOK, str(session_file(p, folder))])
    s["hooks"] = {**s["hooks"], "SessionStart": [{"hooks": [{"type": "command", "command": hook}]}]}
    out = folder / ".claude" / "settings.local.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(s, indent=2) + "\n", encoding="utf-8")
    session_file(p, folder).parent.mkdir(parents=True, exist_ok=True)


def read_session(p: Project, folder: Path, wait_s: float = 30.0) -> str | None:
    """The session id the folder's chat reported, polled once a second; None when none came."""
    f = session_file(p, folder)
    end = time.monotonic() + wait_s
    while True:
        if f.exists() and (sid := f.read_text(encoding="utf-8").strip()):
            return sid
        if time.monotonic() >= end:
            return None
        time.sleep(1)
