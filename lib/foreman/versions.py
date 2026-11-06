"""The foreman a session runs, against the one installed.

Claude Code keeps a session on the plugin version it started with: its hooks and the `bin/` on the Bash tool's PATH
stay in that version's folder until the owner types `/reload-plugins` in the session (or resumes it). When a new
version is installed, Claude Code writes `.orphaned_at` in the folder of the replaced one and deletes the folder 14
days later. So a session can tell it was left behind with one `stat` of its own root.

- `own(env)`: what this process runs, {version, root, kind}. In a hook the root is `CLAUDE_PLUGIN_ROOT`; in the CLI
  (the variable does not reach the Bash tool) the folder this code runs from. `kind` is `installed`, `working_copy`
  (the root has `.git`), `inline` (`--plugin-dir`: the data folder ends in `-inline`) or `none` (no root or no
  version). Only `installed` can be behind: the others run live code or are a deliberate test.
- `installed(own, best)`: {version, root} of the installed foreman: the best root of the trace (install.ranked over
  `install/installs.json`, or `best`, the root `install.record` just chose) when it is an installed copy that is not
  orphaned; if this session's root is orphaned, also the highest sibling folder (`<root>/../*`) with `bin/foreman`,
  no `.orphaned_at` and no `.git` (the cache's own layout: it covers the time between `claude plugin update` and the
  first session that records the new version). None when nothing qualifies.
- `behind(own, inst)`: None, or {mine, root, installed, installed_root, older}: an installed session whose root is
  orphaned or whose version is lower than the installed one. With an orphaned root and nothing newer known,
  `installed` is None («a newer install, version not known yet»). A downgrade (orphaned, installed lower) counts
  too, with `older` False.
- `<state root>/install/sessions.json`: one record per session, {session: {version, root, kind, project, cwd, seen,
  last, told, claude_code, entrypoint}}, written by the hooks on SessionStart and UserPromptSubmit and by the hooks
  module's `session.start` (hooks_mod.start, the only writer of `claude_code`); never from a subagent (it shares the
  session and its version with its parent). `entrypoint` is the session's host as Claude Code gives it in the
  environment of its hooks (`CLAUDE_CODE_ENTRYPOINT`: `cli`, `claude-desktop`, `sdk-ts`…): information, and only
  ever a reason not to ask the owner for anything (reloads_itself: the desktop app loads a new version by itself),
  never a reason to ask. It is rewritten only when the version, root, kind, project, cwd, Claude Code version or
  entrypoint change, when `last` is more than an hour old, or when `told` changes; entries not seen for
  SESSIONS_DAYS days go, and at most SESSIONS_KEEP stay. `told` is the installed version (or `?` when not known)
  this session was already told about: once per installed version.
- The grace (`appeared`, `settling`): some hosts load a newly installed version into their sessions by themselves
  (the Claude desktop app does, with no /reload-plugins). So a session is only counted as behind, and the owner is
  only asked for `/reload-plugins`, once it was seen still running the old version GRACE_S seconds or more after the
  installed version appeared on the machine (`appeared`: the earliest of the installed root's `seen` in
  installs.json and the `.orphaned_at` mark of the session's own root; unknown counts as long ago). Until then it is
  `settling`: on the old version but maybe about to load the new one. What decides is what the session runs, never
  its host.
- `pending(ev, project)` / `mark_told`: what the hooks use (hooks.version_notice): record the session and say whether
  it has to be told now (not while settling: a later prompt or tick tells it).
- `estimate(started, installs)`: for a session with no record (it runs a foreman older than this module), the
  highest installed (not working copy) version whose `seen` in installs.json is not after the session's start.
  Shown with «≈»; never decides anything by itself. A `/reload-plugins` needs no guess: the hooks module's
  `session.start` fires again and `hook mod start` (hooks_mod.py) records the new root and version.
- `of_session(session)`: what one session runs (its own root in a hook of that session, else its record, else the
  estimate) and whether it is behind: for the loop pass (foreman_behind) and `foreman session`.
- `live_sessions()`: the running Claude Code sessions (their heartbeats, heartbeat.live) of the registered
  projects, each with its version (recorded or estimated) and whether it is behind; for `foreman session --all` and the
  panel. `label(entry)` gives «0.16.0», «≈0.16.0» or «?».

- `CLAUDE_CODE_MIN`: the oldest Claude Code foreman supports. `claude_code_version(env)` asks the binary
  (`$CLAUDE_CODE_EXECPATH`, else `claude` on PATH) for `--version`; `claude_code_below_min(version)` says whether
  it is lower (False when the version is unknown: never blocks).

Nothing here raises into a hook: the hook wraps every call.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import install, notes, util

if TYPE_CHECKING:
    from .config import Project
    from .util import PathLike

Own = dict[str, Any]  # own(): {version, root, kind}; also the same three keys of a recorded session
Found = dict[str, Any]  # installed(), sibling(): {version, root}
Behind = dict[str, Any]  # behind(): {mine, root, installed, installed_root, older} (+ key, session, estimated)
Record = dict[
    str, Any
]  # a session of sessions.json: {version, root, kind, project, cwd, seen, last, told, claude_code, entrypoint}
Entry = dict[str, Any]  # a live_sessions() entry
Env = Mapping[str, str]  # an environment: os.environ or a copy of it

SESSIONS_DAYS = 2  # a session record not seen for this long is dropped
SESSIONS_KEEP = 200  # at most this many session records
REFRESH_SECONDS = install.REFRESH_SECONDS
CLAUDE_CODE_MIN = (
    "2.1.299"  # the oldest Claude Code foreman supports: the single source (README, CHANGELOG, check, who)
)
UNKNOWN = "?"  # `told` when the new version is not known yet
GRACE_S = 180  # a session on an older foreman this soon after the install may still load it by itself
ENTRYPOINT_VAR = "CLAUDE_CODE_ENTRYPOINT"  # the session's host (only ever a reason not to ask: reloads_itself)
SELF_RELOADING = ("claude-desktop",)  # hosts that load a newly installed plugin version into a session by themselves


def sessions_file() -> Path:
    return install.install_dir() / "sessions.json"


def _stamp(now: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now))


def orphaned(root: PathLike | None) -> bool:
    """Whether Claude Code marked this plugin folder as replaced (the one stat a tool call pays)."""
    return bool(root) and os.path.exists(os.path.join(str(root), ".orphaned_at"))


def own(env: Env | None = None, root: PathLike | None = None) -> Own:
    """{version, root, kind} of what this process runs (see the module doc)."""
    env = os.environ if env is None else env
    hook_root = env.get("CLAUDE_PLUGIN_ROOT")
    if root is None:
        root = os.path.realpath(hook_root) if hook_root else str(install.ROOT)
    root = str(root)
    version = install.root_version(root)
    data = env.get("CLAUDE_PLUGIN_DATA") or ""
    if hook_root and data.rstrip("/").endswith("-inline"):
        kind = "inline"
    elif not version:
        kind = "none"
    elif install.working_copy(root):
        kind = "working_copy"
    else:
        kind = "installed"
    return {"version": version, "root": root, "kind": kind}


def _installed_copy(root: PathLike) -> bool:
    return install.usable(root) and not install.working_copy(root) and not orphaned(root)


def sibling(root: PathLike) -> Found | None:
    """{version, root} of the highest installed sibling of `root` (same cache folder) not orphaned, or None."""
    best: Found | None = None
    try:
        entries = list(Path(root).parent.iterdir())
    except OSError:
        return None
    for p in entries:
        if str(p) == str(root) or not p.is_dir() or not _installed_copy(p):
            continue
        v = install.root_version(p)
        if v and (best is None or install.version_key(v) > install.version_key(best["version"])):
            best = {"version": v, "root": str(p)}
    return best


def installed(me: Own | None = None, best: PathLike | None = None) -> Found | None:
    """{version, root} of the installed foreman, or None (see the module doc)."""
    if best is None:
        ranked = install.ranked(install._read_installs())
        best = ranked[0] if ranked else None
    found: list[Found] = []
    if best and _installed_copy(best):
        v = install.root_version(best)
        if v:
            found.append({"version": v, "root": str(best)})
    if me and me.get("kind") == "installed" and orphaned(me["root"]):
        s = sibling(me["root"])
        if s:
            found.append(s)
    return max(found, key=lambda x: install.version_key(x["version"])) if found else None


def behind(me: Own | None, inst: Found | None) -> Behind | None:
    """None, or what to say to a session left behind (see the module doc)."""
    if not me or me.get("kind") != "installed" or not me.get("version"):
        return None
    mine = me["version"]
    other = inst if inst and inst.get("root") != me["root"] else None
    newer = bool(other and install.version_key(other["version"]) > install.version_key(mine))
    if not (orphaned(me["root"]) or newer):
        return None
    return {
        "mine": mine,
        "root": me["root"],
        "installed": other["version"] if other else None,
        "installed_root": other["root"] if other else None,
        "older": newer,
    }


def appeared(
    installed_root: PathLike | None, root: PathLike | None = None, installs: install.Installs | None = None
) -> float | None:
    """When the installed version (at `installed_root`) appeared on this machine, as epoch seconds: the earliest of
    its `seen` in installs.json and the mtime of the `.orphaned_at` mark of `root` (the session's own folder, which
    Claude Code marks when it installs the new one). None when neither is known."""
    times: list[float] = []
    if installed_root:
        installs = install._read_installs() if installs is None else installs
        t = util.iso_epoch((installs.get(str(installed_root)) or {}).get("seen"))
        if t is not None:
            times.append(t)
    if root:
        with contextlib.suppress(OSError):
            times.append(os.stat(os.path.join(str(root), ".orphaned_at")).st_mtime)
    return min(times) if times else None


def settling(b: Behind | None, observed: float | None, installs: install.Installs | None = None) -> bool:
    """Whether a session behind (`b`, a behind() dict) may still load the installed version by itself: it was last
    seen on the old version (`observed`, epoch seconds) less than GRACE_S after that version appeared. False when
    it is not behind, or when the time it appeared or the observation is not known (counted as behind, as
    before the grace existed), and always with GRACE_S 0 (no grace)."""
    if not b or observed is None or GRACE_S <= 0:
        return False
    t = appeared(b.get("installed_root"), b.get("root"), installs)
    return t is not None and observed < t + GRACE_S


def told_key(b: Behind | None) -> str:
    return (b or {}).get("installed") or UNKNOWN


# ---------- the per-session record ----------


def read_sessions() -> dict[str, Record]:
    d = util.read_json(sessions_file(), {})
    return {k: v for k, v in d.items() if isinstance(v, dict)} if isinstance(d, dict) else {}


def reloads_itself(session: str | None) -> bool:
    """Whether the host of `session` (its record's `entrypoint`) loads a newly installed version by itself, with no
    /reload-plugins (SELF_RELOADING: the desktop app, once the session is free). Unknown is False. Only ever a
    reason not to ask the owner."""
    return bool(session) and read_sessions().get(session or "", {}).get("entrypoint") in SELF_RELOADING


def _prune(d: dict[str, Record], now: float) -> dict[str, Record]:
    cutoff = _stamp(now - SESSIONS_DAYS * 86400)
    kept = {k: v for k, v in d.items() if (v.get("last") or v.get("seen") or "") >= cutoff}
    if len(kept) > SESSIONS_KEEP:
        kept = dict(sorted(kept.items(), key=lambda kv: kv[1].get("last") or "")[-SESSIONS_KEEP:])
    return kept


def _updated(
    e: Record | None,
    me: Own,
    project: str | None,
    cwd: str | None,
    now: float,
    claude_code: str | None = None,
    entrypoint: str | None = None,
) -> Record | None:
    """The record `e` should become, or None if it can stay as it is. `claude_code` (the session's Claude Code
    version) is only known to the hooks module, `entrypoint` only where the session's environment reaches: None
    leaves the recorded one as it is."""
    stamp = _stamp(now)
    fields: dict[str, Any] = {
        "version": me.get("version"),
        "root": me.get("root"),
        "kind": me.get("kind"),
        "project": project,
        "cwd": cwd,
    }
    if claude_code:
        fields["claude_code"] = claude_code
    if entrypoint:
        fields["entrypoint"] = entrypoint
    if not e:
        return {**fields, "seen": stamp, "last": stamp, "told": None}
    if any(e.get(k) != v for k, v in fields.items()):
        moved = e.get("version") != fields["version"] or e.get("root") != fields["root"]
        return {**e, **fields, "seen": stamp if moved else e.get("seen", stamp), "last": stamp}
    if (e.get("last") or "") < _stamp(now - REFRESH_SECONDS):
        return {**e, "last": stamp}
    return None


def record_session(
    session: str | None,
    me: Own,
    project: str | None,
    cwd: str | None,
    now: float | None = None,
    claude_code: str | None = None,
    entrypoint: str | None = None,
) -> Record | None:
    """Record (or refresh) one session; returns its record. Writes only when something changes."""
    now = time.time() if now is None else now
    if not session:
        return None
    f = sessions_file()
    e = read_sessions().get(session)
    if _updated(e, me, project, cwd, now, claude_code, entrypoint) is None:
        return e
    with notes.file_lock(f):
        d = read_sessions()
        new = _updated(d.get(session), me, project, cwd, now, claude_code, entrypoint)
        if new is None:
            return d.get(session)
        d[session] = new
        util.write_json(f, _prune(d, now))
        return new


def mark_told(session: str, key: str, now: float | None = None) -> None:
    """Remember that `session` was told about the installed version `key`."""
    now = time.time() if now is None else now
    f = sessions_file()
    with notes.file_lock(f):
        d = read_sessions()
        e = d.get(session)
        if not e or e.get("told") == key:
            return
        d[session] = {**e, "told": key}
        util.write_json(f, _prune(d, now))


def pending(
    ev: Mapping[str, Any],
    project: str | None,
    env: Env | None = None,
    best: PathLike | None = None,
    now: float | None = None,
) -> Behind | None:
    """For a hook: record the session of `ev` (in `project`, a name) and return what to tell it now, or None: a
    `behind` dict with `key` (for mark_told) when it is behind, past the grace (settling: this process runs the old
    version now) and was not told about this installed version yet. Never for a subagent (`agent_id`)."""
    if ev.get("agent_id"):
        return None
    env = os.environ if env is None else env
    now = time.time() if now is None else now
    session = ev.get("session_id")
    me = own(env)
    rec = (
        record_session(session, me, project, ev.get("cwd"), now=now, entrypoint=env.get(ENTRYPOINT_VAR) or None)
        if session
        else None
    )
    b = behind(me, installed(me, best)) if me["kind"] == "installed" else None
    if not b or not session or settling(b, now):
        return None
    key = told_key(b)
    if rec and rec.get("told") == key:
        return None
    return {**b, "key": key, "session": session}


# ---------- sessions without a record, and the live list ----------


def estimate(started: float | None, installs: install.Installs | None = None) -> str | None:
    """The version a session that started at `started` (epoch seconds) most likely runs: the highest installed
    version (not a working copy) first seen before it started, from installs.json. None if none qualifies."""
    if started is None:
        return None
    installs = install._read_installs() if installs is None else installs
    stamp = _stamp(started)
    best: str | None = None
    for root, e in installs.items():
        v = e.get("version")
        if not v or (e.get("seen") or "9") > stamp or install.working_copy(root):
            continue
        if best is None or install.version_key(v) > install.version_key(best):
            best = v
    return best


def label(entry: Mapping[str, Any]) -> str:
    """«0.16.0», «≈0.16.0» (estimated) or «?» for an entry of live_sessions."""
    v = entry.get("version")
    if not v:
        return UNKNOWN
    return ("≈" if entry.get("estimated") else "") + v


def live_sessions(projects: Iterable[Project] | None = None, inst: Found | None = None) -> list[Entry]:
    """The running Claude Code sessions of the registered active projects, from their heartbeats (heartbeat.live:
    foreman's hooks module ticks in each): [{session, name, project, cwd, started, at, status, version, root, kind,
    estimated, behind, settling, orchestrator, entrypoint}], with `installed` the result of installed() (passed or
    computed once). `name` is the marked orchestrator's (orchestrator.json; Claude Code tells no other); `status`
    busy or idle at the last tick, `at` its time (epoch). `behind` is True for a recorded installed session behind
    (orphaned root or lower version) and for an estimated version lower than the installed one, once past the
    grace (its last tick came GRACE_S or more after the installed version appeared); before that `settling` is True
    instead. False when unknown. `entrypoint` is the recorded host (information only)."""
    from . import config, heartbeat, orchestrator

    if projects is None:
        try:
            projects = config.registered_projects()
        except config.ConfigError:
            projects = []
    inst = installed() if inst is None else inst
    records = read_sessions()
    installs = install._read_installs()
    out: list[Entry] = []
    for p in projects:
        o = orchestrator.current(p) or {}
        for sid, d in heartbeat.live(p):
            started = util.iso_epoch(d.get("started"))
            at = util.iso_epoch(d.get("at"))
            rec = records.get(sid) or {}
            b: Behind | None
            if rec.get("version"):
                me = {"version": rec["version"], "root": rec.get("root"), "kind": rec.get("kind") or "installed"}
                b = behind(me, inst if inst else installed(me))
                estimated = False
            else:
                estimated = not d.get("version")
                v = estimate(started, installs) if estimated else d["version"]
                me = {"version": v, "root": None, "kind": None}
                newer = bool(v and inst and install.version_key(v) < install.version_key(inst["version"]))
                b = {"root": None, "installed_root": inst["root"]} if newer and inst else None
            wait = settling(b, at, installs)
            is_behind = b is not None and not wait
            out.append(
                {
                    "session": sid,
                    "name": o.get("name", "") if o.get("session") == sid else "",
                    "project": p.name,
                    "cwd": d.get("cwd") or "",
                    "started": started,
                    "at": at,
                    "status": "busy" if d.get("busy") else "idle",
                    "version": me["version"],
                    "root": me["root"],
                    "kind": me["kind"],
                    "estimated": estimated,
                    "behind": is_behind,
                    "settling": wait,
                    "orchestrator": o.get("session") == sid,
                    "entrypoint": rec.get("entrypoint"),
                }
            )
    return out


def of_session(
    session: str | None,
    entries: Iterable[Entry] | None = None,
    env: Env | None = None,
    inst: Found | None = None,
) -> dict[str, Any] | None:
    """What `session` runs, or None when nothing is known: {version, root, kind, estimated, installed, behind}, with
    `installed` the result of installed() and `behind` a behind() dict (plus `estimated` and `settling`) or None. In
    a hook of that session (CLAUDE_PLUGIN_ROOT in `env`) its own root, which is exact; otherwise its record in
    sessions.json; otherwise the estimate of its live entry (`entries`: live_sessions(), computed when needed). An
    estimate is information only: it never decides anything (see the module doc). `settling` (see the module doc)
    takes the time it was seen on that version from the hook itself (now), else its live entry's last tick, else
    its record's `last`."""
    if not session:
        return None
    env = os.environ if env is None else env
    hit: Entry | None = None
    if env.get("CLAUDE_PLUGIN_ROOT"):
        me, estimated, observed = own(env), False, time.time()
    else:
        rec = read_sessions().get(session)
        if rec and rec.get("version"):
            me = {"version": rec["version"], "root": rec.get("root"), "kind": rec.get("kind") or "installed"}
            estimated = False
            hit = next((e for e in entries or [] if e["session"] == session), None)
            observed = (hit or {}).get("at") or util.iso_epoch(rec.get("last"))
        else:
            entries = live_sessions(inst=inst) if entries is None else entries
            hit = next((e for e in entries if e["session"] == session), None)
            if not hit or not hit.get("version"):
                return None
            me = {"version": hit["version"], "root": None, "kind": None}
            estimated = bool(hit.get("estimated"))
            observed = hit.get("at")
    if inst is None:
        inst = installed(me if me.get("kind") == "installed" else None)
    if estimated:
        newer = bool(inst and install.version_key(me["version"]) < install.version_key(inst["version"]))
        b = (
            {
                "mine": me["version"],
                "root": None,
                "installed": inst["version"],
                "installed_root": inst["root"],
                "older": True,
            }
            if newer and inst  # newer implies inst
            else None
        )
    else:
        b = behind(me, inst)
    if b:
        b = {**b, "estimated": estimated, "settling": settling(b, observed)}
    return {**me, "estimated": estimated, "installed": inst, "behind": b}


def version_tuple(text: str | None) -> tuple[int, ...] | None:
    """(major, minor, patch) from the start of a `claude --version` line («2.1.300 (Claude Code)»), else None."""
    m = re.match(r"\s*v?(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(x) for x in m.groups()) if m else None


def claude_code_version(env: Env | None = None) -> str | None:
    """The installed Claude Code's version («2.1.300»), or None when the binary is not found, fails or prints
    something unparsable. The binary is `$CLAUDE_CODE_EXECPATH` (set in a session's hooks); `claude` on PATH only
    stands in to find it. Never raises."""
    env = os.environ if env is None else env
    exe = env.get("CLAUDE_CODE_EXECPATH")
    if not exe or not os.path.isfile(exe):
        exe = shutil.which("claude", path=env.get("PATH"))
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10, env=dict(env), check=False)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    t = version_tuple(r.stdout) if r.returncode == 0 else None
    return ".".join(str(x) for x in t) if t else None


def claude_code_below_min(version: str | None) -> bool:
    """Whether `version` is a parsable version lower than CLAUDE_CODE_MIN (unknown: False)."""
    t = version_tuple(version)
    return t is not None and t < (version_tuple(CLAUDE_CODE_MIN) or (0, 0, 0))


ORPHAN_TOLD_SECONDS = 600  # without a session id, the «older foreman» line is not repeated for this long
ORPHAN_TOLD_SESSIONS = 20  # session ids remembered per root


def orphan_told_file() -> Path:
    return install.install_dir() / "orphan-told.json"


def orphan_told(root: str, version: str, env: Mapping[str, str] | None = None, now: float | None = None) -> bool:
    """Whether the «older foreman» line for `root` at `version` was already said: in the same session
    (CLAUDE_CODE_SESSION_ID) or, without one, in the last ORPHAN_TOLD_SECONDS. Records it when it was not.
    `<state root>/install/orphan-told.json`: {root: {version, at, sessions: [...]}}."""
    env = os.environ if env is None else env
    now = time.time() if now is None else now
    session = env.get("CLAUDE_CODE_SESSION_ID") or None
    f = orphan_told_file()
    with notes.file_lock(f):
        d = util.read_json(f, {})
        d = d if isinstance(d, dict) else {}
        e = d.get(root)
        e = e if isinstance(e, dict) else {}
        same = e.get("version") == version
        seen = list(e.get("sessions") or []) if same else []
        if same and (session in seen if session else now - float(e.get("at") or 0) < ORPHAN_TOLD_SECONDS):
            return True
        if session:
            seen = (seen + [session])[-ORPHAN_TOLD_SESSIONS:]
        d[root] = {"version": version, "at": now, "sessions": seen}
        util.write_json(f, d)
    return False


def orphan_line(
    root: PathLike | None = None, once: bool = False, env: Mapping[str, str] | None = None, now: float | None = None
) -> str | None:
    """The stderr line of a CLI that runs from a plugin folder Claude Code marked `.orphaned_at` (this session was
    left on a replaced version), None otherwise; the caller asks only for the commands that act (cli.acts). One stat
    unless it is orphaned. None too during the grace after the new install (settling: the session may still load it
    by itself).
    With `once`, None too when it was already said for this root and version (orphan_told)."""
    root = str(install.ROOT if root is None else root)
    if not orphaned(root):
        return None
    me = own({}, root=root)
    if me["kind"] != "installed":
        return None
    if settling({"root": root, "installed_root": None}, time.time() if now is None else now):
        return None
    if once and orphan_told(root, me["version"], env=env, now=now):
        return None
    inst = installed(me)
    newer = f"replaced by {inst['version']}" if inst else "replaced by a newer install"
    return (
        f"foreman: this is foreman {me['version']}, the one this session started with ({newer}): the owner "
        "types /reload-plugins in this session to run the installed one"
    )


def session_version_line(session: str) -> str:
    """The foreman a marked session runs (`foreman session`): recorded, or estimated with «≈»; never changes the exit
    code."""
    try:
        v = of_session(session, env={})
    except Exception:  # noqa: BLE001 (a version notice is optional: no record means 'not known')
        v = None
    if not v:
        return "foreman version of that session not known (no record, no estimate)"
    lbl = label(v)
    est = " (estimated: no record of its own)" if v["estimated"] else ""
    b = v["behind"]
    if b and b.get("settling"):
        inst = b.get("installed") or "a newer install"
        return (
            f"foreman {lbl} in that session{est}, {inst} installed a moment ago: the session may still load it by "
            f"itself (some hosts do, the desktop app among them); not behind until {GRACE_S // 60} min "
            "after the install"
        )
    if b:
        inst = b.get("installed") or "a newer install"
        return (
            f"foreman {lbl} in that session{est}, {inst} installed: behind; the owner types /reload-plugins "
            "there (the desktop app loads it by itself)"
        )
    if v.get("kind") in ("working_copy", "inline"):
        return f"foreman {lbl} in that session ({v['kind'].replace('_', ' ')})"
    return f"foreman {lbl} in that session{est}" + (", the installed one" if v.get("installed") else "")


def sessions_command(only_behind: bool = False, as_json: bool = False) -> int:
    """`foreman session --all`: the live sessions of the registered products with their foreman; exit 0 always.
    `only_behind`: those on an older foreman, behind or still settling after the install. The installed foreman is
    looked for from this CLI's own root too: run from a replaced folder (an older session's PATH), the newer sibling
    is the installed one even before any session recorded it."""
    inst = installed(own({}))
    entries = sorted(live_sessions(inst=inst), key=lambda e: (e["project"], e.get("name") or "", e["session"]))
    if only_behind:
        entries = [e for e in entries if e["behind"] or e.get("settling")]
    if as_json:
        keep = (
            "session name project cwd started status version root kind estimated behind settling orchestrator "
            "entrypoint"
        ).split()
        sessions = [{k: e.get(k) for k in keep} for e in entries]
        print(json.dumps({"installed": inst, "sessions": sessions}, ensure_ascii=False, indent=1))
        return 0
    print(
        f"installed foreman {inst['version']} ({inst['root']})"
        if inst
        else "installed foreman: not known (no session has recorded an installed copy yet)"
    )
    if not entries:
        print("no live session runs an older foreman" if only_behind else "no live session in the registered products")
        return 0
    rows = [
        (
            e["project"],
            e.get("name") or e["session"][:8],
            label(e),
            (
                ("BEHIND " if e["behind"] else "settling " if e.get("settling") else "")
                + ("orchestrator" if e["orchestrator"] else "")
            ).rstrip(),
            e["session"],
        )
        for e in entries
    ]
    widths = [max(len(r[i]) for r in rows) for i in range(4)]
    for r in rows:
        print("  ".join(r[i].ljust(widths[i]) for i in range(4)) + "  " + r[4])
    if any(e["estimated"] for e in entries):
        print(
            "≈: estimated (no record of its own: it started on a foreman older than the per-session record): the "
            "highest version installed before it started, or before its last /reload-plugins"
        )
    if any(e.get("settling") for e in entries):
        print(
            f"settling: on an older foreman less than {GRACE_S // 60} min after the new one was installed; "
            "it may load it by itself (the desktop app does): nothing to ask yet"
        )
    if any(e["behind"] for e in entries):
        print("behind: the owner types /reload-plugins in each one (only they can), or it is relaunched")
    return 0
