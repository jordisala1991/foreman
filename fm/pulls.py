"""Pull requests (the loop's side: the loop pushes the task branches, the agents never do).
`running` and `sync` push a running task's new commits and, once it has some, gives it a draft PR (`[integrate] pr = draft`);
`prepare` pushes a delivered branch if needed and makes sure it has its PR; `deliver` marks it ready and posts the verdict as a PR review.
Every function calls the provider's operations whatever the platform; the local provider answers each as a no-op, and
a task with no PR (`publish` returned nothing) has nothing to deliver to."""

from dataclasses import replace
from pathlib import Path
from typing import Any

from . import backlog, describe, inline, proc, providers, util, verdict
from .config import Project
from .inline import LineComment, Review, Thread
from .providers import Permanent, Provider, ProviderError, Transient, registry
from .verdict import Case, Finding


def change_of(i: dict[str, Any], head: str) -> dict[str, Any]:
    acceptance: backlog.Acceptance = i.get("acceptance") or {}
    pr: dict[str, Any] = i.get("pr") or {}
    return {
        "id": i["id"],
        "title": i["title"],
        "branch": i["branch"],
        "head": head,
        "claim": acceptance.get("claim", ""),
        "body": "",
        "pr": pr.get("number"),
        "merge": i.get("merge", ""),
    }


def _head(p: Project, branch: str) -> str:
    return proc.run(["git", "-C", str(p.repo), "rev-parse", "--verify", branch]).out.strip()


def _commits(p: Project, branch: str) -> int:
    r = proc.run(["git", "-C", str(p.repo), "rev-list", "--count", f"{p.get('project', 'main')}..{branch}"])
    return int(r.out.strip() or 0)


def _open(p: Project, provider: Provider, i: dict[str, Any], head: str, draft: bool) -> None:
    change = change_of(i, head)
    change["body"] = describe.wrap(change["claim"] or change["title"])
    ref = provider.publish(change, draft)
    if not ref:  # a provider with no pull requests
        return
    backlog.update(p, i["id"], pr=ref)
    backlog.event(p, i["id"], f"opened {'draft ' if draft else ''}PR #{ref['number']}: {ref['url']}")


def running(p: Project) -> list[dict[str, Any]]:
    """The tasks whose branch the loop keeps on the platform: in progress, with a branch, not blocked."""
    return [i for i in backlog.issues(backlog.load(p)) if i["status"] == "in progress" and i.get("branch") and not i.get("blocked")]


def sync(p: Project, i: dict[str, Any]) -> None:
    """One running task: with commits of its own its branch is pushed, and (`pr = draft`) it gets its draft PR. Failures
    are the caller's (`loop.guarded`)."""
    provider = registry.get(p)
    head = _head(p, i["branch"])
    if not head or not _commits(p, i["branch"]):
        return
    change = change_of(i, head)
    if not provider.pushed(change):
        provider.push(change)
    if providers.pr_mode(p) == "draft" and not i.get("pr"):
        _open(p, provider, i, head, True)


def prepare(p: Project, i: dict[str, Any], head: str) -> tuple[str, str]:
    """For a delivered branch: ('ok', ci) ready to be checked, with the CI result read now; ('wait', why) CI still
    running. An unpushed branch is pushed here; failures are the caller's (`loop.guarded`)."""
    provider = registry.get(p)
    if not provider.pushed(change_of(i, head)):
        provider.push(change_of(i, head))
    if not i.get("pr"):
        _open(p, provider, i, head, False)
    ci = provider.ci(change_of(backlog.get(backlog.load(p), i["id"]), head))
    return ("wait", "CI is still running") if ci == "pending" else ("ok", ci)


def description(p: Project, i: dict[str, Any], head: str, at: str) -> str:
    """The task's report as its description, with fm's footer; the claim (or title) when there is no usable report."""
    report: dict[str, Any] = i.get("report") or {}
    acceptance: backlog.Acceptance = i.get("acceptance") or {}
    path: str = report.get("file") or ""
    text = ""
    if path:
        try:
            text = Path(path).read_text()
        except OSError:
            text = ""
    if not text.strip():
        text = acceptance.get("claim") or i["title"]
    return describe.render(text, describe.footer(head, describe.review_line(i.get("review")), at))


def findings_of(i: dict[str, Any], head: str) -> list[Finding]:
    """The open findings of the review of this tip (none when there is no review, it is of another tip, or unreadable)."""
    v: dict[str, Any] = i.get("review") or {}
    if not v.get("file") or v.get("tip") != head:
        return []
    try:
        return verdict.findings(Path(v["file"]).read_text())
    except OSError:
        return []


def case_of(i: dict[str, Any], c: dict[str, Any]) -> Case:
    v: dict[str, Any] = i.get("review") or {}
    counts = (v.get("high", 0), v.get("medium", 0), v.get("low", 0)) if v.get("tip") == c["head"] else None
    report: dict[str, Any] = i.get("report") or {}
    return Case(c["risk"], c.get("reasons", []), c["tests"], c.get("ci", ""), c["head"], report.get("summary") or i["title"], c["files"], c["lines"], counts)


def _visible(p: Project, provider: Provider, i: dict[str, Any], change: dict[str, Any], found: list[Finding]) -> dict[str, set[int]]:
    if not any(":" in f.place for f in found):
        return {}
    try:
        return inline.visible_lines(provider.diff(change))
    except (ProviderError, Transient, Permanent) as e:
        backlog.event(p, i["id"], f"could not read the diff, review posted without inline comments: {e}"[:300])
        return {}


def _threads(p: Project, provider: Provider, i: dict[str, Any], change: dict[str, Any]) -> list[Thread]:
    try:
        return provider.threads(change)
    except (ProviderError, Transient, Permanent) as e:
        backlog.event(p, i["id"], f"could not read the review threads, nothing settled: {e}"[:300])
        return []


def _settle(p: Project, provider: Provider, i: dict[str, Any], change: dict[str, Any], gone: list[Thread]) -> None:
    """Resolves the threads of fixed findings, after the review is posted. A failure is noted; the thread is tried again next tip."""
    for t in gone:
        try:
            provider.settle(change, t, change["head"])
        except (ProviderError, Transient, Permanent) as e:
            backlog.event(p, i["id"], f"could not settle a fixed finding, will retry next tip: {e}"[:300])
            if isinstance(e, Transient):
                return  # the network is down: do not spend 30 s per remaining thread


def _post_review(p: Project, provider: Provider, i: dict[str, Any], c: dict[str, Any]) -> None:
    change = change_of(i, c["head"])
    found = findings_of(i, c["head"])
    comments = inline.line_comments(found, _visible(p, provider, i, change, found))
    threads = _threads(p, provider, i, change)
    case = case_of(i, c)
    gone = inline.fixed(threads, found) if verdict.complete(case, found) else []

    def review_of(placed: list[LineComment]) -> Review:
        since = (len(gone), len(inline.fresh(placed, threads))) if threads else None
        body = verdict.render(replace(case, since=since), found, {x.place for x in placed})
        return Review(f"review-{c['head']}", body, placed)

    review = review_of(comments)
    try:
        provider.review(change, review)
    except (Transient, Permanent) as e:
        if not comments:
            raise
        backlog.event(p, i["id"], f"inline comments rejected, review posted without them: {e}"[:300])
        provider.review(change, review_of([]))
    _settle(p, provider, i, change, gone)


def _describe(p: Project, provider: Provider, i: dict[str, Any], head: str, pr: dict[str, Any]) -> dict[str, Any]:
    """Sets the PR description for this tip. A failure never holds back the verdict comment or the merge: it is noted
    once on the task (`pr_error`) and retried next tick."""
    try:
        change = change_of(i, head)
        provider.describe(change, describe.merge_body(provider.body(change), description(p, i, head, util.now())))
    except Exception as e:
        if i.get("pr_error") != str(e):
            backlog.update(p, i["id"], pr_error=str(e))
            backlog.event(p, i["id"], f"could not set the PR description, will retry: {e}"[:300])
        return pr
    pr = {**pr, "described": head}
    backlog.update(p, i["id"], pr=pr, pr_error="")
    return pr


def deliver(p: Project, i: dict[str, Any], c: dict[str, Any]) -> None:
    """Marks the PR ready for review and posts the risk and verdict (once per checked tip)."""
    provider = registry.get(p)
    i = backlog.get(backlog.load(p), i["id"])
    pr: dict[str, Any] = i.get("pr") or {}
    if not pr:
        return
    change = change_of(i, c["head"])
    if pr.get("draft"):
        provider.ready(change)
        pr = {**pr, "draft": False}
        backlog.update(p, i["id"], pr=pr)
        backlog.event(p, i["id"], f"PR #{pr['number']} ready for review")
    if pr.get("described") != c["head"]:
        pr = _describe(p, provider, i, c["head"], pr)
    if pr.get("commented") != c["head"]:
        _post_review(p, provider, i, c)
        backlog.update(p, i["id"], pr={**pr, "commented": c["head"], "at": util.now()})
