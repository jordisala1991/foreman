"""The loop's talk with running agents: the owner's answers delivered, a silent agent nudged, a task returned to its agent."""

from typing import Any

from . import backlog, sessions, util
from .agents import idle_s, send, update_agent
from .config import Project

NUDGE_IDLE_S = 600  # an agent's turn ended and its transcript is quiet this long: nudge it (then block it)


def message_for(i: dict[str, Any], q: dict[str, Any]) -> str:
    """What an agent is told about a delivered answer, by its kind."""
    if q.get("kind") in ("conflict", "review"):
        return q["answer"]
    elif q.get("kind") == "rejection":
        return f"The owner rejected task {i['id']}: «{q['answer']}». Fix it, review again if you change much, and run `fm done` again."
    elif q.get("kind") == "note":
        return f"A note from outside fm for task {i['id']} ({q.get('source')}): «{q['answer']}»."
    elif q.get("kind") == "permission":
        return f"The owner answered your request {q['id']} ({q.get('action')}): «{q['answer']}». " + (
            "It is allowed once: run it again." if q.get("allowed") else "It is not allowed."
        )
    else:
        return f"The owner answered your question {q['id']}: «{q['answer']}». Carry on with the task."


def deliver_answers(p: Project, rows: dict[str, Any]) -> None:
    d = backlog.load(p)
    sent: set[str] = set()  # one answer per task per tick: the session is working after the first
    for q in backlog.undelivered(d):
        i = backlog.get(d, q["task"])
        if i["status"] in backlog.CLOSED:
            backlog.mark_delivered(p, q["id"])
            continue
        agent: dict[str, Any] = i.get("agent") or {}
        if i["id"] in sent:
            continue  # one answer per task per tick, sent or failed: the rest waits
        if sessions.busy(rows, agent.get("session")):
            continue  # it is busy (maybe talking with the owner): next tick
        msg = message_for(i, q)
        if send(p, i, msg):
            backlog.mark_delivered(p, q["id"])
            sent.add(i["id"])
            update_agent(p, i["id"], nudged=None, started=util.now())
            backlog.event(p, i["id"], f"answer {q['id']} delivered to {i['agent']['name']}")
        else:
            sent.add(i["id"])
            backlog.update(p, i["id"], undelivered=i.get("undelivered", 0) + 1)


def watch_agents(p: Project, rows: dict[str, Any]) -> None:
    d = backlog.load(p)
    waiting = {q["task"] for q in backlog.open_questions(d)} | {q["task"] for q in backlog.undelivered(d)}
    for i in backlog.tasks(d):
        a: dict[str, Any] = i.get("agent") or {}
        if i["status"] not in backlog.RUNNING or i.get("blocked") or i["id"] in waiting:
            continue
        if not a.get("session") or sessions.busy(rows, a.get("session")):
            continue  # unknown yet (fill_sessions), or busy
        if idle_s(a) < NUDGE_IDLE_S:
            continue  # it may be waiting for the owner, who is talking to it

        if not a.get("nudged"):
            how = 'fm ready (--plan PATH, or --no-plan "why trivial")' if a.get("kind") == "planning" else "fm done"
            if send(
                p,
                i,
                f"Your turn ended but task {i['id']} is not finished: nothing says so. If it is, run "
                f"`{how}`; if you need the owner, `fm ask`; if it cannot be done, `fm block`. "
                "Otherwise carry on.",
            ):
                update_agent(p, i["id"], nudged=util.now())
                backlog.update(p, i["id"], nudges=i.get("nudges", 0) + 1)
                backlog.event(p, i["id"], f"{a.get('name')} ended its turn without finishing: nudged")
                continue
            update_agent(p, i["id"], nudged=util.now())  # a failed nudge counts too: the block below then fires
        if (util.age_s(a.get("nudged")) or 0) > NUDGE_IDLE_S and idle_s(a) > NUDGE_IDLE_S:
            backlog.update(p, i["id"], blocked="its agent stopped without finishing, even after a nudge")
            backlog.event(p, i["id"], "blocked: agent stopped without finishing")


def return_to_agent(p: Project, tid: str, kind: str, note: str) -> None:
    """Returns the task to its agent (status in progress), with a note the loop delivers to its session."""
    with backlog.edit(p) as d:
        i = backlog.get(d, tid)
        i["status"] = "in progress"
        i.pop("check", None)
        for k in ("accepted", "merge", "merging"):  # a merge ref or marker of the old tip must not close the new work
            i.pop(k, None)
        if kind == "rejection":
            i["rework"] = i.get("rework", 0) + 1
        elif kind == "conflict":
            i["conflicts"] = i.get("conflicts", 0) + 1
        agent: dict[str, Any] = {**(i.get("agent") or {}), "started": util.now(), "nudged": None}
        i["agent"] = agent
        backlog.message(d, i, "R", ("rejection", "conflict", "review"), kind=kind, text="review", answer=note, at=util.now())
    backlog.event(p, tid, f"→ in progress: returned to its agent ({kind}): {note}")
