"""The orchestrator-only commands (the `[deny.grants]` groups, guard.py and grants.py): the log of the guard's
decisions on them and of the owner's answers.

The guard hook writes one line per decision in <state>/approved.jsonl (decided): {at, verdict: allow|deny, why
(agent | session | process | grant, for a deny), session, agent_id, agent (its name), orchestrator (the mark's
name), pattern, command (its first 300 characters), tool_use_id, for why=process marked_pid (the mark's pid, null
when it records none), for why=grant group (the group without a grant), and on an allow grant ([{group, via, at,
answer}], the live grants it used)}; on an allow it also writes a line to today's notes (who ran what, and each
grant: group, the owner, when and through which surface). The owner's answers to the hooks module's question
(hooks_mod.grant, grants.py) are lines too (granted): {at, verdict: grant | deny | late | revoke, why: grant, group,
answer (yes | no | dismissed | timeout | unavailable), surfaces, session, orchestrator, command, tool_use_id, until
(on a grant), cooldown_until (after a no), pending (the request kept for the panel), refused (session | process: a
yes that came where it grants nothing)}; `foreman permissions grants off` writes {verdict: revoke, group, session}. A
reserved file a Write/Edit call of an agent or another session tried (guard.check_file) is a deny line too, with pattern
`file:<class>` and file (its path) instead of command. So who
approved what, and when, is foreman's own record of the owner's yes, never a text the orchestrator writes. The log
holds no exit code: the guard runs before the command. Past TRIM_BYTES the log keeps its last LOG_DAYS days. The
loop pass reads the refusals of agents, of other sessions and of other processes of a mark with one, commands and
files (refusals_since: notice orchestrator_only_denied).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import i18n, identity, notes

if TYPE_CHECKING:
    from .config import Project
    from .guard import Hit
    from .identity import Event

LOG_DAYS = 30
TRIM_BYTES = 512 * 1024
COMMAND_CHARS = 300


def log_file(project: Project) -> Path:
    return project.state / "approved.jsonl"


def _append(project: Project, rec: dict[str, Any]) -> None:
    f = log_file(project)
    f.parent.mkdir(parents=True, exist_ok=True)
    with notes.file_lock(f):
        with open(f, "a") as out:
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if f.stat().st_size > TRIM_BYTES:
            cut = (datetime.now() - timedelta(days=LOG_DAYS)).strftime("%Y-%m-%dT%H:%M:%S")
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines(True)
            f.write_text("".join(ln for ln in lines if (_read(ln) or {}).get("at", "") >= cut))


def _read(line: str) -> dict[str, Any] | None:
    try:
        rec = json.loads(line)
    except ValueError:
        return None
    return rec if isinstance(rec, dict) else None


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")  # to the microsecond: refusals_since compares it


def decided(project: Project, ev: Event, hit: Hit, allowed: bool) -> None:
    """The guard hook's decision on an orchestrator-only command: a line in the log and, when it passes, a line in
    today's notes. Never raises."""
    from . import orchestrator

    try:
        cmd = str((ev.get("tool_input") or {}).get("command") or "")
        aid = ev.get("agent_id") or None
        who = (orchestrator.current(project) or {}).get("name") or None
        _append(
            project,
            {
                "at": _now(),
                "verdict": "allow" if allowed else "deny",
                "why": None if allowed else hit.why,
                "session": ev.get("session_id"),
                "agent_id": aid,
                "agent": identity.agent_name(project.state, aid) if aid else None,
                "orchestrator": who,
                "pattern": str(hit),
                **({"file": hit.path} if hit.where == "file" else {"command": cmd[:COMMAND_CHARS]}),
                "tool_use_id": ev.get("tool_use_id"),
                **({"marked_pid": orchestrator.record(project).get("pid")} if hit.why == "process" else {}),
                **({"group": hit.group} if hit.why == "grant" else {}),
                **({"grant": hit.grant} if allowed and hit.grant else {}),
            },
        )
        if allowed:
            t = i18n.for_project(project)
            line = t["approved_note"].format(who=who or "orchestrator", command=" ".join(cmd.split())[:COMMAND_CHARS])
            for g in hit.grant or []:
                line += t["approved_note_grant"].format(
                    group=g.get("group"), owner=project.owner, at=str(g.get("at") or "")[11:16], via=g.get("via")
                )
            notes.note(project, "-", line)
    except Exception:  # noqa: BLE001 (the guard never fails a call over its log)
        pass


def granted(project: Project, rec: dict[str, Any]) -> None:
    """A line for the owner's answer to the hooks module's question on a [deny.grants] group (hooks_mod.grant)."""
    _append(project, {"at": _now(), "why": "grant", **rec})


def refusals_since(project: Project, since: str) -> list[dict[str, Any]]:
    """The log's refusals of an agent, another session or another process when the mark had one (not the grant's,
    nor those of a mark without a process) after `since` (ISO time)."""
    try:
        text = log_file(project).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        rec = _read(line)
        if (
            rec
            and rec.get("verdict") == "deny"
            and (rec.get("why") in ("agent", "session") or (rec.get("why") == "process" and rec.get("marked_pid")))
            and rec.get("at", "") > since
        ):
            out.append(rec)
    return out
