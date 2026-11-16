"""The repo adapter: the one module that runs git on a repo (platform calls stay in `providers/github`)."""

from pathlib import Path

from .. import proc
from .repo import FileDiff, Follows, MergeOutcome, RepoError


class GitRepo:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _run(self, *args: str, cwd: Path | None = None, check: bool = True) -> proc.Result:
        try:
            r = proc.run(["git", "-C", str(cwd or self.path), *args])
        except proc.ProcError as e:
            raise RepoError(str(e)) from e
        if check and r.code != 0:
            reason = (r.err or r.out).strip()
            raise RepoError(f"git {' '.join(args)}: {reason[:300]}", reason=reason)
        return r

    def head(self, ref: str) -> str:
        return self._run("rev-parse", "--verify", ref).out.strip()

    def find(self, ref: str) -> str:
        r = self._run("rev-parse", "--verify", "--quiet", ref, check=False)
        return r.out.strip() if r.code == 0 else ""

    def commits_ahead(self, base: str, branch: str) -> int:
        return int(self._run("rev-list", "--count", f"{base}..{branch}").out.strip())

    def diff_stat(self, base: str, branch: str) -> list[FileDiff]:
        rows: list[FileDiff] = []
        for ln in self._run("diff", "--numstat", "-M", f"{base}...{branch}").out.splitlines():
            a, d, path = ln.split("\t", 2)
            rows.append(FileDiff(path, int(a) if a.isdigit() else 0, int(d) if d.isdigit() else 0))
        return rows

    def deleted_files(self, base: str, branch: str) -> list[str]:
        return self._run("diff", "--name-only", "--diff-filter=D", f"{base}...{branch}").out.split()

    def conflicts(self, base: str, branch: str) -> bool:
        r = self._run("merge-tree", "--write-tree", self.head(base), self.head(branch), check=False)
        if r.code not in (0, 1):
            reason = (r.err or r.out).strip()
            raise RepoError(f"git merge-tree: {reason[:300]}", reason=reason)
        return r.code == 1

    def follows(self, tip: str, head: str, main: str) -> Follows:
        parents = self._run("rev-list", "--parents", "-n", "1", head).out.split()[1:]
        if len(parents) != 2 or tip not in parents:
            return Follows()
        m = parents[1 - parents.index(tip)]
        if not self.is_ancestor(m, main):
            return Follows()
        auto = self._run("merge-tree", "--write-tree", "--no-messages", tip, m, check=False).out.split("\n")[0].strip()
        tree = self._run("rev-parse", f"{head}^{{tree}}").out.strip()
        return Follows("clean") if tree == auto else Follows("resolved", auto)

    def is_ancestor(self, ref: str, of: str) -> bool:
        return self._run("merge-base", "--is-ancestor", ref, of, check=False).code == 0

    def worktrees(self) -> list[Path]:
        listing = self._run("worktree", "list", "--porcelain", check=False).out
        return [Path(ln[len("worktree ") :]) for ln in listing.splitlines() if ln.startswith("worktree ")]

    def add_worktree(self, path: Path, branch: str, base: str) -> None:
        if self.find(branch):
            self._run("worktree", "add", str(path), branch)
        else:
            self._run("worktree", "add", "-b", branch, str(path), base)

    def add_detached(self, path: Path, commit: str) -> None:
        self._run("worktree", "add", "--detach", str(path), commit)

    def merge_in_worktree(self, path: Path, commit: str) -> None:
        self._run("merge", "--no-commit", "--no-ff", commit, cwd=path)

    def remove_worktree(self, path: Path | str) -> None:
        self._run("worktree", "remove", "--force", str(path), check=False)

    def delete_branch(self, branch: str) -> None:
        self._run("branch", "-D", branch, check=False)

    def merges_of(self, task_id: str, since: str, until: str) -> list[str]:
        return self._run("log", "--format=%H", f"--grep=^Merge {task_id}: ", f"{since}..{until}", check=False).out.split()

    def current_branch(self) -> str:
        return self._run("rev-parse", "--abbrev-ref", "HEAD").out.strip()

    def dirty(self) -> bool:
        return bool(self._run("status", "--porcelain", "--untracked-files=no").out.strip())

    def merge(self, main: str, branch: str, message: str, squash: bool) -> MergeOutcome:
        if squash:
            r = self._run("merge", "--squash", branch, check=False)
            if r.code == 0:
                r = self._run("commit", "-q", "--allow-empty", "-m", message, check=False)
        else:
            r = self._run("merge", "--no-ff", "-m", message, branch, check=False)
        if r.code != 0:
            self._run("merge", "--abort", check=False)
            self._run("reset", "--hard", "-q", "HEAD", check=False)  # a failed squash leaves no merge to abort
            return MergeOutcome(conflict=self.conflicts(main, branch), why=(r.err or r.out).strip()[:300])
        return MergeOutcome(commit=self.head("HEAD"))

    def origin_url(self) -> str:
        r = self._run("remote", "get-url", "origin", check=False)
        return r.out.strip() if r.code == 0 else ""
