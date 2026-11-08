"""The loop's talk with running agents: the owner's answers delivered, a silent agent nudged, a task sent back."""

from typing import Any

from . import backlog, sessions, util
from .agents import idle_s, send
from .config import Project

NUDGE_IDLE_S = 600  # an agent's turn ended and its transcript is quiet this long: nudge it (then block it)
MAX_SEND_FAILS = 3


def deliver_answers(p: Project, rows: dict[str, Any]) -> None:
    d = backlog.load(p)
    for q in backlog.undelivered(d):
        i = backlog.get(d, q["issue"])
        if i["status"] in backlog.CLOSED:
            backlog.mark_delivered(p, q["id"])
            continue
        agent: dict[str, Any] = i.get("agent") or {}
        if sessions.busy(rows, agent.get("session")):
            continue  # it is busy (maybe talking with the owner): next tick
        if q.get("kind") in ("conflict", "review"):
            msg = q["answer"]
        elif q.get("kind") == "rejection":
            msg = (
                f"The owner sent task {i['id']} back from the integration queue: «{q['answer']}». Fix it, review "
                "again if you change much, and run `fm done` again."
            )
        elif q.get("kind") == "note":
            msg = f"A note from outside fm for task {i['id']} ({q.get('source')}): «{q['answer']}»."
        elif q.get("kind") == "permission":
            msg = f"The owner answered your request {q['id']} ({q.get('action')}): «{q['answer']}». " + (
                "It is allowed once: run it again." if q.get("allowed") else "It is not allowed."
            )
        else:
            msg = f"The owner answered your question {q['id']}: «{q['answer']}». Carry on with the task."
        if send(p, i, msg):
            backlog.mark_delivered(p, q["id"])
            backlog.update(p, i["id"], agent={**i["agent"], "nudged": None, "started": util.now()})
            backlog.event(p, i["id"], f"answer {q['id']} delivered to {i['agent']['name']}")
        else:
            fails = i.get("undelivered", 0) + 1
            backlog.update(p, i["id"], undelivered=fails)
            if fails >= MAX_SEND_FAILS:
                backlog.mark_delivered(p, q["id"])
                backlog.update(p, i["id"], blocked=f"its agent could not be reached to deliver {q['id']}: «{q['answer']}». Open it and tell it, or relaunch.")


def watch_agents(p: Project, rows: dict[str, Any]) -> None:
    d = backlog.load(p)
    waiting = {q["issue"] for q in backlog.open_questions(d)} | {q["issue"] for q in backlog.undelivered(d)}
    for i in backlog.issues(d):
        a: dict[str, Any] = i.get("agent") or {}
        if i["status"] not in backlog.RUNNING or i.get("blocked") or i["id"] in waiting:
            continue
        if not a.get("session") or sessions.busy(rows, a.get("session")):
            continue  # unknown yet (fill_sessions), or busy
        if idle_s(a) < NUDGE_IDLE_S:
            continue  # it may be waiting for the owner, who is talking to it

        if not a.get("nudged"):
            how = 'fm ready (--plan PATH, or --no-plan "why trivial")' if a.get("kind") == "define" else "fm done"
            if send(
                p,
                i,
                f"Your turn ended but task {i['id']} is not finished: nothing says so. If it is, run "
                f"`{how}`; if you need the owner, `fm ask`; if it cannot be done, `fm block`. "
                "Otherwise carry on.",
            ):
                backlog.update(p, i["id"], agent={**a, "nudged": util.now()}, nudges=i.get("nudges", 0) + 1)
                backlog.event(p, i["id"], f"{a.get('name')} ended its turn without finishing: nudged")
                continue
        if (util.age_s(a.get("nudged")) or 0) > NUDGE_IDLE_S and idle_s(a) > NUDGE_IDLE_S:
            backlog.update(p, i["id"], blocked="its agent stopped without finishing, even after a nudge")
            backlog.event(p, i["id"], "blocked: agent stopped without finishing")


def send_back(p: Project, iid: str, kind: str, note: str) -> None:
    """Back to its agent (status in progress), with a note the loop delivers to its session."""
    with backlog.edit(p) as d:
        i = backlog.get(d, iid)
        n = sum(1 for q in d["decisions_resolved"] if q.get("issue") == iid and q.get("kind") in ("rejection", "conflict", "review"))
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
        d["decisions_resolved"].append(
            {
                "id": f"{iid} R{n + 1}",
                "issue": iid,
                "kind": kind,
                "agent": agent.get("name"),
                "text": "integration",
                "answer": note,
                "resolved": util.now()[:10],
                "at": util.now(),
                "delivered": False,
                "options": [],
                "title": i["title"],
            }
        )
    backlog.event(p, iid, f"→ in progress: sent back ({kind}): {note}")
