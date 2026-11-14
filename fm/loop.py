"""The loop: a plain process, no model. Each tick, for each registered project:

1. heartbeat (state/loop.json), read by the health check «loop dead» and by the panel
2. sources: plug-in signals become triage issues (sources.py; none shipped yet)
3. answers: the owner's answers go back to the agent that asked (its own session, resumed)
4. agents: one that ended its turn without finishing (no fm done / ready / ask / block) is nudged once, then blocked
5. pulls: with a provider that has PRs (GitHub), a running task's branch is pushed and it gets a draft PR (pulls.py)
6. queue: accepted tasks are merged first; then a branch whose head or main's moved is checked (tests, diff, risk) in
   the background, one at a time, and the autonomy level may merge it. A check slower than util.BACKGROUND_WAIT_S does not hold
   the tick up: it finishes on its own and the next ticks go on.
7. health: broken states become triage issues, deduplicated (health.py)
8. launch: while there are free slots, a planning agent for a `backlog` issue, the responsible agent for a `ready`
   one. The loop never plans, never reviews, never talks to the owner.
"""

import os
import re
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from . import backlog, health, integrate, proc, providers, pulls, risk, sessions, sources, util
from .agents import fill_sessions, idle_s, launch, launch_reviewer, running
from .config import Project, registered
from .talk import NUDGE_IDLE_S, deliver_answers, send_back, watch_agents

PRIORITY = {p: n for n, p in enumerate(backlog.PRIORITIES)}
MAX_ROUNDS = 2  # what is still open after round 2 goes to the owner


def check_key(p: Project) -> str:
    return f"{p.state}:check"


def run_queue(p: Project, rows: dict[str, Any]) -> None:
    """Merges first (quick); then at most one branch check is started per tick, the rest on the next ticks. Each task is
    read again just before its turn."""
    main = p.get("project", "main")
    in_review = [i for i in backlog.issues(backlog.load(p)) if i["status"] == "in review"]
    for i in in_review:
        stop_review_sessions(p, i, rows)
    waiting = [i["id"] for i in in_review if not i.get("blocked")]
    for iid in waiting:
        i = backlog.get(backlog.load(p), iid)
        if i["status"] == "in review" and i.get("accepted") and not i.get("blocked"):
            guarded(p, iid, "merge it", lambda i=i: merge_one(p, i, main))
    key = check_key(p)
    started = util.STARTED.get(key, 0)
    for iid in waiting:
        i = backlog.get(backlog.load(p), iid)
        if i["status"] != "in review" or i.get("blocked") or i.get("accepted"):
            continue
        guarded(p, iid, "check it", lambda i=i: queue_one(p, i, main, rows))
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


def conflict_note(main: str) -> str:
    return (
        f"{main} moved and your branch conflicts with it. Merge {main} into your branch, resolve keeping both changes, run the tests, and run `fm done` again."
    )


def merge_one(p: Project, i: dict[str, Any], main: str) -> None:
    """The merge step of an accepted task; a conflict with main sends it back to its agent."""
    try:
        integrate.merge_accepted(p, i["id"])
    except integrate.MergeConflict:
        send_back(p, i["id"], "conflict", conflict_note(main))


TRANSIENT_ATTEMPTS = 3  # consecutive Transient failures of one task before it is blocked


def guarded(p: Project, iid: str, what: str, fn: Callable[[], object], field: str = "retry") -> None:
    """One issue's step: a Transient failure is retried next tick (noted once per error); the 3rd in a row blocks the
    issue with the last error; a success resets the count. Any other failure blocks it at once. The others go on.
    The count lives in `{field}_count` and `{field}_error`: the background check has its own, apart from the tick's."""
    err, cnt = f"{field}_error", f"{field}_count"
    try:
        fn()
        i = backlog.get(backlog.load(p), iid)
        if i.get(err) or i.get(cnt):
            backlog.update(p, iid, **{err: "", cnt: 0})
    except providers.Transient as e:
        i = backlog.get(backlog.load(p), iid)
        count = int(i.get(cnt) or 0) + 1
        if count >= TRANSIENT_ATTEMPTS:
            backlog.update(p, iid, blocked=f"the loop could not {what}: {e}"[:300], **{err: "", cnt: 0})
            backlog.event(p, iid, f"blocked: could not {what} {TRANSIENT_ATTEMPTS} times in a row: {e}"[:300])
            return
        backlog.update(p, iid, **{cnt: count, err: str(e)})
        if i.get(err) != str(e):  # the platform hiccuped: stay as is, try again next tick, say it once
            backlog.event(p, iid, f"could not {what}, will retry: {e}"[:300])
    except Exception as e:
        backlog.update(p, iid, blocked=f"the loop could not {what}: {e}"[:300])
        backlog.event(p, iid, f"blocked: could not {what}: {e}"[:300])


def run_pulls(p: Project) -> None:
    for i in pulls.running(p):
        guarded(p, i["id"], "push it or open its PR", lambda i=i: pulls.sync(p, i))


def review_pending(p: Project, i: dict[str, Any], head: str, rows: dict[str, Any]) -> bool:
    """True while this tip has no independent verdict yet: runs the hunter, then the reviewer, or blocks if one ended without its file."""
    v: dict[str, Any] = i.get("review") or {}
    h: dict[str, Any] = i.get("hunter") or {}
    r: dict[str, Any] = i.get("reviewer") or {}
    if v.get("tip") == head:
        return False
    role, last = ("reviewer", r) if h.get("tip") == head else ("hunter", h)
    free = running(backlog.load(p)) < int(p.get("agents", "slots")) and not (p.state / "paused").exists()
    if last.get("tip") != head:
        if role == "hunter" and free:
            launch_reviewer(p, i, head, "hunter")
        elif role == "reviewer" and Path(h.get("file", "")).exists() and not working(h, rows) and free:
            launch_reviewer(p, i, head, "reviewer")
        elif role == "reviewer" and not Path(h.get("file", "")).exists() and ended(h, rows):
            block_review(p, i["id"], "its hunter ended without its findings file")
    elif ended(last, rows):
        block_review(p, i["id"], "its reviewer ended without a verdict (`fm verdict`)")
    return True


def working(a: dict[str, Any], rows: dict[str, Any]) -> bool:
    return bool(a.get("session")) and sessions.busy(rows, a["session"])


def ended(a: dict[str, Any], rows: dict[str, Any]) -> bool:
    return bool(a.get("session")) and not sessions.busy(rows, a["session"]) and idle_s(a) > NUDGE_IDLE_S


def block_review(p: Project, iid: str, why: str) -> None:
    backlog.update(p, iid, blocked=why)
    backlog.event(p, iid, f"blocked: {why}")


def queue_one(p: Project, i: dict[str, Any], main: str, rows: dict[str, Any]) -> None:
    """One step of a branch in review. A check that takes longer than util.BACKGROUND_WAIT_S goes on in the background."""
    head = integrate.git(p.repo, "rev-parse", "--verify", i.get("branch", "HEAD")).out.strip()
    main_head = integrate.git(p.repo, "rev-parse", main, check=False).out.strip()
    if review_pending(p, i, head, rows):  # no verdict on this tip yet: an independent review first
        return
    v: dict[str, Any] = i.get("review") or {}
    if risk.review_step(v, MAX_ROUNDS) == "back":
        send_back(
            p,
            i["id"],
            "review",
            f"The independent review of your branch (round {v['round']}) leaves {backlog.open_findings(v)} open: read {v['file']}. "
            "For each one, make the smallest change that settles it, or answer it in one line if you think it is wrong; add "
            "nothing it does not ask for. Add one `### Round N` to the «Review rounds» section of your report, one line per "
            "finding, and run `fm done` again.",
        )
        backlog.update(p, i["id"], review={**v, "sent_back": True})
        return
    step, why = pulls.prepare(p, i, head)  # on ok, `why` is the CI result read now
    if step == "wait":
        return
    c: dict[str, Any] = i.get("check") or {}
    stale = bool(i.get("pr")) and c.get("ci") != why  # CI moves without head or main moving
    if c.get("head") != head or c.get("main") != main_head or stale:
        ci = why if i.get("pr") else None
        t = util.start_background(check_key(p), p.state / "errors.jsonl", lambda: check_guarded(p, i["id"], ci))
        if t is None:
            return
        t.join(util.BACKGROUND_WAIT_S)
        c = backlog.get(backlog.load(p), i["id"]).get("check") or {}
        if t.is_alive() or c.get("head") != head:  # still running, or it failed (the task says why)
            return
    if c.get("conflict"):
        send_back(p, i["id"], "conflict", conflict_note(main))
        return
    pulls.deliver(p, i, c)
    if integrate.merges_alone(p, c):
        try:
            integrate.accept(p, i["id"], by=f"autonomy level {p.get('integrate', 'autonomy')}")
            integrate.merge_accepted(p, i["id"])
        except integrate.IntegrateError as e:
            backlog.event(p, i["id"], f"automatic integration failed: {e}")


def check_guarded(p: Project, iid: str, ci: str | None) -> None:
    """The check, in its thread: its own retry count (the tick's belongs to the queue step), 3 Transient in a row block."""
    guarded(p, iid, "check it", lambda: integrate.check(p, iid, ci), "check_retry")


def sort_key(i: dict[str, Any]) -> tuple[int, int, int]:
    m = re.search(r"(\d+)$", i["id"])
    return (PRIORITY.get(i.get("priority", "none"), 9), 0 if i["status"] == "ready" else 1, int(m.group(1)) if m else 0)


def launch_free(p: Project) -> None:
    if (p.state / "paused").exists():
        return
    d = backlog.load(p)
    free = int(p.get("agents", "slots")) - running(d)
    todo = sorted((i for i in backlog.issues(d) if i["status"] in ("backlog", "ready") and not i.get("blocked")), key=sort_key)
    for i in todo[: max(0, free)]:
        guarded(p, i["id"], "launch it", lambda i=i: launch(p, i))


def beat(p: Project, step: str) -> None:
    util.write_json(p.state / "loop.json", {"pid": os.getpid(), "time": util.now(), "epoch": time.time(), "step": step})


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
        ("launch", lambda: launch_free(p)),
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
            projects = [Project.load(h) for h in (homes or registered())]
            for p in projects:
                tick(p)
            if once:
                return
            time.sleep(min((int(p.get("health", "loop_seconds")) for p in projects), default=30))


def launchd_plist(fm_bin: str, job: str = "loop", args: tuple[str, ...] = ()) -> str:
    """A launchd job (`loop` or `panel`) that starts at login and is restarted if it dies. Only PATH goes in the
    environment: the Claude token is never written here; the loop reads it from the Keychain (token.py)."""
    py = proc.run(["/usr/bin/which", "python3"]).out.strip() or "python3"
    argv = "".join(f"<string>{escape(a)}</string>" for a in (py, fm_bin, job, *args))
    log = f"{escape(str(Path.home()))}/Library/Logs/fm-{job}.log"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>dev.fm.{job}</string>
  <key>ProgramArguments</key><array>{argv}</array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>{escape(os.environ.get("PATH", ""))}</string></dict>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
</dict></plist>
"""
