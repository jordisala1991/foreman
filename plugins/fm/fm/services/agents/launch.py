from pathlib import Path
from typing import Any, NamedTuple

from ... import util
from ...core.project.config import Project
from ...core.review.reviews import review_round
from ...core.rules import decide
from ...core.tasks import backlog
from .. import scratch
from ..claude import sessions
from . import brief, worktree

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


SCOPE = {  # a review of one fix only (decide.RULES), in the same brief slot as round 2; `$base` is what it compares with
    "reject": "7. **This review checks only the agent's fix to the owner's note** (in «The owner's notes»): `git diff $base $tip`.",
    "conflict": "7. **This review checks only the agent's resolution of a conflict with $main**: `git diff $base $tip`, where `$base` is git's "
    "automatic merge, so the diff is only what the agent changed.",
    "failing": "7. **This review checks only the agent's fix of failing tests or CI**: `git diff $base $tip`.",
}


class Scope(NamedTuple):  # why a review is launched (a key of decide.RULES) and what it compares with ("": the whole branch)
    rule: str
    base: str = ""


ROUNDS = Scope("round")  # the rounds: the whole branch, then the fixes


def launch(p: Project, i: dict[str, Any]) -> None:
    kind = "planning" if i["status"] == "backlog" else "responsible"
    name = brief.agent_name(p, i["id"], kind)
    own = i if kind == "responsible" else {}  # the task's choice is for its responsible agent only
    model = own.get("model") or p.get("agents", "planning_model" if kind == "planning" else "model")
    effort = own.get("effort") or p.get("agents", "planning_effort" if kind == "planning" else "effort")
    keys: dict[str, Any] = {}
    if kind == "responsible":
        wt, branch = worktree.worktree(p, i)
        i = {**i, "worktree": str(wt), "branch": branch}
        keys = {"worktree": str(wt), "branch": branch}
        cwd = wt
        brief.seed_report(p, i["id"], wt)
    else:
        cwd = p.repo
        brief.plan_file(p, i["id"]).parent.mkdir(parents=True, exist_ok=True)
    backlog.update(p, i["id"], **keys)  # the guard reads the worktree from the backlog
    try:
        short, sid = sessions.start(
            p, sessions.Agent(i["id"], name, model, effort, util.session_title(i["id"], kind, i["title"])), brief.brief(p, i, kind), cwd
        )
    except sessions.SessionError as e:
        backlog.update(p, i["id"], blocked=f"could not launch its agent: {e}")
        backlog.event(p, i["id"], f"launch failed: {e}")
        return
    agent = {"name": name, "short": short, "session": sid, "kind": kind, "model": model, "effort": effort, "started": util.now(), "cwd": str(cwd)}
    with backlog.edit(p) as d:
        it = backlog.get(d, i["id"])
        still = it["status"] == i["status"] and not it.get("blocked")
        if still:
            it["status"] = "refining" if kind == "planning" else "in progress"
            it["agent"] = agent
            if sid:
                record_session(it, sid, kind)
    if not still:  # the owner cancelled or changed it while the session started
        sessions.stop(p, short)
        backlog.event(p, i["id"], f"launch of {name} undone: the task changed meanwhile")
        return
    backlog.event(p, i["id"], f"launched {name} ({kind}, {model}, {effort}, session {short})")


def record_session(it: dict[str, Any], sid: str, kind: str) -> None:
    """Stores a session id in the task's `sessions` and its kind (planning|responsible|review) in `session_kinds`."""
    it["sessions"] = [*it.get("sessions", []), sid]
    it["session_kinds"] = {**it.get("session_kinds", {}), sid: kind}


def fill_sessions(p: Project, rows: dict[str, Any]) -> None:
    """A session not listed yet at launch: its full id from the short one, on a later tick."""
    for i in backlog.tasks(backlog.load(p)):
        for role in ("agent", "hunter", "reviewer", "mechanic"):
            a: dict[str, Any] = i.get(role) or {}
            if a.get("short") and not a.get("session"):
                sid = sessions.find(rows, a["short"])
                if sid:
                    with backlog.edit(p) as d:
                        it = backlog.get(d, i["id"])
                        it[role] = {**it[role], "session": sid}
                        record_session(it, sid, a.get("kind", "") if role in ("agent", "mechanic") else "review")


def review_file(p: Project, i: dict[str, Any], tip: str, role: str) -> Path:
    """The hunter's findings file or the reviewer's review of this round and tip."""
    rnd = len(i.get("reviews", [])) + 1
    suffix = "-hunter" if role == "hunter" else ""
    return p.home / "reviews" / f"{i['id']}-r{rnd}-{tip[:9]}{suffix}.md"


def launch_reviewer(p: Project, i: dict[str, Any], tip: str, role: str = "reviewer", scope: Scope = ROUNDS) -> None:
    """One of the two review sessions: the `hunter` lists what it suspects, then the `reviewer` checks each finding. With
    a `scope.base`, it reviews only what changed from it (an owner reject's fix, a conflict's resolution)."""
    rnd = review_round(i)
    all_reviews: list[dict[str, Any]] = i.get("reviews") or []
    reviews = all_reviews[int(i.get("review_from", 0)) :]
    prev = reviews[-1] if reviews else {}
    name = f"{i['id'].lower()}-{'hunt' if role == 'hunter' else 'review'}"
    model = p.get("agents", "review_model")
    effort = p.get("agents", "review_effort")
    hunter_file = str(i["hunter"]["file"]) if role == "reviewer" else str(review_file(p, i, tip, "hunter"))
    out = review_file(p, i, tip, role)
    out.parent.mkdir(parents=True, exist_ok=True)
    extra: dict[str, Any] = {
        "round": rnd,
        "plan": i.get("plan_file", ""),
        "tip": tip,
        "hunter_file": hunter_file,
        "review_file": str(out),
        "scratch": str(scratch.make(p, i["id"], rnd, tip, role)),
        "round2": (HUNTER_ROUND2 if role == "hunter" else ROUND2) if rnd > 1 else "",
        "base": scope.base,
        "previous": prev.get("file", ""),
        "previous_tip": prev.get("tip", ""),
    }
    if scope.base:
        extra["round2"] = SCOPE[scope.rule] + " Nothing outside that diff is a finding."
    cwd = Path(i["worktree"])
    title = util.session_title(i["id"], "review" if role == "reviewer" else role, i["title"])
    short, sid = sessions.start(p, sessions.Agent(i["id"], name, model, effort, title), brief.brief(p, i, role, extra), cwd)
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
    backlog.event(p, i["id"], f"launched {name} ({role}, review round {rnd} of {tip[:9]}, {model}, session {short}); rule: {decide.RULES[scope.rule]}")
