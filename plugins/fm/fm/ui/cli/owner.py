"""The owner's commands: set up a project, file and edit tasks, answer, allow, accept, reject, go, cancel, unblock, pause."""

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from ... import providers, util
from ...core.oversight import guard
from ...core.project.config import Project
from ...core.project.registry import register
from ...core.tasks import backlog, blockers, editing, questions, subtasks
from ...providers.repo import RepoError
from ...services.agents.running import review_running
from ...services.delivery import merge
from ...services.delivery.git import DeliveryError
from ...steps import talk
from . import init
from .common import home


def cmd_init(a: argparse.Namespace) -> None:
    h = Path(a.home or ".").resolve()
    for line in init.init(h, a.prefix, a.repo):
        print(line)
    register(h)
    print(f"project {Project.load(h).name} at {h} (registered)")


def cmd_add(a: argparse.Namespace) -> None:
    p = home(a)
    status = "triage" if a.triage or (a.parent and os.environ.get("FM_TASK")) else "backlog"  # a parent agent's waits for a Go
    tasks = backlog.tasks(backlog.load(p))
    ids = blockers.ids(a.blocked_by or [])
    by_agent = a.parent and subtasks.agent_refusal(tasks, os.environ.get("FM_TASK", ""), a.parent, "subtasks are filed")
    if why := by_agent or (a.parent and subtasks.refusal(tasks, a.parent)) or blockers.refusal(tasks, "", ids):
        sys.exit(f"foreman: {why}")
    tid = backlog.add(
        p,
        a.title,
        detail=a.detail or "",
        status=status,
        criteria=a.criteria or "",
        goal=a.goal or "",
        priority=a.priority,
        parent=a.parent or "",
        blocked_by=ids,
        model=a.model or "",
        effort=a.effort or "",
        type=a.type or "",
    )
    print(tid)


def cmd_edit(a: argparse.Namespace) -> None:
    p = home(a)
    fields: dict[str, Any] = {f: v for f in editing.FIELDS if (v := getattr(a, f))}
    if a.blocked_by:
        fields["blocked_by"] = blockers.ids(a.blocked_by)
    with backlog.edit(p) as d:
        i = backlog.get(d, a.id)
        by_agent = subtasks.agent_refusal(backlog.tasks(d), os.environ.get("FM_TASK", ""), i.get("parent", ""), "tasks are edited")
        if why := by_agent or editing.refusal(i, fields, backlog.tasks(d)):
            sys.exit(f"foreman: {why}")
        old = dict(i)
        new = editing.edited(i, fields)
        i.clear()
        i.update(new)
    reset = old["status"] != new["status"] or ("plan_file" in old and "plan_file" not in new)
    backlog.event(p, a.id, "edited by the owner: " + ", ".join(fields) + ("; plan dropped, back to backlog" if reset else ""))
    after = "; it stays blocked until foreman unblock" if new.get("blocked") else "; it will be planned again"
    print(f"{a.id} edited" + (after if reset else ""))


def cmd_answer(a: argparse.Namespace) -> None:
    p = home(a)
    q = questions.answer(p, a.qid, a.text)
    print(f"answered {q['id']}; the loop gives it to {q.get('agent')}")


def cmd_allow(a: argparse.Namespace, allowed: bool = True) -> None:
    p = home(a)
    q = next((q for q in backlog.load(p)["decisions"] if q["id"] == a.qid), None)
    if not q or q.get("kind") != "permission":
        sys.exit(f"foreman: {a.qid} is not an open permission request")
    if allowed:
        guard.grant(p, q["task"], q["action"])
    questions.answer(p, a.qid, a.text or ("allowed once" if allowed else "not allowed"))
    with backlog.edit(p) as d:
        next(x for x in d["decisions_resolved"] if x["id"] == a.qid)["allowed"] = allowed


def cmd_accept(a: argparse.Namespace) -> None:
    p = home(a)
    try:
        merge.accept(p, a.id)
        print(f"{a.id} accepted: the loop merges it next")
    except (DeliveryError, RepoError, providers.ConfigError) as e:
        sys.exit(f"foreman: {e}")


def cmd_reject(a: argparse.Namespace) -> None:
    p = home(a)
    if backlog.get(backlog.load(p), a.id)["status"] != "in review":
        sys.exit(f"foreman: {a.id} is not in review")
    talk.return_to_agent(p, a.id, "rejection", a.note)


def cmd_review(a: argparse.Namespace) -> None:
    p = home(a)
    with backlog.edit(p) as d:
        i = backlog.get(d, a.id)
        if i["status"] != "in review":
            sys.exit(f"foreman: {a.id} is not in review")
        if i.get("accepted"):
            sys.exit(f"foreman: {a.id} is accepted: the loop merges it next")
        if review_running(i):
            sys.exit(f"foreman: {a.id} has a review running: wait for its verdict")
        for k in ("review", "hunter", "reviewer", "check", "review_for"):
            i.pop(k, None)
        i["blocked"] = ""
        i["review_from"] = len(i.get("reviews", []))
    backlog.event(p, a.id, "owner asked for a fresh review from round 1")
    print(f"{a.id}: the loop reviews it again from scratch")


def cmd_go(a: argparse.Namespace) -> None:
    p = home(a)
    i = backlog.get(backlog.load(p), a.id)
    acceptance: backlog.Acceptance = i.get("acceptance") or {}
    if not acceptance.get("criteria"):
        sys.exit(f"foreman: {a.id}: define its acceptance criteria with the owner first")
    backlog.set_status(p, a.id, "backlog", "let in by the owner", blocked="")


def cmd_cancel(a: argparse.Namespace) -> None:
    try:
        merge.close(home(a), a.id, "canceled", a.why or "by the owner")
    except (DeliveryError, RepoError) as e:
        sys.exit(f"foreman: {e}")


def cmd_unblock(a: argparse.Namespace) -> None:
    p = home(a)
    i = backlog.update(p, a.id, blocked="", pr_error="", retry_error="", retry_count=0)
    if i["status"] in backlog.RUNNING and a.relaunch:
        backlog.set_status(p, a.id, "backlog" if i["status"] == "refining" else "ready", "relaunch")


def cmd_pause(a: argparse.Namespace) -> None:
    p = home(a)
    f = p.state / "paused"
    if a.off:
        f.unlink(missing_ok=True)
    else:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(util.now())
    if a.off:
        print("launching resumed")
    else:
        print(f"launching paused; tasks still in flight: {backlog.in_flight(backlog.load(p))}")
