"""The local provider: the branch is merged into the main checkout of the repo with git. Nothing is pushed."""

from typing import Any

from .. import proc
from . import Provider, ProviderError


class Local(Provider):
    name = "local"
    merges = ("squash", "merge")
    has_ci = False  # no CI: merged here, in the repo

    def git(self, *args: str, check: bool = True) -> proc.Result:
        try:
            r = proc.run(["git", "-C", str(self.p.repo), *args])
        except proc.ProcError as e:
            raise ProviderError(str(e)) from e
        if check and r.code != 0:
            raise ProviderError(f"git {' '.join(args)}: {(r.err or r.out).strip()[:300]}")
        return r

    def merge(self, change: dict[str, Any], strategy: str) -> str:
        main, branch = self.p.get("project", "main"), change["branch"]
        if self.git("rev-parse", "--abbrev-ref", "HEAD").out.strip() != main:
            raise ProviderError(f"the main checkout {self.p.repo} is not on {main}")
        if self.git("status", "--porcelain", "--untracked-files=no").out.strip():
            raise ProviderError(f"the main checkout {self.p.repo} has uncommitted changes")
        msg = f"Integrate {change['id']}: {change['title']}"
        if strategy == "squash":
            r = self.git("merge", "--squash", branch, check=False)
            if r.code == 0:
                r = self.git("commit", "-q", "--allow-empty", "-m", msg, check=False)
        else:
            r = self.git("merge", "--no-ff", "-m", msg, branch, check=False)
        if r.code != 0:
            self.git("merge", "--abort", check=False)
            self.git("reset", "--hard", "-q", "HEAD", check=False)  # a failed squash leaves no merge to abort
            raise ProviderError(f"merge failed: {(r.err or r.out).strip()[:300]}")
        return self.git("rev-parse", "HEAD").out.strip()

    def state(self, change: dict[str, Any]) -> dict[str, Any]:
        main = self.p.get("project", "main")
        ref = change.get("merge") or ""
        merged = False
        if ref:  # the merge commit we made: is it in main?
            merged = self.git("merge-base", "--is-ancestor", ref, main, check=False).code == 0
        elif change.get("head"):  # merged some other way (by hand): the branch tip is in main
            merged = self.git("merge-base", "--is-ancestor", change["head"], main, check=False).code == 0
        return {"merged": merged, "closed": merged}
