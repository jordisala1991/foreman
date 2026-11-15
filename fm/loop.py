"""The loop: a plain process, no model. Each tick, for each registered project:

1. heartbeat (state/loop.json), read by the health check «loop dead» and by the panel
2. sources: plug-in signals become triage tasks (sources.py; none shipped yet)
3. answers: the owner's answers go back to its agent (its own session, resumed)
4. agents: one that ended its turn without finishing (no fm done / ready / ask / block) is nudged once, then blocked
5. pulls: with a provider that has PRs (GitHub), a running task's branch is pushed and it gets a draft PR (pulls.py)
6. queue: accepted tasks are merged first; then a branch whose head or main's moved is checked (tests, diff, risk) in
   the background, one at a time, and the autonomy may merge it. A check slower than util.BACKGROUND_WAIT_S does not hold
   the tick up: it finishes on its own and the next ticks go on.
7. health: broken states become triage tasks, deduplicated (health.py)
8. launch: while there are free slots, a planning agent for a `backlog` task, the responsible agent for a `ready`
   one; a task whose blockers are not all done waits. The loop never plans, never reviews, never talks to the owner.
   Before it, a let-in parent gets its parent agent outside the slots, told of each subtask merged or canceled (parents.py).
9. mechanic: a diagnosing helper for each health finding in triage, in the slots launch left free (mechanic.py)
"""

import os
import re
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import (
    backlog,
    blockers,
    decide,
    delivery,
    health,
    launcher,
    mechanic,
    mergecheck,
    parents,
    providers,
    pulls,
    release,
    sessions,
    sources,
    subtasks,
    util,
)
from .agents import fill_sessions, launch, running
from .config import Project, registered
from .providers import registry
from .reviewing import apply_review, seen_of, working
from .talk import deliver_answers, return_to_agent, watch_agents

PRIORITY = {p: n for n, p in enumerate(backlog.PRIORITIES)}
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
            delivery.merge_accepted(p, i["id"])
    except delivery.MergeConflict:
        return_to_agent(p, i["id"], "conflict", decide.conflict_note(main), by=acc.get("by", ""))


def gate_merge(p: Project, i: dict[str, Any]) -> bool:
    """May this accepted task merge now? With a test command, only once its tests pass on it merged with main as main is
    now (run here, in the background, one at a time); a failure goes back to its agent, or past the row, to the owner."""
    tid = i["id"]
    for first in (True, False):
        i = backlog.get(backlog.load(p), tid)
        c: dict[str, Any] = i.get("check") or {}
        head, main_head = (delivery.git(p.repo, "rev-parse", r).out.strip() for r in (i["branch"], p.get("project", "main")))
        as_today = not p.get("merge", "test") or delivery.conflicts(p, i["branch"])
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


def merged_guarded(p: Project, tid: str) -> None:
    guarded(p, tid, "check it merged with main", lambda: mergecheck.merged_check(p, tid), "check_retry")


TRANSIENT_ATTEMPTS = 3  # consecutive Transient failures of one task before it is blocked


def guarded(p: Project, tid: str, what: str, fn: Callable[[], object], field: str = "retry") -> None:
    """One task's step: a Transient failure is retried next tick (noted once per error); the 3rd in a row blocks the
    task with the last error; a success resets the count. Any other failure blocks it at once. The others go on.
    The count lives in `{field}_count` and `{field}_error`: the background check has its own, apart from the tick's."""
    err, cnt = f"{field}_error", f"{field}_count"
    try:
        fn()
        i = backlog.get(backlog.load(p), tid)
        if i.get(err) or i.get(cnt):
            backlog.update(p, tid, **{err: "", cnt: 0})
    except providers.Transient as e:
        i = backlog.get(backlog.load(p), tid)
        count = int(i.get(cnt) or 0) + 1
        if count >= TRANSIENT_ATTEMPTS:
            backlog.update(p, tid, blocked=f"the loop could not {what}: {e}"[:300], **{err: "", cnt: 0})
            backlog.event(p, tid, f"blocked: could not {what} {TRANSIENT_ATTEMPTS} times in a row: {e}"[:300])
            return
        backlog.update(p, tid, **{cnt: count, err: str(e)})
        if i.get(err) != str(e):  # the platform hiccuped: stay as is, try again next tick, say it once
            backlog.event(p, tid, f"could not {what}, will retry: {e}"[:300])
    except Exception as e:
        backlog.update(p, tid, blocked=f"the loop could not {what}: {e}"[:300])
        backlog.event(p, tid, f"blocked: could not {what}: {e}"[:300])


def run_pulls(p: Project) -> None:
    for i in pulls.running(p):
        guarded(p, i["id"], "push it or open its PR", lambda i=i: pulls.sync(p, i))


def queue_one(p: Project, i: dict[str, Any], main: str, rows: dict[str, Any]) -> None:
    """One step of a branch in review. A check that takes longer than util.BACKGROUND_WAIT_S goes on in the background."""
    head = delivery.git(p.repo, "rev-parse", "--verify", i.get("branch", "HEAD")).out.strip()
    main_head = delivery.git(p.repo, "rev-parse", main, check=False).out.strip()
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
            delivery.accept(p, i["id"], by=by)
            backlog.update(p, i["id"], resolving="")
            if gate_merge(p, backlog.get(backlog.load(p), i["id"])):
                delivery.merge_accepted(p, i["id"])
        except delivery.DeliveryError as e:
            backlog.event(p, i["id"], f"automatic merge failed: {e}")
    elif i.get("resolving"):
        backlog.update(p, i["id"], resolving="")
        backlog.event(p, i["id"], "its accept is not carried over: the owner decides")


def check_guarded(p: Project, tid: str, ci: str | None) -> None:
    """The check, in its thread: its own retry count (the tick's belongs to the queue step), 3 Transient in a row block."""
    guarded(p, tid, "check it", lambda: delivery.check(p, tid, ci), "check_retry")


def sort_key(i: dict[str, Any]) -> tuple[int, int, int]:
    m = re.search(r"(\d+)$", i["id"])
    return (PRIORITY.get(i.get("priority", "none"), 9), 0 if i["status"] == "ready" else 1, int(m.group(1)) if m else 0)


def launch_free(p: Project) -> None:
    if (p.state / "paused").exists():
        return
    d = backlog.load(p)
    free = int(p.get("agents", "slots")) - running(d)
    tasks = backlog.tasks(d)
    waiting = (i for i in tasks if i["status"] in ("backlog", "ready") and not i.get("blocked") and not blockers.pending(i, tasks))
    todo = sorted((i for i in waiting if not subtasks.of(tasks, i["id"])), key=sort_key)
    for i in todo[: max(0, free)]:
        guarded(p, i["id"], "launch it", lambda i=i: launch(p, i))


def beat(p: Project, step: str) -> None:
    util.write_json(
        p.state / "loop.json",
        {"pid": os.getpid(), "time": util.now(), "epoch": time.time(), "step": step, "version": launcher.ROOT.name},
    )


def tick(p: Project) -> None:
    beat(p, "start")
    rows = sessions.listing(p)
    steps = [
        ("sessions", lambda: fill_sessions(p, rows)),
        ("sources", lambda: sources.run(p)),
        ("answers", lambda: deliver_answers(p, rows)),
        ("agents", lambda: watch_agents(p, rows)),
        ("pulls", lambda: run_pulls(p)),
        ("queue", lambda: run_queue(p, rows)),
        ("health", lambda: health.run(p, rows)),
        ("blockers", lambda: release.run(p)),
        ("parents", lambda: parents.run(p, rows)),
        ("launch", lambda: launch_free(p)),
        ("mechanic", lambda: mechanic.run(p, rows)),
    ]
    for name, step in steps:
        beat(p, name)
        try:
            step()
        except Exception as e:  # one broken step must not stop the others, health files it
            util.append_jsonl(p.state / "errors.jsonl", {"time": util.now(), "step": name, "error": repr(e), "trace": traceback.format_exc()[-2000:]})


def forever(homes: list[str] | None = None, once: bool = False) -> None:
    import fcntl

    from .config import registry_file

    with open(str(registry_file()) + ".loop.lock", "a") as held:  # the lock lasts as long as the loop
        try:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("fm: another loop is running for this registry") from None
        while True:
            if why := launcher.stale():  # another version installed: exit, launchd starts the new one
                raise SystemExit(f"fm: {why}")
            projects = [Project.load(h) for h in (homes or registered())]
            for p in projects:
                tick(p)
            if once:
                return
            time.sleep(min((int(p.get("health", "loop_seconds")) for p in projects), default=30))
