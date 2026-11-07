"""The agents' state: which agents live (<state>/agents.json), their one state (working, waiting, delivered,
stopped), their last sign of life, and `foreman agents`. Read by the loop pass, the hooks, delivery, identity and the
panel; the loop pass itself (what to act on) is loop.py."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import time
from collections.abc import Collection
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import identity, util

if TYPE_CHECKING:
    from .config import Project
    from .identity import Agent


def agents_file(project: Project) -> Path:
    return project.state / "agents.json"


def agents(project: Project) -> dict[str, Agent]:
    return util.read_json(agents_file(project), {})


WAIT_LIMIT = 3 * 3600  # an agent stopped without delivering counts as live this long (the last resort)
LIVE_STATES = ("working", "waiting")
SESSION_GONE = ("failed", "stopped")  # `claude agents --json` states of a session that is over and not done
CLAUDE_TIMEOUT_S = 10


def agent_state(h: Agent, now: float | None = None) -> str:
    """The one state of an agents.json entry, for the loop, `foreman agents` and the panel:
    `stopped` (marked with `foreman agents --stop`, or stopped without delivering more than WAIT_LIMIT ago, or a
    session agent whose session `claude agents --json` lists as failed or stopped: `claude_state`),
    `delivered` (it ran `foreman progress --done`: the agents hook sets `delivered`, and clears it when the agent is
    resumed), `waiting` (it stopped without delivering: waiting for a background task of its own) or `working`."""
    if h.get("stopped"):
        return "stopped"
    if h.get("delivered"):
        return "delivered"
    if h.get("claude_state") in SESSION_GONE:  # a session agent Claude Code says stopped or failed (refresh_sessions)
        return "stopped"
    end = h.get("end")
    if end:
        return "waiting" if (now or time.time()) - (util.iso_epoch(end) or 0) <= WAIT_LIMIT else "stopped"
    return "working"


def session_of(h: Agent) -> str | None:
    """The session an agent runs in (its transcript's, else the one recorded), None when not known."""
    from . import transcripts

    return transcripts.session_of_subagent(h.get("transcript")) or h.get("session")


def last_sign(h: Agent) -> float | None:
    """Epoch seconds of an agent's last sign of life: the latest of its recorded tool activity (`activity`, else
    `start`) and its transcript's modification time; None if neither is known."""
    times: list[float | None] = [util.iso_epoch(h.get("activity") or h.get("start"))]
    with contextlib.suppress(OSError, TypeError, ValueError):
        times.append(os.path.getmtime(h.get("transcript") or ""))
    found = [t for t in times if t is not None]
    return max(found) if found else None


def quiet_minutes(project: Project, h: Agent, busy: Collection[str] = (), now: float | None = None) -> int | None:
    """Minutes a live agent (agent_state) has been quiet (last_sign) when that is at least [agents] stalled_min
    (default 20) and its name is not in `busy` (lock.people: it holds or waits for a lock), else None. One rule for
    the loop's `stalled_agents` and the panel's «maybe stuck»: usually waiting for a background job that already
    ended."""
    now = now or time.time()
    last = last_sign(h)
    if agent_state(h, now) not in LIVE_STATES or last is None:
        return None
    if now - last < 60 * project.section("agents").get("stalled_min", 20):
        return None
    if busy and h.get("name") in busy:
        return None
    return int((now - last) / 60)


def claude_rows(timeout: int = CLAUDE_TIMEOUT_S) -> list[dict[str, Any]] | None:
    """The rows of `claude agents --json`, or None when they cannot be read (no `claude`, an error, not JSON)."""
    exe = shutil.which("claude")
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "agents", "--json"], capture_output=True, text=True, timeout=timeout, check=False)
        rows = json.loads(r.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return [x for x in rows if isinstance(x, dict)] if isinstance(rows, list) else None


def refresh_sessions(project: Project) -> bool:
    """Crosses the session agents (identity.SESSION_MARK) of agents.json with `claude agents --json`: a session Claude
    Code lists as failed or stopped is recorded as `claude_state` (agent_state then says `stopped`, unless it
    delivered); one that is listed otherwise, or woken again, loses it. No session agent, or rows that cannot be read:
    nothing is run or changed. Whether it changed an entry. Never raises."""
    try:
        if not any(isinstance(h, dict) and h.get(identity.SESSION_MARK) for h in agents(project).values()):
            return False
        rows = claude_rows()
        if rows is None:
            return False
        state = {x["sessionId"]: x.get("state") for x in rows if isinstance(x.get("sessionId"), str)}
        changed = False
        f = agents_file(project)
        with util.locked_json(f, {}) as d:
            for sid, h in d.items():
                if not isinstance(h, dict) or not h.get(identity.SESSION_MARK) or sid not in state:
                    continue
                st = state[sid]
                if st in SESSION_GONE and h.get("claude_state") != st:
                    h["claude_state"], changed = st, True
                elif st not in SESSION_GONE and h.pop("claude_state", None):
                    changed = True
            if changed:
                util.write_json(f, d)
        return changed
    except Exception:  # noqa: BLE001 (a pass never fails over the cross-check)
        return False


def stop_session(session_id: str) -> str | None:
    """`claude stop SESSION_ID`: None when it stopped, else why not (for the one who asked)."""
    exe = shutil.which("claude")
    if not exe:
        return "no `claude` command in PATH"
    try:
        r = subprocess.run([exe, "stop", session_id], capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        return str(e)
    if r.returncode:
        return " ".join((r.stderr or r.stdout).split())[:200] or f"exit {r.returncode}"
    return None


def stop_agent(project: Project, aid: str, errors: list[str] | None = None) -> list[str]:
    """Marks the agents matching `aid` (id, prefix or name) stopped; a session agent's session is also stopped
    (`claude stop`), and a failure of that goes into `errors`."""
    f = agents_file(project)
    sessions: list[str] = []
    with util.locked_json(f, {}) as d:
        hits = [k for k in d if k == aid or k.startswith(aid) or d[k].get("name") == aid]
        for k in hits:
            d[k]["stopped"] = True
            if d[k].get(identity.SESSION_MARK):
                sessions.append(k)
        util.write_json(f, d)
    for sid in sessions:
        why = stop_session(sid)
        if why and errors is not None:
            errors.append(f"{sid[:8]}: claude stop failed ({why})")
    return hits


def agents_json(project: Project) -> str:
    """agents.json with each agent's computed state (agent_state) as `state` and its identity (identity.of: the
    recorded one, with the fallback for what is missing) as `identity`."""
    from .backlog import Backlog, BacklogError  # lazy: the hooks read agents.json without the backlog

    b = Backlog(project)
    try:
        data = b.load()
        branch_of = {
            s["agent"]: s["branch"]
            for _, i, _ in b.iter_issues(data, archived=False)
            for s in i["subs"]
            if s.get("agent") and s.get("branch") and s.get("status") == "in progress"
        }
    except (BacklogError, OSError, ValueError, KeyError):
        branch_of = {}
    return json.dumps(
        {
            k: {**h, "state": agent_state(h), "identity": identity.of(project, h, branch_of=branch_of)}
            for k, h in agents(project).items()
        },
        ensure_ascii=False,
        indent=1,
    )


def agents_compact(project: Project) -> str:
    """`foreman agents` by default: one line per agent: id (8), name, state, issue/sub, branch, minutes
    (from its start to its end, or to now), model and, once delivered, its outcome. `--json` gives agents_json."""
    full = json.loads(agents_json(project))
    now = time.time()
    lines: list[str] = []
    for aid, h in full.items():
        ident = h.get("identity") or {}
        start = util.iso_epoch(h.get("start"))
        end = util.iso_epoch(h.get("end")) if h.get("end") else None
        mins = f"{max(0, round(((end or now) - start) / 60))}min" if start else "-"
        issue = ident.get("issue") or "-"
        if ident.get("sub"):
            issue += "/" + ident["sub"]
        parts = [
            aid[:8],
            ident.get("name") or "-",
            h["state"],
            issue,
            ident.get("branch") or "-",
            mins,
            h.get("model") or h.get("model_asked") or "-",
        ]
        if h.get("delivered") and h.get("outcome"):
            parts.append(h["outcome"])
        lines.append(" ".join(parts))
    return "\n".join(lines) if lines else "no agents"
