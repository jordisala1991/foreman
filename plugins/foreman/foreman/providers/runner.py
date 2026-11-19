"""The runner port: every agent session fm runs, behind one interface. One session per `SessionRef`; what crosses the port is
typed (a `State`, a `SessionRef`, a `Rows` snapshot), never claude's raw listing. The adapter is `claude.Claude`, handed out
by `registry.runner(p)`; the registry's `rows(p)` is the tick's snapshot.

`stop_live` and `settings` are shared: they use only the port."""

import os
import shlex
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from .. import launcher
from ..core.project import plugin
from ..core.project.config import Project
from ..core.tasks import backlog


class State(Enum):
    WORKING = "working"
    WAITING = "waiting"  # live and not idle: waiting for an answer or a permission
    IDLE = "idle"  # ended its turn
    ENDED = "ended"  # stopped
    GONE = "gone"  # not in the snapshot


@dataclass(frozen=True)
class Agent:
    """Who a session is for: its task, its name, its model."""

    tid: str
    name: str
    model: str
    effort: str
    title: str  # display only («ID role · title», util.session_title): `name` stays the identity


@dataclass(frozen=True)
class SessionRef:
    """What the backlog's agent keeps of its session."""

    short: str  # "" when unknown
    session: str | None
    terminal: str | None = None  # the tab's handle and pane: empty for claude
    pane: str | None = None
    started: str | None = None  # the agent's `started` (launch, each send)

    def fields(self) -> dict[str, str | None]:
        """The agent's keys for this ref: `terminal` and `pane` only when the ref has a terminal."""
        f: dict[str, str | None] = {"short": self.short, "session": self.session}
        if self.terminal:
            f |= {"terminal": self.terminal, "pane": self.pane}
        return f


def ref_of(agent: Mapping[str, Any] | None) -> SessionRef | None:
    """The `SessionRef` of a task's agent record; None when it has neither a short id nor a session."""
    if not agent or not (agent.get("short") or agent.get("session")):
        return None
    return SessionRef(agent.get("short") or "", agent.get("session"), agent.get("terminal"), agent.get("pane"), agent.get("started"))


class Rows(Protocol):
    """One snapshot of the runner's sessions per tick."""

    def state(self, ref: SessionRef | None) -> State: ...
    def busy(self, ref: SessionRef | None) -> bool: ...  # WORKING or WAITING, and not idle
    def alive(self, ref: SessionRef | None) -> bool: ...
    def find(self, short: str) -> str | None: ...  # the full session id of a short one
    def short(self, session: str) -> str | None: ...
    def has_sessions(self) -> bool: ...


class Runner(Protocol):
    def start(self, agent: Agent, brief: str, cwd: Path) -> SessionRef: ...
    def send(self, ref: SessionRef, message: str, cwd: Path) -> SessionRef: ...
    def rows(self) -> Rows | None: ...  # None: unreadable; callers do nothing, as today
    def stop(self, ref: SessionRef) -> None: ...


class RunnerError(Exception):
    pass


class RunnerUnavailable(RunnerError):
    pass


class _NoRows:
    """The snapshot of an unreadable listing: no session is known."""

    def state(self, ref: SessionRef | None) -> State:
        return State.GONE

    def busy(self, ref: SessionRef | None) -> bool:
        return False

    def alive(self, ref: SessionRef | None) -> bool:
        return False

    def find(self, short: str) -> str | None:
        return None

    def short(self, session: str) -> str | None:
        return None

    def has_sessions(self) -> bool:
        return False


NO_ROWS: Rows = _NoRows()


def settings(p: Project, tid: str, agent: str) -> dict[str, Any]:
    """The agent's `--settings`. The fm plugin is off: an agent gets nothing from it, its brief is its only instructions."""
    bin_ = launcher.ROOT / "bin"
    env = {
        "FOREMAN_HOME": str(p.home),
        "FOREMAN_TASK": tid,
        "FOREMAN_AGENT": agent,
        "PATH": f"{bin_}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    guard = shlex.join([sys.executable, str(bin_ / "foreman"), "guard"])
    s: dict[str, Any] = {
        "worktree": {"bgIsolation": "none"},
        "env": env,
        "enabledPlugins": {plugin.PLUGIN: False},
        "hooks": {"PreToolUse": [{"matcher": "Bash|Edit|Write|MultiEdit|NotebookEdit", "hooks": [{"type": "command", "command": guard}]}]},
    }
    extra: dict[str, Any] = p.get("agents", "settings") or {}
    for k, v in extra.items():  # the project's extra settings, e.g. enabledPlugins
        s[k] = {**s[k], **v} if isinstance(v, dict) and isinstance(s.get(k), dict) else v
    return s


def stop_live(p: Project, tid: str, ref: SessionRef | None, runner: Runner, rows: Rows) -> None:
    """Best-effort: stops a recorded agent's session unless the runner shows it ended. A failure is noted on the task,
    never raised: cleanup must not block a merge, a cancel or a review."""
    if ref and ref.short and rows.state(ref) is not State.ENDED:
        try:
            runner.stop(ref)
        except RunnerError as e:
            backlog.event(p, tid, f"could not stop session {ref.short}: {e}")
