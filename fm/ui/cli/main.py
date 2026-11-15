"""`fm`: the one command. Agents use `ask`, `done`, `ready`, `block`, `add` (their task comes from FM_TASK);
the owner and the panel use the rest; launchd runs `fm loop`; Claude Code runs `fm guard` as the agents' hook."""

import argparse
import sys
from collections.abc import Callable

from ...core.project import config
from ...core.tasks import backlog
from .agent import cmd_ask, cmd_block, cmd_diagnose, cmd_done, cmd_ready, cmd_verdict
from .ops import cmd_guard, cmd_health, cmd_launchd, cmd_lock, cmd_loop, cmd_metrics, cmd_panel
from .owner import (
    cmd_accept,
    cmd_add,
    cmd_allow,
    cmd_answer,
    cmd_cancel,
    cmd_edit,
    cmd_go,
    cmd_init,
    cmd_pause,
    cmd_reject,
    cmd_review,
    cmd_unblock,
)
from .parser import parser
from .plugin import cmd_project, cmd_session_context
from .view import cmd_list, cmd_show

HANDLERS: dict[str, Callable[[argparse.Namespace], object]] = {
    "init": cmd_init,
    "add": cmd_add,
    "edit": cmd_edit,
    "list": cmd_list,
    "show": cmd_show,
    "ask": cmd_ask,
    "done": cmd_done,
    "verdict": cmd_verdict,
    "ready": cmd_ready,
    "block": cmd_block,
    "diagnose": cmd_diagnose,
    "answer": cmd_answer,
    "allow": lambda a: cmd_allow(a, True),
    "deny": lambda a: cmd_allow(a, False),
    "accept": cmd_accept,
    "reject": cmd_reject,
    "review": cmd_review,
    "go": cmd_go,
    "cancel": cmd_cancel,
    "unblock": cmd_unblock,
    "pause": cmd_pause,
    "loop": cmd_loop,
    "health": cmd_health,
    "metrics": cmd_metrics,
    "lock": cmd_lock,
    "guard": cmd_guard,
    "panel": cmd_panel,
    "launchd": cmd_launchd,
    "project": cmd_project,
    "session-context": cmd_session_context,
}


def main(argv: list[str] | None = None) -> None:
    a = parser(__doc__).parse_args(argv)
    try:
        HANDLERS[a.cmd](a)
    except (backlog.BacklogError, config.ProjectError) as e:
        sys.exit(f"fm: {e}")
