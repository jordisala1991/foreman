"""Pull requests for a GitHub project (the loop's side: the loop pushes the task branches, the agents never do).
`open_drafts` pushes a running task's new commits and, once it has some, gives it a draft PR (`[integrate] pr = draft`);
`prepare` pushes a delivered branch if needed and makes sure it has its PR; `deliver` marks it ready and comments the risk and the review's verdict.
With the local provider every function here does nothing."""

from pathlib import Path
from typing import Any

from . import backlog, describe, proc, providers, util
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


def push(p: Project, change: dict[str, Any]) -> None:
    """Pushes the task's branch to origin from the main repo (worktree branches live there). Never forced, never
    the main branch: a rejected push (the branch diverged) is an error, not something to overwrite."""
    branch = change["branch"]
    if branch == p.get("project", "main"):
        raise providers.Permanent(f"refusing to push {branch}: it is the main branch")
    ref = f"refs/heads/{branch}"
    try:
        r = proc.run(["git", "-C", str(p.repo), "push", "origin", f"{ref}:{ref}"], timeout=60)
    except proc.ProcError as e:
        raise (providers.Transient if e.timed_out else providers.Permanent)(str(e)) from None
    if r.code != 0:
        raise providers.Permanent(f"git push {branch}: {(r.err or r.out).strip()[:300]}")


def _open(p: Project, provider: Provider, i: dict[str, Any], head: str, draft: bool) -> None:
    change = change_of(i, head)
    change["body"] = describe.wrap(change["claim"] or change["title"])
    ref = provider.publish(change, draft)
    backlog.update(p, i["id"], pr=ref, pr_error="")
    backlog.event(p, i["id"], f"opened {'draft ' if draft else ''}PR #{ref['number']}: {ref['url']}")


def open_drafts(p: Project) -> None:
    """Loop step: a task in progress with commits of its own has its branch pushed, and (`pr = draft`) a draft PR once
    it is. One task's failure never stops the others, and none blocks the task (its agent is working fine): the error
    is noted once and the push and the PR are retried next tick."""
    provider = registry.get(p)
    if provider.name != "github":
        return
    for i in backlog.issues(backlog.load(p)):
        if i["status"] != "in progress" or not i.get("branch") or i.get("blocked"):
            continue
        head = _head(p, i["branch"])
        try:
            if not head or not _commits(p, i["branch"]):
                continue
            change = change_of(i, head)
            if not provider.pushed(change):
                push(p, change)
            if providers.pr_mode(p) == "draft" and not i.get("pr"):
                _open(p, provider, i, head, True)
        except Exception as e:
            if i.get("pr_error") != str(e):
                backlog.update(p, i["id"], pr_error=str(e))
                backlog.event(p, i["id"], f"could not push or open its PR, will retry: {e}"[:300])


def prepare(p: Project, i: dict[str, Any], head: str) -> tuple[str, str]:
    """For a delivered branch: ('ok', ci) ready to be checked, with the CI result read now; ('wait', why) CI still
    running. An unpushed branch is pushed here; a failed push raises (the loop blocks the task with git's error)."""
    provider = registry.get(p)
    if provider.name != "github":
        return "ok", ""
    if not provider.pushed(change_of(i, head)):
        push(p, change_of(i, head))
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


def verdict_text(i: dict[str, Any], c: dict[str, Any]) -> str:
    v: dict[str, Any] = i.get("review") or {}
    review = "no review" if not v else f"round {v['round']}: H {v['high']}, M {v['medium']}" + (f" ({v['file']})" if v.get("file") else "")
    reasons = "\n".join(f"- {r}" for r in c.get("reasons", []))
    return f"**fm: merge risk {c['risk']}**\n\n{reasons}\n\nTests: {c['tests']} · CI: {c.get('ci') or 'none'}\nIndependent review, {review}\n"


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
    if provider.name != "github":
        return
    i = backlog.get(backlog.load(p), i["id"])
    pr: dict[str, Any] = i.get("pr") or {}
    change = change_of(i, c["head"])
    if pr.get("draft"):
        provider.ready(change)
        pr = {**pr, "draft": False}
        backlog.update(p, i["id"], pr=pr)
        backlog.event(p, i["id"], f"PR #{pr['number']} ready for review")
    if pr.get("described") != c["head"]:
        pr = _describe(p, provider, i, c["head"], pr)
    if pr.get("commented") != c["head"]:
        provider.comment(change, verdict_text(i, c))
        backlog.update(p, i["id"], pr={**pr, "commented": c["head"], "at": util.now()})
