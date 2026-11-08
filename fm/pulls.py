"""Pull requests (the loop's side: the loop pushes the task branches, the agents never do).
`running` and `sync` push a running task's new commits and, once it has some, gives it a draft PR (`[integrate] pr = draft`);
`prepare` pushes a delivered branch if needed and makes sure it has its PR; `deliver` marks it ready and keeps one verdict comment up to date.
Every function calls the provider's operations whatever the platform; the local provider answers each as a no-op, and
a task with no PR (`publish` returned nothing) has nothing to deliver to."""

from pathlib import Path
from typing import Any

from . import backlog, describe, proc, providers, util, verdict
from .config import Project
from .providers import Provider, registry


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


def verdict_for(i: dict[str, Any], c: dict[str, Any]) -> str:
    """The verdict comment's text for this check: the review file's findings are read here, never its path shown."""
    review: dict[str, Any] | None = i.get("review")
    text = ""
    if review and review.get("file"):
        try:
            text = Path(review["file"]).read_text()
        except OSError:
            text = ""
    return verdict.render(c["risk"], c.get("reasons", []), f"Tests: {c['tests']} · CI: {c.get('ci') or 'none'}", review, text)


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
    """Marks the PR ready for review and updates the one verdict comment (once per checked tip)."""
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
        provider.comment(change, verdict_for(i, c), "verdict")
        backlog.update(p, i["id"], pr={**pr, "commented": c["head"], "at": util.now()})
