"""The local provider: the branch is merged into the main checkout of the repo with git. Nothing is pushed."""

from __future__ import annotations

import subprocess
from typing import Any

from . import Provider, ConfigError  # noqa: F401


class ProviderError(Exception):
    pass


class Local(Provider):
    name = "local"

    def git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        r = subprocess.run(["git", "-C", str(self.p.repo), *args], capture_output=True, text=True, check=False)
        if check and r.returncode != 0:
            raise ProviderError(f"git {' '.join(args)}: {(r.stderr or r.stdout).strip()[:300]}")
        return r

    def merge(self, change: dict[str, Any], strategy: str) -> str:
        main, branch = self.p.get("project", "main"), change["branch"]
        if self.git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip() != main:
            raise ProviderError(f"the main checkout {self.p.repo} is not on {main}")
        if self.git("status", "--porcelain", "--untracked-files=no").stdout.strip():
            raise ProviderError(f"the main checkout {self.p.repo} has uncommitted changes")
        msg = f"Integrate {change['id']}: {change['title']}"
        if strategy == "squash":
            r = self.git("merge", "--squash", branch, check=False)
            if r.returncode == 0:
                r = self.git("commit", "-q", "--allow-empty", "-m", msg, check=False)
        else:
            r = self.git("merge", "--no-ff", "-m", msg, branch, check=False)
        if r.returncode != 0:
            self.git("merge", "--abort", check=False)
            self.git("reset", "--hard", "-q", "HEAD", check=False)  # a failed squash leaves no merge to abort
            raise ProviderError(f"merge failed: {(r.stderr or r.stdout).strip()[:300]}")
        return self.git("rev-parse", "HEAD").stdout.strip()

    def state(self, change: dict[str, Any]) -> dict[str, Any]:
        main = self.p.get("project", "main")
        ref = change.get("merge") or ""
        merged = False
        if ref:  # the merge commit we made: is it in main?
            merged = self.git("merge-base", "--is-ancestor", ref, main, check=False).returncode == 0
        elif change.get("head"):  # merged some other way (by hand): the branch tip is in main
            merged = self.git("merge-base", "--is-ancestor", change["head"], main, check=False).returncode == 0
        return {"merged": merged, "closed": merged}
