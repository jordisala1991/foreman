"""A responsible agent's worktree and branch, made from the task's repo, and that repo's `post_create` run once in it; under Orca, the
other agents' folders."""

import shlex
from pathlib import Path
from typing import Any

from ... import proc
from ...core.project.config import Project
from ...providers import registry

POST_CREATE_TIMEOUT = 300  # seconds, each step


class PostCreateError(Exception):
    """A post_create step failed or timed out; the worktree (and the branch, if this launch made it) is gone."""


def worktree(p: Project, i: dict[str, Any]) -> tuple[Path, str]:
    branch = f"{p.get('project', 'branch_prefix')}{i['id'].lower()}"
    wt = p.path("worktrees") / i["id"].lower()
    if not wt.exists():
        wt.parent.mkdir(parents=True, exist_ok=True)
        repo = registry.repo(p)
        made = not repo.find(branch)
        repo.add_worktree(wt, branch, p.get("project", "main"))
        for cmd in p.post_create:
            if (failed := _run(cmd, p.repo, wt)) is not None:
                repo.remove_worktree(wt)
                if made:
                    repo.delete_branch(branch)
                left = f"; its worktree {wt} could not be removed: remove it before unblocking" if wt.exists() else ""
                raise PostCreateError(f"post_create step failed ({cmd}): {failed}{left}")
    return wt, branch


def _run(cmd: str, main: Path, wt: Path) -> str | None:
    """Runs one step in `wt`; why it failed (the last 300 chars of its output), or None when it did not."""
    try:
        r = proc.run(["sh", "-c", cmd.format(main=shlex.quote(str(main)), wt=shlex.quote(str(wt)))], cwd=wt, timeout=POST_CREATE_TIMEOUT)
    except proc.ProcError as e:
        return str(e)
    return (r.out + r.err).strip()[-300:] if r.code else None


def agent_folder(p: Project, name: str, default: Path, ref: str) -> Path:
    """Where an agent runs: `default` for the claude runner; under Orca, a folder of its own, a detached worktree of `ref`
    in `state/agent-folders` (one per agent: two agents sharing one carried settings file swap tasks, FN-218 F3; and
    never the worktrees folder, where health would take it for an orphan)."""
    if p.get("agents", "runner") != "orca":
        return default
    repo = registry.repo(p)
    commit = repo.head(ref)
    folder = p.state / "agent-folders" / f"{name}-{commit[:9]}"
    if not folder.exists():
        folder.parent.mkdir(parents=True, exist_ok=True)
        repo.add_detached(folder, commit)
    return folder


def remove_agent_folders(p: Project, tid: str) -> None:
    """Removes the task's agent folders once its sessions are stopped."""
    for folder in sorted((p.state / "agent-folders").glob(f"{tid.lower()}-*")):
        registry.repo(p).remove_worktree(folder)
