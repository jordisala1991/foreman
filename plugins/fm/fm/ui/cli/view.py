"""fm list and fm show: the backlog and one task, as read."""

import argparse
import json

from ...core.rules import decide
from ...core.tasks import backlog, blockers, questions, recurring, subtasks, tasktypes
from .common import home


def cmd_list(a: argparse.Namespace) -> None:
    p = home(a)
    d = backlog.load(p)
    all_ = backlog.tasks(d)
    status = {i["id"]: i["status"] for i in all_}
    for i in subtasks.ordered(all_):
        if i["status"] in backlog.CLOSED and not a.all:
            continue
        agent: backlog.Agent = i.get("agent") or {}
        ag = agent.get("name", "")
        title = ("  " if i.get("parent") in status and (a.all or status[i["parent"]] not in backlog.CLOSED) else "") + i["title"]
        note = (f"  [blocked: {i['blocked']}]" if i.get("blocked") else "") + (f"  [{w}]" if (w := blockers.label(i, all_)) else "")
        note += f"  [recurring: {name}]" if (name := recurring.name_of(i)) else ""
        print(f"{i['id']:8} {i['status']:12} {tasktypes.of(i, all_):10} {ag:14} {title}{note}")
    for q in questions.open_questions(d):
        print(f"? {q['id']} ({q.get('agent')}): {q['text']}")


def cmd_show(a: argparse.Namespace) -> None:
    p = home(a)
    d = backlog.load(p)
    i = recurring.shown(tasktypes.shown(backlog.get(d, a.id), backlog.tasks(d)))
    i = {**i, "checked_on": decide.ran_on(i["check"])} if i.get("check") else i
    if wait := blockers.pending(i, backlog.tasks(d)):
        i = {**i, "waiting_for": wait}
    if subs := subtasks.of(backlog.tasks(d), a.id):
        i = {**i, "subtasks": [subtasks.summary(s, questions.open_questions(d, s["id"]), backlog.tasks(d)) for s in subs]}
    print(json.dumps(i, indent=2, ensure_ascii=False))
