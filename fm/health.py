"""The system watches itself. Each check finds broken states and returns findings {key, title, detail}; each new
finding becomes one `triage` task (the owner lets it in, then it is defined and fixed like any task), deduplicated:
not while a task with its key is open, and not again while the finding stays (state/seen.json); it is filed again
only if it went away and came back.

Every finding has three parts: what it means, the evidence fm gathered, and a hypothesis marked pending; none
proposes a fix.

Checks: loop dead (heartbeat too old: seen from outside the loop, `fm health` and the panel) · agent stuck (its
session working but its transcript silent for `stuck_minutes`) · agent stopped (blocked after a nudge) · launch failed
· orphan worktree (under the worktrees folder with no live task) · tests failing on main (the project's test command
on main's head, every `ci_every_minutes`; one task per failing episode) · answer not delivered for 10 minutes · loop step errors."""

import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import backlog, config, delivery, login, util
from .agents import idle_s
from .config import Project

Finding = dict[str, str]


def finding(key: str, title: str, meaning: str, evidence: str) -> Finding:
    """Every finding says what it means, the evidence fm gathered, and a hypothesis still pending; it proposes no fix."""
    detail = f"What it means: {meaning}\n\nEvidence:\n{evidence}\n\nHypothesis: pending"
    return {"key": key, "title": title, "meaning": meaning, "evidence": evidence, "hypothesis": "pending", "detail": detail}


def loop_dead(p: Project) -> list[Finding]:
    hb = util.read_json(p.state / "loop.json", {})
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


def agents(p: Project, rows: dict[str, Any]) -> list[Finding]:
    out: list[Finding] = []
    stuck_s = 60 * int(p.get("health", "stuck_minutes"))
    for i in backlog.tasks(backlog.load(p)):
        a: backlog.Agent = i.get("agent") or {}
        row: dict[str, Any] = rows.get(a.get("session") or "") or {}
        if i["status"] in backlog.RUNNING and row.get("state") == "working":
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
    for q in backlog.undelivered(backlog.load(p)):
        since = q.get("answered_at") or q.get("at") or str(q.get("by", "")).rpartition(", ")[2]
        if (util.age_s(since) or 0) > 60 * UNDELIVERED_MINUTES:
            title = f"{q['task']}'s agent has not received a {q.get('kind') or 'question'} message"
            evidence = f"{q['id']} was answered at {since} and is still not delivered after {UNDELIVERED_MINUTES} min: «{q.get('answer')}»."
            out.append(finding(f"undelivered:{q['id']}", title, "An answer has not reached its agent.", evidence))
    return out


def orphan_worktrees(p: Project) -> list[Finding]:
    root = p.path("worktrees").resolve()
    live = {str(Path(i["worktree"]).resolve()) for i in backlog.tasks(backlog.load(p)) if i.get("worktree") and i["status"] not in backlog.CLOSED}
    out: list[Finding] = []
    listing = delivery.git(p.repo, "worktree", "list", "--porcelain", check=False).out
    for ln in listing.splitlines():
        if ln.startswith("worktree "):
            wt = Path(ln.split(" ", 1)[1]).resolve()
            if root in wt.parents and str(wt) not in live:
                meaning = "A worktree under the worktrees folder belongs to no open task."
                out.append(finding(f"orphan:{wt.name}", f"Orphan worktree {wt.name}", meaning, str(wt)))
    return out


def failing_step(tail: str, command: str) -> str:
    """The target of the first make error line in `tail`; the command itself when there is none."""
    m = re.search(r"\*\*\* \[(?:[^\]\s]*:\d+: )?([^\]]+)\] Error", tail)
    return m.group(1) if m else command


def record_main_tests(p: Project, head: str) -> None:
    """Runs the tests on `head` and writes ci.json. `since` stamps when the current failing episode began."""
    result, tail = delivery.tests_at(p, head, "main")
    before = util.read_json(p.state / "ci.json", {})
    since = before.get("since") if before.get("result") == "fail" == result and before.get("since") else util.now()
    util.write_json(p.state / "ci.json", {"head": head, "at": util.now(), "result": result, "tail": tail, "since": since})


def main_tests(p: Project, run: bool = True) -> list[Finding]:
    """With `run` False (the panel, `fm health`) it only reads the last result; the loop runs the tests."""
    cmd = p.get("merge", "test")
    if not cmd:
        return []
    head = delivery.git(p.repo, "rev-parse", p.get("project", "main"), check=False).out.strip()
    ci = util.read_json(p.state / "ci.json", {})
    due = ci.get("head") != head or (util.age_s(ci.get("at")) or 1e9) > 60 * int(p.get("health", "ci_every_minutes"))
    if due and run:  # slow: in the background, the tick waits a little and goes on; the result lands in ci.json
        t = util.start_background(f"{p.state}:main-tests", p.state / "errors.jsonl", lambda: record_main_tests(p, head))
        if t:
            t.join(util.BACKGROUND_WAIT_S)
            ci = util.read_json(p.state / "ci.json", {})
    if ci.get("result") != "fail":
        return []
    head, tail = ci.get("head") or head, ci.get("tail") or "(no output recorded)"
    meaning = "The project's test command fails on main: what merges next lands on a broken base."
    evidence = f"Command: `{cmd}`\nCommit: {head[:12]}\nFailing step: `{failing_step(tail, cmd)}`\nLast lines of its output:\n{tail}"
    return [finding(f"ci:{ci.get('since') or head[:12]}", "Tests fail on main", meaning, evidence)]


def loop_errors(p: Project) -> list[Finding]:
    out: list[Finding] = []
    for e in util.read_jsonl(p.state / "errors.jsonl")[-50:]:
        if (util.age_s(e.get("time")) or 1e9) < 3600:
            first = (e.get("error") or "")[:60]
            meaning = "A step of the loop raised an error, so it did not do its work."
            evidence = f"{e.get('error')}\n\n{e.get('trace', '')[-800:]}"
            out.append(finding(f"error:{e.get('step')}:{first}", f"The loop's step «{e.get('step')}» fails", meaning, evidence))
    return out


def findings(p: Project, rows: dict[str, Any], outside: bool = False) -> list[Finding]:
    out = [*loop_dead(p), *login_check(p)] if outside else []
    checks = (
        lambda: agents(p, rows),
        lambda: answers_undelivered(p),
        lambda: orphan_worktrees(p),
        lambda: main_tests(p, run=not outside),
        lambda: loop_errors(p),
    )
    for check in checks:
        try:
            out += check()
        except Exception as e:
            meaning = "A health check raised an exception, so its findings are missing."
            out.append(finding(f"health:{e!r}"[:80], "A health check fails", meaning, repr(e)))
    return out


def run(p: Project, rows: dict[str, Any], outside: bool = False) -> list[str]:
    """Files the new findings; the ids filed."""
    found = {f["key"]: f for f in findings(p, rows, outside)}
    filed: list[str] = []
    with util.locked_json(p.state / "seen.json", {}) as seen:
        for key, f in found.items():
            if key not in seen:
                tid = backlog.add(p, f["title"], detail=f["detail"], status="triage", source=f"health:{key}")
                if tid:
                    filed.append(tid)
            seen[key] = util.now()
        for key in [k for k in seen if k not in found]:
            del seen[key]
    return filed


def python_note() -> list[str]:
    """bin/fm's line when the hook found no Python new enough (see bin/fm)."""
    try:
        text = config.python_note_file().read_text(encoding="utf-8").strip()
    except OSError:
        return []
    return ["! " + text] if text else []


def shared_folders(projects: list[Project]) -> list[str]:
    """A folder two projects claim: `fm` refuses to pick one there."""
    return [f"! {f} is claimed by two projects: {' and '.join(names)}" for f, names in config.shared(projects).items()]


def old_links(p: Project) -> list[str]:
    """The links `fm init` made before the plugin (FN-143), in the home or a repo: the plugin replaces them, so they
    are to be deleted."""
    out: list[str] = []
    for folder in dict.fromkeys([p.home, *p.repos]):
        where = "the home" if folder == p.home else str(folder)
        found = [rel for rel in (".claude/fm.md", ".claude/skills/define-task") if (folder / rel).is_symlink()]
        lines = [f"! {where} still has the old link {rel} (FN-143): the plugin replaces it; delete it" for rel in found]
        if folder == p.home and found[:1] == [".claude/fm.md"] and _includes_fm_md(p.home / "CLAUDE.md"):
            lines[0] += ", and remove the line @.claude/fm.md from CLAUDE.md"
        out += lines
    return out


def _includes_fm_md(claude_md: Path) -> bool:
    try:
        lines = claude_md.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):  # no CLAUDE.md, or one fm cannot read: the link is still named
        return False
    return "@.claude/fm.md" in (line.strip() for line in lines)


def repos_note(p: Project) -> str:
    n = len(p.repos)
    return f"note: {p.name} has {n} repos; its tasks run in the first one, {p.repo}" if n > 1 else ""
