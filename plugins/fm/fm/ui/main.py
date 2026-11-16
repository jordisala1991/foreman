"""`foreman`, as bin/foreman runs it: the CLI's commands, and `foreman panel`, which starts what sits beside the CLI (the panel imports the
CLI's functions, so the CLI cannot import the panel)."""

import argparse

from .cli.main import main as cli_main


def panel(a: argparse.Namespace) -> None:
    from .panel import server

    server.serve(a.port, a.host)


def main(argv: list[str] | None = None) -> None:
    cli_main(argv, {"panel": panel})
