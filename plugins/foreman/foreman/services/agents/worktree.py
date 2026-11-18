"""A responsible agent's worktree and branch, made from the task's repo, and that repo's `post_create` run once in it."""

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
