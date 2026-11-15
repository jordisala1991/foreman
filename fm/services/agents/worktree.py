from pathlib import Path
from typing import Any

from ...core.project.config import Project
from ..delivery import git


def worktree(p: Project, i: dict[str, Any]) -> tuple[Path, str]:
    branch = f"{p.get('project', 'branch_prefix')}{i['id'].lower()}"
    wt = p.path("worktrees") / i["id"].lower()
    if not wt.exists():
        wt.parent.mkdir(parents=True, exist_ok=True)
        exists = git.git(p.repo, "rev-parse", "--verify", "--quiet", branch, check=False).code == 0
        args = ["worktree", "add", str(wt), branch] if exists else ["worktree", "add", "-b", branch, str(wt), p.get("project", "main")]
        git.git(p.repo, *args)
    return wt, branch
