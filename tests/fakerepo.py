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
        self.staged: dict[Path, list[str]] = {}  # worktree -> the commits merged in it, not committed (git's --no-commit)
        self.branch_at: dict[Path, str] = {}  # worktree -> the branch checked out there ("" when detached)
        self.files: dict[str, dict[str, bytes]] = {}  # commit made by commit_files -> its tree (path -> content)
        self.remotes: dict[str, str] = {}  # remote name -> its URL
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
        seen: set[str] = set()
        todo = [commit]
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

    def _checkout(self, path: Path, commit: str, branch: str = "") -> None:
        if path in self.trees or path.is_file() or (path.is_dir() and any(path.iterdir())):
            raise RepoError(f"git worktree add {path}: already exists", reason="already exists")
        if branch and branch in (self.current_branch(), *self.branch_at.values()):
            raise RepoError(f"git worktree add {path} {branch}: {branch} is already checked out", reason="already checked out")
        path.mkdir(parents=True, exist_ok=True)
        self.trees[path], self.branch_at[path] = commit, branch

    def is_checkout(self) -> bool:
        return True

    def untracked(self) -> list[str]:
        return []

    def add_worktree(self, path: Path, branch: str, base: str) -> None:
        self._checkout(path, self.heads.get(branch) or self.head(base), branch)
        self.heads.setdefault(branch, self.trees[path])

    def add_detached(self, path: Path, commit: str) -> None:
        self._checkout(path, self.head(commit))

    def merge_in_worktree(self, path: Path, commit: str) -> None:
        if path not in self.trees:
            raise RepoError(f"git -C {path} merge {commit}: not a git repository", reason="not a git repository")
        if any(self.conflicts(side, commit) for side in (self.trees[path], *self.staged.get(path, []))):
            raise RepoError(f"git merge {commit}: conflict", reason="conflict")
        self.staged.setdefault(path, []).append(self.head(commit))  # HEAD stays: the merge is only staged

    def remove_worktree(self, path: Path | str) -> None:
        if self.trees.pop(Path(path), None) is not None:  # git removes only a worktree it knows
            del self.branch_at[Path(path)]
            self.staged.pop(Path(path), None)
            shutil.rmtree(path, ignore_errors=True)

    def delete_branch(self, branch: str) -> None:
        if branch not in (self.current_branch(), *self.branch_at.values()):  # git refuses a checked-out branch
            self.heads.pop(branch, None)

    def merges_of(self, task_id: str, since: str, until: str) -> list[str]:
        return [c for c in reversed(self._ahead(since, until)) if self.msgs[c].startswith(f"Merge {task_id}: ")]

    def current_branch(self) -> str:
        return "main"

    def dirty(self) -> bool:
        return False

    def _tree(self, commit: str) -> dict[str, bytes]:
        """The files of `commit`: a commit_files commit's own tree; any other, its first parent's files, with what each
        merged-in parent's side changed (added, changed or deleted) and the paths it changed itself (content b"").
        Creation order puts every parent before its child."""
        h, trees = self._history(commit), dict[str, dict[str, bytes]]()
        for c in (c for c in self.order if c in h):
            first, *merged = self.parents[c] or ("",)
            tree = dict(self.files.get(c) or trees.get(first, {}))
            for p in merged if c not in self.files else []:
                for path in self._side_paths(first, p, trees):
                    if path in trees[p]:
                        tree[path] = trees[p][path]
                    else:
                        tree.pop(path, None)
            tree.update({f.path: b"" for f in self.diffs[c][0] if f.path not in self.diffs[c][1]})
            for d in self.diffs[c][1]:
                tree.pop(d, None)
            trees[c] = tree
        return trees[commit]

    def _side_paths(self, base: str, branch: str, trees: dict[str, dict[str, bytes]]) -> set[str]:
        """The paths the commits of `branch` not in `base` changed, commit_files commits included."""
        side = self._ahead(base, branch)
        out = {f.path for c in side for f in self.diffs[c][0]} | {d for c in side for d in self.diffs[c][1]}
        for c in (c for c in side if c in self.files):
            before = trees[self.parents[c][0]] if self.parents[c] else {}
            out |= {k for k in self.files[c].keys() | before.keys() if self.files[c].get(k) != before.get(k)}
        return out

    def commit_files(self, parent: str, folder: str, files: dict[str, Path], message: str) -> str:
        tree = self._tree(self.head(parent)) if parent else {}
        new = {**tree, **{f"{folder}/{n}": f.read_bytes() for n, f in files.items()}}
        if new == tree:
            return ""
        c = self._new((self.head(parent),) if parent else (), [], [], message)
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

    def remote_url(self, remote: str) -> str:
        return self.remotes.get(remote, "")
