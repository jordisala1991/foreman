from pathlib import Path
from typing import Any

from ... import proc
from ...core.project.config import Project


class DeliveryError(Exception):
    pass


class MergeConflict(DeliveryError):
    """The accepted branch conflicts with main: it goes back to its agent."""


def git(repo: Path, *args: str, check: bool = True) -> proc.Result:
    try:
        r = proc.run(["git", "-C", str(repo), *args])
    except proc.ProcError as e:
        raise DeliveryError(str(e)) from e
    if check and r.code != 0:
        raise DeliveryError(f"git {' '.join(args)}: {(r.err or r.out).strip()[:300]}")
    return r


def diff_stat(p: Project, branch: str) -> list[dict[str, Any]]:
    main = p.get("project", "main")
    out = git(p.repo, "diff", "--numstat", "-M", f"{main}...{branch}").out
    rows: list[dict[str, Any]] = []
    for ln in out.splitlines():
        a, d, path = ln.split("\t", 2)
        rows.append({"path": path, "added": int(a) if a.isdigit() else 0, "deleted": int(d) if d.isdigit() else 0})
    return rows


def deleted_files(p: Project, branch: str) -> list[str]:
    main = p.get("project", "main")
    return git(p.repo, "diff", "--name-only", "--diff-filter=D", f"{main}...{branch}").out.split()


def conflicts(p: Project, branch: str) -> bool:
    r = git(p.repo, "merge-tree", "--write-tree", p.get("project", "main"), branch, check=False)
    return r.code != 0


def merge_of(p: Project, tip: str, head: str) -> tuple[str, str]:
    """How `head` follows the reviewed `tip`: ("clean", "") when it is a merge of `tip` and a main commit whose tree is
    git's automatic merge of the two; ("resolved", that automatic tree) when the agent resolved it otherwise; ("", "")
    when it is no such merge (plain commits, a rebase, a merge of another branch)."""
    parents = git(p.repo, "rev-list", "--parents", "-n", "1", head).out.split()[1:]
    if len(parents) != 2 or tip not in parents:
        return "", ""
    m = parents[1 - parents.index(tip)]
    if git(p.repo, "merge-base", "--is-ancestor", m, p.get("project", "main"), check=False).code != 0:
        return "", ""
    auto = git(p.repo, "merge-tree", "--write-tree", "--no-messages", tip, m, check=False).out.split("\n")[0].strip()
    return ("clean", "") if git(p.repo, "rev-parse", f"{head}^{{tree}}").out.strip() == auto else ("resolved", auto)
