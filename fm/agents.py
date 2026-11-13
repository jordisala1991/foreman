"""The agents' side of the loop: the brief, the worktree, launching a task's agent and its reviewer, messaging an agent."""

import string
import time
from pathlib import Path
from typing import Any

from . import backlog, costs, integrate, lock, sessions, util
from .config import Project

BRIEFS = Path(__file__).parent / "briefs"


def agent_name(p: Project, iid: str, kind: str) -> str:
    return f"{iid.lower()}-define" if kind == "define" else iid.lower()


ROUND2 = (
    "7. **Round $round checks the fixes**, not the whole branch again: read the previous review "
    "(`$previous`), `git diff $previous_tip..$branch` and the «Review rounds» section of the report. Each "
    "earlier finding is fixed, answered (with a reason you accept), or still open. No new findings, "
    "except a fix that is itself Wrong (shown) or Too much."
)
HUNTER_ROUND2 = (
    "7. **Round $round looks at the fixes**, not the whole branch again: read the previous review (`$previous`), "
    "`git diff $previous_tip..$branch` and the «Review rounds» section of the report. List only earlier findings "
    "that are still open and anything wrong or too much in the fixes themselves."
)


def report_file(p: Project, iid: str) -> Path:
    return p.home / "reports" / f"{iid}.md"


def seed_report(p: Project, iid: str, worktree: Path) -> None:
    """Gives a work agent its report as a template to fill: the repo's PR template (plus a Review rounds section) or
    fm's own. An existing report is never overwritten (a relaunch keeps the agent's work)."""
    f = report_file(p, iid)
    if f.exists():
        return
    theirs = worktree / ".github" / "pull_request_template.md"
    text = (BRIEFS / "report.md").read_text(encoding="utf-8").replace("ID", iid)
    if theirs.is_file():
        text = theirs.read_text(encoding="utf-8").rstrip() + "\n\n## Review rounds\n"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8")


def plan_file(p: Project, iid: str) -> Path:
    return p.home / "plans" / f"{iid}.md"


def plan_section(i: dict[str, Any]) -> str:
    if i.get("plan_file"):
        return (
            f"# The plan\nThe task has a plan: `{i['plan_file']}`. Read it whole and follow it. If it does not hold "
            "on a detail (a name, a line, the order of steps), decide, and list it in your report under «Off the plan» with why; "
            "if it would change the goal, the criterion, a decision or the out of scope, `fm ask` and end your turn."
        )
    return "# The plan\nNo plan exists for this task (it was judged trivial): work from the goal and criterion."


def owner_notes_section(p: Project, iid: str) -> str:
    """The owner's corrections on this task (rejection notes), oldest first; empty when there are none."""
    notes = [q for q in backlog.load(p)["decisions_resolved"] if q.get("issue") == iid and q.get("kind") == "rejection"]
    if not notes:
        return ""
    lines = "\n".join(f"- {q.get('at') or q.get('resolved', '')}: {q.get('answer', '')}" for q in notes)
    return (
        f"\n# The owner's notes on this task\n{lines}\n"
        "A note overrides the plan and the criterion where they differ; the latest note wins. A departure backed by a note "
        "is not a finding, and the report's «Off the plan» should cite it.\n"
    )


def brief(p: Project, i: dict[str, Any], kind: str, extra: dict[str, Any] | None = None) -> str:
    a: dict[str, Any] = i.get("acceptance") or {}
    values = {
        "id": i["id"],
        "project": p.name,
        "title": i["title"],
        "detail": i.get("detail", ""),
        "claim": a.get("claim") or i["title"],
        "criterion": a.get("criterion", ""),
        "repo": str(p.repo),
        "worktree": i.get("worktree", ""),
        "branch": i.get("branch", ""),
        "main": p.get("project", "main"),
        "test": p.get("integrate", "test") or "(none configured)",
        "lessons": str(backlog.ensure_lessons(p)),
        "report": str(report_file(p, i["id"])),
        "plan": str(plan_file(p, i["id"])),
        "plan_section": plan_section(i),
        "round2": "",
        "owner_notes": owner_notes_section(p, i["id"]) if kind in ("reviewer", "hunter") else "",
        "resources": lock.describe(p.cfg),
        **(extra or {}),
    }
    if values.get("round2"):
        values["round2"] = string.Template(values["round2"]).safe_substitute(values)
    text = string.Template((BRIEFS / f"{kind}.md").read_text(encoding="utf-8")).safe_substitute(values)
    return f"[fm {kind} {i['id']}]\n{text}"


def worktree(p: Project, i: dict[str, Any]) -> tuple[Path, str]:
    branch = f"{p.get('project', 'branch_prefix')}{i['id'].lower()}"
    wt = p.path("worktrees") / i["id"].lower()
    if not wt.exists():
        wt.parent.mkdir(parents=True, exist_ok=True)
        exists = integrate.git(p.repo, "rev-parse", "--verify", "--quiet", branch, check=False).code == 0
        args = ["worktree", "add", str(wt), branch] if exists else ["worktree", "add", "-b", branch, str(wt), p.get("project", "main")]
        integrate.git(p.repo, *args)
    return wt, branch


def launch(p: Project, i: dict[str, Any]) -> None:
    kind = "define" if i["status"] == "backlog" else "work"
    name = agent_name(p, i["id"], kind)
    model = p.get("agents", "define_model" if kind == "define" else "model")
    effort = p.get("agents", "define_effort" if kind == "define" else "effort")
    keys: dict[str, Any] = {}
    if kind == "work":
        wt, branch = worktree(p, i)
        i = {**i, "worktree": str(wt), "branch": branch}
        keys = {"worktree": str(wt), "branch": branch}
        cwd = wt
        seed_report(p, i["id"], wt)
    else:
        cwd = p.repo
        plan_file(p, i["id"]).parent.mkdir(parents=True, exist_ok=True)
    backlog.update(p, i["id"], **keys)  # the guard reads the worktree from the backlog
    try:
        short, sid = sessions.start(p, sessions.Agent(i["id"], name, model, effort), brief(p, i, kind), cwd)
    except sessions.SessionError as e:
        backlog.update(p, i["id"], blocked=f"could not launch its agent: {e}")
        backlog.event(p, i["id"], f"launch failed: {e}")
        return
    agent = {"name": name, "short": short, "session": sid, "kind": kind, "model": model, "effort": effort, "started": util.now(), "cwd": str(cwd)}
    with backlog.edit(p) as d:
        it = backlog.get(d, i["id"])
        still = it["status"] == i["status"] and not it.get("blocked")
        if still:
            it["status"] = "refining" if kind == "define" else "in progress"
            it["agent"] = agent
            if sid:
                record_session(it, sid, kind)
    if not still:  # the owner cancelled or changed it while the session started
        sessions.stop(p, short)
        backlog.event(p, i["id"], f"launch of {name} undone: the issue changed meanwhile")
        return
    backlog.event(p, i["id"], f"launched {name} ({kind}, {model}, session {short})")


def record_session(it: dict[str, Any], sid: str, kind: str) -> None:
    """Stores a session id in the issue's `sessions` and its kind (define|work|review) in `session_kinds`."""
    it["sessions"] = [*it.get("sessions", []), sid]
    it["session_kinds"] = {**it.get("session_kinds", {}), sid: kind}


def fill_sessions(p: Project, rows: dict[str, Any]) -> None:
    """A session not listed yet at launch: its full id from the short one, on a later tick."""
    for i in backlog.issues(backlog.load(p)):
        for role in ("agent", "hunter", "reviewer"):
            a: dict[str, Any] = i.get(role) or {}
            if a.get("short") and not a.get("session"):
                sid = next((s for s, r in rows.items() if r.get("id") == a["short"] or s.startswith(a["short"])), None)
                if sid:
                    with backlog.edit(p) as d:
                        it = backlog.get(d, i["id"])
                        it[role] = {**it[role], "session": sid}
                        record_session(it, sid, a.get("kind", "") if role == "agent" else "review")


def idle_s(a: dict[str, Any]) -> float:
    """Seconds since the agent's transcript last changed (or since it was started/resumed, if later)."""
    files = costs.files_of(a.get("session") or "")
    last = max((f.stat().st_mtime for f in files), default=0.0)
    since = util.age_s(a.get("started"))
    return min(time.time() - last if last else 1e9, since if since is not None else 1e9)


def reviewer_active(i: dict[str, Any]) -> bool:
    """A review pass works on the current tip: the reviewer without a verdict, or the hunter before its file exists."""
    return i["status"] == "in review" and not i.get("blocked") and review_running(i)


def review_running(i: dict[str, Any]) -> bool:
    """A review pass has no result yet on its tip, blocked task or not."""
    v: dict[str, Any] = i.get("review") or {}
    h: dict[str, Any] = i.get("hunter") or {}
    r: dict[str, Any] = i.get("reviewer") or {}
    if r and r["tip"] != v.get("tip"):
        return True
    return bool(h) and h["tip"] not in (v.get("tip"), r.get("tip")) and not Path(h["file"]).exists()


def running(d: dict[str, Any]) -> int:
    return sum(1 for i in backlog.issues(d) if (i["status"] in backlog.RUNNING and not i.get("blocked")) or reviewer_active(i))


def review_file(p: Project, i: dict[str, Any], tip: str, role: str) -> Path:
    """The hunter's findings file or the verifier's review of this round and tip."""
    rnd = len(i.get("reviews", [])) + 1
    suffix = "-hunter" if role == "hunter" else ""
    return p.home / "reviews" / f"{i['id']}-r{rnd}-{tip[:9]}{suffix}.md"


def launch_reviewer(p: Project, i: dict[str, Any], tip: str, role: str = "reviewer") -> None:
    """One of the two review passes: the `hunter` lists what it suspects, then the `reviewer` checks each finding."""
    rnd = backlog.review_round(i)
    all_reviews: list[dict[str, Any]] = i.get("reviews") or []
    reviews = all_reviews[int(i.get("review_from", 0)) :]
    prev = reviews[-1] if reviews else {}
    name = f"{i['id'].lower()}-{'hunt' if role == 'hunter' else 'review'}"
    model = p.get("agents", "review_model")
    effort = p.get("agents", "review_effort")
    hunt: dict[str, Any] = i.get("hunter") or {}
    hunter_file = str(hunt["file"]) if role == "reviewer" else str(review_file(p, i, tip, "hunter"))
    out = review_file(p, i, tip, role)
    out.parent.mkdir(parents=True, exist_ok=True)
    extra: dict[str, Any] = {
        "round": rnd,
        "plan": i.get("plan_file", ""),
        "tip": tip,
        "hunter_file": hunter_file,
        "review_file": str(out),
        "round2": (HUNTER_ROUND2 if role == "hunter" else ROUND2) if rnd > 1 else "",
        "previous": prev.get("file", ""),
        "previous_tip": prev.get("tip", ""),
    }
    cwd = Path(i["worktree"])
    short, sid = sessions.start(p, sessions.Agent(i["id"], name, model, effort), brief(p, i, role, extra), cwd)
    with backlog.edit(p) as d:
        it = backlog.get(d, i["id"])
        launch = {
            "name": name,
            "short": short,
            "session": sid,
            "model": model,
            "effort": effort,
            "started": util.now(),
            "round": rnd,
            "tip": tip,
            "cwd": str(cwd),
            "file": str(out),
        }
        it[role] = launch
        if role == "reviewer":
            it["reviewers"] = [*it.get("reviewers", []), launch]
        if sid:
            record_session(it, sid, "review")
    backlog.event(p, i["id"], f"launched {name} ({role}, review round {rnd} of {tip[:9]}, {model}, session {short})")


def update_agent(p: Project, iid: str, **fields: Any) -> None:
    """Merges fields into the issue's agent, from the current backlog (not from a read made earlier)."""
    with backlog.edit(p) as d:
        it = backlog.get(d, iid)
        it["agent"] = {**(it.get("agent") or {}), **fields}


def send(p: Project, i: dict[str, Any], message: str) -> bool:
    """Resumes the issue's agent with a message; False when it has no session to resume."""
    a: dict[str, Any] = i.get("agent") or {}
    if not a.get("session"):
        return False
    try:
        sessions.resume(p, a["session"], message, Path(a.get("cwd") or p.repo))
    except sessions.SessionError as e:
        backlog.event(p, i["id"], f"could not reach {a['name']}: {e}")
        return False
    return True
