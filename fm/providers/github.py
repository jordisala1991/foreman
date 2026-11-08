"""The GitHub provider: every task is a pull request, driven with `gh`. Only the loop runs it (the guard stops `gh pr`
for agents); the agent only pushes its own branch. Nothing here publishes: no tag, no release, no push."""

import json
import re
import subprocess
from typing import Any

from . import Permanent, Provider, Transient

TIMEOUT = 30  # seconds for each gh / git call: a hung network must not freeze the loop


TRANSIENT = re.compile(r"timed? ?out|connection|could not resolve|network|HTTP 5\d\d|\b50[0-4]\b|rate limit|temporar|unavailable|\bEOF\b|TLS", re.I)


class TransientError(Transient):
    pass


class PermanentError(Permanent):
    pass


def failure(what: str, text: str) -> Exception:
    """What went wrong with a gh/git call: a network or server hiccup is retried; anything else (gh rejecting the PR,
    a missing branch or PR) is permanent."""
    text = text.strip()[:300]
    return (TransientError if TRANSIENT.search(text) else PermanentError)(f"{what}: {text}")


class GitHub(Provider):
    name = "github"

    def run(self, prog: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        argv = [prog, *args] if prog == "gh" else [prog, "-C", str(self.p.repo), *args]
        try:
            r = subprocess.run(argv, capture_output=True, text=True, check=False, cwd=self.p.repo if prog == "gh" else None, timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            raise TransientError(f"{prog} {' '.join(args[:3])}: timed out after {TIMEOUT}s") from None
        if check and r.returncode != 0:
            raise failure(f"{prog} {' '.join(args[:3])}", r.stderr or r.stdout)
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
            raise failure("gh pr checks", r.stderr or r.stdout) from None
        if buckets & {"fail", "cancel"}:
            return "fail"
        if "pending" in buckets:
            return "pending"
        return "pass" if "pass" in buckets else "none"

    def merge(self, change: dict[str, Any], strategy: str) -> str:
        """Merges the PR once (a retry after a crash finds it merged). Returns the merge commit; if GitHub has not
        merged it yet, a marker that the merge was requested, so it is not requested twice."""
        v = self.view(change, "state,mergeCommit")
        if v["state"] != "MERGED":
            msg = ["--subject", f"Integrate {change['id']}: {change['title']}"] if strategy != "rebase" else []
            self.run("gh", "pr", "merge", str(change["pr"]), f"--{strategy}", *msg)
            v = self.view(change, "state,mergeCommit")
        return self.merge_commit(change, v) if v["state"] == "MERGED" else f"pr-{change['pr']}-merge-requested"

    def merge_commit(self, change: dict[str, Any], v: dict[str, Any]) -> str:
        """The merge commit of a merged PR: from `gh pr view`, else the API. Still none: fail (retried) rather than
        invent a reference."""
        commit: dict[str, Any] = v.get("mergeCommit") or {}
        sha = str(commit.get("oid") or "")
        if not sha:
            sha = self.run("gh", "api", f"repos/{{owner}}/{{repo}}/pulls/{change['pr']}", "--jq", ".merge_commit_sha").stdout.strip()
        if not sha or sha == "null":
            raise TransientError(f"PR #{change['pr']} is merged but GitHub shows no merge commit yet")
        return sha

    def state(self, change: dict[str, Any]) -> dict[str, Any]:
        v = self.view(change, "state,mergeCommit")
        if v["state"] != "MERGED":
            return {"merged": False, "closed": v["state"] != "OPEN"}
        self.catch_up()
        return {"merged": True, "closed": True, "commit": self.merge_commit(change, v)}

    def catch_up(self) -> None:
        """Best effort: bring the main checkout up to the merged main so the next checks see it (fetch + fast-forward
        only; never a push)."""
        main = self.p.get("project", "main")
        self.run("git", "fetch", "-q", "origin", main, check=False)
        on_main = self.run("git", "rev-parse", "--abbrev-ref", "HEAD", check=False).stdout.strip() == main
        clean = not self.run("git", "status", "--porcelain", "--untracked-files=no", check=False).stdout.strip()
        if on_main and clean:
            self.run("git", "merge", "-q", "--ff-only", f"origin/{main}", check=False)
