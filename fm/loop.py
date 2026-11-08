"""The loop: a plain process, no model. Each tick, for each registered project:

1. heartbeat (state/loop.json), read by the health check «loop dead» and by the panel
2. sources: plug-in signals become triage issues (sources.py; none shipped yet)
3. answers: the owner's answers go back to the agent that asked (its own session, resumed)
4. agents: one that ended its turn without finishing (no fm done / ready / ask / block) is nudged once, then blocked
5. pulls: with a GitHub remote, a task whose branch is pushed gets a draft PR (pulls.py)
6. queue: a delivered branch is checked (tests, diff, risk); the autonomy level may merge it
7. health: broken states become triage issues, deduplicated (health.py)
8. launch: while there are free slots, a defining agent for a `backlog` issue, the responsible agent for a `ready`
   one. The loop never plans, never reviews, never talks to the owner.
"""

import os
import re
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import backlog, health, integrate, proc, providers, pulls, sessions, sources, util
from .agents import fill_sessions, idle_s, launch, launch_reviewer, running
from .config import Project, registered
from .talk import NUDGE_IDLE_S, deliver_answers, send_back, watch_agents

PRIORITY = {p: n for n, p in enumerate(backlog.PRIORITIES)}
MAX_ROUNDS = 2  # what is still open after round 2 goes to the owner


def run_queue(p: Project, rows: dict[str, Any]) -> None:
    main = p.get("project", "main")
    for i in backlog.issues(backlog.load(p)):
        if i["status"] != "in review" or i.get("blocked"):
            continue
        if i.get("accepted"):
            guarded(p, i["id"], "merge", lambda i=i: integrate.merge_accepted(p, i["id"]))
            continue
        guarded(p, i["id"], "check", lambda i=i: queue_one(p, i, main, rows))


def guarded(p: Project, iid: str, what: str, fn: Callable[[], object]) -> None:
    """One issue's step: a failure blocks that issue with the reason, the others go on."""
    try:
        fn()
        if backlog.get(backlog.load(p), iid).get("retry_error"):
            backlog.update(p, iid, retry_error="")
    except providers.Transient as e:  # the platform hiccuped: stay in the queue, try again next tick, say it once
        if backlog.get(backlog.load(p), iid).get("retry_error") != str(e):
            backlog.update(p, iid, retry_error=str(e))
            backlog.event(p, iid, f"could not {what} it, will retry: {e}"[:300])
    except Exception as e:
        backlog.update(p, iid, blocked=f"the loop could not {what} it: {e}"[:300])
        backlog.event(p, iid, f"blocked: could not {what}: {e}"[:300])


def review_pending(p: Project, i: dict[str, Any], head: str, rows: dict[str, Any]) -> bool:
    """True while this tip has no independent verdict yet: launches the reviewer, or blocks if it ended without one."""
    v: dict[str, Any] = i.get("review") or {}
    r: dict[str, Any] = i.get("reviewer") or {}
    if v.get("tip") == head:
        return False
    if r.get("tip") == head:
        if r.get("session") and not sessions.busy(rows, r["session"]) and idle_s(r) > NUDGE_IDLE_S:
            backlog.update(p, i["id"], blocked="its reviewer ended without a verdict (`fm verdict`)")
            backlog.event(p, i["id"], "blocked: reviewer ended without a verdict")
    elif running(backlog.load(p)) < int(p.get("agents", "slots")) and not (p.state / "paused").exists():
        launch_reviewer(p, i, head)
    return True


def queue_one(p: Project, i: dict[str, Any], main: str, rows: dict[str, Any]) -> None:
    head = integrate.git(p.repo, "rev-parse", "--verify", i.get("branch", "HEAD")).out.strip()
    main_head = integrate.git(p.repo, "rev-parse", main, check=False).out.strip()
    if review_pending(p, i, head, rows):  # no verdict on this tip yet: an independent review first
        return
    v: dict[str, Any] = i.get("review") or {}
    if v["high"] + v["medium"] and v["round"] < MAX_ROUNDS:
        send_back(
            p,
            i["id"],
            "review",
            f"The independent review of your branch (round {v['round']}) leaves "
            f"H {v['high']}, M {v['medium']} open: read {v['file']}. Fix or refute each H and M finding with "
            "proof, nothing else; add one `### Round N` to the «Review rounds» section of your report and run `fm done` again.",
        )
        return
    step, why = pulls.prepare(p, i, head)  # on ok, `why` is the CI result read now
    if step == "wait":
        return
    c: dict[str, Any] = i.get("check") or {}
    stale = bool(i.get("pr")) and c.get("ci") != why  # CI moves without head or main moving
    if c.get("head") != head or c.get("main") != main_head or stale:
        c = integrate.check(p, i["id"], why if i.get("pr") else None)
    if c.get("conflict"):
        send_back(
            p,
            i["id"],
            "conflict",
            f"{main} moved and your branch conflicts with it. Merge {main} into "
            "your branch, resolve keeping both changes, run the tests, and run `fm done` again.",
        )
        return
    pulls.deliver(p, i, c)
    if integrate.merges_alone(p, c):
        try:
            integrate.accept(p, i["id"], by=f"autonomy level {p.get('integrate', 'autonomy')}")
            integrate.merge_accepted(p, i["id"])
        except integrate.IntegrateError as e:
            backlog.event(p, i["id"], f"automatic integration failed: {e}")


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
        guarded(p, i["id"], "launch", lambda i=i: launch(p, i))


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
        ("pulls", lambda: pulls.open_drafts(p)),
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


def launchd_plist(fm_bin: str, label: str = "dev.fm.loop") -> str:
    """The launchd job that keeps the loop alive (KeepAlive restarts it if it dies)."""
    py = proc.run(["/usr/bin/which", "python3"]).out.strip() or "python3"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key><array><string>{py}</string><string>{fm_bin}</string><string>loop</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>{os.environ.get("PATH", "")}</string></dict>
  <key>StandardErrorPath</key><string>{Path.home()}/Library/Logs/fm-loop.log</string>
</dict></plist>
"""
