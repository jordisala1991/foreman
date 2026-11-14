"""What every `fm` command needs: the project it works on and the task it speaks for."""

import argparse
import os
import sys
from pathlib import Path

from .config import Project


def home(args: argparse.Namespace) -> Project:
    h = args.home or os.environ.get("FM_HOME")
    if not h:
        here = Path.cwd()
        h = next((str(d) for d in (here, *here.parents) if (d / "fm.toml").exists()), None)
    if not h:
        sys.exit("fm: no project: pass --home, set FM_HOME, or run inside a folder with fm.toml")
    return Project.load(h)


def mine(args: argparse.Namespace) -> str:
    tid = os.environ.get("FM_TASK")
    if not tid:
        sys.exit("fm: which task? (FM_TASK, set in an agent's session)")
    return tid
