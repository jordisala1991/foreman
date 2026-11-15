"""`fm`, as bin/fm runs it: the CLI's commands, and `fm panel`, which starts what sits beside the CLI (the panel imports the
CLI's functions, so the CLI cannot import the panel)."""

import argparse

from .cli.main import main as cli_main
from .panel import server


def panel(a: argparse.Namespace) -> None:
    server.serve(a.port, a.host)


def main(argv: list[str] | None = None) -> None:
    cli_main(argv, {"panel": panel})
