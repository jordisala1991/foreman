"""Health checks of the repos of a project with several (DESIGN.md `## Several repos`): a repo that is not a git
checkout, and fm or Claude files inside a repo. A one-repo project has neither check."""

from ...core.project import repos as rules
from ...core.project.config import Project
from ...providers import registry
from .checks import Finding, finding, usable, views


def not_checkouts(p: Project) -> list[Finding]:
    out: list[Finding] = []
    if not p.several:
        return out
    for key, v in views(p):
        if not registry.repo(v).is_checkout():
            why = "not a git checkout" if v.repo.exists() else "does not exist"
            meaning = "A repo of the project cannot be used: its worktrees and the tests of its main are not checked."
            out.append(finding(f"repo-gone:{key}", f"Repo {key} is not a git checkout", meaning, f"Path: {v.repo} ({why})", repo=key))
    return out


def stray_files(p: Project) -> list[Finding]:
    """One finding per repo holding untracked or ignored files that fm and Claude keep out of the project's repos."""
    out: list[Finding] = []
    if not p.several:
        return out
    for key, v in usable(p):
        paths = rules.stray(registry.repo(v).untracked())
        if paths:
            meaning = "Something wrote a file fm and Claude keep out of the project's repos; the repo is no longer clean."
            evidence = f"Repo: {key}\n" + "\n".join(f"Path: {path}" for path in paths)
            title = f"fm or Claude files in repo {key}: " + ", ".join(paths)
            out.append(finding(f"files:{key}:" + " ".join(paths), title, meaning, evidence, repo=key))
    return out
