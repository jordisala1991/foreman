from pathlib import Path
from typing import Any

from .. import util
from ..core.project.config import Project
from ..core.rules import decide
from ..core.tasks import backlog
from ..providers import registry
from ..services.claude import sessions
from ..services.delivery import git, merge, pulls
from ..services.delivery.git import DeliveryError, MergeConflict
from ..steps.reviewing import apply_review, seen_of, working
from ..steps.talk import return_to_agent
from .guarded import check_guarded, guarded, merged_guarded

MAX_ROUNDS = 2  # what is still open after round 2 goes to the owner


def check_key(p: Project) -> str:
    return f"{p.state}:check"


def run_queue(p: Project, rows: dict[str, Any]) -> None:
    """Merges first (quick); then at most one branch check is started per tick, the rest on the next ticks. Each task is
    read again just before its turn."""
    main = p.get("project", "main")
    in_review = [i for i in backlog.tasks(backlog.load(p)) if i["status"] == "in review"]
    for i in in_review:
        stop_review_sessions(p, i, rows)
    waiting = [i["id"] for i in in_review if not i.get("blocked")]
    for tid in waiting:
        i = backlog.get(backlog.load(p), tid)
        if i["status"] == "in review" and i.get("accepted") and not i.get("blocked"):
            guarded(p, tid, "merge it", lambda i=i: merge_one(p, i, main))
    key = check_key(p)
    started = util.STARTED.get(key, 0)
    for tid in waiting:
        i = backlog.get(backlog.load(p), tid)
        if i["status"] != "in review" or i.get("blocked") or i.get("accepted"):
            continue
        guarded(p, tid, "check it", lambda i=i: queue_one(p, i, main, rows))
        if util.STARTED.get(key, 0) != started:
            return


def stop_review_sessions(p: Project, i: dict[str, Any], rows: dict[str, Any]) -> None:
    """A hunter whose findings file is written, and a reviewer whose verdict is recorded, have nothing more to do."""
    v: dict[str, Any] = i.get("review") or {}
    h: dict[str, Any] = i.get("hunter") or {}
    r: dict[str, Any] = i.get("reviewer") or {}
    if h.get("file") and Path(h["file"]).exists() and live(h, rows) and not working(h, rows):
        sessions.stop_live(p, i["id"], h, rows)
    if v and v.get("tip") == r.get("tip") and live(r, rows):
        sessions.stop_live(p, i["id"], r, rows)


def live(a: dict[str, Any], rows: dict[str, Any]) -> bool:
    """Listed and not stopped: never `gone`, which is also what an unreadable listing shows."""
    return sessions.state(rows, a.get("session")) not in ("stopped", "gone")


def merge_one(p: Project, i: dict[str, Any], main: str) -> None:
    """The merge step of an accepted task; a conflict with main returns it to its agent, keeping who accepted it."""
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
        i = backlog.get(backlog.load(p), tid)
        c: dict[str, Any] = i.get("check") or {}
        head, main_head = (git.git(p.repo, "rev-parse", r).out.strip() for r in (i["branch"], p.get("project", "main")))
        as_today = not p.get("merge", "test") or git.conflicts(p, i["branch"])
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
        backlog.update(p, tid, accepted=None)
        backlog.event(p, tid, f"its accept lapses: tests fail merged with main {main_head[:9]}: the owner decides")
    return step == "merge"


def queue_one(p: Project, i: dict[str, Any], main: str, rows: dict[str, Any]) -> None:
    """One step of a branch in review. A check that takes longer than util.BACKGROUND_WAIT_S goes on in the background."""
    head = git.git(p.repo, "rev-parse", "--verify", i.get("branch", "HEAD")).out.strip()
    main_head = git.git(p.repo, "rev-parse", main, check=False).out.strip()
    step = decide.review_step(seen_of(p, i, head, rows), i.get("review") or {}, MAX_ROUNDS)
    if step.do != "go":  # no verdict on this tip yet (a review first, or the last one holds), or its findings go back
        apply_review(p, i, head, step)
        return
    closed = "none" if decide.closes_review(i.get("review") or {}, MAX_ROUNDS) else ""
    if i.get("review_for", "") != closed:  # acted on: what a later tip gets is the rounds again, or (closed) nothing
        backlog.update(p, i["id"], review_for=closed)
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
        c = backlog.get(backlog.load(p), i["id"]).get("check") or {}
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
        backlog.update(p, i["id"], failure_streak=0)
    if action == "conflict":
        return_to_agent(p, i["id"], "conflict", decide.conflict_note(p.get("project", "main")), by=by)
        return
    pulls.deliver(p, i, c)
    if action == "merge":
        try:
            merge.accept(p, i["id"], by=by)
            backlog.update(p, i["id"], resolving="")
            if gate_merge(p, backlog.get(backlog.load(p), i["id"])):
                merge.merge_accepted(p, i["id"])
        except DeliveryError as e:
            backlog.event(p, i["id"], f"automatic merge failed: {e}")
    elif i.get("resolving"):
        backlog.update(p, i["id"], resolving="")
        backlog.event(p, i["id"], "its accept is not carried over: the owner decides")
