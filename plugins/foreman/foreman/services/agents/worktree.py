from pathlib import Path
from typing import Any

from ...core.project.config import Project
from ...providers import registry


def worktree(p: Project, i: dict[str, Any]) -> tuple[Path, str]:
    branch = f"{p.get('project', 'branch_prefix')}{i['id'].lower()}"
    wt = p.path("worktrees") / i["id"].lower()
    if not wt.exists():
        wt.parent.mkdir(parents=True, exist_ok=True)
        registry.repo(p).add_worktree(wt, branch, p.get("project", "main"))
    return wt, branch
