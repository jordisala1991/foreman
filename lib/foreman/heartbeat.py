"""Whether a Claude Code session of a project still runs: the heartbeat foreman's hooks module writes.

Every session of an active project runs the module's timer (hooks/mod/foreman.ts, one tick every TICK_S seconds);
each tick asks `foreman hook mod tick`, which writes the session's heartbeat here before anything else. Its
`session.start` writes one too (`started`), and its `session.end` marks it ended. Nothing reads Claude Code's own
files to tell whether a session runs.

<state>/sessions/<session>.json: {at, started, busy, version, claude_code, cwd, interactive?, ended?, agents?}
- at: the last tick (local ISO, util.now()); started: when the module started in the session (its session.start,
  again after /reload-plugins), or the first tick seen when none was recorded (a /clear goes on under a new id);
- busy: whether a main-loop turn was running at the tick; version: the foreman that wrote it; claude_code: the
  Claude Code version the module gave at its start; cwd: the session's folder; interactive: whether a person is at
  the prompt, as the module's start said (`session.start`'s `isInteractive`: true in the terminal, false in a `-p`
  run or an SDK host), kept by the ticks; information only, nothing decides on it;
- ended: the time of its `session.end` (the session closed, /clear or another took its place);
- agents: in the marked orchestrator's session only, its subagents as `$.agent.list()` gave them at the tick
  ({id: status}, status pending, running, waiting, idle, completed, failed or killed).

liveness(): "alive" while the last tick is at most GONE_TICKS ticks old and the session did not end; "gone" when it
ended or GONE_TICKS ticks went by without one (a crashed or closed Claude Code, a machine asleep: the next tick
says alive again); "unknown" when the session has no heartbeat (the module never ran there: not loaded, Claude Code
below foreman's minimum, or a foreman older than this one). Files untouched for KEEP_DAYS go. Never raises.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from . import util

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

    from .config import Project

Rec = dict[str, Any]  # a session's heartbeat record: {at, started, busy, version, claude_code, cwd, agents?, ended?}

TICK_S = 60  # the module's timer (hooks/mod/foreman.ts TICK_MS); fixed, no config key
GONE_TICKS = 2  # a session whose last tick is older than this many ticks is gone
KEEP_DAYS = 7
AGENT_STATUSES = ("pending", "running", "waiting", "idle", "completed", "failed", "killed")
ENDED_STATUSES = ("completed", "failed", "killed")  # the agent's loop is over, whatever foreman last heard
SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


def sessions_dir(project: Project) -> Path:
    return project.state / "sessions"


def beat_file(project: Project, session: object) -> Path | None:
    """<state>/sessions/<session>.json, or None for a session id that is not a plain name."""
    ok = isinstance(session, str) and SESSION_ID.match(session)
    return sessions_dir(project) / f"{session}.json" if ok else None


def _stamp(now: datetime | None = None) -> str:
    return (now or util.now()).strftime("%Y-%m-%dT%H:%M:%S")


def agent_statuses(agents: object) -> dict[str, str]:
    """{id: status} of a `$.agent.list()` answer as the module passes it ([{id, status, …}]); unknown entries
    dropped."""
    out = {}
    for a in agents if isinstance(agents, list) else []:
        if isinstance(a, dict) and isinstance(a.get("id"), str) and a.get("status") in AGENT_STATUSES:
            out[a["id"]] = a["status"]
    return out


def beat(
    project: Project,
    session: object,
    cwd: object = None,
    busy: object = False,
    claude_code: object = None,
    agents: dict[str, str] | None = None,
    start: bool = False,
    now: datetime | None = None,
    interactive: object = None,
) -> Rec:
    """Writes the session's heartbeat (a tick, or the module's start when `start`): `agents` ({id: status}) is kept
    only when given; `interactive` (a bool) replaces the recorded one, which a tick keeps (a start without it drops
    it). Returns the record. Raises ValueError for a session id that is not a plain name."""
    from . import versions

    f = beat_file(project, session)
    if f is None:
        raise ValueError(f"not a session id: {session!r}")
    before = read(project, session) or {}
    at = _stamp(now)
    rec = {
        "at": at,
        "started": at if start or not before.get("started") or before.get("ended") else before["started"],
        "busy": bool(busy),
        "version": versions.own()["version"],
        "claude_code": claude_code if isinstance(claude_code, str) and claude_code else before.get("claude_code"),
        "cwd": cwd if isinstance(cwd, str) and cwd else before.get("cwd"),
    }
    keep = interactive if isinstance(interactive, bool) else None if start else before.get("interactive")
    if isinstance(keep, bool):
        rec["interactive"] = keep
    if agents is not None:
        rec["agents"] = agents
    f.parent.mkdir(parents=True, exist_ok=True)
    util.write_json(f, rec)
    if start:
        prune(project, now)
    return rec


def end(project: Project, session: object, now: datetime | None = None) -> bool:
    """Marks the session's heartbeat ended (its `session.end`); False when it had none or the id is not a name."""
    rec, f = read(project, session), beat_file(project, session)
    if rec is None or f is None:  # no record without its file
        return False
    rec["ended"] = _stamp(now)
    rec["busy"] = False
    util.write_json(f, rec)
    return True


def prune(project: Project, now: datetime | None = None) -> None:
    cut = (now or util.now()).timestamp() - KEEP_DAYS * 86400
    try:
        files = list(sessions_dir(project).glob("*.json"))
    except OSError:
        return
    for f in files:
        try:
            if f.stat().st_mtime < cut:
                f.unlink()
        except OSError:
            pass


def read(project: Project, session: object) -> Rec | None:
    """The session's heartbeat record, or None (none written, unreadable, not a session id)."""
    f = beat_file(project, session)
    d = util.read_json(f, None) if f else None
    return d if isinstance(d, dict) and isinstance(d.get("at"), str) else None


def state_of(rec: Rec | None, now: datetime | None = None) -> str:
    """ "alive", "gone" or "unknown" of one heartbeat record (None: unknown); see the module doc."""
    if rec is None:
        return "unknown"
    if rec.get("ended"):
        return "gone"
    at = util.iso_epoch(rec.get("at"))
    if at is None:
        return "unknown"
    return "alive" if (now or util.now()).timestamp() - at <= GONE_TICKS * TICK_S else "gone"


def liveness(project: Project, session: object, now: datetime | None = None) -> dict[str, Any]:
    """{"state", "at", "started_ts", "busy", "ended", "version", "claude_code"} of a session (state_of its
    heartbeat; the rest None when it has none). Never raises."""
    rec = read(project, session) if session else None
    out = {
        "state": state_of(rec, now),
        "at": None,
        "started_ts": None,
        "busy": None,
        "ended": None,
        "version": None,
        "claude_code": None,
    }
    if rec is not None:
        out.update(
            at=rec.get("at"),
            started_ts=util.iso_epoch(rec.get("started")),
            busy=bool(rec.get("busy")),
            ended=rec.get("ended"),
            version=rec.get("version"),
            claude_code=rec.get("claude_code"),
        )
    return out


def live(project: Project, now: datetime | None = None) -> list[tuple[str, Rec]]:
    """[(session, record)] of the project's sessions whose heartbeat says alive, newest tick first. Never raises."""
    try:
        files = list(sessions_dir(project).glob("*.json"))
    except OSError:
        return []
    out = []
    for f in files:
        d = util.read_json(f, None)
        if isinstance(d, dict) and SESSION_ID.match(f.stem) and state_of(d, now) == "alive":
            out.append((f.stem, d))
    out.sort(key=lambda x: x[1].get("at") or "", reverse=True)
    return out


def agents_of(project: Project, session: object) -> dict[str, str]:
    """{agent id: status} of the session's subagents at its last tick (the marked orchestrator's only), or {}."""
    rec = read(project, session) or {}
    a = rec.get("agents")
    return {k: v for k, v in a.items() if v in AGENT_STATUSES} if isinstance(a, dict) else {}
