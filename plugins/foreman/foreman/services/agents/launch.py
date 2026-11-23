from pathlib import Path
from typing import Any, NamedTuple

from ... import util
from ...core.project.config import Project
from ...core.review.reviews import review_round
from ...core.rules import decide
from ...core.tasks import backlog, chains, questions, runs, subtasks
from ...providers import registry
from ...providers.runner import Agent, Rows, RunnerError, RunnerUnavailable, SessionRef, State, ref_of
from .. import scratch
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
        try:
            wt, branch = worktree.worktree(p, i)
        except worktree.PostCreateError as e:
            registry.backlog(p).update(i["id"], blocked=str(e))
            backlog.event(p, i["id"], f"launch failed: {e}")
            return
        i = {**i, "worktree": str(wt), "branch": branch}
        keys = {"worktree": str(wt), "branch": branch}
        cwd = wt
        brief.seed_report(p, i["id"], wt)
    else:
        cwd = worktree.agent_folder(p, name, p.repo, p.get("project", "main"))
        brief.plan_file(p, i["id"]).parent.mkdir(parents=True, exist_ok=True)
    registry.backlog(p).update(i["id"], **keys)  # the guard reads the worktree from the backlog
    runner = registry.runner(p)
    try:
        ref = runner.start(Agent(i["id"], name, model, effort, util.session_title(i["id"], kind, i["title"])), brief.brief(p, i, kind), cwd)
    except RunnerUnavailable as e:  # Orca is down: the next tick tries again
        backlog.event(p, i["id"], f"launch postponed: {e}")
        return
    except RunnerError as e:
        registry.backlog(p).update(i["id"], blocked=f"could not launch its agent: {e}")
        backlog.event(p, i["id"], f"launch failed: {e}")
        return
    agent = {"name": name, **ref.fields(), "kind": kind, "model": model, "effort": effort, "started": util.now(), "cwd": str(cwd)}
    with registry.backlog(p).edit() as d:
        it = backlog.get(d, i["id"])
        still = it["status"] == i["status"] and not it.get("blocked")
        if still:
            it["status"] = "refining" if kind == "planning" else "in progress"
            it["agent"] = agent
            record_session(it, ref, kind)
    if not still:  # the owner cancelled or changed it while the session started
        runner.stop(ref)
        backlog.event(p, i["id"], f"launch of {name} undone: the task changed meanwhile")
        return
    record_run(p, i["id"], kind, ref, agent)
    backlog.event(p, i["id"], f"launched {name} ({kind}, {model}, {effort}, session {ref.short})")


def ended(a: dict[str, Any], rows: Rows) -> bool:
    """A parent agent whose session ended or is gone from the listing. False for another kind, for a session not found yet
    (`fill_sessions`) and for a listing that could not be read, where every session looks gone."""
    return a.get("kind") == "parent" and bool(a.get("session")) and rows.has_sessions() and rows.state(ref_of(a)) in (State.ENDED, State.GONE)


def launch_parent(p: Project, i: dict[str, Any]) -> SessionRef | None:
    """Starts the parent agent of task `i`: its first, or one that replaces an ended session and is told where the task
    stands, the wakeups it missed included. The new session, or None when the launch was postponed, failed or undone."""
    tid = i["id"]
    name = brief.agent_name(p, tid, "parent")
    model, effort = p.get("agents", "planning_model"), p.get("agents", "planning_effort")
    runner = registry.runner(p)
    cwd = worktree.agent_folder(p, name, p.repo, p.get("project", "main"))
    old: dict[str, Any] = i.get("agent") or {}
    d = registry.backlog(p).load()
    missed = [q for q in questions.undelivered(d) if q["task"] == tid] if old else []
    standing = chains.standing(backlog.tasks(d), i, [questions.message_for(i, q) for q in missed]) if old else ""
    told = subtasks.unwoken(backlog.tasks(d), tid, d["decisions_resolved"])  # closed subtasks its brief shows: the same snapshot
    try:
        title = util.session_title(tid, "parent", i["title"])
        ref = runner.start(Agent(tid, name, model, effort, title), _parent_brief(p, i, standing), cwd)
    except RunnerUnavailable as e:  # Orca is down: the next tick tries again
        backlog.event(p, tid, f"launch postponed: {e}")
        return None
    except RunnerError as e:
        registry.backlog(p).update(tid, blocked=f"could not launch its agent: {e}")
        backlog.event(p, tid, f"launch failed: {e}")
        return None
    agent = {"name": name, **ref.fields(), "kind": "parent", "model": model, "effort": effort, "started": util.now(), "cwd": str(cwd)}
    with registry.backlog(p).edit() as d:
        it = backlog.get(d, tid)
        now: dict[str, Any] = it.get("agent") or {}
        still = it["status"] == "backlog" and not it.get("blocked") and now.get("session") == old.get("session")
        if still:
            it["agent"] = agent
            record_session(it, ref, "parent")
            for s in told:  # the brief shows them: no wakeup for them
                questions.message(d, it, "S", ("subtask",), kind="subtask", text="subtask", answer="in its brief", subtask=s["id"], delivered=True)
    if not still:  # the owner cancelled or changed it while the session started
        runner.stop(ref)
        backlog.event(p, tid, f"launch of {name} undone: the task changed meanwhile")
        return None
    record_run(p, tid, run_kind(i, agent), ref, agent)
    for q in missed:  # the brief carries them: the loop must not send them again
        registry.backlog(p).mark_delivered(q["id"])
    backlog.event(p, tid, f"launched {name} (parent, {model}, {effort}, session {ref.short})")
    return ref


def _parent_brief(p: Project, i: dict[str, Any], standing: str) -> str:
    """A chain parent's agent reads `chain.md`, told its lens report, cap and budget; any other parent's reads `parent.md`."""
    c: backlog.Chain | None = i.get("chain")
    extra: dict[str, Any] = {"standing": standing}
    if not c:
        return brief.brief(p, i, "parent", extra)
    extra |= {"lens": c["lens"], "lens_report": str(brief.lens_file(p, c["lens"])), "max_steps": c["max_steps"], "max_lines": c["max_lines"]}
    return brief.brief(p, i, "chain", extra)


def record_session(it: dict[str, Any], ref: SessionRef, kind: str) -> None:
    """Stores a session's id in the task's `sessions`, its kind (planning|responsible|review) in `session_kinds` and, when
    it has a terminal, its tab in `session_tabs`. Nothing for a session whose id is not known yet."""
    sid = ref.session
    if sid is None:
        return
    it["sessions"] = [*it.get("sessions", []), sid]
    it["session_kinds"] = {**it.get("session_kinds", {}), sid: kind}
    if ref.terminal:
        it["session_tabs"] = {**it.get("session_tabs", {}), sid: ref.tab()}


def run_kind(task: dict[str, Any], record: dict[str, Any]) -> str:
    """The run kind of an `agent` or `mechanic` record: its `kind`, but `chain` for the parent of a task with a chain."""
    kind = str(record.get("kind") or "")
    return "chain" if kind == "parent" and task.get("chain") else kind


def record_run(p: Project, tid: str, kind: str, ref: SessionRef, record: dict[str, Any]) -> None:
    """A new run of `kind` for the session `ref`, with the model and effort of its launch `record`."""
    runs.start(p, tid, kind, runs.Session(ref.session or "", registry.runner(p).handle(ref), record["model"], record["effort"]))


def fill_sessions(p: Project, rows: Rows) -> None:
    """A session not listed yet at launch: its full id from the short one, on a later tick."""
    for i in backlog.tasks(registry.backlog(p).load()):
        for role in ("agent", "hunter", "reviewer", "mechanic"):
            a: dict[str, Any] = i.get(role) or {}
            if a.get("short") and not a.get("session"):
                sid = rows.find(a["short"])
                if sid:
                    with registry.backlog(p).edit() as d:
                        it = backlog.get(d, i["id"])
                        it[role] = {**it[role], "session": sid}
                        ref = SessionRef(a["short"], sid, a.get("terminal"), a.get("pane"), dispatch=a.get("dispatch"), coordinator=a.get("coordinator"))
                        record_session(it, ref, a.get("kind", "") if role in ("agent", "mechanic") else "review")
                    runs.attach(p, i["id"], {"hunter": "hunter", "reviewer": "review"}.get(role) or run_kind(i, a), sid)


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
    cwd = worktree.agent_folder(p, name, Path(i["worktree"]), tip)
    title = util.session_title(i["id"], "review" if role == "reviewer" else role, i["title"])
    try:
        ref = registry.runner(p).start(Agent(i["id"], name, model, effort, title), brief.brief(p, i, role, extra), cwd)
    except RunnerUnavailable as e:  # Orca is down: the review is decided again next tick
        backlog.event(p, i["id"], f"launch postponed: {e}")
        return
    with registry.backlog(p).edit() as d:
        it = backlog.get(d, i["id"])
        launch = {
            "name": name,
            **ref.fields(),
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
        record_session(it, ref, "review")
    record_run(p, i["id"], "review" if role == "reviewer" else "hunter", ref, launch)
    backlog.event(p, i["id"], f"launched {name} ({role}, review round {rnd} of {tip[:9]}, {model}, session {ref.short}); rule: {decide.RULES[scope.rule]}")
