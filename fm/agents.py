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
    "7. **Round 2 checks the fixes**, it does not reopen the branch: read the previous review (`$previous`), "
    "`git diff $previous_tip..$branch` and the «Review, round 1» section of the report. Each earlier finding is "
    "fixed (the commit or test that shows it), refuted (proof you accept) or still open. New findings only in "
    "what the fixes changed."
)


def report_file(p: Project, iid: str) -> Path:
    return p.home / "reports" / f"{iid}.md"


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
    keys: dict[str, Any] = {}
    if kind == "work":
        wt, branch = worktree(p, i)
        i = {**i, "worktree": str(wt), "branch": branch}
        keys = {"worktree": str(wt), "branch": branch}
        cwd = wt
    else:
        cwd = p.repo
        plan_file(p, i["id"]).parent.mkdir(parents=True, exist_ok=True)
    backlog.update(p, i["id"], **keys)  # the guard reads the worktree from the backlog
    try:
        short, sid = sessions.start(p, sessions.Agent(i["id"], name, model), brief(p, i, kind), cwd)
    except sessions.SessionError as e:
        backlog.update(p, i["id"], blocked=f"could not launch its agent: {e}")
        backlog.event(p, i["id"], f"launch failed: {e}")
        return
    agent = {"name": name, "short": short, "session": sid, "kind": kind, "model": model, "started": util.now(), "cwd": str(cwd)}
    with backlog.edit(p) as d:
        it = backlog.get(d, i["id"])
        still = it["status"] == i["status"] and not it.get("blocked")
        if still:
            it["status"] = "refining" if kind == "define" else "in progress"
            it["agent"] = agent
            it["sessions"] = [*it.get("sessions", []), *([sid] if sid else [])]
    if not still:  # the owner cancelled or changed it while the session started
        sessions.stop(p, short)
        backlog.event(p, i["id"], f"launch of {name} undone: the issue changed meanwhile")
        return
    backlog.event(p, i["id"], f"launched {name} ({kind}, {model}, session {short})")


def fill_sessions(p: Project, rows: dict[str, Any]) -> None:
    """A session not listed yet at launch: its full id from the short one, on a later tick."""
    for i in backlog.issues(backlog.load(p)):
        for role in ("agent", "reviewer"):
            a: dict[str, Any] = i.get(role) or {}
            if a.get("short") and not a.get("session"):
                sid = next((s for s, r in rows.items() if r.get("id") == a["short"] or s.startswith(a["short"])), None)
                if sid:
                    with backlog.edit(p) as d:
                        it = backlog.get(d, i["id"])
                        it[role] = {**it[role], "session": sid}
                        it["sessions"] = [*it.get("sessions", []), sid]


def idle_s(a: dict[str, Any]) -> float:
    """Seconds since the agent's transcript last changed (or since it was started/resumed, if later)."""
    files = costs.files_of(a.get("session") or "")
    last = max((f.stat().st_mtime for f in files), default=0.0)
    since = util.age_s(a.get("started"))
    return min(time.time() - last if last else 1e9, since if since is not None else 1e9)


def reviewer_active(i: dict[str, Any]) -> bool:
    """Its reviewer works on the current tip and has given no verdict for it yet."""
    r: dict[str, Any] = i.get("reviewer") or {}
    v: dict[str, Any] = i.get("review") or {}
    return i["status"] == "in review" and bool(r) and r.get("tip") != v.get("tip") and not i.get("blocked")


def running(d: dict[str, Any]) -> int:
    return sum(1 for i in backlog.issues(d) if (i["status"] in backlog.RUNNING and not i.get("blocked")) or reviewer_active(i))


def launch_reviewer(p: Project, i: dict[str, Any], tip: str) -> None:
    rnd = len(i.get("reviews", [])) + 1
    reviews: list[dict[str, Any]] = i.get("reviews") or []
    prev = reviews[-1] if reviews else {}
    name = f"{i['id'].lower()}-review"
    model = p.get("agents", "review_model")
    review_file = p.home / "reviews" / f"{i['id']}-r{rnd}.md"
    review_file.parent.mkdir(parents=True, exist_ok=True)
    extra: dict[str, Any] = {
        "round": rnd,
        "plan": i.get("plan_file", ""),
        "tip": tip,
        "review_file": str(review_file),
        "round2": ROUND2 if rnd > 1 else "",
        "previous": prev.get("file", ""),
        "previous_tip": prev.get("tip", ""),
    }
    cwd = Path(i["worktree"])
    short, sid = sessions.start(p, sessions.Agent(i["id"], name, model), brief(p, i, "reviewer", extra), cwd)
    with backlog.edit(p) as d:
        it = backlog.get(d, i["id"])
        it["reviewer"] = {
            "name": name,
            "short": short,
            "session": sid,
            "model": model,
            "started": util.now(),
            "round": rnd,
            "tip": tip,
            "cwd": str(cwd),
            "file": str(review_file),
        }
        it["sessions"] = [*it.get("sessions", []), *([sid] if sid else [])]
    backlog.event(p, i["id"], f"launched {name} (review round {rnd} of {tip[:9]}, {model}, session {short})")


def send(p: Project, i: dict[str, Any], message: str) -> bool:
    """Resumes the issue's agent with a message; False when it has no session to resume."""
    a: dict[str, Any] = i.get("agent") or {}
    if not a.get("session"):
        return False
    try:
        who = sessions.Agent(i["id"], a["name"], a.get("model") or p.get("agents", "model"))
        sessions.resume(p, who, a["session"], message, Path(a.get("cwd") or p.repo))
    except sessions.SessionError as e:
        backlog.event(p, i["id"], f"could not reach {a['name']}: {e}")
        return False
    return True
