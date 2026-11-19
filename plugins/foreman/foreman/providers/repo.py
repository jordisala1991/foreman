"""The repo port: every git operation fm runs on a repo. One repo per instance, refs passed in; what crosses it is typed
(heads, diff counts, conflicts, worktrees, the merge outcome), never raw git output. The adapter is `gitrepo.GitRepo`,
handed out by `registry.repo(p)`."""

from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple, Protocol


class RepoError(Exception):
    """A git operation that failed: `reason` is git's own text when git ran and refused, "" when it did not run."""

    def __init__(self, message: str, reason: str = "") -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class FileDiff:
    """One file a branch changes against its base: lines added and deleted (0 for a binary file)."""

    path: str
    added: int
    deleted: int


class Follows(NamedTuple):
    """How a new head follows a reviewed tip: kind "clean" (a merge of main whose tree is git's automatic merge),
    "resolved" (the agent resolved it otherwise; `tree` is the automatic one), or "" (no such merge)."""

    kind: str = ""
    tree: str = ""


@dataclass(frozen=True)
class MergeOutcome:
    """A merge into main: `commit` is the new head ("" = it did not merge); then `conflict` says whether the branch
    conflicts with main, and `why` is git's refusal."""

    commit: str = ""
    conflict: bool = False
    why: str = ""


class Repo(Protocol):
    def head(self, ref: str) -> str:
        """The commit `ref` names; raises RepoError when it names none."""
        ...

    def find(self, ref: str) -> str:
        """The commit `ref` names, "" when it names none."""
        ...

    def commits_ahead(self, base: str, branch: str) -> int:
        """How many commits `branch` has that `base` has not."""
        ...

    def diff_stat(self, base: str, branch: str) -> list[FileDiff]:
        """The files `branch` changes since it left `base`."""
        ...

    def deleted_files(self, base: str, branch: str) -> list[str]:
        """The files `branch` deletes since it left `base`."""
        ...

    def conflicts(self, base: str, branch: str) -> bool:
        """Would merging `branch` into `base` conflict? Raises RepoError when git fails (an unknown ref, unrelated
        histories), never reads that as a conflict."""
        ...

    def follows(self, tip: str, head: str, main: str) -> Follows:
        """How `head` follows the reviewed `tip` (see `Follows`)."""
        ...

    def is_ancestor(self, ref: str, of: str) -> bool:
        """Is `ref` in the history of `of`?"""
        ...

    def worktrees(self) -> list[Path]:
        """The repo's worktrees, as git prints them ([] when git refuses)."""
        ...

    def add_worktree(self, path: Path, branch: str, base: str) -> None:
        """Checks `branch` out at `path`, creating it from `base` when it does not exist."""
        ...

    def add_detached(self, path: Path, commit: str) -> None:
        """Checks `commit` out at `path`, detached."""
        ...

    def merge_in_worktree(self, path: Path, commit: str) -> None:
        """Merges `commit` into the worktree at `path`, without committing."""
        ...

    def remove_worktree(self, path: Path | str) -> None:
        """Removes the worktree at `path`, forced; nothing when there is none."""
        ...

    def delete_branch(self, branch: str) -> None:
        """Deletes `branch`, forced; nothing when there is none."""
        ...

    def merges_of(self, task_id: str, since: str, until: str) -> list[str]:
        """The commits in `since..until` whose message starts `Merge {task_id}: `."""
        ...

    def current_branch(self) -> str:
        """The branch the main checkout is on."""
        ...

    def dirty(self) -> bool:
        """Has the main checkout uncommitted changes to tracked files?"""
        ...

    def commit_files(self, parent: str, folder: str, files: dict[str, Path], message: str) -> str:
        """Adds `files` to `folder/` of commit `parent`'s tree (`""`: an orphan root), as one new commit, no checkout
        touched; returns it, `""` when the tree already holds them. RepoError on failure."""
        ...

    def fast_forward(self, ref: str) -> None:
        """`merge --ff-only ref` in the main checkout; RepoError when it cannot."""
        ...

    def merge(self, main: str, branch: str, message: str, squash: bool) -> MergeOutcome:
        """Merges `branch` into `main` in the main checkout (squashed or as a merge commit); on failure leaves the
        checkout as it was."""
        ...

    def remote_url(self, remote: str) -> str:
        """The URL of that remote, "" when there is none."""
        ...
