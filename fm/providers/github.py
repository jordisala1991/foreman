"""The GitHub provider: every task is a pull request, driven with `gh`. Only the loop runs it (the guard stops `gh pr`
for agents); the agent only pushes its own branch. Nothing here publishes: no tag, no release, no push."""

import json
import subprocess
from typing import Any

from . import Provider


class ProviderError(Exception):
    pass


class GitHub(Provider):
    name = "github"

    def run(self, prog: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        argv = [prog, *args] if prog == "gh" else [prog, "-C", str(self.p.repo), *args]
        r = subprocess.run(argv, capture_output=True, text=True, check=False, cwd=self.p.repo if prog == "gh" else None)
        if check and r.returncode != 0:
            raise ProviderError(f"{prog} {' '.join(args[:3])}: {(r.stderr or r.stdout).strip()[:300]}")
        return r

    def view(self, change: dict[str, Any], fields: str) -> dict[str, Any]:
        out: dict[str, Any] = json.loads(self.run("gh", "pr", "view", str(change["pr"]), "--json", fields).stdout)
        return out

    def pushed(self, change: dict[str, Any]) -> bool:
        r = self.run("git", "ls-remote", "origin", f"refs/heads/{change['branch']}", check=False)
        return r.returncode == 0 and r.stdout.split()[:1] == [change["head"]]

    def publish(self, change: dict[str, Any], draft: bool) -> dict[str, Any]:
        body = change.get("claim") or change["title"]
        args = ["pr", "create", "--head", change["branch"], "--base", self.p.get("project", "main")]
        args += ["--title", f"{change['id']}: {change['title']}", "--body", body]
        url = self.run("gh", *args, *(["--draft"] if draft else [])).stdout.strip().splitlines()[-1]
        return {"number": int(url.rstrip("/").rsplit("/", 1)[-1]), "url": url, "draft": draft}

    def ready(self, change: dict[str, Any]) -> None:
        self.run("gh", "pr", "ready", str(change["pr"]))

    def comment(self, change: dict[str, Any], text: str) -> None:
        self.run("gh", "pr", "comment", str(change["pr"]), "--body", text)

    def ci(self, change: dict[str, Any]) -> str:
        r = self.run("gh", "pr", "checks", str(change["pr"]), "--json", "bucket", check=False)  # exit 8 = pending
        try:
            buckets = {c["bucket"] for c in json.loads(r.stdout)}
        except (ValueError, KeyError, TypeError):
            if "no checks" in (r.stderr + r.stdout).lower():
                return "none"
            raise ProviderError(f"gh pr checks: {(r.stderr or r.stdout).strip()[:300]}") from None
        if buckets & {"fail", "cancel"}:
            return "fail"
        if "pending" in buckets:
            return "pending"
        return "pass" if "pass" in buckets else "none"

    def merge(self, change: dict[str, Any], strategy: str) -> str:
        v = self.view(change, "state,mergeCommit")
        if v["state"] != "MERGED":  # a retry after a crash finds it merged already
            msg = ["--subject", f"Integrate {change['id']}: {change['title']}"] if strategy != "rebase" else []
            self.run("gh", "pr", "merge", str(change["pr"]), f"--{strategy}", *msg)
            v = self.view(change, "state,mergeCommit")
        commit: dict[str, Any] = v.get("mergeCommit") or {}
        return str(commit.get("oid") or "merged")

    def state(self, change: dict[str, Any]) -> dict[str, Any]:
        s = self.view(change, "state")["state"]
        if s == "MERGED":
            self.catch_up()
        return {"merged": s == "MERGED", "closed": s != "OPEN"}

    def catch_up(self) -> None:
        """Best effort: bring the main checkout up to the merged main so the next checks see it (fetch + fast-forward
        only; never a push)."""
        main = self.p.get("project", "main")
        self.run("git", "fetch", "-q", "origin", main, check=False)
        on_main = self.run("git", "rev-parse", "--abbrev-ref", "HEAD", check=False).stdout.strip() == main
        clean = not self.run("git", "status", "--porcelain", "--untracked-files=no", check=False).stdout.strip()
        if on_main and clean:
            self.run("git", "merge", "-q", "--ff-only", f"origin/{main}", check=False)
