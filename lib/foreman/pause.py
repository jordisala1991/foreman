"""The loop pause: while `<state>/pause` exists (the panel writes it, /api/pause) the product is paused.

What respects it: the loop pass summary starts with a PAUSED line and leaves out the WIP line that asks to launch
agents (loop.py); the guard rejects `foreman integrate`, `foreman worktree` and `foreman launch --repo` (it makes a
worktree) for the paused product (hooks.py, hook guard); the `pause-agents` hook denies launching a production
subagent from the orchestrator's session.
Everything here only reads, and never raises on a missing or odd pause file.
"""

from __future__ import annotations

import re
import time
from pathlib import PurePath
from typing import TYPE_CHECKING, Optional

from .shell import commands, foreman_call, words

if TYPE_CHECKING:
    from .config import Project

Call = tuple[Optional[str], str, list[str]]  # (project given with --project, subcommand, the rest)
Verdict = tuple["Project", str, str]  # (paused project, since, the blocked subcommand)

BLOCKED = ("integrate", "worktree", "launch")  # launch only with --repo (it makes a worktree)
SHELLS = ("bash", "sh", "zsh")


def since(project: Project) -> str | None:
    """'HH:MM' the product was paused at, or None if it is not paused (or the project is not active)."""
    if not project.active:
        return None
    f = project.state / "pause"
    try:
        text = f.read_text(errors="replace")
        mtime = f.stat().st_mtime
    except OSError:
        return None
    m = re.search(r"\b(\d{1,2}:\d{2})\b", text)
    return m.group(1) if m else time.strftime("%H:%M", time.localtime(mtime))


def foreman_calls(cmd: str, depth: int = 0) -> list[Call]:
    """(project name given with --project or None, subcommand, the rest) for each foreman call in a command line:
    bare `foreman`, any path to it (`bin/foreman`, absolute), `python bin/foreman`, behind the wrappers the guard
    skips (env, time-limit, foreman lock … run), inside $(…) and inside `bash -c "…"` (the guard's lexer and its
    foreman recognizer). The text arguments of foreman note/progress/inbox and git commit are text, not calls."""
    out: list[Call] = []
    for seg in commands(cmd):
        w = words(seg)
        if len(w) > 2 and PurePath(w[0]).name in SHELLS and w[1] == "-c" and depth < 3:
            out += foreman_calls(w[2], depth + 1)
            continue
        call = foreman_call(w)
        if call:
            out.append(call)
    return out


def target(project: Project, name: str | None) -> Project | None:
    """The project a `--project NAME` call acts on (the session's own project when NAME is None or its own name)."""
    if name in (None, project.name):
        return project
    from . import config

    try:
        return next((p for p in config.registered_projects() if p.name == name and p.active), None)
    except Exception:  # noqa: BLE001 (a broken registry reads as no such project)
        return None


def guard_check(project: Project, cmd: str) -> Verdict | None:
    """None, or (paused project, since, the blocked call) for the first `foreman integrate|worktree|launch --repo` the
    command runs on a paused product. `foreman worktree --list` and `--help` only read: they pass."""
    for name, sub, rest in foreman_calls(cmd):
        if sub not in BLOCKED or {"--list", "--help", "-h"} & set(rest):
            continue
        if sub == "launch" and not any(w == "--repo" or w.startswith("--repo=") for w in rest):
            continue  # a launch without a worktree (a planner) passes, like the planners pause-agents lets through
        p = target(project, name)
        at = since(p) if p is not None else None
        if p is not None and at:
            return p, at, sub
    return None


def guard_message(project: Project, verdict: Verdict) -> str:
    from . import i18n

    p, at, sub = verdict
    return i18n.for_project(p)["pause_guard"].format(project=p.name, since=at, what=sub, owner=p.owner)
