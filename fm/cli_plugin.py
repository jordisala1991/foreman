"""The plugin's commands: `fm session-context` (Claude Code's SessionStart hook) and `fm project` (the skill's values)."""

import argparse
import contextlib
import os
import sys
from pathlib import Path

from . import config, plugin
from .cli_common import home

BIN = Path(__file__).resolve().parent.parent / "bin"


def cmd_session_context(a: argparse.Namespace) -> None:
    """In a project's folder: the instructions, and this bin/ first on the session's PATH (Claude Code appends it last,
    after macOS's own /usr/bin/fm). Whatever fails prints nothing: a hook's error would be a message in a session that
    should get none."""
    with contextlib.suppress(OSError):
        config.python_note_file().unlink(missing_ok=True)
    try:
        projects, broken = config.load_all()
        out = plugin.session_context(sys.stdin.read(), os.environ, [*projects, *broken])
    except Exception:  # see the docstring: the hook stays silent
        return
    if not out:
        return
    print(out)
    try:
        if env_file := os.environ.get("CLAUDE_ENV_FILE"):
            with Path(env_file).open("a", encoding="utf-8") as f:
                f.write(f'export PATH="{BIN}:$PATH"\n')
    except OSError:  # the instructions are out; fm stays reachable by its full path
        pass


def cmd_project(a: argparse.Namespace) -> None:
    vals = plugin.values(home(a))
    for k in plugin.PLACEHOLDERS:
        print(f"{k}: {vals[k]}")
