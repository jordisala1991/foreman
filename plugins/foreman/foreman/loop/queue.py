from pathlib import Path
from typing import Any

from .. import util
from ..core.project.config import Project
from ..core.rules import decide
from ..core.tasks import backlog
from ..providers import registry
from ..providers.repo import RepoError
from ..providers.runner import Rows, State, ref_of, stop_live
from ..services.delivery import git, merge, pulls
from ..services.delivery.git import DeliveryError, MergeConflict
from ..steps.reviewing import apply_review, seen_of, working
from ..steps.talk import return_to_agent
from .guarded import check_guarded, guarded, merged_guarded

MAX_ROUNDS = 2  # what is still open after round 2 goes to the owner


def check_key(p: Project) -> str:
    return f"{p.state}:check"


def run_queue(p: Project, rows: Rows) -> None:
    """Merges first (quick); then at most one branch check is started per tick, the rest on the next ticks. Each task is
    read again just before its turn (each in its own repo, `for_task`)."""
    in_review = [i for i in backlog.tasks(registry.backlog(p).load()) if i["status"] == "in review"]
    for i in in_review:
        stop_review_sessions(p, i, rows)
    waiting = [tid for tid, _ in decide.queue_order(in_review)]
    for tid in waiting:
        i = backlog.get(registry.backlog(p).load(), tid)
        if decide.queue_turn(i) == "merge":
            guarded(p, tid, "merge it", lambda i=i: merge_one(p, i))
    key = check_key(p)
    started = util.STARTED.get(key, 0)  # after the merges: their merged checks are not the tick's branch check
    for tid in waiting:
        i = backlog.get(registry.backlog(p).load(), tid)
        if decide.queue_turn(i) != "check":
            continue
        guarded(p, tid, "check it", lambda i=i: queue_one(p, i, rows))
        if decide.queue_full(util.STARTED.get(key, 0) - started):
            return


def stop_review_sessions(p: Project, i: dict[str, Any], rows: Rows) -> None:
    """A hunter whose findings file is written, and a reviewer whose verdict is recorded, have nothing more to do."""
    v: dict[str, Any] = i.get("review") or {}
    h: dict[str, Any] = i.get("hunter") or {}
    r: dict[str, Any] = i.get("reviewer") or {}
    if h.get("file") and Path(h["file"]).exists() and live(h, rows) and not working(h, rows):
        stop_live(p, i["id"], ref_of(h), registry.runner(p), rows)
    if v and v.get("tip") == r.get("tip") and live(r, rows):
        stop_live(p, i["id"], ref_of(r), registry.runner(p), rows)


def live(a: dict[str, Any], rows: Rows) -> bool:
    """Listed and not stopped: never `gone`, which is also what an unreadable listing shows."""
    return rows.state(ref_of(a)) not in (State.ENDED, State.GONE)


def merge_one(p: Project, i: dict[str, Any]) -> None:
    """The merge step of an accepted task; a conflict with main returns it to its agent, keeping who accepted it."""
    p = p.for_task(i)
    main = p.get("project", "main")
    acc: dict[str, Any] = i.get("accepted") or {}
    try:
        if gate_merge(p, i):
            merge.merge_accepted(p, i["id"])
    except MergeConflict:
        return_to_agent(p, i["id"], "conflict", decide.conflict_note(main), by=acc.get("by", ""))


def gate_merge(p: Project, i: dict[str, Any]) -> bool:
    """May this accepted task merge now? With a test command, only once its tests pass on it merged with main as main is
    now (run here, in the background, one at a time); a failure goes back to its agent, or past the row, to the owner."""
    tid = i["id"]
    for first in (True, False):
        i = backlog.get(registry.backlog(p).load(), tid)
        c: dict[str, Any] = i.get("check") or {}
        head, main_head = (registry.repo(p).head(r) for r in (i["branch"], p.get("project", "main")))
        has_test = bool(p.get("merge", "test"))
        as_today = decide.as_today(has_test, has_test and git.conflicts(p, i["branch"]))
        step = decide.merge_gate(c, head, main_head, int(i.get("failure_streak") or 0), as_today)
        if step != "check" or not first:
            break
        t = util.start_background(check_key(p), p.state / "errors.jsonl", lambda: merged_guarded(p, tid))
        if t is None:
            return False
        t.join(util.BACKGROUND_WAIT_S)
        if t.is_alive():
            return False
    if step == "failing":
        pr: dict[str, Any] = i.get("pr") or {}
        return_to_agent(p, tid, "failing", decide.failing_note(c, pr.get("url", "")))
    elif step == "owner":
        registry.backlog(p).update(tid, accepted=None)
        backlog.event(p, tid, f"its accept lapses: tests fail merged with main {main_head[:9]}: the owner decides")
    return step == "merge"


def queue_one(p: Project, i: dict[str, Any], rows: Rows) -> None:
    """One step of a branch in review. A check that takes longer than util.BACKGROUND_WAIT_S goes on in the background."""
    p = p.for_task(i)
    main = p.get("project", "main")
    r = registry.repo(p)
    head = r.head(i.get("branch", "HEAD"))
    main_head = r.find(main)
    step = decide.review_step(seen_of(p, i, head, rows), i.get("review") or {}, MAX_ROUNDS)
    if step.do != "go":  # no verdict on this tip yet (a review first, or the last one holds), or its findings go back
        apply_review(p, i, head, step)
        return
    closed = "none" if decide.closes_review(i.get("review") or {}, MAX_ROUNDS) else ""
    if i.get("review_for", "") != closed:  # acted on: what a later tip gets is the rounds again, or (closed) nothing
        registry.backlog(p).update(i["id"], review_for=closed)
    pull, why = pulls.prepare(p, i, head)  # on ok, `why` is the CI result read now
    if pull == "wait":
        return
    c: dict[str, Any] = i.get("check") or {}
    ci = why if i.get("pr") else None  # CI moves without head or main moving
    if decide.checks_again(c, head, main_head, ci):
        t = util.start_background(check_key(p), p.state / "errors.jsonl", lambda: check_guarded(p, i["id"], ci))
        if t is None:
            return
        t.join(util.BACKGROUND_WAIT_S)
        c = backlog.get(registry.backlog(p).load(), i["id"]).get("check") or {}
        if t.is_alive() or c.get("head") != head:  # still running, or it failed (the task says why)
            return
    failed = int(i.get("failure_streak") or 0)
    act_on(p, i, c, decide.after_check(c, p.get("merge", "autonomy"), registry.get(p).has_ci, i.get("resolving", ""), failed))


def act_on(p: Project, i: dict[str, Any], c: dict[str, Any], action: str) -> None:
    """After the check: back to its agent for failing tests or CI, or for a conflict someone wants merged; or deliver
    it, merging it when wanted (by the accept a conflict carried over, or the autonomy). An accept carried over lapses
    when the owner must decide. Without a test command, a check without failing tests or CI ends a row of failures."""
    by = i.get("resolving") or "autonomy " + p.get("merge", "autonomy")
    if action == "failing":
        pr: dict[str, Any] = i.get("pr") or {}
        return_to_agent(p, i["id"], "failing", decide.failing_note(c, pr.get("url", "")))
        return
    if i.get("failure_streak") and not decide.failing(c) and not p.get("merge", "test"):  # with tests: only the merge
        registry.backlog(p).update(i["id"], failure_streak=0)
    if action == "conflict":
        return_to_agent(p, i["id"], "conflict", decide.conflict_note(p.get("project", "main")), by=by)
        return
    guarded(p, i["id"], "set the PR description", lambda: pulls.describe_pr(p, i, c), "pr")
    pulls.deliver(p, i, c)
    if action == "merge":
        try:
            merge.accept(p, i["id"], by=by)
            registry.backlog(p).update(i["id"], resolving="")
            if gate_merge(p, backlog.get(registry.backlog(p).load(), i["id"])):
                merge.merge_accepted(p, i["id"])
        except (DeliveryError, RepoError) as e:
            backlog.event(p, i["id"], f"automatic merge failed: {e}")
    elif i.get("resolving"):
        registry.backlog(p).update(i["id"], resolving="")
        backlog.event(p, i["id"], "its accept is not carried over: the owner decides")
