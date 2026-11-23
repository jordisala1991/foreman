"""The loop's talk with running agents: the owner's answers delivered, one waiting in its Orca chat noted, a task returned
to its agent."""

from typing import Any

from .. import util
from ..core.project.config import Project
from ..core.tasks import backlog, questions, recovery, runs
from ..providers import registry
from ..providers.runner import Rows, State, ref_of
from ..services.agents.launch import ended, record_run, run_kind
from ..services.agents.running import send, update_agent

EVENT_KINDS = {"rejection": "rejected", "conflict": "conflict", "failing": "failing", "session": "session"}  # a review return stays `other`


def deliver_answers(p: Project, rows: Rows) -> None:
    d = registry.backlog(p).load()
    sent: set[str] = set()  # one answer per task per tick: the session is working after the first
    for q in questions.undelivered(d):
        i = backlog.get(d, q["task"])
        if i["status"] in backlog.CLOSED:
            registry.backlog(p).mark_delivered(q["id"])
            continue
        agent: dict[str, Any] = i.get("agent") or {}
        if i["id"] in sent:
            continue  # one answer per task per tick, sent or failed: the rest waits
        if rows.busy(ref_of(agent)):
            continue  # it is busy (maybe talking with the owner): next tick
        if ended(agent, rows):
            continue  # a parent whose session ended: `parents.run` starts a new session for it
        msg = questions.message_for(i, q)
        if send(p, i, msg):
            registry.backlog(p).mark_delivered(q["id"])
            sent.add(i["id"])
            update_agent(p, i["id"], started=util.now())
            if (kind := run_kind(i, agent)) in recovery.IDLE_KINDS:  # a wakeup: a new turn, a new run
                wake_run(p, i["id"], kind)
            backlog.event(p, i["id"], f"answer {q['id']} delivered to {i['agent']['name']}")
        else:
            sent.add(i["id"])
            registry.backlog(p).update(i["id"], undelivered=i.get("undelivered", 0) + 1)


def wake_run(p: Project, tid: str, kind: str) -> None:
    """A parent or chain resumed with a wakeup: its run so far ends done (it was idle, its turn was over) and a new run of
    its session, as recorded after the send, starts."""
    agent: dict[str, Any] = backlog.get(registry.backlog(p).load(), tid).get("agent") or {}
    if ref := ref_of(agent):
        runs.end(p, tid, (kind,), "done")
        record_run(p, tid, kind, ref, agent)


def watch_waiting(p: Project, rows: Rows) -> None:
    """Under Orca, one event when an agent starts waiting for an answer in its chat (the owner answers it there), marked
    `waiting_seen`, and cleared once it no longer waits."""
    if p.get("agents", "runner") != "orca":
        return
    for i in backlog.tasks(registry.backlog(p).load()):
        for role in ("agent", "mechanic", "hunter", "reviewer"):
            a: dict[str, Any] = i.get(role) or {}
            waits = bool(a) and rows.state(ref_of(a)) is State.WAITING
            if waits == bool(a.get("waiting_seen")):
                continue
            with registry.backlog(p).edit() as d:
                it = backlog.get(d, i["id"])
                it[role] = {**it[role], "waiting_seen": util.now() if waits else None}
            if waits:
                backlog.event(p, i["id"], f"{a.get('name')} waits for an answer in its Orca chat")


def return_to_agent(p: Project, tid: str, kind: str, note: str, by: str = "") -> None:
    """Returns the task to its agent (status in progress), with a note the loop delivers to its session. A reject, a
    conflict or failing tests or CI start a review of only that fix, from round 1 (`review_for`, `review_from`); a
    conflict keeps who accepted it (`resolving`, from `by`), so it merges once resolved without a new accept. Failing
    keeps its check (the panel shows why it is back) and counts the returns (`failures`) and the row (`failure_streak`)."""
    with registry.backlog(p).edit() as d:
        i = backlog.get(d, tid)
        i["status"] = "in progress"
        if kind != "failing":
            i.pop("check", None)
        for k in ("accepted", "merge", "merging"):  # a merge ref or marker of the old tip must not close the new work
            i.pop(k, None)
        if kind == "rejection":
            i["rework"] = i.get("rework", 0) + 1
            i.pop("resolving", None)
        elif kind == "conflict":
            i["conflicts"] = i.get("conflicts", 0) + 1
            if by:
                i["resolving"] = by
        elif kind == "failing":
            i["failures"] = i.get("failures", 0) + 1
            i["failure_streak"] = i.get("failure_streak", 0) + 1
        if kind in ("rejection", "conflict", "failing"):
            i["review_for"] = "reject" if kind == "rejection" else kind
            i["review_from"] = len(i.get("reviews") or [])
        else:
            i.pop("review_for", None)
        agent: dict[str, Any] = {**(i.get("agent") or {}), "started": util.now()}
        i["agent"] = agent
        questions.message(d, i, "R", ("rejection", "conflict", "failing", "review", "session"), kind=kind, text="review", answer=note, at=util.now())
    runs.end(p, tid, ("hunter", "review"), "canceled")  # a review of the old tip has nothing left to decide
    if ref := ref_of(agent):  # the same session works again: a new run of it (the last ended at `foreman done`)
        record_run(p, tid, "responsible", ref, agent)
    backlog.event(p, tid, f"→ in progress: returned to its agent ({kind}): {note}", EVENT_KINDS.get(kind, "other"))


def unblock(p: Project, tid: str) -> None:
    """The owner clears a block (plain unblock). In the same write, a blocked running task's agent is told: a message the
    loop delivers before recovery looks at it; its session is a new run, so a second death is seen too."""
    with registry.backlog(p).edit() as d:
        i = backlog.get(d, tid)
        was_blocked = bool(i.get("blocked"))
        i.update(blocked="", pr_error="", retry_error="", retry_count=0)
        agent: dict[str, Any] = i.get("agent") or {}
        ref = ref_of(agent)
        if not was_blocked or i["status"] not in backlog.RUNNING or ref is None:
            return
        questions.message(d, i, "U", ("unblock",), kind="unblock", text="unblock", answer="unblock", at=util.now())
    record_run(p, tid, run_kind(i, agent), ref, agent)
    backlog.event(p, tid, "owner cleared the block: its agent is told")
