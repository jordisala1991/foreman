"""An in-memory Repo port (foreman/providers/repo.py) for tests that do not need git itself: a commit graph with refs,
worktree folders made and removed on disk, and git's meaning of each operation (tests/test_fake_repo.py holds it to
test_gitrepo.py's expected values). Where it is coarser than git: a conflict is a path both sides change (git looks at
the hunks); `diff_stat` adds up each commit's lines (git gives the net diff, so lines added then removed count on the
fake); `worktrees()` lists only the worktrees added (git also lists the main checkout, which the fake has no path for).
A test hands it to the code with the `fake_git` fixture (conftest.py)."""

import hashlib
import shutil
from pathlib import Path

from foreman.providers.repo import FileDiff, Follows, MergeOutcome, RepoError


class FakeRepo:
    def __init__(self) -> None:
        self.parents: dict[str, tuple[str, ...]] = {}  # commit -> its parents
        self.diffs: dict[str, tuple[list[FileDiff], list[str]]] = {}  # commit -> (files it changes, files it deletes)
        self.msgs: dict[str, str] = {}
        self.order: list[str] = []  # commits, oldest first
        self.heads: dict[str, str] = {}  # ref -> commit
        self.trees: dict[Path, str] = {}  # worktree -> the commit checked out there
        self.files: dict[str, dict[str, bytes]] = {}  # commit made by commit_files -> its tree (path -> content)
        self.heads["main"] = self._new((), [], [], "init")

    def _new(self, parents: tuple[str, ...], files: list[FileDiff], deleted: list[str], message: str) -> str:
        c = hashlib.sha1(str(len(self.order)).encode()).hexdigest()
        self.parents[c], self.diffs[c], self.msgs[c] = parents, (files, deleted), message
        self.order.append(c)
        return c

    # Test-facing

    def commit(self, ref: str, *files: FileDiff, deleted: tuple[str, ...] = (), message: str = "change") -> str:
        """A new commit on `ref` (made from main when it does not exist) changing `files`; returns it."""
        self.heads[ref] = self._new((self.heads.get(ref) or self.heads["main"],), list(files), list(deleted), message)
        return self.heads[ref]

    def messages(self, ref: str = "main") -> list[str]:
        """The messages of `ref`'s history, newest last."""
        h = self._history(self.head(ref))
        return [self.msgs[c] for c in self.order if c in h]

    # Helpers

    def _history(self, commit: str) -> set[str]:
        seen, todo = set(), [commit]
        while todo:
            c = todo.pop()
            if c not in seen:
                seen.add(c)
                todo.extend(self.parents[c])
        return seen

    def _ahead(self, base: str, branch: str) -> list[str]:
        """The commits in `branch`'s history and not in `base`'s, oldest first."""
        h = self._history(self.head(branch)) - self._history(self.head(base))
        return [c for c in self.order if c in h]

    def _paths(self, commits: list[str]) -> set[str]:
        return {f.path for c in commits for f in self.diffs[c][0]}

    # The port

    def head(self, ref: str) -> str:
        if c := self.find(ref):
            return c
        raise RepoError(f"git rev-parse --verify {ref}: unknown revision", reason="unknown revision")

    def find(self, ref: str) -> str:
        return self.heads.get(ref) or (ref if ref in self.parents else "")

    def commits_ahead(self, base: str, branch: str) -> int:
        return len(self._ahead(base, branch))

    def diff_stat(self, base: str, branch: str) -> list[FileDiff]:
        out: dict[str, FileDiff] = {}
        for c in self._ahead(base, branch):
            for f in self.diffs[c][0]:
                was = out.get(f.path, FileDiff(f.path, 0, 0))
                out[f.path] = FileDiff(f.path, was.added + f.added, was.deleted + f.deleted)
        return list(out.values())

    def deleted_files(self, base: str, branch: str) -> list[str]:
        return [d for c in self._ahead(base, branch) for d in self.diffs[c][1]]

    def conflicts(self, base: str, branch: str) -> bool:
        return bool(self._paths(self._ahead(base, branch)) & self._paths(self._ahead(branch, base)))

    def follows(self, tip: str, head: str, main: str) -> Follows:
        return Follows()

    def is_ancestor(self, ref: str, of: str) -> bool:
        return bool(self.find(ref)) and bool(self.find(of)) and self.find(ref) in self._history(self.head(of))

    def worktrees(self) -> list[Path]:
        return list(self.trees)

    def _checkout(self, path: Path, commit: str) -> None:
        if path in self.trees or (path.exists() and any(path.iterdir())):
            raise RepoError(f"git worktree add {path}: already exists", reason="already exists")
        path.mkdir(parents=True, exist_ok=True)
        self.trees[path] = commit

    def add_worktree(self, path: Path, branch: str, base: str) -> None:
        self._checkout(path, self.heads.get(branch) or self.head(base))
        self.heads.setdefault(branch, self.trees[path])

    def add_detached(self, path: Path, commit: str) -> None:
        self._checkout(path, self.head(commit))

    def merge_in_worktree(self, path: Path, commit: str) -> None:
        if self.conflicts(self.trees[path], commit):
            raise RepoError(f"git merge {commit}: conflict", reason="conflict")

    def remove_worktree(self, path: Path | str) -> None:
        if self.trees.pop(Path(path), None) is not None:  # git removes only a worktree it knows
            shutil.rmtree(path, ignore_errors=True)

    def delete_branch(self, branch: str) -> None:
        self.heads.pop(branch, None)

    def merges_of(self, task_id: str, since: str, until: str) -> list[str]:
        return [c for c in reversed(self._ahead(since, until)) if self.msgs[c].startswith(f"Merge {task_id}: ")]

    def current_branch(self) -> str:
        return "main"

    def dirty(self) -> bool:
        return False

    def commit_files(self, parent: str, folder: str, files: dict[str, Path], message: str) -> str:
        tree = self.files.get(self.head(parent), {}) if parent else {}
        new = {**tree, **{f"{folder}/{n}": f.read_bytes() for n, f in files.items()}}
        if new == tree:
            return ""
        c = self._new((parent,) if parent else (), [], [], message)
        self.files[c] = new
        return c

    def fast_forward(self, ref: str) -> None:
        if not self.is_ancestor("main", ref):
            raise RepoError(f"git merge --ff-only {ref}: not possible to fast-forward", reason="not possible to fast-forward")
        self.heads["main"] = self.head(ref)

    def merge(self, main: str, branch: str, message: str, squash: bool) -> MergeOutcome:
        if self.conflicts(main, branch):
            return MergeOutcome("", True, "conflict")
        ahead = self._ahead(main, branch)
        if not ahead and not squash:  # already up to date: git makes no commit
            return MergeOutcome(self.head(main))
        files = [f for c in ahead for f in self.diffs[c][0]] if squash else []  # a merge commit reaches them through its parent
        deleted = [d for c in ahead for d in self.diffs[c][1]] if squash else []
        parents = (self.head(main),) if squash else (self.head(main), self.head(branch))
        self.heads[main] = self._new(parents, files, deleted, message)
        return MergeOutcome(self.heads[main])

    def origin_url(self) -> str:
        return ""
