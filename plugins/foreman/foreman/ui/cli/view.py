"""foreman list and foreman show: the backlog and one task, as read."""

import argparse

from ...core.rules import decide
from ...core.tasks import backlog, blockers, chains, listing, questions, recurring, subtasks, taskrepo, tasktypes
from ...providers import registry
from .common import home, print_json


def _model(r: listing.TaskRow) -> str:
    """`  [model: opus, effort: xhigh]`, either alone, or nothing when the task sets neither."""
    shown = ", ".join(f"{k}: {v}" for k, v in (("model", r["model"]), ("effort", r["effort"])) if v)
    return f"  [{shown}]" if shown else ""


def _note(r: listing.TaskRow) -> str:
    note = (f"  [priority: {r['priority']}]" if r["priority"] != "none" else "") + _model(r)
    note += f"  [parent: {r['parent']}]" if r["parent"] else ""
    note += (f"  [blocked: {r['blocked']}]" if r["blocked"] else "") + (f"  [{r['waiting']}]" if r["waiting"] else "")
    note += f"  [recurring: {r['recurring']}]" if r["recurring"] else ""
    return note + (f"  [chain: {c['lens']}, step {c['step']} of {c['cap']}]" if (c := r["chain"]) else "")


def cmd_list(a: argparse.Namespace) -> None:
    p = home(a)
    d = registry.backlog(p).load()
    rows = listing.rows(backlog.tasks(d), a.all, p.several)
    if a.json:
        print_json(rows)
        return
    width = max(len(k) for k in [*p.repo_keys, "-"]) if p.several else 0
    listed = {r["id"] for r in rows}
    for r in rows:
        title = ("  " if r["parent"] in listed else "") + r["title"]
        repo = f"{r['repo']:{width}} " if p.several else ""
        print(f"{r['id']:8} {r['status']:12} {repo}{r['type']:10} {r['agent']:14} {title}{_note(r)}")
    for q in questions.open_questions(d):
        print(f"? {q['id']} ({q.get('agent')}): {q['text']}")


def cmd_show(a: argparse.Namespace) -> None:
    p = home(a)
    b = registry.backlog(p)
    d = b.load()
    i: backlog.Task = recurring.shown(taskrepo.shown(tasktypes.shown(b.find(a.id), backlog.tasks(d)), backlog.tasks(d), p.several))
    i = {**i, "checked_on": decide.ran_on(i["check"])} if i.get("check") else i
    if wait := blockers.pending(i, backlog.tasks(d)):
        i = {**i, "waiting_for": wait}
    if subs := subtasks.of(backlog.tasks(d), a.id):
        i = {**i, "subtasks": [subtasks.summary(s, questions.open_questions(d, s["id"]), backlog.tasks(d)) for s in subs]}
    if c := chains.progress(backlog.tasks(d), i):
        i = {**i, "chain": {**i["chain"], "step": c["step"]}}
    print_json(i)
