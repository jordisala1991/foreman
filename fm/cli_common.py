"""What every `fm` command needs: the project it works on and the task it speaks for."""

import argparse
import os
import sys
from pathlib import Path

from . import config
from .config import Project


def home(args: argparse.Namespace) -> Project:
    """--project NAME, --home, FM_HOME, FM_PROJECT, the project owning the current folder, the nearest fm.toml."""
    if args.project:
        return named(args.project)
    h = args.home or os.environ.get("FM_HOME")
    if h:
        return Project.load(h)
    if os.environ.get("FM_PROJECT"):
        return named(os.environ["FM_PROJECT"])
    return here()


def _names(projects: list[Project], broken: list[str]) -> str:
    names = ", ".join(sorted(p.name for p in projects)) or "none"
    return names + "".join(f"; unreadable: {b}" for b in broken)


def named(name: str) -> Project:
    projects, broken = config.load_all()
    found = [p for p in projects if p.name == name]
    if not found:
        sys.exit(f"fm: no project named {name!r}; registered: {_names(projects, broken)}")
    if len(found) > 1:
        sys.exit(f"fm: {len(found)} projects are named {name!r}: {', '.join(str(p.home) for p in found)}")
    return found[0]


def here() -> Project:
    (projects, broken), cwd = config.load_all(), Path.cwd()
    found = config.owners(projects, cwd)
    if len(found) == 1:
        return found[0][0]
    if found:
        sys.exit(f"fm: {found[0][1]} belongs to {' and '.join(p.name for p, _ in found)}; pass --project NAME")
    h = next((d for d in (cwd, *cwd.parents) if (d / "fm.toml").exists()), None)
    if h is None:
        sys.exit(f"fm: no project here: {cwd} belongs to no registered project (registered: {_names(projects, broken)}); pass --project NAME")
    return Project.load(h)


def mine(args: argparse.Namespace) -> str:
    tid = os.environ.get("FM_TASK")
    if not tid:
        sys.exit("fm: which task? (FM_TASK, set in an agent's session)")
    return tid
