"""The system watches itself. Each check finds broken states and returns findings {key, title, detail}; each new
finding becomes one `triage` issue (the owner lets it in, then it is defined and fixed like any task), deduplicated:
not while an issue with its key is open, and not again while the finding stays (state/seen.json); it is filed again
only if it went away and came back.

Every finding has three parts: what it means, the evidence fm gathered, and a hypothesis marked pending; none
proposes a fix.

Checks: loop dead (heartbeat too old: seen from outside the loop, `fm health` and the panel) · agent stuck (its
session working but its transcript silent for `stuck_minutes`) · agent stopped (blocked after a nudge) · launch failed
· orphan worktree (under the worktrees folder with no live issue) · tests failing on main (the project's test command
on main's head, every `ci_every_minutes`; one task per failing episode) · answer not delivered for 10 minutes · loop step errors."""

import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import backlog, integrate, token, util
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


KEYCHAIN_MINUTES = 5  # unreadable this long before it is a finding: a transient failure is not


def keychain_unreadable(p: Project, reason: str) -> list[Finding]:
    """Counted by time, so one `fm health` and the panel agree: the first failure is stamped, a later check shows it."""
    state = util.read_json(p.state / "keychain.json", {})
    since = state.get("since") or util.now()
    util.write_json(p.state / "keychain.json", {"since": since})
    if (util.age_s(since) or 0) < 60 * KEYCHAIN_MINUTES:
        return []
    meaning = "fm cannot read the Claude token from the Keychain; agents launch without one."
    return [finding("keychain-unreadable", f"Keychain unreadable: {reason}", meaning, f"Unreadable since {since}: {reason}.")]


def login_expiring(login: token.Login) -> list[Finding]:
    if not login.expiring() or login.expires is None:
        return []
    when = login.expires.astimezone().strftime("%Y-%m-%d %H:%M")
    return [finding("login-expiring", "The Claude login expires soon", "The Claude login will stop working soon.", f"It expires on {when}.")]


def claude_token(
    p: Project,
    read: Callable[[], str] | None = None,
    works: Callable[[str, str], bool] = token.works,
    probe: bool = True,
    login: Callable[[str], token.Login] = token.login_status,
) -> list[Finding]:
    """The agents' authentication. While the normal login works the token is not used: only a warning when the login
    is about to expire. When it does not work the agents run on the long-lived token: no token, a token Claude rejects
    (only with `probe`; the panel does not probe), or a line saying they run on it and do not show in the Claude app.
    An unreadable Keychain is a finding after KEYCHAIN_MINUTES."""
    claude = p.get("agents", "claude")
    status = login(claude)
    if status.ok:
        return login_expiring(status)
    try:
        tok = token.get(read)
    except token.Unreadable as e:
        return keychain_unreadable(p, str(e))
    util.write_json(p.state / "keychain.json", {})
    if not tok:
        meaning = "The Claude login does not work and there is no fallback token: agents cannot authenticate."
        return [finding("token-missing", "No token", meaning, "Login check failed; no token in the environment or the Keychain.")]
    if probe and not works(claude, tok):
        meaning = "The Claude login does not work and Claude rejects the fallback token."
        return [finding("token-bad", "Token does not work", meaning, "Login check failed; the probe with the fallback token was refused.")]
    meaning = "The login does not work, so agents run on the long-lived token and do not show in the Claude app."
    return [finding("token-fallback", "Agents run on the long-lived token", meaning, "Login check failed; a fallback token is present.")]


def agents(p: Project, rows: dict[str, Any]) -> list[Finding]:
    out: list[Finding] = []
    stuck_s = 60 * int(p.get("health", "stuck_minutes"))
    for i in backlog.issues(backlog.load(p)):
        a: backlog.Agent = i.get("agent") or {}
        row: dict[str, Any] = rows.get(a.get("session") or "") or {}
        if i["status"] in backlog.RUNNING and row.get("state") == "working":
            quiet = idle_s(a)
            if quiet > stuck_s:
                meaning = "A working agent has produced no output for longer than stuck_minutes."
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
            title = f"{q['issue']}'s agent has not received a {q.get('kind') or 'question'} message"
            evidence = f"{q['id']} was answered at {since} and is still not delivered after {UNDELIVERED_MINUTES} min: «{q.get('answer')}»."
            out.append(finding(f"undelivered:{q['id']}", title, "An answer has not reached its agent.", evidence))
    return out


def orphan_worktrees(p: Project) -> list[Finding]:
    root = p.path("worktrees").resolve()
    live = {str(Path(i["worktree"]).resolve()) for i in backlog.issues(backlog.load(p)) if i.get("worktree") and i["status"] not in backlog.CLOSED}
    out: list[Finding] = []
    listing = integrate.git(p.repo, "worktree", "list", "--porcelain", check=False).out
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
    result, tail = integrate.tests_at(p, head, "main")
    before = util.read_json(p.state / "ci.json", {})
    # an older ci.json has no `since`: its failing key was the commit, so the open task keeps matching
    carried = before.get("since") or (before.get("head") or "")[:12]
    since = carried if before.get("result") == "fail" == result and carried else util.now()
    util.write_json(p.state / "ci.json", {"head": head, "at": util.now(), "result": result, "tail": tail, "since": since})


def main_tests(p: Project, run: bool = True) -> list[Finding]:
    """With `run` False (the panel, `fm health`) it only reads the last result; the loop runs the tests."""
    cmd = p.get("integrate", "test")
    if not cmd:
        return []
    head = integrate.git(p.repo, "rev-parse", p.get("project", "main"), check=False).out.strip()
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


def findings(p: Project, rows: dict[str, Any], outside: bool = False, probe: bool = True) -> list[Finding]:
    out = [*loop_dead(p), *claude_token(p, probe=probe)] if outside else []
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
                iid = backlog.add(p, f["title"], detail=f["detail"], status="triage", source=f"health:{key}")
                if iid:
                    filed.append(iid)
            seen[key] = util.now()
        for key in [k for k in seen if k not in found]:
            del seen[key]
    return filed
