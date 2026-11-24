"""The system watches itself. Each check finds broken states and returns findings {key, title, detail}; each new
finding becomes one `triage` task (the owner lets it in, then it is defined and fixed like any task), deduplicated:
not while a task with its key is open, and not again while the finding stays (state/seen.json); it is filed again
only if it went away and came back.

Every finding has three parts: what it means, the evidence fm gathered, and a hypothesis marked pending; none
proposes a fix.

Checks: loop dead (heartbeat too old: seen from outside the loop, `foreman health` and the panel) · agent stuck (its
session working but its transcript silent for `stuck_minutes`) · agent stopped (blocked by the recover step) · launch failed
· orphan worktree (under the worktrees folder of any repo with no live task) · tests failing on main (the project's test command
on main's head, every `ci_every_minutes`; one task per failing episode) · a flaky check on main (failed, then passed on a
second run of the same commit) · answer not delivered for 10 minutes · loop step errors."""

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ... import util
from ...core.project.config import Project
from ...core.tasks import backlog, questions
from ...providers import registry
from ...providers.claude import login
from ...providers.runner import Rows, State, ref_of
from ...services.agents.running import idle_s

Finding = dict[str, str]


def finding(key: str, title: str, meaning: str, evidence: str, repo: str = "") -> Finding:
    """Every finding says what it means, the evidence fm gathered, and a hypothesis still pending; it proposes no fix.
    A finding about one repo of several names it."""
    detail = f"What it means: {meaning}\n\nEvidence:\n{evidence}\n\nHypothesis: pending"
    out = {"key": key, "title": title, "meaning": meaning, "evidence": evidence, "hypothesis": "pending", "detail": detail}
    return {**out, "repo": repo} if repo else out


def views(p: Project) -> list[tuple[str, Project]]:
    """The project as each repo's tasks see it, with its key; one repo is one view with no key."""
    if p.several:
        return [(key, p.for_task({"repo": key})) for key in p.repo_keys]
    return [("", p.for_task({}))]


def usable(p: Project) -> list[tuple[str, Project]]:
    """The views whose repo can be used: with several repos, those that are a git checkout."""
    return [(key, v) for key, v in views(p) if not p.several or registry.repo(v).is_checkout()]


def heartbeat(p: Project) -> dict[str, Any]:
    """The loop's last beat; {} when loop.json is missing or not an object."""
    return util.json_object(util.read_json(p.state / "loop.json", {})) or {}


def loop_beat(p: Project) -> dict[str, Any]:
    """The loop's last beat's time and whether the loop is alive, as the panel and `health --json` show them."""
    return {"time": heartbeat(p).get("time"), "alive": not loop_dead(p)}


def loop_dead(p: Project) -> list[Finding]:
    hb = heartbeat(p)
    limit = 3 * int(p.get("health", "loop_seconds"))
    late = time.time() - float(hb.get("epoch") or 0)
    if late > limit:
        when = hb.get("time") or "never"
        meaning = "The loop is not ticking: nothing in the backlog moves."
        return [finding("loop-dead", "The loop is not running", meaning, f"Last tick: {when}; it is dead after {limit} s.")]
    return []


def login_expiring(status: login.Login) -> list[Finding]:
    if not status.expiring() or status.expires is None:
        return []
    when = status.expires.astimezone().strftime("%Y-%m-%d %H:%M")
    return [finding("login-expiring", "The Claude login expires soon", "The Claude login will stop working soon: run /login.", f"It expires on {when}.")]


def login_check(p: Project, check: Callable[[str], login.Login] = login.login_status) -> list[Finding]:
    """The agents' authentication is the normal login: a warning when it is about to expire, a finding when it does not work."""
    status = check(p.get("agents", "claude"))
    if status.ok:
        return login_expiring(status)
    meaning = "Agents cannot start until the owner runs /login."
    evidence = "`claude auth status` shows no working login: not logged in, or the login has expired."
    return [finding("login-failed", "The Claude login does not work", meaning, evidence)]


def agents(p: Project, rows: Rows) -> list[Finding]:
    out: list[Finding] = []
    stuck_s = 60 * int(p.get("health", "stuck_minutes"))
    for i in backlog.tasks(registry.backlog(p).load()):
        a: backlog.Agent = i.get("agent") or {}
        if i["status"] in backlog.RUNNING and rows.state(ref_of(a)) is State.WORKING:
            quiet = idle_s(a)
            if quiet > stuck_s:
                meaning = "A running agent has produced no output for longer than stuck_minutes."
                evidence = f"{a.get('name')} ({i['id']}), silent {int(quiet // 60)} min while working; limit {stuck_s // 60} min."
                out.append(finding(f"stuck:{i['id']}:{a.get('session')}", f"{i['id']}'s agent looks stuck", meaning, evidence))
        blocked = i.get("blocked") or ""
        if i["status"] not in backlog.CLOSED and blocked.startswith(("its agent stopped", "could not launch")):
            meaning = "The task is blocked and waits for someone to decide."
            out.append(finding(f"blocked:{i['id']}:{blocked[:40]}", f"{i['id']} is blocked", meaning, f"{i['title']}: {blocked}"))
    return out


UNDELIVERED_MINUTES = 10


def answers_undelivered(p: Project) -> list[Finding]:
    out: list[Finding] = []
    for q in questions.undelivered(registry.backlog(p).load()):
        since = q.get("answered_at") or q.get("at") or str(q.get("by", "")).rpartition(", ")[2]
        if (util.age_s(since) or 0) > 60 * UNDELIVERED_MINUTES:
            title = f"{q['task']}'s agent has not received a {q.get('kind') or 'question'} message"
            evidence = f"{q['id']} was answered at {since} and is still not delivered after {UNDELIVERED_MINUTES} min: «{q.get('answer')}»."
            out.append(finding(f"undelivered:{q['id']}", title, "An answer has not reached its agent.", evidence))
    return out


def orphan_worktrees(p: Project) -> list[Finding]:
    live = {str(Path(i["worktree"]).resolve()) for i in backlog.tasks(registry.backlog(p).load()) if i.get("worktree") and i["status"] not in backlog.CLOSED}
    out: list[Finding] = []
    for key, v in usable(p):
        root = v.path("worktrees").resolve()
        for wt in registry.repo(v).worktrees():
            wt = wt.resolve()
            if root in wt.parents and str(wt) not in live:
                meaning = "A worktree under the worktrees folder belongs to no open task."
                title = f"Orphan worktree {wt.name}" + (f" (repo {key})" if key else "")
                out.append(finding(f"orphan:{wt.name}", title, meaning, str(wt), repo=key))
    return out


def loop_errors(p: Project) -> list[Finding]:
    out: list[Finding] = []
    for e in util.read_jsonl(p.state / "errors.jsonl")[-50:]:
        if (util.age_s(e.get("time")) or 1e9) < 3600:
            first = (e.get("error") or "")[:60]
            meaning = "A step of the loop raised an error, so it did not do its work."
            evidence = f"{e.get('error')}\n\n{e.get('trace', '')[-800:]}"
            out.append(finding(f"error:{e.get('step')}:{first}", f"The loop's step «{e.get('step')}» fails", meaning, evidence))
    return out
