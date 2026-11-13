"""The GitHub provider: every task is a pull request, driven with `gh`. Only the loop runs it (the guard stops `gh pr`
for agents); the loop also pushes the task branches. Nothing here publishes: no tag, no release, no push to main."""

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Literal

from .. import proc, util
from . import Conflict, Permanent, Provider, ProviderError, RateLimited, Transient

PUSH_TIMEOUT = 20  # seconds for a push


# Only these exact phrases will not fix themselves; anything else (a network error that says "closed") is transient.
PERMANENT = re.compile(r"pull request is closed|is not mergeable|merge conflict|cannot be merged", re.I)


CONFLICT = re.compile(r"merge conflict", re.I)
RATE = re.compile(r"rate limit", re.I)
QUOTA = "github_quota.json"  # in state/: until when gh is paused, and the figures GitHub gave
FALLBACK_PAUSE = 60  # seconds, when the reset cannot be read
MAX_PAUSE = 3600  # a wrong clock or a hand-edited file never pauses gh for longer than this


def resources_of(raw: object) -> dict[str, dict[str, Any]]:
    """The quota resources of `gh api rate_limit` (name -> limit, remaining, reset); {} when it does not fit."""
    found = (util.json_object(raw) or {}).get("resources")
    items = (util.json_object(found) or {}).items()
    return {k: o for k, v in items if (o := util.json_object(v)) is not None}


def reset_at(raw: object, now: float) -> float:
    """When the exhausted quota resets, from `gh api rate_limit`; `now` + a minute when that cannot be read."""
    resets: list[float] = []
    for r in resources_of(raw).values():
        reset = r.get("reset")
        if r.get("remaining") == 0 and isinstance(reset, (int, float)) and reset > now:
            resets.append(reset)
    return float(max(resets)) if resets else now + FALLBACK_PAUSE


def waiting(until: float) -> str:
    return f"GitHub API rate limit: gh paused until {time.strftime('%H:%M:%S', time.localtime(until))}"


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
Bucket = Literal["pass", "fail", "cancel", "pending", "skipping"]
STATES: dict[str, State] = {s: s for s in ("OPEN", "MERGED", "CLOSED")}
BUCKETS: dict[str, Bucket] = {b: b for b in ("pass", "fail", "cancel", "pending", "skipping")}


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


def buckets(raw: object) -> set[Bucket]:
    """`gh pr checks --json bucket` as the set of its buckets; ProviderError if it does not fit."""
    items = util.json_list(raw)
    if not isinstance(raw, list):
        raise ProviderError(f"gh pr checks: expected a JSON list, gh gave {raw!r}"[:300])
    out: set[Bucket] = set()
    for item in items:
        o = util.json_object(item)
        b = BUCKETS.get(o["bucket"]) if o and isinstance(o.get("bucket"), str) else None
        if b is None:
            raise ProviderError(f"gh pr checks: unexpected check {item!r}"[:300])
        out.add(b)
    return out


class GitHub(Provider):
    has_ci = True
    name = "github"

    def paused_until(self) -> float:
        try:
            kept = util.read_json(self.p.state / QUOTA, None)
        except ValueError:  # util.read_json no longer forgives a broken file; a broken pause file is no pause
            return 0.0
        until = (util.json_object(kept) or {}).get("until")
        return min(float(until), time.time() + MAX_PAUSE) if isinstance(until, (int, float)) else 0.0

    def hold(self) -> None:
        """No gh call while GitHub's limit is used up."""
        until = self.paused_until()
        if time.time() < until:
            raise RateLimited(waiting(until), until)

    def limited(self, text: str) -> RateLimited:
        """gh said the limit is exceeded: read when it resets (free call), keep it in state/, and pause."""
        try:
            raw: object = json.loads(proc.run(["gh", "api", "rate_limit"], cwd=self.p.repo).out)
        except (proc.ProcError, ValueError):
            raw = None
        until = reset_at(raw, time.time())
        keep = ("limit", "remaining", "reset")
        resources = {k: {f: v[f] for f in keep if f in v} for k, v in resources_of(raw).items()}
        util.write_json(self.p.state / QUOTA, {"seen": util.now(), "until": until, "error": text.strip()[:200], "resources": resources})
        return RateLimited(waiting(until), until)

    def run(self, prog: str, *args: str, check: bool = True) -> proc.Result:
        if prog == "gh":
            self.hold()
        argv = [prog, *args] if prog == "gh" else [prog, "-C", str(self.p.repo), *args]
        try:
            r = proc.run(argv, cwd=self.p.repo if prog == "gh" else None)
        except proc.ProcError as e:
            raise (TransientError if e.timed_out else PermanentError)(str(e)) from None
        if prog == "gh" and r.code != 0 and RATE.search(r.err + r.out):
            raise self.limited(r.err or r.out)
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
            raw: object = json.loads(r.out)
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

    def comment(self, change: dict[str, Any], text: str) -> None:
        self.run("gh", "pr", "comment", str(change["pr"]), "--body", text)

    def is_open(self, change: dict[str, Any]) -> bool:
        return self.view(change).state == "OPEN"

    def close(self, change: dict[str, Any], text: str) -> None:
        self.run("gh", "pr", "close", str(change["pr"]), "--comment", text)

    def ci(self, change: dict[str, Any]) -> str:
        r = self.run("gh", "pr", "checks", str(change["pr"]), "--json", "bucket", check=False)  # exit 8 = pending
        try:
            found = buckets(json.loads(r.out))
        except ValueError:
            if "no checks" in (r.err + r.out).lower():
                return "none"
            raise failure("gh pr checks", r.err or r.out) from None
        if found & {"fail", "cancel"}:
            return "fail"
        if "pending" in found:
            return "pending"
        return "pass" if "pass" in found else "none"

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
