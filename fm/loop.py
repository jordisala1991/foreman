"""The loop: a plain process, no model. Each tick, for each registered project:

1. heartbeat (state/loop.json), read by the health check «loop dead» and by the panel
2. sources: plug-in signals become triage issues (sources.py; none shipped yet)
3. answers: the owner's answers go back to the agent that asked (its own session, resumed)
4. agents: one that ended its turn without finishing (no fm done / ready / ask / block) is nudged once, then blocked
5. queue: a delivered branch is checked (tests, diff, risk); the autonomy level may merge it
6. health: broken states become triage issues, deduplicated (health.py)
7. launch: while there are free slots, a defining agent for a `backlog` issue, the responsible agent for a `ready`
   one. The loop never plans, never reviews, never talks to the owner.
"""

from __future__ import annotations

import os
import re
import string
import subprocess
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import backlog, costs, health, integrate, lock, sessions, sources, util
from .config import Project, registered

BRIEFS = Path(__file__).parent / "briefs"
PRIORITY = {p: n for n, p in enumerate(backlog.PRIORITIES)}
NUDGE_IDLE_S = 600  # an agent's turn ended and its transcript is quiet this long: nudge it (then block it)
MAX_SEND_FAILS = 3


def agent_name(p: Project, iid: str, kind: str) -> str:
    return f"{iid.lower()}-define" if kind == "define" else iid.lower()


ROUND2 = (
    "7. **Round 2 checks the fixes**, it does not reopen the branch: read the previous review (`$previous`), "
    "`git diff $previous_tip..$branch` and the «Review, round 1» section of the report. Each earlier finding is "
    "fixed (the commit or test that shows it), refuted (proof you accept) or still open. New findings only in "
    "what the fixes changed."
)
MAX_ROUNDS = 2  # what is still open after round 2 goes to the owner


def report_file(p: Project, iid: str) -> Path:
    return p.home / "reports" / f"{iid}.md"


def brief(p: Project, i: dict[str, Any], kind: str, extra: dict[str, Any] | None = None) -> str:
    a = util.sub(i, "acceptance")
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
        exists = integrate.git(p.repo, "rev-parse", "--verify", "--quiet", branch, check=False).returncode == 0
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
    backlog.update(p, i["id"], **keys)  # the guard reads the worktree from the backlog
    try:
        short, sid = sessions.start(p, i["id"], name, model, brief(p, i, kind), cwd)
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
            a = util.sub(i, role)
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
    r, v = util.sub(i, "reviewer"), util.sub(i, "review")
    return i["status"] == "in review" and bool(r) and r.get("tip") != v.get("tip") and not i.get("blocked")


def running(d: dict[str, Any]) -> int:
    return sum(1 for i in backlog.issues(d) if (i["status"] in backlog.RUNNING and not i.get("blocked")) or reviewer_active(i))


def launch_reviewer(p: Project, i: dict[str, Any], tip: str) -> None:
    rnd = len(i.get("reviews", [])) + 1
    prev: dict[str, Any] = (util.items(i, "reviews") or [{}])[-1]
    name = f"{i['id'].lower()}-review"
    model = p.get("agents", "review_model")
    review_file = p.home / "reviews" / f"{i['id']}-r{rnd}.md"
    review_file.parent.mkdir(parents=True, exist_ok=True)
    extra: dict[str, Any] = {
        "round": rnd,
        "tip": tip,
        "review_file": str(review_file),
        "round2": ROUND2 if rnd > 1 else "",
        "previous": prev.get("file", ""),
        "previous_tip": prev.get("tip", ""),
    }
    cwd = Path(i["worktree"])
    short, sid = sessions.start(p, i["id"], name, model, brief(p, i, "reviewer", extra), cwd)
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
    a = util.sub(i, "agent")
    if not a.get("session"):
        return False
    try:
        sessions.resume(p, i["id"], a["name"], a.get("model") or p.get("agents", "model"), a["session"], message, Path(a.get("cwd") or p.repo))
    except sessions.SessionError as e:
        backlog.event(p, i["id"], f"could not reach {a['name']}: {e}")
        return False
    return True


def deliver_answers(p: Project, rows: dict[str, Any]) -> None:
    d = backlog.load(p)
    for q in backlog.undelivered(d):
        i = backlog.get(d, q["issue"])
        if i["status"] in backlog.CLOSED:
            backlog.mark_delivered(p, q["id"])
            continue
        if sessions.busy(rows, (util.sub(i, "agent")).get("session")):
            continue  # it is busy (maybe talking with the owner): next tick
        if q.get("kind") in ("conflict", "review"):
            msg = q["answer"]
        elif q.get("kind") == "rejection":
            msg = (
                f"The owner sent task {i['id']} back from the integration queue: «{q['answer']}». Fix it, review "
                "again if you change much, and run `fm done` again."
            )
        elif q.get("kind") == "note":
            msg = f"A note from outside fm for task {i['id']} ({q.get('source')}): «{q['answer']}»."
        elif q.get("kind") == "permission":
            msg = f"The owner answered your request {q['id']} ({q.get('action')}): «{q['answer']}». " + (
                "It is allowed once: run it again." if q.get("allowed") else "It is not allowed."
            )
        else:
            msg = f"The owner answered your question {q['id']}: «{q['answer']}». Carry on with the task."
        if send(p, i, msg):
            backlog.mark_delivered(p, q["id"])
            backlog.update(p, i["id"], agent={**i["agent"], "nudged": None, "started": util.now()})
            backlog.event(p, i["id"], f"answer {q['id']} delivered to {i['agent']['name']}")
        else:
            fails = i.get("undelivered", 0) + 1
            backlog.update(p, i["id"], undelivered=fails)
            if fails >= MAX_SEND_FAILS:
                backlog.mark_delivered(p, q["id"])
                backlog.update(p, i["id"], blocked=f"its agent could not be reached to deliver {q['id']}: «{q['answer']}». Open it and tell it, or relaunch.")


def watch_agents(p: Project, rows: dict[str, Any]) -> None:
    d = backlog.load(p)
    waiting = {q["issue"] for q in backlog.open_questions(d)} | {q["issue"] for q in backlog.undelivered(d)}
    for i in backlog.issues(d):
        a = util.sub(i, "agent")
        if i["status"] not in backlog.RUNNING or i.get("blocked") or i["id"] in waiting:
            continue
        if not a.get("session") or sessions.busy(rows, a.get("session")):
            continue  # unknown yet (fill_sessions), or busy
        if idle_s(a) < NUDGE_IDLE_S:
            continue  # it may be waiting for the owner, who is talking to it

        if not a.get("nudged"):
            how = "fm ready" if a.get("kind") == "define" else "fm done"
            if send(
                p,
                i,
                f"Your turn ended but task {i['id']} is not finished: nothing says so. If it is, run "
                f"`{how}`; if you need the owner, `fm ask`; if it cannot be done, `fm block`. "
                "Otherwise carry on.",
            ):
                backlog.update(p, i["id"], agent={**a, "nudged": util.now()}, nudges=i.get("nudges", 0) + 1)
                backlog.event(p, i["id"], f"{a.get('name')} ended its turn without finishing: nudged")
                continue
        if (util.age_s(a.get("nudged")) or 0) > NUDGE_IDLE_S and idle_s(a) > NUDGE_IDLE_S:
            backlog.update(p, i["id"], blocked="its agent stopped without finishing, even after a nudge")
            backlog.event(p, i["id"], "blocked: agent stopped without finishing")


def send_back(p: Project, iid: str, kind: str, note: str) -> None:
    """Back to its agent (status in progress), with a note the loop delivers to its session."""
    with backlog.edit(p) as d:
        i = backlog.get(d, iid)
        n = sum(1 for q in d["decisions_resolved"] if q.get("issue") == iid and q.get("kind") in ("rejection", "conflict", "review"))
        i["status"] = "in progress"
        i.pop("check", None)
        for k in ("accepted", "merge", "merging"):  # a merge ref or marker of the old tip must not close the new work
            i.pop(k, None)
        if kind == "rejection":
            i["rework"] = i.get("rework", 0) + 1
        elif kind == "conflict":
            i["conflicts"] = i.get("conflicts", 0) + 1
        i["agent"] = {**(util.sub(i, "agent")), "started": util.now(), "nudged": None}
        d["decisions_resolved"].append(
            {
                "id": f"{iid} R{n + 1}",
                "issue": iid,
                "kind": kind,
                "agent": i["agent"].get("name"),
                "text": "integration",
                "answer": note,
                "resolved": util.now()[:10],
                "delivered": False,
                "options": [],
                "title": i["title"],
            }
        )
    backlog.event(p, iid, f"→ in progress: sent back ({kind}): {note}")


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
    except Exception as e:  # noqa: BLE001
        backlog.update(p, iid, blocked=f"the loop could not {what} it: {e}"[:300])
        backlog.event(p, iid, f"blocked: could not {what}: {e}"[:300])


def queue_one(p: Project, i: dict[str, Any], main: str, rows: dict[str, Any]) -> None:
    head = integrate.git(p.repo, "rev-parse", "--verify", i.get("branch", "HEAD")).stdout.strip()
    main_head = integrate.git(p.repo, "rev-parse", main, check=False).stdout.strip()
    v, r = util.sub(i, "review"), util.sub(i, "reviewer")
    if v.get("tip") != head:  # no verdict on this tip yet: an independent review first
        if r.get("tip") == head:
            if r.get("session") and not sessions.busy(rows, r["session"]) and idle_s(r) > NUDGE_IDLE_S:
                backlog.update(p, i["id"], blocked="its reviewer ended without a verdict (`fm verdict`)")
                backlog.event(p, i["id"], "blocked: reviewer ended without a verdict")
        elif running(backlog.load(p)) < int(p.get("agents", "slots")) and not (p.state / "paused").exists():
            launch_reviewer(p, i, head)
        return
    if v["high"] + v["medium"] and v["round"] < MAX_ROUNDS:
        send_back(
            p,
            i["id"],
            "review",
            f"The independent review of your branch (round {v['round']}) leaves "
            f"H {v['high']}, M {v['medium']} open: read {v['file']}. Fix or refute each H and M finding with "
            "proof, nothing else; add «Review, round N» to your report and run `fm done` again.",
        )
        return
    c = util.sub(i, "check")
    if c.get("head") != head or c.get("main") != main_head:
        c = integrate.check(p, i["id"])
    if c.get("conflict"):
        send_back(
            p,
            i["id"],
            "conflict",
            f"{main} moved and your branch conflicts with it. Merge {main} into "
            "your branch, resolve keeping both changes, run the tests, and run `fm done` again.",
        )
        return
    if integrate.merges_alone(p, c["risk"]):
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
        ("queue", lambda: run_queue(p, rows)),
        ("health", lambda: health.run(p, rows)),
        ("launch", lambda: launch_free(p)),
    ]
    for name, step in steps:
        beat(p, name)
        try:
            step()
        except Exception as e:  # noqa: BLE001 - one broken step must not stop the others, health files it
            util.append_jsonl(p.state / "errors.jsonl", {"time": util.now(), "step": name, "error": repr(e), "trace": traceback.format_exc()[-2000:]})


def forever(homes: list[str] | None = None, once: bool = False) -> None:
    import fcntl

    from .config import registry_file

    lock = open(str(registry_file()) + ".loop.lock", "a")  # noqa: SIM115 - held for the life of the process
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
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
    py = subprocess.run(["/usr/bin/which", "python3"], capture_output=True, text=True).stdout.strip() or "python3"
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
