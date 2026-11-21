"""The loop's talk with running agents: the owner's answers delivered, a silent agent nudged, one waiting in its Orca chat
noted, a task returned to its agent."""

from typing import Any

from .. import util
from ..core.project.config import Project
from ..core.tasks import backlog, questions
from ..providers import registry
from ..providers.runner import Rows, State, ref_of
from ..services.agents.running import idle_s, send, update_agent

NUDGE_IDLE_S = 600  # an agent's turn ended and its transcript is quiet this long: nudge it (then block it)


def message_for(i: dict[str, Any], q: dict[str, Any]) -> str:
    """What an agent is told about a delivered answer, by its kind."""
    if q.get("kind") in ("conflict", "failing", "review", "subtask"):
        return q["answer"]
    elif q.get("kind") == "rejection":
        return f"The owner rejected task {i['id']}: «{q['answer']}». Fix it, review again if you change much, and run `foreman done` again."
    elif q.get("kind") == "note":
        return f"A note from outside fm for task {i['id']} ({q.get('source')}): «{q['answer']}»."
    elif q.get("kind") == "permission":
        return f"The owner answered your request {q['id']} ({q.get('action')}): «{q['answer']}». " + (
            "It is allowed once: run it again." if q.get("allowed") else "It is not allowed."
        )
    else:
        return f"The owner answered your question {q['id']}: «{q['answer']}». Carry on with the task."


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
        msg = message_for(i, q)
        if send(p, i, msg):
            registry.backlog(p).mark_delivered(q["id"])
            sent.add(i["id"])
            update_agent(p, i["id"], nudged=None, started=util.now())
            backlog.event(p, i["id"], f"answer {q['id']} delivered to {i['agent']['name']}")
        else:
            sent.add(i["id"])
            registry.backlog(p).update(i["id"], undelivered=i.get("undelivered", 0) + 1)


def watch_agents(p: Project, rows: Rows) -> None:
    d = registry.backlog(p).load()
    waiting = {q["task"] for q in questions.open_questions(d)} | {q["task"] for q in questions.undelivered(d)}
    for i in backlog.tasks(d):
        a: dict[str, Any] = i.get("agent") or {}
        if i["status"] not in backlog.RUNNING or i.get("blocked") or i["id"] in waiting:
            continue
        if not a.get("session") or rows.busy(ref_of(a)):
            continue  # unknown yet (fill_sessions), or busy
        if idle_s(a) < NUDGE_IDLE_S:
            continue  # it may be waiting for the owner, who is talking to it

        if not a.get("nudged"):
            how = 'foreman ready (--plan PATH, or --no-plan "why trivial")' if a.get("kind") == "planning" else "foreman done"
            if send(
                p,
                i,
                f"Your turn ended but task {i['id']} is not finished: nothing says so. If it is, run "
                f"`{how}`; if you need the owner, `foreman ask`; if it cannot be done, `foreman block`. "
                "Otherwise carry on.",
            ):
                update_agent(p, i["id"], nudged=util.now())
                registry.backlog(p).update(i["id"], nudges=i.get("nudges", 0) + 1)
                backlog.event(p, i["id"], f"{a.get('name')} ended its turn without finishing: nudged")
                continue
            update_agent(p, i["id"], nudged=util.now())  # a failed nudge counts too: the block below then fires
        if (util.age_s(a.get("nudged")) or 0) > NUDGE_IDLE_S and idle_s(a) > NUDGE_IDLE_S:
            registry.backlog(p).update(i["id"], blocked="its agent stopped without finishing, even after a nudge")
            backlog.event(p, i["id"], "blocked: agent stopped without finishing")


def watch_waiting(p: Project, rows: Rows) -> None:
    """Under Orca, one event when an agent starts waiting for an answer in its chat (the owner answers it there), marked
    `waiting_seen` as a nudge is, and cleared once it no longer waits."""
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
        agent: dict[str, Any] = {**(i.get("agent") or {}), "started": util.now(), "nudged": None}
        i["agent"] = agent
        questions.message(d, i, "R", ("rejection", "conflict", "failing", "review"), kind=kind, text="review", answer=note, at=util.now())
    backlog.event(p, tid, f"→ in progress: returned to its agent ({kind}): {note}")
