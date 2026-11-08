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


PUSH_TIMEOUT_S = 20
PUSH_BACKOFF_TICKS = 3
PUSH_ATTEMPTS = 3  # consecutive failures of one task's push before it is blocked


class PushError(Exception):
    """A push that did not happen; the message is git's own."""


def push(p: Project, change: dict[str, Any]) -> None:
    """Pushes the task's branch to origin from the main repo (worktree branches live there). Never forced, never
    the main branch. Any failure raises PushError with git's error: the errors are not told apart by their text."""
    branch = change["branch"]
    if branch == p.get("project", "main"):
        raise PushError(f"refusing to push {branch}: it is the main branch")
    ref = f"refs/heads/{branch}"
    try:
        r = proc.run(["git", "-C", str(p.repo), "push", "origin", f"{ref}:{ref}"], timeout=PUSH_TIMEOUT_S)
    except proc.ProcError as e:
        raise PushError(f"git push {branch}: {e}") from None
    if r.code != 0:
        raise PushError(f"git push {branch}: {(r.err or r.out).strip()[:300]}")


def backing_off(p: Project, i: dict[str, Any]) -> bool:
    """A task whose push just failed is left alone for a few ticks, so a hung remote cannot stall every tick."""
    age = util.age_s(i.get("push_failed_at"))
    return age is not None and age < PUSH_BACKOFF_TICKS * int(p.get("health", "loop_seconds"))


def push_task(p: Project, i: dict[str, Any], change: dict[str, Any]) -> None:
    """`push`, counting consecutive failures on the task: each one starts the backoff and is retried (Transient);
    the PUSH_ATTEMPTS-th raises PushError with git's last error, for the caller to block the task. A success resets."""
    try:
        push(p, change)
    except PushError as e:
        failures = int(i.get("push_failures") or 0) + 1
        if failures >= PUSH_ATTEMPTS:
            backlog.update(p, i["id"], push_failures=0, push_failed_at="")
            raise
        backlog.update(p, i["id"], push_failures=failures, push_failed_at=util.now())
        raise providers.Transient(str(e)) from None
    if i.get("push_failed_at") or i.get("push_failures"):
        backlog.update(p, i["id"], push_failed_at="", push_failures=0)


def _open(p: Project, provider: Provider, i: dict[str, Any], head: str, draft: bool) -> None:
    change = change_of(i, head)
    change["body"] = describe.wrap(change["claim"] or change["title"])
    ref = provider.publish(change, draft)
    backlog.update(p, i["id"], pr=ref, pr_error="")
    backlog.event(p, i["id"], f"opened {'draft ' if draft else ''}PR #{ref['number']}: {ref['url']}")


def _running(p: Project, provider: Provider, i: dict[str, Any]) -> None:
    head = _head(p, i["branch"])
    if not head or not _commits(p, i["branch"]):
        return
    change = change_of(i, head)
    if not provider.pushed(change):
        push_task(p, i, change)
    if providers.pr_mode(p) == "draft" and not i.get("pr"):
        _open(p, provider, i, head, True)


def _failed(p: Project, i: dict[str, Any], e: Exception) -> None:
    if isinstance(e, PushError):  # it failed PUSH_ATTEMPTS times in a row
        backlog.update(p, i["id"], blocked=f"the loop could not push it: {e}"[:300])
        backlog.event(p, i["id"], f"blocked: could not push: {e}"[:300])
    elif i.get("pr_error") != str(e):
        backlog.update(p, i["id"], pr_error=str(e))
        backlog.event(p, i["id"], f"could not push or open its PR, will retry: {e}"[:300])


def open_drafts(p: Project) -> None:
    """Loop step: a task in progress with commits of its own has its branch pushed, and (`pr = draft`) a draft PR once
    it is. One task's failure never stops the others. A push that fails PUSH_ATTEMPTS times in a row blocks the task with git's last
    error; any other failure is noted once and retried: a failed push is not retried for a few ticks."""
    provider = registry.get(p)
    if provider.name != "github":
        return
    for i in backlog.issues(backlog.load(p)):
        if i["status"] != "in progress" or not i.get("branch") or i.get("blocked") or backing_off(p, i):
            continue
        try:
            _running(p, provider, i)
        except Exception as e:
            _failed(p, i, e)


def prepare(p: Project, i: dict[str, Any], head: str) -> tuple[str, str]:
    """For a delivered branch: ('ok', ci) ready to be checked, with the CI result read now; ('wait', why) CI still
    running. An unpushed branch is pushed here; a push that fails is Transient (it stays queued and is retried); the third in a row raises
    PushError, and the loop blocks the task with git's error."""
    provider = registry.get(p)
    if provider.name != "github":
        return "ok", ""
    if not provider.pushed(change_of(i, head)):
        if backing_off(p, i):
            return "wait", "its last push failed, trying again shortly"
        push_task(p, i, change_of(i, head))
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
