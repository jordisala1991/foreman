"""The loop's recover step: an active run whose session ended, never started or went silent is closed as failed with its
reason (`core/tasks/recovery.py`), then retried, its session resumed or else started again through its kind's own launch
path, or handed to the owner. It runs first thing after a loop restart too: the ledger and the runner's listing are all
it reads, and `state/runner.json` keeps the runner's epoch to tell a runner restart from a session's own end."""

from typing import Any

from .. import util
from ..core.project.config import Project
from ..core.tasks import backlog, questions, recovery, runs
from ..providers import registry
from ..providers.runner import Rows, State, ref_of
from ..services.agents.launch import record_run
from ..services.agents.running import idle_s, last_said, send
from .live import ROLE
from .mechanic import note_missing

FINISH = {
    "planning": 'run `foreman ready (--plan PATH, or --no-plan "why trivial")`',
    "responsible": "run `foreman done`",
    "review": "run `foreman verdict`",
    "hunter": "write your findings file",
    "mechanic": "run `foreman diagnose`",
}
DROPPED = ("hunter", "review", "mechanic")  # a dropped record is launched again by the review step or the mechanic step


def message(tid: str, kind: str, why: str) -> str:
    return (
        f"Your session ended before task {tid} was finished ({why}). Carry on where you left off. To finish: "
        f"{FINISH[kind]}; if you need the owner, `foreman ask`; if it cannot be done, `foreman block`."
    )


def run(p: Project, rows: Rows) -> None:
    """Each active run of an open task that is neither blocked nor waiting for the loop; nothing on an unreadable listing."""
    if not rows.has_sessions():
        return
    state = p.state / "runner.json"
    seen = str(util.read_json(state, {}).get("epoch") or "")
    restarted = bool(rows.epoch() and seen and rows.epoch() != seen)
    d = registry.backlog(p).load()
    waiting = questions.tasks_waiting(d)
    active = [r for r in runs.load(p) if r.active]
    for i in backlog.tasks(d):
        if i["status"] in backlog.CLOSED or i.get("blocked") or i["id"] in waiting:
            continue
        for r in (r for r in active if r.task == i["id"]):
            recover(p, i, r, rows, restarted)
    if rows.epoch():
        util.write_json(state, {"epoch": rows.epoch()})


def recover(p: Project, i: dict[str, Any], r: runs.Run, rows: Rows, restarted: bool) -> None:
    role = ROLE.get(r.kind, "agent")
    record: dict[str, Any] = i.get(role) or {}
    ref = ref_of(record)
    if ref is None:
        return
    seen = rows.state(ref)
    facts = recovery.Facts(
        known=bool(record.get("session")),
        ended=seen in (State.ENDED, State.GONE),
        restarted=restarted,
        idle=seen is State.IDLE,
        quiet_s=idle_s(record),
        age_s=util.age_s(r.started) or 0,
    )
    why = recovery.reason(r.kind, facts)
    if not why:
        return
    tid = i["id"]
    runs.end(p, tid, (r.kind,), "failed", why)
    n = recovery.streak([x for x in runs.load(p) if x.task == tid and x.kind == r.kind])
    if recovery.next_step(why, n) == "owner":
        to_owner(p, i, r.kind, why, n)
    elif r.kind not in recovery.IDLE_KINDS:  # a parent is never resumed: the parents step launches a new one, told where it stands
        retry(p, i, r.kind, why, n)


def retry(p: Project, i: dict[str, Any], kind: str, why: str, n: int) -> None:
    """Resumes the session; when the runner cannot, the kind's own launch path starts it again."""
    tid, role = i["id"], ROLE.get(kind, "agent")
    if send(p, i, message(tid, kind, why), role):
        with registry.backlog(p).edit() as d:
            it = backlog.get(d, tid)
            record: dict[str, Any] = {**it[role], "started": util.now()}
            it[role] = record
        if ref := ref_of(record):
            record_run(p, tid, kind, ref, record)
        backlog.event(p, tid, f"{kind} run ended ({why}): resumed, attempt {n + 1}")
        return
    if kind in DROPPED:
        drop(p, tid, role)
    else:
        registry.backlog(p).set_status(tid, "backlog" if i["status"] == "refining" else "ready", f"recovery: {why}")
    backlog.event(p, tid, f"{kind} run ended ({why}): started again")


def to_owner(p: Project, i: dict[str, Any], kind: str, why: str, n: int) -> None:
    """One need for the owner: a mechanic's missing diagnosis, else the task blocked with the reason and its last words."""
    tid = i["id"]
    said = last_said(i.get(ROLE.get(kind, "agent")) or {})
    if kind == "mechanic":
        note_missing(p, tid, f"stopped without finishing ({why}, attempt {n}); it last said: {said or 'nothing'}")
        return
    text = f"its agent stopped without finishing ({why}, attempt {n}); it last said: {said or 'nothing'}"[:300]
    registry.backlog(p).update(tid, blocked=text)
    if kind in DROPPED:  # an unblock then lets the review step launch it again
        drop(p, tid, ROLE[kind])
    backlog.event(p, tid, f"blocked: {kind} run ended ({why}) {n} times in a row")


def drop(p: Project, tid: str, role: str) -> None:
    with registry.backlog(p).edit() as d:
        backlog.get(d, tid).pop(role, None)
