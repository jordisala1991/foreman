"""The GitHub provider: every task is a pull request, driven with `gh`. Only the loop runs it (the guard stops `gh pr`
for agents); the loop also pushes the task branches. Nothing here publishes: no tag, no release, no push to main."""

import json
import re
from dataclasses import dataclass
from typing import Any, Literal

from .. import proc, util
from ..inline import Review, Thread, resolve_links
from . import Conflict, Permanent, Provider, ProviderError, Transient, gh_review, gh_threads
from .gh_review import marker

POLL = 20  # seconds a pending (or no-checks) CI answer is reused: one read serves every task of a poll
PUSH_TIMEOUT = 20  # seconds for a push


# Only these exact phrases will not fix themselves; anything else (a network error that says "closed") is transient.
PERMANENT = re.compile(r"pull request is closed|is not mergeable|merge conflict|cannot be merged", re.I)


CONFLICT = re.compile(r"merge conflict", re.I)


class TransientError(Transient):
    pass


class PermanentError(Permanent):
    pass


class ConflictError(PermanentError, Conflict):
    pass


def failure(what: str, text: str) -> Exception:
    """What went wrong with a gh/git call: transient by default (retried, noted once); permanent only what is known
    not to fix itself: a closed PR, a conflict."""
    text = text.strip()[:300]
    if CONFLICT.search(text):
        return ConflictError(f"{what}: {text}")
    return (PermanentError if PERMANENT.search(text) else TransientError)(f"{what}: {text}")


State = Literal["OPEN", "MERGED", "CLOSED"]
STATES: dict[str, State] = {s: s for s in ("OPEN", "MERGED", "CLOSED")}


@dataclass(frozen=True)
class PullRequest:
    """A pull request as fm sees it: the only shape that leaves this module, never gh's JSON."""

    number: int
    url: str
    draft: bool
    state: State
    merge_commit: str  # "" until merged


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


ROLLUP = {"SUCCESS": "pass", "FAILURE": "fail", "ERROR": "fail", "PENDING": "pending", "EXPECTED": "pending"}


def _at(o: dict[str, Any] | None, *keys: str) -> dict[str, Any]:
    """Down the objects `keys` of a GraphQL answer; {} where one is missing or null (a PR with no checks has no rollup)."""
    for k in keys:
        o = util.json_object((o or {}).get(k))
    return o or {}


def _pull(item: object) -> tuple[str, dict[str, str]]:
    """One GraphQL pull request node as (number, {head, ci}); ProviderError if it does not fit."""
    n = util.json_object(item)
    if not n or not isinstance(n.get("number"), int) or not isinstance(n.get("headRefOid"), str):
        raise ProviderError(f"gh api graphql: unexpected pull request {item!r}"[:300])
    last = util.json_object(next(iter(util.json_list(_at(n, "commits").get("nodes"))), None))
    state = _at(last, "commit", "statusCheckRollup").get("state")
    return str(n["number"]), {"head": n["headRefOid"], "ci": ROLLUP.get(str(state), "none")}


def open_prs(raw: object) -> dict[str, dict[str, str]]:
    """The answer of the open-PRs query as {number: {head, ci}}; ProviderError if it does not fit."""
    return dict(_pull(n) for n in util.json_list(_at(util.json_object(raw), "data", "repository", "pullRequests").get("nodes")))


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

    def view(self, change: dict[str, Any]) -> PullRequest:
        r = self.run("gh", "pr", "view", str(change["pr"]), "--json", "number,url,isDraft,state,mergeCommit")
        try:
            return pull_request(json.loads(r.out))
        except ValueError:
            raise ProviderError(f"gh pr view: not JSON: {r.out.strip()[:200]!r}") from None

    def pushed(self, change: dict[str, Any]) -> bool:
        r = self.run("git", "ls-remote", "origin", f"refs/heads/{change['branch']}", check=False)
        return r.code == 0 and r.out.split()[:1] == [change["head"]]

    def push(self, change: dict[str, Any]) -> None:
        """`git push origin refs/heads/<branch>:refs/heads/<branch>` from the main repo (worktree branches live there):
        never forced, never the main branch. Any failure is Transient with git's error; the text is not classified."""
        branch = change["branch"]
        if branch == self.p.get("project", "main"):
            raise ProviderError(f"refusing to push {branch}: it is the main branch")
        ref = f"refs/heads/{branch}"
        try:
            r = proc.run(["git", "-C", str(self.p.repo), "push", "origin", f"{ref}:{ref}"], timeout=PUSH_TIMEOUT)
        except proc.ProcError as e:
            raise TransientError(f"git push {branch}: {e}") from None
        if r.code != 0:
            raise TransientError(f"git push {branch}: {(r.err or r.out).strip()[:300]}")

    def publish(self, change: dict[str, Any], draft: bool) -> dict[str, Any]:
        args = ["pr", "create", "--head", change["branch"], "--base", self.p.get("project", "main")]
        args += ["--title", self.title(change), "--body", change["body"]]
        url = self.run("gh", *args, *(["--draft"] if draft else [])).out.strip().splitlines()[-1]
        return {"number": int(url.rstrip("/").rsplit("/", 1)[-1]), "url": url, "draft": draft}

    @staticmethod
    def title(change: dict[str, Any]) -> str:
        return f"{change['id']}: {change['title']}"

    def body(self, change: dict[str, Any]) -> str:
        r = self.run("gh", "pr", "view", str(change["pr"]), "--json", "body")
        try:
            raw = json.loads(r.out)
        except ValueError:
            raise ProviderError(f"gh pr view: not JSON: {r.out.strip()[:200]!r}") from None
        o = util.json_object(raw)
        if o is None:
            raise ProviderError(f"gh pr view: expected a JSON object, gh gave {raw!r}"[:300])
        return _field(o, "body", str, "gh pr view")

    def describe(self, change: dict[str, Any], body: str) -> None:
        self.run("gh", "pr", "edit", str(change["pr"]), "--title", self.title(change), "--body", body)

    def ready(self, change: dict[str, Any]) -> None:
        self.run("gh", "pr", "ready", str(change["pr"]))

    def diff(self, change: dict[str, Any]) -> str:
        return self.run("gh", "pr", "diff", str(change["pr"]), "--color=never").out

    def markers(self, change: dict[str, Any], what: str) -> set[str]:
        """The `<!-- fm:KEY -->` markers in the bodies of the PR's reviews or review comments."""
        url = f"repos/{{owner}}/{{repo}}/pulls/{change['pr']}/{what}"
        r = self.run("gh", "api", url, "--paginate", "--jq", '.[] | (.body // "") | scan("<!-- fm:[a-z0-9-]+ -->")')
        return set(r.out.split("\n")) - {""}

    def review(self, change: dict[str, Any], review: Review) -> int:
        if marker(review.key) in self.markers(change, "reviews"):
            return 0
        known = self.markers(change, "comments")
        new = [c for c in review.comments if marker(c.key) not in known]
        base = self.run("gh", "repo", "view", "--json", "url", "-q", ".url").out.strip()
        payload = {
            "commit_id": change["head"],
            "event": "COMMENT",
            "body": resolve_links(f"{review.body}\n\n{marker(review.key)}", base, change["head"], {}),
            "comments": [{"path": c.path, "line": c.line, "side": "RIGHT", "body": f"{c.body}\n\n{marker(c.key)}"} for c in new],
        }
        url = f"repos/{{owner}}/{{repo}}/pulls/{change['pr']}/reviews"
        posted = self.run("gh", "api", "-X", "POST", url, "--input", "-", input=json.dumps(payload))
        gh_review.link_comments(self.run, change, review, new, (base, posted.out))
        return len(new)

    def threads(self, change: dict[str, Any]) -> list[Thread]:
        args = ["api", "graphql", "--paginate", "-F", "owner={owner}", "-F", "repo={repo}", "-F", f"number={change['pr']}"]
        return gh_threads.parse(self.run("gh", *args, "-f", f"query={gh_threads.READ}", "--jq", gh_threads.JQ).out)

    def settle(self, change: dict[str, Any], thread: Thread, sha: str) -> None:
        try:
            self.run("gh", "api", "graphql", "-f", f"query={gh_threads.RESOLVE}", "-f", f"id={thread.id}")
        except (Transient, Permanent):
            body = gh_threads.fixed_text(thread.key, sha)
            self.run("gh", "api", "graphql", "-f", f"query={gh_threads.REPLY}", "-f", f"id={thread.id}", "-f", f"body={body}")

    def is_open(self, change: dict[str, Any]) -> bool:
        return self.view(change).state == "OPEN"

    def close(self, change: dict[str, Any], text: str) -> None:
        self.run("gh", "pr", "close", str(change["pr"]), "--comment", text)

    def ci(self, change: dict[str, Any]) -> str:
        """The PR's CI. A pass or fail is kept for its commit and not read again; pending or no checks is reused for POLL
        seconds. A read asks GitHub once for every open PR, so all tasks of a poll share one call; a PR that is not in that
        answer (past the first 100, or no longer open) is read on its own."""
        pr, head = str(change["pr"]), change["head"]
        e = util.read_json(self.p.state / "pr_ci.json", {}).get(pr)
        if not (e and e["head"] == head and (e["ci"] in ("pass", "fail") or (util.age_s(e["at"]) or 1e9) < POLL)):
            self.refresh(pr)
            e = util.read_json(self.p.state / "pr_ci.json", {}).get(pr)
        return e["ci"] if e and e["head"] == head else "pending"  # GitHub has not shown this commit yet

    def graphql(self, query: str, *fields: str) -> object:
        r = self.run("gh", "api", "graphql", "-F", "owner={owner}", "-F", "name={repo}", *fields, "-f", f"query={query}")
        try:
            return json.loads(r.out)
        except ValueError:
            raise ProviderError(f"gh api graphql: not JSON: {r.out.strip()[:200]!r}") from None

    def refresh(self, pr: str) -> None:
        """One GraphQL call: head commit and check state of every open PR into state/pr_ci.json; then, if `pr` is not among
        them, one more for that PR."""
        fields = "number headRefOid commits(last: 1) { nodes { commit { statusCheckRollup { state } } } }"
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
            found.update([_pull(_at(util.json_object(one), "data", "repository").get("pullRequest"))])
        at = util.now()
        kept = util.read_json(self.p.state / "pr_ci.json", {})
        util.write_json(self.p.state / "pr_ci.json", {**kept, **{n: {**e, "at": at} for n, e in found.items()}})

    def merge(self, change: dict[str, Any], strategy: str) -> str:
        """Merges the PR once (a retry after a crash finds it merged). Returns the merge commit; if GitHub has not
        merged it yet, a marker that the merge was requested, so it is not requested twice."""
        v = self.view(change)
        if v.state != "MERGED":
            msg = ["--subject", f"Integrate {change['id']}: {change['title']}"] if strategy != "rebase" else []
            self.run("gh", "pr", "merge", str(change["pr"]), f"--{strategy}", *msg)
            v = self.view(change)
        return self.merge_commit(change, v) if v.state == "MERGED" else f"pr-{change['pr']}-merge-requested"

    def merge_commit(self, change: dict[str, Any], v: PullRequest) -> str:
        """The merge commit of a merged PR: from `gh pr view`, else the API. Still none: fail (retried) rather than
        invent a reference."""
        sha = v.merge_commit
        if not sha:
            sha = self.run("gh", "api", f"repos/{{owner}}/{{repo}}/pulls/{change['pr']}", "--jq", ".merge_commit_sha").out.strip()
        if not sha or sha == "null":
            raise TransientError(f"PR #{change['pr']} is merged but GitHub shows no merge commit yet")
        return sha

    def state(self, change: dict[str, Any]) -> dict[str, Any]:
        v = self.view(change)
        if v.state != "MERGED":
            return {"merged": False, "closed": v.state != "OPEN"}
        self.catch_up()
        return {"merged": True, "closed": True, "commit": self.merge_commit(change, v)}

    def catch_up(self) -> None:
        """Best effort: bring the main checkout up to the merged main so the next checks see it (fetch + fast-forward
        only; never a push)."""
        main = self.p.get("project", "main")
        self.run("git", "fetch", "-q", "origin", main, check=False)
        on_main = self.run("git", "rev-parse", "--abbrev-ref", "HEAD", check=False).out.strip() == main
        clean = not self.run("git", "status", "--porcelain", "--untracked-files=no", check=False).out.strip()
        if on_main and clean:
            self.run("git", "merge", "-q", "--ff-only", f"origin/{main}", check=False)
