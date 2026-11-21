"""foreman backlog setup: makes the project's Board (`[backlog]` in foreman.toml) fm's. foreman backlog cutover: moves the
open tasks of backlog.json onto a Board and points foreman.toml at it."""

import argparse
import sys

from ...core.project import backlog_table
from ...providers import registry
from .common import home


def cmd_backlog(a: argparse.Namespace) -> None:
    if a.action == "setup":
        lines = registry.setup_board(home(a))
        print("\n".join(lines) or "the Board and the label are already set up")
        return
    if a.repo is None or a.board is None:
        sys.exit("foreman: backlog cutover needs --repo OWNER/NAME and --project N")
    table = {"kind": "github", "repo": a.repo, "project": a.board} | ({"owner": a.owner} if a.owner else {})
    try:
        b = backlog_table.board(table)
    except ValueError as e:
        sys.exit(f"foreman: {e}")
    for line in registry.cutover(home(a), b, a.dry_run):
        print(line, flush=True)
