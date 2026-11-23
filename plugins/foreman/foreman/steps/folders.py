"""The agent folders under Orca (`state/agent-folders`): each tick removes the ones whose agent finished its part and whose
session is not in use, and every one of a closed task. A removal that fails is a line in `errors.jsonl`; the folder is still
there, so the next tick tries again."""

from pathlib import Path
from typing import Any

from .. import util
from ..core.project.config import Project
from ..core.rules import decide
from ..core.tasks import backlog
from ..core.tasks.recovery import QUIET_S
from ..providers import registry
from ..providers.runner import Rows, SessionRef, ref_from_tab, ref_of
from ..services.agents.running import idle_s
from ..services.agents.worktree import owner_of, remove_agent_folder

KIND = {"parent": "parent", "planning": "planning", "mechanic": "mechanic"}  # session kind by folder role: the hunter and reviewer share one


def output_in(i: dict[str, Any], role: str, commit: str) -> bool:
    """The agent of a folder made at `commit` has written what it was for: the mechanic's diagnosis, the hunter's findings
    file, a verdict for the reviewer's tip. A folder of an older launch (the record names another tip) is over."""
    if role == "mechanic":
        return bool(i.get("diagnosis"))
    if role == "hunt":
        h: dict[str, Any] = i.get("hunter") or {}
        return not str(h.get("tip", "")).startswith(commit) or Path(h.get("file", "")).exists()
    r: dict[str, Any] = i.get("reviewer") or {}
    verdicts: list[dict[str, Any]] = [*(i.get("reviews") or []), i.get("review") or {}]
    return not str(r.get("tip", "")).startswith(commit) or any(str(v.get("tip", "")).startswith(commit) for v in verdicts)


def sessions_of(i: dict[str, Any], role: str, commit: str, rows: Rows) -> list[SessionRef]:
    """The sessions of the folder's own agent: a hunter's or reviewer's, by its record (the two share one session kind) and the
    tip its folder was made at; the others', by the session kind of the role."""
    if role in KIND:
        tabs: dict[str, list[str | None]] = i.get("session_tabs", {})
        kinds: dict[str, str] = i.get("session_kinds", {})
        return [ref_from_tab(rows.short(sid) or "", sid, tabs.get(sid)) for sid in i.get("sessions", []) if kinds.get(sid) == KIND[role]]
    records = [i.get("hunter")] if role == "hunt" else [*i.get("reviewers", []), i.get("reviewer")]
    return [r for a in records if a and str(a.get("tip", "")).startswith(commit) and (r := ref_of(a))]


def facts(i: dict[str, Any], role: str, commit: str, rows: Rows) -> decide.FolderFacts:
    refs = sessions_of(i, role, commit, rows)
    return decide.FolderFacts(
        role,
        i["status"],
        output_in(i, role, commit),
        any(rows.busy(r) for r in refs),
        any(rows.alive(r) and idle_s({"session": r.session}) <= QUIET_S for r in refs),
    )


def run(p: Project, rows: Rows) -> None:
    if not rows.has_sessions():  # Orca unreadable: no session is known, so none is known to be unused
        return
    tasks = {i["id"].lower(): i for i in backlog.tasks(registry.backlog(p).load())}
    for folder in sorted((p.state / "agent-folders").glob("*")):
        owner = owner_of(folder)
        if not owner or owner[0] not in tasks:
            continue
        i = tasks[owner[0]]
        if decide.folder_goes(facts(i, owner[1], folder.name[-9:], rows)):
            try:
                remove_agent_folder(p, registry.repo(p.for_task(i)), folder)
            except Exception as e:  # one folder must not stop the others: next tick tries again
                util.log_error(p.state / "errors.jsonl", "folders", f"{folder.name}: {e!r}", trace="")
