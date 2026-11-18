"""The GitHub provider: every task is a pull request, driven with `gh`. Only the loop runs it (the guard stops `gh pr`
for agents); the loop also pushes the task branches. Nothing here publishes: no tag, no release, no push to main. The other
push fm makes is the `foreman-evidence` branch (images of the reports, never forced, built with git plumbing so the checkout
and the task branch are untouched)."""

import contextlib
import json
from pathlib import Path
from typing import Any

from ... import proc, util
from ...core.review import evidence
from ...core.review.inline import Review
from .. import Change, ChangeState, FailedCheck, Provider, ProviderError, Published
from ..repo import RepoError
from . import review as ghreview
from .checks import down, open_prs, pull
from .types import STATES, PermanentError, PullRequest, TransientError, failure

POLL = 20  # seconds a pending (or no-checks) CI answer is reused: one read serves every task of a poll
PUSH_TIMEOUT = 20  # seconds for a push


def _field[T](o: dict[str, Any], key: str, kind: type[T], what: str) -> T:
    v = o.get(key)
    if not isinstance(v, kind) or (kind is int and isinstance(v, bool)):
        raise ProviderError(f"{what}: `{key}` should be {kind.__name__}, gh gave {v!r}")
    return v


def pull_request(raw: object) -> PullRequest:
    """`gh pr view --json number,url,isDraft,state,mergeCommit` as a PullRequest; ProviderError if it does not fit."""
    what = "gh pr view"
    o = util.json_object(raw)
    if o is None:
        raise ProviderError(f"{what}: expected a JSON object, gh gave {raw!r}"[:300])
    state = STATES.get(_field(o, "state", str, what))
    if state is None:
        raise ProviderError(f"{what}: unknown state {o['state']!r}")
    merge = o.get("mergeCommit")
    commit = ""
    if merge is not None:
        mo = util.json_object(merge)
        if mo is None:
            raise ProviderError(f"{what}: `mergeCommit` should be an object or null, gh gave {merge!r}")
        commit = _field(mo, "oid", str, what)
    return PullRequest(_field(o, "number", int, what), _field(o, "url", str, what), _field(o, "isDraft", bool, what), state, commit)


class GitHub(Provider):
    has_ci = True
    name = "github"

    def run(self, prog: str, *args: str, check: bool = True, input: str | None = None) -> proc.Result:
        argv = [prog, *args] if prog == "gh" else [prog, "-C", str(self.p.repo), *args]
        try:
            r = proc.run(argv, cwd=self.p.repo if prog == "gh" else None, input=input)
        except proc.ProcError as e:
            raise (TransientError if e.timed_out else PermanentError)(str(e)) from None
        if check and r.code != 0:
            raise failure(f"{prog} {' '.join(args[:3])}", r.err or r.out)
        return r

    def view(self, change: Change) -> PullRequest:
        r = self.run("gh", "pr", "view", str(change.pr), "--json", "number,url,isDraft,state,mergeCommit")
        try:
            return pull_request(json.loads(r.out))
        except ValueError:
            raise ProviderError(f"gh pr view: not JSON: {r.out.strip()[:200]!r}") from None

    def pushed(self, change: Change) -> bool:
        r = self.run("git", "ls-remote", self.p.remote, f"refs/heads/{change.branch}", check=False)
        return r.code == 0 and r.out.split()[:1] == [change.head]

    def push(self, change: Change) -> None:
        """`git push <remote> refs/heads/<branch>:refs/heads/<branch>` from the main repo (worktree branches live there):
        never forced, never the main branch. Any failure is Transient with git's error; the text is not classified."""
        branch = change.branch
        if branch == self.p.get("project", "main"):
            raise ProviderError(f"refusing to push {branch}: it is the main branch")
        ref = f"refs/heads/{branch}"
        try:
            r = proc.run(["git", "-C", str(self.p.repo), "push", self.p.remote, f"{ref}:{ref}"], timeout=PUSH_TIMEOUT)
        except proc.ProcError as e:
            raise TransientError(f"git push {branch}: {e}") from None
        if r.code != 0:
            raise TransientError(f"git push {branch}: {(r.err or r.out).strip()[:300]}")

    def publish(self, change: Change, draft: bool) -> Published:
        args = ["pr", "create", "--head", change.branch, "--base", self.p.get("project", "main")]
        args += ["--title", self.title(change), "--body", change.body]
        url = self.run("gh", *args, *(["--draft"] if draft else [])).out.strip().splitlines()[-1]
        return Published(int(url.rstrip("/").rsplit("/", 1)[-1]), url, draft)

    @staticmethod
    def title(change: Change) -> str:
        return f"{change.id}: {change.title}"

    def body(self, change: Change) -> str:
        r = self.run("gh", "pr", "view", str(change.pr), "--json", "body")
        try:
            raw = json.loads(r.out)
        except ValueError:
            raise ProviderError(f"gh pr view: not JSON: {r.out.strip()[:200]!r}") from None
        o = util.json_object(raw)
        if o is None:
            raise ProviderError(f"gh pr view: expected a JSON object, gh gave {raw!r}"[:300])
        return _field(o, "body", str, "gh pr view")

    def describe(self, change: Change, body: str) -> None:
        self.run("gh", "pr", "edit", str(change.pr), "--title", self.title(change), "--body", body)

    def evidence(self, change: Change, files: dict[str, Path]) -> dict[str, str]:
        """Puts the images in `ID/` of the orphan branch `foreman-evidence` (added to what is there, one commit, a plain push
        that the remote refuses if it moved: retried next tick); nothing is pushed when they are all there already."""
        files = {n: f for n, f in files.items() if evidence.bare(n) and f.stat().st_size <= evidence.MAX_BYTES}
        if not files:
            return {}
        tid = change.id
        ref = f"refs/heads/{evidence.BRANCH}"
        parent = (self.run("git", "ls-remote", self.p.remote, ref).out.split() or [""])[0]
        if parent:
            self.run("git", "fetch", "-q", self.p.remote, ref)
        try:
            commit = self.repo.commit_files(parent, tid, files, f"evidence {tid}")
        except RepoError as e:
            raise TransientError(str(e)) from None
        if commit:
            self.run("git", "push", "-q", self.p.remote, f"{commit}:{ref}")
        slug = self.run("gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner").out.strip()
        return {n: f"https://github.com/{slug}/blob/{evidence.BRANCH}/{tid}/{n}?raw=true" for n in files}

    def ready(self, change: Change) -> None:
        self.run("gh", "pr", "ready", str(change.pr))

    def diff(self, change: Change) -> str:
        return self.run("gh", "pr", "diff", str(change.pr), "--color=never").out

    def review(self, change: Change, review: Review) -> int:
        return ghreview.post(self.run, change, review)

    def is_open(self, change: Change) -> bool:
        return self.view(change).state == "OPEN"

    def close(self, change: Change, text: str) -> None:
        self.run("gh", "pr", "close", str(change.pr), "--comment", text)

    def ci(self, change: Change) -> str:
        """The PR's CI. A pass or fail is kept for its commit and not read again; pending or no checks is reused for POLL
        seconds. A read asks GitHub once for every open PR, so all tasks of a poll share one call; a PR that is not in that
        answer (past the first 100, or no longer open) is read on its own."""
        pr, head = str(change.pr), change.head
        e = util.read_json(self.p.state / "pr_ci.json", {}).get(pr)
        if not (e and e["head"] == head and (e["ci"] in ("pass", "fail") or (util.age_s(e["at"]) or 1e9) < POLL)):
            self.refresh(pr)
            e = util.read_json(self.p.state / "pr_ci.json", {}).get(pr)
        return e["ci"] if e and e["head"] == head else "pending"  # GitHub has not shown this commit yet

    def failed_checks(self, change: Change) -> list[FailedCheck]:
        """The failing checks the last CI read stored for this tip (no call to GitHub)."""
        e = util.read_json(self.p.state / "pr_ci.json", {}).get(str(change.pr))
        return [FailedCheck(f["name"], f["url"]) for f in e.get("failed", [])] if e and e["head"] == change.head else []

    def graphql(self, query: str, *fields: str) -> object:
        r = self.run("gh", "api", "graphql", "-F", "owner={owner}", "-F", "name={repo}", *fields, "-f", f"query={query}")
        try:
            return json.loads(r.out)
        except ValueError:
            raise ProviderError(f"gh api graphql: not JSON: {r.out.strip()[:200]!r}") from None

    def refresh(self, pr: str) -> None:
        """One GraphQL call: head commit and check state of every open PR into state/pr_ci.json; then, if `pr` is not among
        them, one more for that PR."""
        checks = "... on CheckRun { name conclusion detailsUrl } ... on StatusContext { context state targetUrl }"
        rollup = f"statusCheckRollup {{ state contexts(first: 50) {{ nodes {{ __typename {checks} }} }} }}"
        fields = f"number headRefOid commits(last: 1) {{ nodes {{ commit {{ {rollup} }} }} }}"
        found = open_prs(
            self.graphql(
                "query($owner: String!, $name: String!) { repository(owner: $owner, name: $name) "
                f"{{ pullRequests(states: OPEN, first: 100) {{ nodes {{ {fields} }} }} }} }}"
            )
        )
        if pr not in found:
            one = self.graphql(
                "query($owner: String!, $name: String!, $number: Int!) { repository(owner: $owner, name: $name) "
                f"{{ pullRequest(number: $number) {{ {fields} }} }} }}",
                "-F",
                f"number={pr}",
            )
            found.update([pull(down(util.json_object(one), "data", "repository").get("pullRequest"))])
        at = util.now()
        kept = util.read_json(self.p.state / "pr_ci.json", {})
        util.write_json(self.p.state / "pr_ci.json", {**kept, **{n: {**e, "at": at} for n, e in found.items()}})

    def merge(self, change: Change, strategy: str) -> str:
        """Merges the PR once (a retry after a crash finds it merged). Returns the merge commit; if GitHub has not
        merged it yet, a marker that the merge was requested, so it is not requested twice."""
        v = self.view(change)
        if v.state != "MERGED":
            msg = ["--subject", f"Merge {change.id}: {change.title}"] if strategy != "rebase" else []
            self.run("gh", "pr", "merge", str(change.pr), f"--{strategy}", *msg)
            v = self.view(change)
        return self.merge_commit(change, v) if v.state == "MERGED" else f"pr-{change.pr}-merge-requested"

    def merge_commit(self, change: Change, v: PullRequest) -> str:
        """The merge commit of a merged PR: from `gh pr view`, else the API. Still none: fail (retried) rather than
        invent a reference."""
        sha = v.merge_commit
        if not sha:
            sha = self.run("gh", "api", f"repos/{{owner}}/{{repo}}/pulls/{change.pr}", "--jq", ".merge_commit_sha").out.strip()
        if not sha or sha == "null":
            raise TransientError(f"PR #{change.pr} is merged but GitHub shows no merge commit yet")
        return sha

    def state(self, change: Change) -> ChangeState:
        v = self.view(change)
        if v.state != "MERGED":
            return ChangeState(False, v.state != "OPEN")
        self.catch_up()
        return ChangeState(True, True, self.merge_commit(change, v))

    def catch_up(self) -> None:
        """Best effort: bring the main checkout up to the merged main so the next checks see it (fetch + fast-forward
        only; never a push)."""
        main = self.p.get("project", "main")
        self.run("git", "fetch", "-q", self.p.remote, main, check=False)
        with contextlib.suppress(RepoError):
            if self.repo.current_branch() == main and not self.repo.dirty():
                self.repo.fast_forward(f"{self.p.remote}/{main}")
