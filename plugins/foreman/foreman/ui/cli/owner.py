"""The owner's commands: set up a project, file and edit tasks, answer, allow, accept, reject, go, cancel, unblock, relaunch,
pause."""

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from ... import providers
from ...core.oversight import guard
from ...core.project.config import Project
from ...core.project.registry import register
from ...core.tasks import backlog, blockers, chains, drags, editing, subtasks, taskrepo
from ...providers import registry
from ...providers.repo import RepoError
from ...providers.runner import RunnerError
from ...services.agents import relaunch
from ...services.agents.running import review_running
from ...services.delivery import merge
from ...services.delivery.git import DeliveryError
from ...steps import talk
from . import init
from .common import home, repo_key


def cmd_init(a: argparse.Namespace) -> None:
    h = Path(a.home or ".").resolve()
    for line in init.init(h, a.prefix, a.repo):
        print(line)
    register(h)
    print(f"project {Project.load(h).name} at {h} (registered)")


def cmd_add(a: argparse.Namespace) -> None:
    p = home(a)
    repo = repo_key(p, a.repo)
    tasks = backlog.tasks(registry.backlog(p).load())
    step = chains.step_fields(tasks, a.parent) if a.parent else None  # a chain's step: the parent's fields, its flags ignored
    triage = not step and (a.triage or (a.parent and os.environ.get("FOREMAN_TASK")))  # a parent agent's waits for a Go
    status = "triage" if triage else "backlog"
    ids = blockers.ids(a.blocked_by or [])
    by_agent = a.parent and subtasks.agent_refusal(tasks, os.environ.get("FOREMAN_TASK", ""), a.parent, "subtasks are filed")
    flags = chains.StepFields(goal=a.goal or "", criteria=a.criteria or "", model=a.model or "", effort=a.effort or "", type=a.type or "")
    fields = step or flags
    by_repo = taskrepo.add_refusal(p.repo_keys, repo, status, fields["criteria"], step is not None)
    by_parent = a.parent and (subtasks.refusal(tasks, a.parent) or chains.refusal(tasks, a.parent))
    if why := by_agent or by_parent or blockers.refusal(tasks, "", ids) or by_repo:
        sys.exit(f"foreman: {why}")
    tid = registry.backlog(p).add(
        a.title,
        detail=a.detail or "",
        status=status,
        priority=a.priority,
        parent=a.parent or "",
        blocked_by=ids,
        repo=repo,
        **fields,
    )
    print(tid)


def cmd_edit(a: argparse.Namespace) -> None:
    p = home(a)
    fields: dict[str, Any] = {f: v for f in editing.FIELDS if (v := getattr(a, f))}
    if a.blocked_by:
        fields["blocked_by"] = blockers.ids(a.blocked_by)
    fields.pop("repo", None)
    if key := repo_key(p, a.repo):
        fields["repo"] = key
    with registry.backlog(p).edit() as d:
        i = backlog.get(d, a.id)
        by_agent = subtasks.agent_refusal(backlog.tasks(d), os.environ.get("FOREMAN_TASK", ""), i.get("parent", ""), "tasks are edited")
        if why := by_agent or editing.refusal(i, fields or ({"repo": a.repo} if a.repo else {}), backlog.tasks(d)):
            sys.exit(f"foreman: {why}")
        if not fields:  # the one repo's own key: accepted, nothing to store
            print(f"{a.id}: nothing to change, this project has one repo")
            return
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
    q = registry.backlog(p).answer(a.qid, a.text)
    print(f"answered {q['id']}; the loop gives it to {q.get('agent')}")


def cmd_allow(a: argparse.Namespace, allowed: bool = True) -> None:
    p = home(a)
    q = next((q for q in registry.backlog(p).load()["decisions"] if q["id"] == a.qid), None)
    if not q or q.get("kind") != "permission":
        sys.exit(f"foreman: {a.qid} is not an open permission request")
    if allowed:
        guard.grant(p, q["task"], q["action"])
    registry.backlog(p).answer(a.qid, a.text or ("allowed once" if allowed else "not allowed"))
    with registry.backlog(p).edit() as d:
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
    if backlog.get(registry.backlog(p).load(), a.id)["status"] != "in review":
        sys.exit(f"foreman: {a.id} is not in review")
    talk.return_to_agent(p, a.id, "rejection", a.note)


def cmd_review(a: argparse.Namespace) -> None:
    p = home(a)
    with registry.backlog(p).edit() as d:
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
    d = registry.backlog(p).load()
    if why := drags.go_checks(backlog.get(d, a.id), backlog.tasks(d), p.repo_keys):
        sys.exit(f"foreman: {why}")
    registry.backlog(p).set_status(a.id, "backlog", "let in by the owner", blocked="")


def cmd_cancel(a: argparse.Namespace) -> None:
    try:
        merge.close(home(a), a.id, "canceled", a.why or "by the owner")
    except (DeliveryError, RepoError) as e:
        sys.exit(f"foreman: {e}")


def cmd_unblock(a: argparse.Namespace) -> None:
    p = home(a)
    i = registry.backlog(p).update(a.id, blocked="", pr_error="", retry_error="", retry_count=0)
    if i["status"] in backlog.RUNNING and a.relaunch:
        registry.backlog(p).set_status(a.id, "backlog" if i["status"] == "refining" else "ready", "relaunch")


def cmd_relaunch(a: argparse.Namespace) -> None:
    if os.environ.get("FOREMAN_TASK"):
        sys.exit("foreman: relaunch is the owner's, not an agent's")
    try:
        old, new = relaunch.relaunch(home(a), a.id)
    except RunnerError as e:
        sys.exit(f"foreman: {e}")
    print(f"{a.id}: relaunched, session {old} → {new}")


def cmd_pause(a: argparse.Namespace) -> None:
    p = home(a)
    p.set_paused(not a.off)
    if a.off:
        print("launching resumed")
    else:
        print(f"launching paused; tasks still in flight: {backlog.in_flight(registry.backlog(p).load())}")
