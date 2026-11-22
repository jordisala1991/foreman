"""`foreman`: the one command. Agents use `ask`, `done`, `ready`, `block`, `add` (their task comes from FOREMAN_TASK);
the owner and the panel use the rest; launchd runs `foreman loop`; Claude Code runs `foreman guard` as the agents' hook."""

import argparse
import sys
from collections.abc import Callable

from ...core.project import config
from ...core.tasks import backlog
from ...providers import ConfigError, Permanent, ProviderError, Transient
from .agent import cmd_ask, cmd_block, cmd_diagnose, cmd_done, cmd_ready, cmd_verdict
from .board import cmd_backlog
from .ops import cmd_agents, cmd_events, cmd_guard, cmd_health, cmd_launchd, cmd_lock, cmd_loop, cmd_metrics, cmd_needs, cmd_runs, cmd_watch
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
    cmd_relaunch,
    cmd_review,
    cmd_unblock,
)
from .parser import parser
from .plugin import cmd_project, cmd_projects, cmd_session_context
from .view import cmd_list, cmd_show

Handler = Callable[[argparse.Namespace], object]

HANDLERS: dict[str, Handler] = {
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
    "relaunch": cmd_relaunch,
    "pause": cmd_pause,
    "loop": cmd_loop,
    "health": cmd_health,
    "metrics": cmd_metrics,
    "backlog": cmd_backlog,
    "lock": cmd_lock,
    "watch": cmd_watch,
    "needs": cmd_needs,
    "agents": cmd_agents,
    "runs": cmd_runs,
    "events": cmd_events,
    "guard": cmd_guard,
    "launchd": cmd_launchd,
    "project": cmd_project,
    "projects": cmd_projects,
    "session-context": cmd_session_context,
}


def main(argv: list[str] | None = None, outside: dict[str, Handler] | None = None) -> None:
    """`outside`: commands the CLI does not own (`panel`), given by what runs it."""
    a = parser(__doc__).parse_args(argv)
    try:
        ({**HANDLERS, **(outside or {})})[a.cmd](a)
    except (backlog.BacklogError, config.ProjectError, ConfigError, ProviderError, Transient, Permanent) as e:
        sys.exit(f"foreman: {e}")
