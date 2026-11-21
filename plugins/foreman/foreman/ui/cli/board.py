"""foreman backlog setup: makes the project's Board (`[backlog]` in foreman.toml) fm's."""

import argparse

from ...providers import registry
from .common import home


def cmd_backlog(a: argparse.Namespace) -> None:
    lines = registry.setup_board(home(a))
    print("\n".join(lines) or "the Board and the label are already set up")
