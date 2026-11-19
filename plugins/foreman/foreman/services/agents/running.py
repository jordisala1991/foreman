"""Which agents are running: idle time, and the tasks that hold a slot."""

import time
from pathlib import Path
from typing import Any

from ... import util
from ...core.oversight import costs
from ...core.project.config import Project
from ...core.tasks import backlog, questions
from ...providers import registry
from ...providers.runner import RunnerError, ref_of


def idle_s(a: backlog.Agent | dict[str, Any]) -> float:
    """Seconds since an agent's transcript last changed (or since it was started/resumed, if later)."""
    files = costs.files_of(a.get("session") or "")
    last = max((f.stat().st_mtime for f in files), default=0.0)
    since = util.age_s(a.get("started"))
    return min(time.time() - last if last else 1e9, since if since is not None else 1e9)


def reviewer_active(i: dict[str, Any]) -> bool:
    """A review works on the current tip: the reviewer without a verdict, or the hunter before its file exists."""
    return i["status"] == "in review" and not i.get("blocked") and review_running(i)


def review_running(i: dict[str, Any]) -> bool:
    """A review has no verdict yet on its tip, blocked task or not."""
    v: dict[str, Any] = i.get("review") or {}
    h: dict[str, Any] = i.get("hunter") or {}
    r: dict[str, Any] = i.get("reviewer") or {}
    if r and r["tip"] != v.get("tip"):
        return True
    return bool(h) and h["tip"] not in (v.get("tip"), r.get("tip")) and not Path(h["file"]).exists()


def diagnosing(i: dict[str, Any]) -> bool:
    """A mechanic is working on this triage task: it has no diagnosis yet."""
    return i["status"] == "triage" and bool(i.get("mechanic")) and not i.get("diagnosis")


def running(d: dict[str, Any]) -> int:
    return sum(1 for i in backlog.tasks(d) if questions.holds_slot(d, i) or reviewer_active(i) or diagnosing(i))


def update_agent(p: Project, tid: str, **fields: Any) -> None:
    """Merges fields into the task's agent, from the current backlog (not from a read made earlier)."""
    with backlog.edit(p) as d:
        it = backlog.get(d, tid)
        it["agent"] = {**(it.get("agent") or {}), **fields}


def send(p: Project, i: dict[str, Any], message: str) -> bool:
    """Resumes the task's agent with a message; False when it has no session to resume."""
    a: dict[str, Any] = i.get("agent") or {}
    ref = ref_of(a)
    if not a.get("session") or ref is None:
        return False
    try:
        new = registry.runner(p).send(ref, message, Path(a.get("cwd") or p.repo))
    except RunnerError as e:
        backlog.event(p, i["id"], f"could not reach {a['name']}: {e}")
        return False
    if new != ref:  # Orca started the chat again under a new dispatch id (after a restart): keep it
        with backlog.edit(p) as d:
            it = backlog.get(d, i["id"])
            it["agent"] = {**(it.get("agent") or {}), **new.fields()}
            it["session_tabs"] = {**it.get("session_tabs", {}), a["session"]: new.tab()}
    return True
