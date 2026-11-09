"""The system watches itself. Each check finds broken states and returns findings {key, title, detail}; each new
finding becomes one `triage` issue (the owner lets it in, then it is defined and fixed like any task), deduplicated:
not while an issue with its key is open, and not again while the finding stays (state/seen.json); it is filed again
only if it went away and came back.

Checks: loop dead (heartbeat too old: seen from outside the loop, `fm health` and the panel) · agent stuck (its
session working but its transcript silent for `stuck_minutes`) · agent stopped (blocked after a nudge) · launch failed
· orphan worktree (under the worktrees folder with no live issue) · tests failing on main (the project's test command
on main's head, every `ci_every_minutes`) · loop step errors."""

import hashlib
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import backlog, costs, integrate, token, util
from .config import Project

Finding = dict[str, str]


def loop_dead(p: Project) -> list[Finding]:
    hb = util.read_json(p.state / "loop.json", {})
    limit = 3 * int(p.get("health", "loop_seconds"))
    late = time.time() - float(hb.get("epoch") or 0)
    if late > limit:
        when = hb.get("time") or "never"
        return [
            {"key": "loop-dead", "title": "The loop is not running", "detail": f"Its last tick was {when}. Start it with `fm loop` or load its launchd job."}
        ]
    return []


def claude_token(p: Project, read: Callable[[], str] | None = None, works: Callable[[str, str], bool] = token.works) -> list[Finding]:
    """No token, or one Claude rejects (probed at most every 30 minutes; the result is cached, never the token)."""
    tok = token.get(read)
    if not tok:
        return [{"key": "token-missing", "title": "No token", "detail": f"No Claude token for the agents. {token.SETUP}"}]
    cache = util.read_json(p.state / "token.json", {})
    stamp = hashlib.sha256(tok.encode()).hexdigest()[:16]
    if cache.get("stamp") != stamp or (util.age_s(cache.get("at")) or 1e9) > 1800:
        cache = {"stamp": stamp, "at": util.now(), "ok": works(p.get("agents", "claude"), tok)}
        util.write_json(p.state / "token.json", cache)
    if not cache.get("ok"):
        return [{"key": "token-bad", "title": "Token does not work", "detail": f"Claude rejects the token. Renew it: {token.RENEW}"}]
    return []


def agents(p: Project, rows: dict[str, Any]) -> list[Finding]:
    out: list[Finding] = []
    stuck_s = 60 * int(p.get("health", "stuck_minutes"))
    for i in backlog.issues(backlog.load(p)):
        a: backlog.Agent = i.get("agent") or {}
        row: dict[str, Any] = rows.get(a.get("session") or "") or {}
        if i["status"] in backlog.RUNNING and row.get("state") == "working":
            files = costs.files_of(a.get("session") or "")
            quiet = min((time.time() - f.stat().st_mtime for f in files), default=0)
            if quiet > stuck_s:
                out.append(
                    {
                        "key": f"stuck:{i['id']}:{a.get('session')}",
                        "title": f"{i['id']}'s agent looks stuck",
                        "detail": f"{a.get('name')} has been silent {int(quiet // 60)} min while working.",
                    }
                )
        blocked = i.get("blocked") or ""
        if i["status"] not in backlog.CLOSED and blocked.startswith(("its agent stopped", "could not launch")):
            out.append({"key": f"blocked:{i['id']}:{blocked[:40]}", "title": f"{i['id']} is blocked", "detail": f"{i['title']}: {blocked}"})
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
                out.append(
                    {
                        "key": f"orphan:{wt.name}",
                        "title": f"Orphan worktree {wt.name}",
                        "detail": f"{wt} belongs to no live issue: remove it, or tie it to one.",
                    }
                )
    return out


def main_tests(p: Project, run: bool = True) -> list[Finding]:
    """With `run` False (the panel, `fm health`) it only reads the last result; the loop runs the tests."""
    if not p.get("integrate", "test"):
        return []
    head = integrate.git(p.repo, "rev-parse", p.get("project", "main"), check=False).out.strip()
    ci = util.read_json(p.state / "ci.json", {})
    due = ci.get("head") != head or (util.age_s(ci.get("at")) or 1e9) > 60 * int(p.get("health", "ci_every_minutes"))
    if due and run:
        ci = {"head": head, "at": util.now(), "result": integrate.tests_at(p, head, "main")}
        util.write_json(p.state / "ci.json", ci)
    result, head = ci.get("result"), ci.get("head") or head
    if result == "fail":
        return [
            {
                "key": f"ci:{head[:12]}",
                "title": "Tests fail on main",
                "detail": f"`{p.get('integrate', 'test')}` fails on {p.get('project', 'main')} at {head[:12]}.",
            }
        ]
    return []


def loop_errors(p: Project) -> list[Finding]:
    out: list[Finding] = []
    for e in util.read_jsonl(p.state / "errors.jsonl")[-50:]:
        if (util.age_s(e.get("time")) or 1e9) < 3600:
            first = (e.get("error") or "")[:60]
            out.append(
                {
                    "key": f"error:{e.get('step')}:{first}",
                    "title": f"The loop's step «{e.get('step')}» fails",
                    "detail": f"{e.get('error')}\n\n{e.get('trace', '')[-800:]}",
                }
            )
    return out


def findings(p: Project, rows: dict[str, Any], outside: bool = False) -> list[Finding]:
    out = [*loop_dead(p), *claude_token(p)] if outside else []
    for check in (lambda: agents(p, rows), lambda: orphan_worktrees(p), lambda: main_tests(p, run=not outside), lambda: loop_errors(p)):
        try:
            out += check()
        except Exception as e:
            out.append({"key": f"health:{e!r}"[:80], "title": "A health check fails", "detail": repr(e)})
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
