"""`foreman worktree REPO NAME BRANCH [BASE]`: a git worktree for an agent, on a new or an existing branch.

Forked from an earlier in-house version (fixed repo and ref/out links): the repo comes from [repos.REPO], the
destination from its `worktree` pattern (default "{worktrees}/{repo}-{name}") and whatever the repo needs after
creating it from `post_create`, shell commands run in the new worktree with {main} (the repo's main checkout), {wt},
{repo}, {name} and {branch} replaced (quoted; also as FOREMAN_* variables, commands.py) (e.g. "cp -c -R
{main}/node_modules {wt}/" clones node_modules with APFS copy-on-write, almost free until it changes). A failed
post_create step removes the worktree and the branch (only
the worktree when the branch already existed locally).

The branch may already exist (e.g. the branch of a PR opened by another session):
- a local branch: the worktree is made on it, without moving it; BASE, if given, is ignored (with a warning);
- only on the remote (after fetching that branch, if the repo has the remote): a local branch tracking it;
- checked out in another worktree: an error that names its path, and nothing is touched; except, for `foreman
  launch` (reuse=True), a worktree foreman made (under the worktrees folder) that no live agent has: the new agent
  takes it as it is ("reusing …"; post_create does not run again), as when a branch goes back to work after a review
  and its first agent cannot be resumed.
"""

from __future__ import annotations

import functools
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from . import commands, util

if TYPE_CHECKING:
    from .config import Project, Table
    from .util import PathLike


class WorktreeError(Exception):
    pass


git = functools.partial(util.git, error=WorktreeError)


def destination(project: Project, repo: Table, name: str, branch: str) -> Path:
    pattern = repo.get("worktree") or "{worktrees}/{repo}-{name}"
    return project.path(pattern.format(worktrees=project.worktrees_dir, repo=repo["key"], name=name, branch=branch))


def exists(repo: PathLike, ref: str) -> bool:
    return (
        subprocess.run(
            ["git", *util.GIT_QUIET, "-C", str(repo), "show-ref", "--verify", "--quiet", ref], check=False
        ).returncode
        == 0
    )


def remote_branch(main: PathLike, remote: str | None, branch: str) -> str | None:
    """remote/branch after fetching it, or None (no such remote, or the branch is not there)."""
    if not remote or remote == "none" or remote not in git(main, "remote", check=False).split():
        return None
    refspec = f"+refs/heads/{branch}:refs/remotes/{remote}/{branch}"
    subprocess.run(
        ["git", *util.GIT_QUIET, "-C", str(main), "fetch", "-q", remote, refspec],
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        check=False,
    )
    return f"{remote}/{branch}" if exists(main, f"refs/remotes/{remote}/{branch}") else None


def reuse_blocker(project: Project, branch: str, path: PathLike) -> str:
    """Why `launch` may not reuse the worktree PATH that holds BRANCH ('' when it may): it is not one foreman made
    (under the worktrees folder), or a live agent has the branch or that worktree."""
    from . import loop

    real = Path(os.path.realpath(path))
    root = Path(os.path.realpath(project.worktrees_dir))
    if real == root or root not in real.parents:
        return f"{path} is not under foreman's worktrees folder {project.worktrees_dir}"
    for h in loop.live_agents(project):
        wt = h.get("worktree")
        if h.get("branch") == branch or (wt and Path(os.path.realpath(wt)) == real):
            return f"the live agent {h.get('name') or h['id'][:8]} has it"
    return ""


def create(
    project: Project, repo_key: str, name: str, branch: str, base: str | None = None, reuse: bool = False
) -> tuple[Path, str, list[str]]:
    """(path, how, warnings): how says where the branch comes from ("from BASE abc1234", "existing branch at …",
    "tracking origin/BRANCH at …", or with REUSE "reusing its worktree at …": a worktree foreman made for BRANCH
    that no live agent has, taken as it is)."""
    repo = project.repo(repo_key)
    main = repo["path"]
    if not main or not (main / ".git").exists():
        raise WorktreeError(f"repo {repo_key}: {main} is not a git checkout")
    wt = destination(project, repo, name, branch)
    for step in repo.get("post_create", []):  # before anything is made: a bad step could not run afterwards
        try:
            commands.expand("repo.post_create", step)
        except commands.PlaceholderError as e:
            raise WorktreeError(f"[repos.{repo_key}] post_create {step!r}: {e}; nothing done") from e
    warnings: list[str] = []
    open_in = worktrees(main)
    if reuse and branch in open_in:
        held = Path(open_in[branch])
        why = reuse_blocker(project, branch, held)
        if why:
            raise WorktreeError(f"branch {branch} is already checked out in {held} and {why}; nothing done")
        if base:
            warnings.append(f"branch {branch} already exists: BASE {base} ignored, the branch is not moved")
        return held, f"reusing its worktree at {git(held, 'rev-parse', '--short', 'HEAD')}", warnings
    if wt.exists():
        raise WorktreeError(f"{wt} already exists")
    if branch in open_in:
        raise WorktreeError(f"branch {branch} is already checked out in {open_in[branch]}; nothing done")
    made_branch = True
    if exists(main, f"refs/heads/{branch}"):
        if base:
            warnings.append(f"branch {branch} already exists: BASE {base} ignored, the branch is not moved")
        opts, start, made_branch = [], branch, False
        how = f"existing branch at {git(main, 'rev-parse', '--short', branch)}"
    elif upstream := remote_branch(main, repo.get("remote", "origin"), branch):
        if base:
            warnings.append(f"branch {branch} exists on {upstream}: BASE {base} ignored")
        opts, start = ["--track", "-b", branch], upstream
        how = f"tracking {upstream} at {git(main, 'rev-parse', '--short', upstream)}"
    else:
        base_ref: str = base or repo.get("main", "main")
        opts, start = ["-b", branch], base_ref
        how = f"from {base_ref} {git(main, 'rev-parse', '--short', base_ref)}"
    wt.parent.mkdir(parents=True, exist_ok=True)
    git(main, "worktree", "add", "-q", *opts, str(wt), start)
    values = {"main": main, "wt": wt, "repo": repo_key, "name": name, "branch": branch}
    for step in repo.get("post_create", []):
        cmd = commands.expand("repo.post_create", step, values)
        r = commands.run("repo.post_create", cmd, project=project, values=values, cwd=wt)
        if r.rc != 0:
            git(main, "worktree", "remove", "--force", str(wt), check=False)
            if made_branch:
                git(main, "branch", "-D", branch, check=False)
            why = (
                f"cut after {commands.POINTS['repo.post_create'].timeout:g} s"
                if r.timed_out
                else r.stderr.strip()[:300]
            )
            raise WorktreeError(f"post_create step failed ({cmd}): {why}; worktree removed")
    return wt, how, warnings


def worktrees(repo_path: PathLike) -> dict[str, str]:
    """{branch: path} of a repo's worktrees (main checkout included)."""
    out: dict[str, str] = {}
    for block in git(repo_path, "worktree", "list", "--porcelain", check=False).split("\n\n"):
        lines = block.splitlines()
        br = [ln.removeprefix("branch refs/heads/") for ln in lines if ln.startswith("branch ")]
        if lines and br:
            out[br[0]] = lines[0].removeprefix("worktree ")
    return out


def all_worktrees(project: Project) -> list[tuple[str, str, str]]:
    """[(repo key, branch, path)] of every configured repo, main checkouts left out."""
    out: list[tuple[str, str, str]] = []
    for key in project.repos:
        repo = project.repo(key)
        if not repo["path"] or not Path(repo["path"]).exists():
            continue
        for br, path in worktrees(repo["path"]).items():
            if Path(path).resolve() != Path(repo["path"]).resolve():
                out.append((key, br, path))
    return out
