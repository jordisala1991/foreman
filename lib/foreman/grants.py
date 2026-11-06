"""The owner's grants for `[deny.grants]` groups: orchestrator-only commands that also need the owner's own yes.

A group is a name and a list of regexes (`[deny.grants] deploy = ["make deploy", …]`). A command that runs one of
its patterns passes only for the marked orchestrator's main thread, from its own process (guard.py: the agent,
session and process checks), and also needs a live grant of that group (guard's why=grant, checked last). A grant
is given only by the owner's answer to a question foreman's hooks module asks in the marked session's main loop
(`hook mod grant`, hooks_mod.py), never by a command: the guard refuses any Bash call that runs `foreman hook mod`
(guard.HOOK_MOD).
Without the module (or while its side of the question is not there) no grant exists and the groups stay closed.

<state>/grants.json (one file, written under its lock):
- `grants`: {group: {session, pid, pid_start, at, until, via, answer, surfaces, orchestrator, tool_use_id}}. A grant
  is live while `until` (local ISO) is ahead, and the mark (orchestrator.record) is the same session with the same
  pid and start time: it ends when the mark moves (relief, /clear to another process) or the process restarts.
- `cooldowns`: {group: {session, until}}: after the owner's no, COOLDOWN_S (10 min, fixed) in which the module does
  not ask again for that group in that session (a timeout or a dismissal sets none).
- `pending`: {group: {at, session, pid, pid_start, orchestrator, command, tool_use_id}}: the last request of a group
  that went unanswered (the question timed out), kept so the owner can approve it later from foreman's panel
  (approve()). It expires `grant_minutes` after `at` (the same window a yes would have opened: a request older than
  that is stale, the orchestrator has moved on); a grant of the group, `foreman permissions grants off` or a mark of
  another session or process clears it (pending() reads only the marked process's live ones).
- `once`: {tool_use_id: {session, kind, path, command, at}}: the owner's yes to write one reserved file of a class
  in ONCE_KINDS (Claude Code's settings, the registry) in one tool call: asked by the hooks module in
  the main thread of any session of the product (`hook mod grant`, a check of a Write/Edit/MultiEdit/NotebookEdit
  call or of a Bash command that writes such a file), written by its answer phase (give_once) and spent by the guard
  on that very call (take_once, from guard.check_file or the Bash rule): the same session, the same tool_use_id, the
  same file (its real path) or the same command, never an agent. It is spent whether the call then passes or not,
  and goes stale ONCE_S after it was given. Not a group: no window, no cooldown, no pending request, no panel.

A grant from the panel (via "panel") carries `sig`, an HMAC of its fields with the panel's grant key
(<state root>/panel/grant.key, mode 600, written by the panel the first time): from_panel() trusts it only when the
key verifies it, so the hooks module lets such a grant through without asking (its memory never heard it given),
while a grants.json written by hand is still asked again (the module's second check).

`foreman permissions grants` lists them; `foreman permissions grants off [GROUP]` removes grants and pending requests
(anyone may: it only takes away). Every decision is also a line in approved.jsonl (approved.granted).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import config, util

if TYPE_CHECKING:
    from .config import Project
    from .gitpush import Resolver

GROUP_NAME = r"^[a-z0-9][a-z0-9-]*$"
COOLDOWN_S = 600  # after the owner's no, the module does not ask again for the group in that session
ONCE_KINDS = ("registry", "claude")  # the reserved classes the owner may let one call write (guard.check_file)
ONCE_S = 600  # a one-use grant not spent within this (its call never reached the guard) is stale
ANSWERS = ("yes", "no", "dismissed", "timeout", "unavailable")
ISO = "%Y-%m-%dT%H:%M:%S"


def file(project: Project) -> Path:
    return project.state / "grants.json"


# foreman's own group, in every product (internal=True): `foreman permissions apply … --opens`, a change of the
# permissions that allows something that was not allowed before, needs the owner's own yes (permissions.py; the
# built-in guard.PERMISSIONS_APPLY already keeps it to the marked orchestrator), and so does `foreman integrate …
# --opens`, a branch that changes a live foreman.toml or guard table that way (integrate.py): one yes covers both for
# grant_minutes. Merged with a product group of the same name.
INTERNAL = {
    "permissions": [
        r"""(^|[\s/'"])foreman\S*['"]?\s+(--project[\s=]\S+\s+)?['"]?permissions['"]?\s"""
        r"""(?=(.*\s)?['"]?apply['"]?(\s|$))(?=(.*\s)?['"]?--opens['"]?(\s|$))""",
        r"""(^|[\s/'"])foreman\S*['"]?\s+(--project[\s=]\S+\s+)?['"]?integrate['"]?\s"""
        r"""(?=(.*\s)?['"]?--opens['"]?(\s|$))""",
    ]
}


def groups(project: Project, internal: bool = False) -> dict[str, list[str]]:
    """{group: [patterns]} of `[deny.grants]` (only well-formed entries: a name and a list of strings); with
    internal=True, also foreman's own INTERNAL group (what a grant, the question and the panel go by)."""
    raw = project.section("deny").get("grants") or {}
    out = (
        {
            str(k): [str(p) for p in v]
            for k, v in raw.items()
            if isinstance(v, list) and all(isinstance(p, str) for p in v) and re.match(GROUP_NAME, str(k))
        }
        if isinstance(raw, dict)
        else {}
    )
    if internal:
        for g, pats in INTERNAL.items():
            out[g] = out.get(g, []) + [p for p in pats if p not in out.get(g, [])]
    return out


def patterns(project: Project, internal: bool = False) -> list[str]:
    """Every group's patterns, in order, without repeats (internal: as for groups())."""
    out: list[str] = []
    for pats in groups(project, internal).values():
        out += [p for p in pats if p not in out]
    return out


def groups_of(project: Project, cmd: str, at: Resolver | None = None) -> list[str]:
    """The groups whose patterns the command runs (guard.deny, the same reading as every [deny] list; at: its
    pushes resolved as for the other lists)."""
    from . import guard

    return [g for g, pats in groups(project, True).items() if pats and guard.deny(pats, cmd, at)]


def minutes(project: Project, key: str, default: int) -> int:
    v = project.section("deny").get(key, default)
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else default


def _empty() -> dict[str, dict[str, Any]]:
    return {"grants": {}, "cooldowns": {}, "pending": {}, "once": {}}


def read(project: Project) -> dict[str, dict[str, Any]]:
    """grants.json as {grants, cooldowns, pending, once}, each a dict (an unreadable file reads empty)."""
    d = util.read_json(file(project), {})
    out = _empty()
    if isinstance(d, dict):
        for k in out:
            if isinstance(d.get(k), dict):
                out[k] = {g: e for g, e in d[k].items() if isinstance(e, dict)}
    return out


def _write(project: Project, d: dict[str, Any]) -> None:
    util.write_json(file(project), d)


def _now() -> str:
    return util.now().strftime(ISO)


def _expiry(project: Project, at: Any) -> str | None:
    """When a pending request made at `at` (local ISO) expires: grant_minutes later."""
    try:
        return (util.fromiso(at) + timedelta(minutes=minutes(project, "grant_minutes", 30))).strftime(ISO)
    except (TypeError, ValueError, AttributeError):
        return None


def _ahead(until: Any) -> bool:
    return isinstance(until, str) and until > _now()


def live(project: Project, group: str) -> dict[str, Any] | None:
    """The group's grant when it is live: `until` ahead, given to the marked session, in the mark's process (the
    recorded pid and start time). None otherwise, also when the mark records no process."""
    from . import orchestrator

    g = read(project)["grants"].get(group)
    mark = orchestrator.record(project)
    if not g or not mark or not isinstance(mark.get("pid"), int):
        return None
    same = (g.get("session"), g.get("pid"), g.get("pid_start")) == (
        mark.get("session"),
        mark.get("pid"),
        mark.get("pid_start"),
    )
    return g if same and _ahead(g.get("until")) else None


def cooling(project: Project, group: str, session: str | None) -> str | None:
    """The end of the group's cooldown in this session (local ISO), or None."""
    c = read(project)["cooldowns"].get(group) or {}
    return c["until"] if c.get("session") == session and _ahead(c.get("until")) else None


def give(
    project: Project, group: str, mark: dict[str, Any], via: str, surfaces: list[str], tool_use_id: str | None
) -> dict[str, Any]:
    """Records a grant of `group` for the mark (orchestrator.record: session, pid, pid_start) for [deny]
    grant_minutes; clears the group's cooldown and pending request. Returns the grant."""
    g = _entry(project, mark, via, surfaces, tool_use_id)
    f = file(project)
    f.parent.mkdir(parents=True, exist_ok=True)
    with util.file_lock(f):
        d = read(project)
        _put(d, group, g)
        _write(project, d)
    return g


def _put(d: dict[str, Any], group: str, g: dict[str, Any]) -> None:
    d["grants"][group] = g
    d["cooldowns"].pop(group, None)
    d["pending"].pop(group, None)


def _entry(
    project: Project, mark: dict[str, Any], via: str, surfaces: list[str], tool_use_id: str | None
) -> dict[str, Any]:
    at = util.now()
    return {
        "session": mark.get("session"),
        "pid": mark.get("pid"),
        "pid_start": mark.get("pid_start"),
        "orchestrator": mark.get("name") or None,
        "at": at.strftime(ISO),
        "until": (at + timedelta(minutes=minutes(project, "grant_minutes", 30))).strftime(ISO),
        "via": via,
        "answer": "yes",
        "surfaces": surfaces,
        "tool_use_id": tool_use_id,
    }


def cool(project: Project, group: str, session: str) -> str:
    """The owner said no: no question for `group` in `session` for COOLDOWN_S. Returns its end (local ISO)."""
    until = (util.now() + timedelta(seconds=COOLDOWN_S)).strftime(ISO)
    f = file(project)
    f.parent.mkdir(parents=True, exist_ok=True)
    with util.file_lock(f):
        d = read(project)
        d["cooldowns"][group] = {"session": session, "until": until}
        _write(project, d)
    return until


def pend(project: Project, group: str, req: dict[str, Any]) -> None:
    """Keeps the group's unanswered request (the last one) for the owner to approve later from the panel."""
    f = file(project)
    f.parent.mkdir(parents=True, exist_ok=True)
    with util.file_lock(f):
        d = read(project)
        d["pending"][group] = {"at": _now(), **req}
        _write(project, d)


def pending(project: Project) -> dict[str, dict[str, Any]]:
    """The live pending requests of the marked session and process, by group, each with `expires` (local ISO)."""
    from . import orchestrator

    mark = orchestrator.record(project)
    out = {}
    for g, r in read(project)["pending"].items():
        exp = _expiry(project, r.get("at"))
        if g in groups(project, True) and _same_mark(r, mark) and _ahead(exp):
            out[g] = {**r, "expires": exp}
    return out


def _same_mark(r: dict[str, Any], mark: dict[str, Any]) -> bool:
    """Whether a request was made in the mark's session and process (a request with no process recorded is not)."""
    if not mark.get("session") or r.get("session") != mark["session"]:
        return False
    return (r.get("pid"), r.get("pid_start")) == (mark.get("pid"), mark.get("pid_start"))


class NotApproved(Exception):
    """approve() refused: `why` is none | expired | session | process | live (and the text says it for the log)."""

    def __init__(self, why: str, text: str, grant: dict[str, Any] | None = None) -> None:
        super().__init__(text)
        self.why = why
        self.grant = grant


def approve(project: Project, group: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """The owner approves the group's pending request from the panel: a grant for the mark, exactly like a yes to the
    question (grant_minutes, bound to the mark's session, pid and start time), via "panel" and signed with the panel's
    grant key. Only when the request is there and not expired, the mark is still the session and process that made
    it, and that process still runs (`ps` unreadable: refused). One request grants once: it is taken under the file's
    lock. Returns (grant, request); raises NotApproved."""
    from . import orchestrator, procs

    f = file(project)
    f.parent.mkdir(parents=True, exist_ok=True)
    key = panel_key(create=True)
    with util.file_lock(f):
        d = read(project)
        r = d["pending"].get(group)
        if r is None:
            raise NotApproved("none", f"no request of the group {group!r} waits")
        mark = orchestrator.record(project)
        why = ""
        if not _ahead(_expiry(project, r.get("at"))):
            why = "expired"
        elif not _same_mark(r, mark):
            why = "session" if r.get("session") != mark.get("session") else "process"
        elif not isinstance(mark.get("pid"), int):
            why = "process"
        else:
            try:
                running = procs.alive(mark["pid"], mark.get("pid_start"), procs.table())
            except procs.Unavailable:
                running = False
            why = "" if running else "process"
        g = d["grants"].get(group)
        if not why and g and live(project, group):
            why = "live"
        if why:
            if why in ("expired", "live", "session"):
                d["pending"].pop(group, None)
                _write(project, d)
            raise NotApproved(why, f"{group}: {why}", d["grants"].get(group) if why == "live" else None)
        g = _entry(project, mark, "panel", ["panel"], r.get("tool_use_id"))
        g["sig"] = _sign(key, group, g)
        _put(d, group, g)
        _write(project, d)
    return g, r


def give_once(
    project: Project, session: str, tool_use_id: str, kind: str, path: str | None, command: str | None
) -> dict[str, Any]:
    """The owner's yes for one call (module doc, `once`): `path` the real path of the file a file tool writes
    (guard.real_path), or `command` the Bash command, as the check judged it. Drops the stale ones. Returns it."""
    if kind not in ONCE_KINDS:
        raise ValueError(f"no one-use grant for the class {kind!r}")
    g = {"session": session, "kind": kind, "path": path, "command": command, "at": _now()}
    f = file(project)
    f.parent.mkdir(parents=True, exist_ok=True)
    with util.file_lock(f):
        d = read(project)
        d["once"] = {t: e for t, e in d["once"].items() if _fresh(e)}
        d["once"][tool_use_id] = g
        _write(project, d)
    return g


def _fresh(e: dict[str, Any]) -> bool:
    try:
        return util.fromiso(str(e["at"])) + timedelta(seconds=ONCE_S) > util.now()
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def take_once(
    project: Project, session: str | None, tool_use_id: str | None, path: str | None = None, command: str | None = None
) -> dict[str, Any] | None:
    """Spends the one-use grant of this call (module doc, `once`) and returns it, or None when there is none for this
    session, tool_use_id and file (path, as give_once took it) or command, or it is stale. One call spends it once:
    it is taken under the file's lock. The guard calls it only for a main-thread call that writes a class of
    ONCE_KINDS."""
    if not session or not tool_use_id:
        return None
    if tool_use_id not in read(project)["once"]:  # the usual case, without the lock
        return None
    f = file(project)
    with util.file_lock(f):
        d = read(project)
        e = d["once"].get(tool_use_id)
        if e is None or e.get("session") != session:
            return None
        if (path is not None and e.get("path") != path) or (command is not None and e.get("command") != command):
            return None
        del d["once"][tool_use_id]
        _write(project, d)
    return e if _fresh(e) else None


def panel_key_file() -> Path:
    return config.state_root() / "panel" / "grant.key"


def panel_key(create: bool = False) -> bytes | None:
    """The panel's grant key (from_panel), created by the panel (create=True) readable by its owner only."""
    f = panel_key_file()
    if create and not f.exists():
        f.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "w") as out:
                out.write(secrets.token_hex(32))
    try:
        k = f.read_text().strip()
    except OSError:
        return None
    return k.encode() if k else None


def _sign(key: bytes | None, group: str, g: dict[str, Any]) -> str:
    fields = [group, *(str(g.get(k)) for k in ("session", "pid", "pid_start", "at", "until", "via"))]
    return hmac.new(key or b"", "\n".join(fields).encode(), hashlib.sha256).hexdigest()


def from_panel(project: Project, group: str) -> dict[str, Any] | None:
    """The group's live grant when the owner gave it from the panel: via "panel" and its `sig` verified with the
    panel's grant key. None otherwise (no key: nothing verifies)."""
    g = live(project, group)
    key = panel_key()
    if not g or g.get("via") != "panel" or not key or not isinstance(g.get("sig"), str):
        return None
    return g if hmac.compare_digest(g["sig"], _sign(key, group, g)) else None


def revoke(project: Project, group: str | None = None) -> list[str]:
    """Removes the grants and pending requests of `group` (None: every group, and the one-use grants of files too).
    Returns the groups whose grant was removed (live or not)."""
    f = file(project)
    if not f.exists():
        return []
    with util.file_lock(f):
        d = read(project)
        names = [g for g in d["grants"] if group is None or g == group]
        for g in names:
            del d["grants"][g]
        for g in [g for g in d["pending"] if group is None or g == group]:
            del d["pending"][g]
        if group is None:
            d["once"] = {}
        _write(project, d)
    return names


def lines(project: Project) -> list[str]:
    """`foreman permissions grants`: one line per group, its state (granted until, cooling down, pending, closed)."""
    d = read(project)
    pend_now = pending(project)
    out = []
    own = groups(project)
    for g in groups(project, True):
        if g not in own and not (live(project, g) or g in pend_now or g in d["cooldowns"]):
            continue  # foreman's own group, shown only while it has a state
        if live(project, g):
            e = d["grants"][g]
            state = f"granted until {e['until'][11:16]} ({e.get('via')}, {e['at'][11:16]}, to {e.get('orchestrator')})"
        elif _ahead((d["cooldowns"].get(g) or {}).get("until")):
            state = f"closed: the owner said no, not asked again until {d['cooldowns'][g]['until'][11:16]}"
        else:
            state = "closed"
        if g in pend_now:
            state += f"; a request waits for the owner since {pend_now[g]['at'][11:16]}"
        out.append(f"{g}: {state}")
    return out or ["no [deny.grants] groups in this project"]
