"""The plugin's commands: `foreman session-context` (Claude Code's SessionStart hook) and `foreman project` (the skill's values)."""

import argparse
import contextlib
import os
import sys

from ...core.project import plugin, registry
from .common import home


def cmd_session_context(a: argparse.Namespace) -> None:
    """In a project's folder: the instructions. Whatever fails prints nothing: a hook's error would be a message in a
    session that should get none."""
    with contextlib.suppress(OSError):
        registry.python_note_file().unlink(missing_ok=True)
    try:
        projects, broken = registry.load_all()
        out = plugin.session_context(sys.stdin.read(), os.environ, [*projects, *broken])
    except Exception:  # see the docstring: the hook stays silent
        return
    if not out:
        return
    print(out)


def cmd_project(a: argparse.Namespace) -> None:
    vals = plugin.values(home(a))
    for k in plugin.PLACEHOLDERS:
        print(f"{k}: {vals[k]}")
