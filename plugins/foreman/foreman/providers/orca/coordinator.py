"""Foreman's own side in Orca: the project's repos known to Orca with their worktrees shown (the owner's Q4), and one
coordinator tab per project with its Run, from which every worker is started and messaged (`--from`, FN-218 F16)."""

from pathlib import Path

from ... import util
from ...core.project.config import Project
from .cli import OrcaError, orca


def ensure_repos(p: Project) -> None:
    """Adds each repo of `foreman.toml` Orca does not know, and shows its worktrees. The `repo set` runs every time: it
    is also what makes Orca read a worktree made since its last scan (a new one is `selector_not_found` until then)."""
    listed = [util.json_object(r) or {} for r in util.json_list(orca(["repo", "list"]).get("repos"))]
    known = {Path(str(r.get("path"))).resolve() for r in listed}
    for repo in p.repos:
        if repo.resolve() not in known:
            orca(["repo", "add", "--path", str(repo)])
        orca(["repo", "set", "--repo", f"path:{repo}", "--external-worktree-visibility", "show"])


def coordinator(p: Project) -> tuple[str, str]:
    """The project's coordinator tab and Run, kept in `state/orca.json`; made again when Orca no longer has the tab."""
    f = p.state / "orca.json"
    kept = util.json_object(util.read_json(f, {})) or {}
    handle, run = str(kept.get("coordinator") or ""), str(kept.get("run") or "")
    if handle and run:
        try:
            orca(["terminal", "show", "--terminal", handle])
            return handle, run
        except OrcaError:
            pass  # the tab is gone (closed, or Orca's terminals restarted): a new one below
    name = f"foreman {p.name}"
    term = util.json_object(orca(["terminal", "create", "--worktree", f"path:{p.repo}", "--title", name, "--command", "sleep 99999999"]).get("terminal"))
    handle = str((term or {}).get("handle") or "")
    run = str(orca(["orchestration", "run-create", "--objective", name, "--from", handle]).get("runId") or "")
    if not handle or not run:
        raise OrcaError("Orca made no coordinator tab or Run")
    util.write_json(f, {"coordinator": handle, "run": run})
    return handle, run
