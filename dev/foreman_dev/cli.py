"""`foreman-dev [--project P] stats|fill|timeline|snapshot …`: the developer tools' one entry (dev/foreman-dev runs it).

- `stats`: what the Claude Code sessions of a product spent, in tokens (stats.py);
- `fill SESSION`: what fills one session's context, piece by piece (fill.py);
- `timeline`: the product's history, one event per line, with each agent's and the orchestrator's spend
  (timeline.py);
- `snapshot DEST`: the public snapshot of foreman's repository, written into an empty folder (snapshot.py).

They read the same registry, state and Claude Code folders foreman does (FOREMAN_REGISTRY, FOREMAN_STATE_HOME,
FOREMAN_CLAUDE_PROJECTS), and never write; `snapshot` writes only into the empty folder it is given.
"""

from __future__ import annotations

import argparse
import sys

from foreman import config, util

from . import fill, snapshot, stats, timeline


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="foreman-dev",
        description="foreman's developer tools: what the sessions spend and fill, the history as a timeline "
        "(they only read) and the public snapshot of the repository (not part of the product)",
    )
    ap.add_argument("--project", dest="global_project", metavar="P", help="the product (as foreman --project P)")
    sp = ap.add_subparsers(dest="cmd", required=True)
    stats.add_parser(sp)
    fill.add_parser(sp)
    timeline.add_parser(sp)
    snapshot.add_parser(sp)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = util.no_abbrev(parser()).parse_args(argv)
    if args.cmd == "stats":
        return stats.run(args, args.global_project)
    if args.cmd == "snapshot":
        return snapshot.run(args)
    if args.cmd == "fill":
        try:
            registered = config.registered_projects()
        except config.ConfigError:
            registered = []
        return fill.run(args, registered)
    try:
        project = config.resolve(args.global_project)
    except config.ConfigError as e:
        print(f"foreman-dev: {e}", file=sys.stderr)
        return 2
    return timeline.run(args, project)
