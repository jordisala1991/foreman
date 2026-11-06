"""Who orchestrates a project: the session marked with `foreman session ID [--name NAME]`.

<state>/session holds the session id alone (the hooks compare it; a hand-written one is the same) and
<state>/orchestrator.json adds {session, name, since}: the name other sessions write to with SendMessage (as
`ListAgents` shows it, given with --name; marking the same session again without one keeps it) and the time it was
marked. Nothing here is invented: no name given, an empty name. When the session marks itself (its own id), the
record also keeps the `claude` process it runs in, {pid, pid_start} (procs.py), and refuses a mark of the same
session from another live process (mark()); runs_here() tells whether a call runs under that process (the guard's
why=process).

Whether the marked session still runs (liveness) is its heartbeat (heartbeat.py), which foreman's hooks module
writes at each tick of its timer: alive, gone (it ended, or no tick for heartbeat.GONE_TICKS ticks) or unknown (no
heartbeat: the module does not run there); health() combines it with the `loop` heartbeat (ok, no_loop, gone,
silent). Claude Code's own files are not read.

The context of the orchestrator's session (the `context_rotate` notice, `foreman session`) is not read
here: foreman's hooks module stores Claude Code's own measure after each turn (hooks_mod.read_measure).
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import heartbeat, notes, util

if TYPE_CHECKING:
    from .config import Project

Mark = dict[str, Any]  # the marked orchestrator {session, name, since}: what current() reads (mark() adds the process)
Health = dict[str, Any]  # health(): see its docstring for each key


def liveness(project: Project, session_id: str | None, now: float | None = None) -> dict[str, Any]:
    """Whether a Claude Code session of the project runs, from its heartbeat (heartbeat.liveness):
    {"state": "alive"|"gone"|"unknown", "at", "started_ts", "busy", "ended", "version", "claude_code"}.
    `now` is epoch seconds (None: util.now()). Never raises."""
    when = datetime.fromtimestamp(now) if now is not None else None
    return heartbeat.liveness(project, (session_id or "").strip(), when)


def every_min(project: Project) -> float:
    """[loop] every_min as a positive number of minutes (60 when it is not one)."""
    every = project.section("loop").get("every_min", 60)
    return every if isinstance(every, (int, float)) and not isinstance(every, bool) and every > 0 else 60


def loop_limit_min(project: Project) -> float:
    """max(2 × [loop] every_min, 90): how long a loop may go without a pass before it counts as not running (a pass
    held behind a long turn and the first pass after resuming a session fit in it)."""
    return max(2 * every_min(project), 90)


def last_pass(project: Project) -> str | None:
    """The `loop` heartbeat (local ISO): the last pass, or None."""
    last = notes.heartbeats(project).get("loop")
    return last if isinstance(last, str) else None


def watches_loop(project: Project) -> bool:
    """True if the `loop` heartbeat says something about the orchestrator: the project has a [loop] prompt (foreman's
    hooks module runs the pass and writes it)."""
    return bool(project.section("loop").get("prompt"))


def pass_window(project: Project, o: Mark | None) -> tuple[str | None, float | None]:
    """(last_pass, window_from_ts) without looking at processes: the `loop` heartbeat (local ISO, or None when not
    watched) and the latest of it and the mark's `since`."""
    last = last_pass(project) if watches_loop(project) else None
    times = [t for t in (util.iso_epoch(last), util.iso_epoch((o or {}).get("since"))) if t is not None]
    return last, (max(times) if times else None)


def pass_late(project: Project, o: Mark, now: float) -> bool | None:
    """Whether the marked session `o` had no pass for loop_limit_min (since the latest of the last pass and the
    mark); None when neither is known."""
    window = pass_window(project, o)[1]
    return None if window is None else now - window > loop_limit_min(project) * 60


def health(project: Project, o: Mark | None = None, now: float | None = None) -> Health | None:
    """The marked orchestrator's health: liveness of its session combined with the `loop` heartbeat, or None (an
    inactive project, nobody marked). {"verdict", "alive", "last_pass", "window_from", "limit_min", "status", "at",
    "ended", "started_ts"}:
    - alive: liveness's state; at: its last tick and ended: its session.end (local ISO, or None); status: "busy"
      or "idle" at that tick (None without a heartbeat); started_ts: when the module started in it (epoch seconds)
      or None;
    - last_pass: the `loop` heartbeat (local ISO) or None; window_from: the latest of last_pass, the mark's `since`
      and, for a live session, when the module started in it (a session just resumed or reloaded is not silent),
      local ISO or None;
    - limit_min: loop_limit_min;
    - verdict: "gone" (gone); "no_loop" (alive and more than limit_min since window_from: foreman's hooks module
      makes no passes in it); "silent" (unknown and past the limit: no passes, and whether it lives cannot be
      told); "ok" otherwise. Without a [loop] prompt the `loop` heartbeat is not looked at: only ok or gone.
    `o` is current()'s record when the caller has it; `now` epoch seconds (None: util.now())."""
    if project.mode == "inactive":
        return None
    o = o or current(project)
    if not o or not o.get("session"):
        return None
    now = util.now().timestamp() if now is None else now
    live = liveness(project, o["session"], now)
    limit = loop_limit_min(project)
    last, window = pass_window(project, o)
    if live["state"] == "alive" and live["started_ts"]:
        window = max(window or 0, live["started_ts"])
    late = window is None or now - window > limit * 60
    if live["state"] == "gone":
        verdict = "gone"
    elif not watches_loop(project) or not late:
        verdict = "ok"
    else:
        verdict = "no_loop" if live["state"] == "alive" else "silent"
    return {
        "verdict": verdict,
        "alive": live["state"],
        "last_pass": last,
        "window_from": datetime.fromtimestamp(window).isoformat(timespec="seconds") if window else None,
        "limit_min": limit,
        "status": None if live["busy"] is None else ("busy" if live["busy"] else "idle"),
        "at": live["at"],
        "ended": live["ended"],
        "started_ts": live["started_ts"],
    }


def session_file(project: Project) -> Path:
    return project.state / "session"


class Refused(Exception):
    """`foreman session ID` from the session ID itself while another live `claude` process holds its mark."""


def mark(project: Project, session_id: str, name: str | None = None, own_session: str | None = None) -> Mark:
    """Marks the orchestrator's session; returns the stored record. Marking the same session again without a name
    keeps the name it had.

    The process (procs.py): when `own_session` (the caller's CLAUDE_CODE_SESSION_ID) is `session_id`, the session
    marks itself and the record keeps the nearest `claude` process above this call: `pid` and `pid_start`. If the
    same session is already marked by another `claude` process that still runs (a copy started with `--resume`),
    it raises Refused and changes nothing; a process that ended (a resume after a close: an SDK host and the desktop app
    start a new one) is relieved. Without `ps` no process is recorded (`pid` None, `process` says why) and the
    guard refuses orchestrator-only commands (fail closed). Marking another session (by hand, from a terminal)
    records no process, unless that session is the one already marked: then it keeps the one it had."""
    from . import procs

    session_id = session_id.strip()
    before = record(project)
    same = before.get("session") == session_id
    kept = before.get("name", "") if same else ""
    proc: dict[str, Any] = {"pid": None, "pid_start": None, "process": "marked by hand"}
    if own_session and own_session.strip() == session_id:
        try:
            table = procs.table()
            me = procs.claude_ancestor(procs=table)
        except procs.Unavailable as e:
            table, me = None, None
            proc["process"] = f"ps unavailable ({e})"
        old = before.get("pid") if same else None
        if isinstance(old, int) and (me is None or me.pid != old) and procs.alive(old, before.get("pid_start"), table):
            raise Refused(old)
        if me:
            proc = {"pid": me.pid, "pid_start": me.start, "process": None}
        elif table is not None:
            proc["process"] = "no claude process above this call"
    elif same:
        proc = {k: before.get(k) for k in ("pid", "pid_start", "process")}
    rec = {
        "session": session_id,
        "name": (name or "").strip() or kept,
        "since": util.now().strftime("%Y-%m-%dT%H:%M:%S"),
        **proc,
    }
    project.state.mkdir(parents=True, exist_ok=True)
    session_file(project).write_text(session_id + "\n")
    util.write_json(project.state / "orchestrator.json", rec)
    return rec


def carry(project: Project, old: str, new: str, procs_table: Any = None) -> Mark | None:
    """Moves the mark from `old` to `new` when `old` is the marked session and this call runs in its process: the
    module's start after a /clear or an in-process resume (hooks_mod.start, `previous`), which go on in the same
    `claude` process under a new session id. With a recorded process, the nearest `claude` above this call must be
    that pid with that start time (procs.py; `ps` unreadable: nothing moves, fail closed), and the moved record keeps
    it; without one (marked by hand) the record moves as it was. The name is kept and `since` is now. Returns the
    new record, or None when nothing moved."""
    from . import procs

    old, new = old.strip(), new.strip()
    rec = record(project)
    if not old or not new or old == new or rec.get("session") != old:
        return None
    pid = rec.get("pid")
    if isinstance(pid, int):
        try:
            me = procs.claude_ancestor(procs=procs_table)
        except procs.Unavailable:
            return None
        if me is None or me.pid != pid or me.start != rec.get("pid_start"):
            return None
    moved = {
        **rec,
        "session": new,
        "since": util.now().strftime("%Y-%m-%dT%H:%M:%S"),
    }
    session_file(project).write_text(new + "\n")
    util.write_json(project.state / "orchestrator.json", moved)
    return moved


def record(project: Project) -> dict[str, Any]:
    """orchestrator.json when it belongs to the session in <state>/session, else {}."""
    try:
        sid = session_file(project).read_text().strip()
    except OSError:
        return {}
    rec = util.read_json(project.state / "orchestrator.json", {})
    return rec if isinstance(rec, dict) and sid and rec.get("session") == sid else {}


def runs_here(project: Project, procs_table: Any = None) -> bool:
    """Whether this call runs under the marked orchestrator's own `claude` process: the nearest one above it is
    the recorded pid with the recorded start time. False without a recorded process, and when `ps` cannot be read
    (procs.Unavailable is caught: fail closed)."""
    from . import procs

    rec = record(project)
    pid, start = rec.get("pid"), rec.get("pid_start")
    if not isinstance(pid, int) or not start:
        return False
    try:
        me = procs.claude_ancestor(procs=procs_table)
    except procs.Unavailable:
        return False
    return bool(me and me.pid == pid and me.start == start)


def process_line(project: Project) -> str:
    """`foreman session`'s line on the mark's process (shown when the project has orchestrator-only commands)."""
    rec = record(project)
    if isinstance(rec.get("pid"), int):
        return (
            f"process: claude pid {rec['pid']}, started {rec.get('pid_start')} UTC (orchestrator-only commands run "
            "there)"
        )
    return (
        f"process: none recorded ({rec.get('process') or 'marked without a process'}): orchestrator-only commands "
        "are refused until the session marks itself with its own id"
    )


def current(project: Project) -> Mark | None:
    """{session, name, since} of the marked orchestrator, or None. `since` falls back to <state>/session's mtime
    when the record is missing or belongs to another session (the id written by hand)."""
    f = session_file(project)
    try:
        sid = f.read_text().strip()
        mtime = f.stat().st_mtime
    except OSError:
        return None
    if not sid:
        return None
    rec = util.read_json(project.state / "orchestrator.json", {})
    if not isinstance(rec, dict) or rec.get("session") != sid:
        rec = {}
    return {
        "session": sid,
        "name": rec.get("name") or "",
        "since": rec.get("since") or time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(mtime)),
    }


def is_orchestrator(project: Project, session_id: str | None) -> bool:
    o = current(project)
    return bool(o and session_id and session_id == o["session"])


def health_line(project: Project, h: Health | None) -> str:
    """The `foreman session` line on the marked session's health (health()), or '' without one."""
    if not h:
        return ""

    def hm(iso: str | None) -> str:
        return iso[11:16] if iso else "none seen"

    last = f"last loop pass {hm(h['last_pass'])}"
    v = h["verdict"]
    if v == "gone":
        return (
            f"NOT ALIVE: {gone_why(h)}; {last}. If you relieve it: `foreman --project "
            f"{project.name} session <your session id> --name <your name>`; otherwise tell {project.owner}."
        )
    if v == "no_loop":
        return (
            f"alive, but no loop pass since {hm(h['window_from'])} (> {h['limit_min']} min): foreman's hooks module "
            "makes no passes in it (`/reload-plugins` in that session)."
        )
    if v == "silent":
        return (
            f"no loop pass since {hm(h['window_from'])} (> {h['limit_min']} min) and no heartbeat from foreman's "
            "hooks module in it: it may be gone."
        )
    if h["alive"] == "alive":
        started = datetime.fromtimestamp(h["started_ts"]).strftime("%H:%M") if h.get("started_ts") else None
        return (
            f"alive: last tick {h['at'][11:19]}"
            + (f", module running since {started}" if started else "")
            + (f" ({h['status']})" if h.get("status") else "")
            + (f"; {last}" if watches_loop(project) else "")
        )
    return "whether it runs cannot be told (no heartbeat from foreman's hooks module in it)" + (
        f"; {last}" if watches_loop(project) else ""
    )


def gone_why(h: Health) -> str:
    """Why a gone session counts as gone, from health(): it ended (session.end) or its ticks stopped."""
    if h.get("ended"):
        return f"the session ended at {h['ended'][11:16]}"
    return f"no heartbeat from it since {h['at'][11:16]}" if h.get("at") else "no heartbeat from it"
