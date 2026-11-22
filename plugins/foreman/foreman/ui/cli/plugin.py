"""The plugin's commands: `foreman session-context` (Claude Code's SessionStart hook), `foreman project` (the skill's values) and
`foreman projects --json` (every registered project, for other programs)."""

import argparse
import contextlib
import os
import sys

from ...core.project import plugin, registry
from ...steps.health import checks
from .common import home, print_json


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


def cmd_projects(a: argparse.Namespace) -> None:
    """Every registered project as JSON (DESIGN.md «The JSON reads»); a home whose foreman.toml cannot be loaded is one line on stderr."""
    projects, broken = registry.load_all()
    for b in broken:
        print(f"foreman: unreadable: {b.home}: {b.reason}", file=sys.stderr)
    print_json(
        [
            {
                "name": p.name,
                "prefix": str(p.get("project", "prefix")),
                "home": str(p.home),
                "repo": str(p.repo),
                "main": str(p.get("project", "main")),
                "autonomy": str(p.get("merge", "autonomy")),
                "strategy": str(p.get("merge", "strategy")),
                "loop_running": not checks.loop_dead(p),
                "slots": plugin.slots(p),
            }
            for p in projects
        ]
    )
