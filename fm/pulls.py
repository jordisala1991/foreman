"""Pull requests for a GitHub project (the loop's side; the agent only pushes its branch). `open_drafts` gives a
running task a draft PR once its first commit is pushed (`[integrate] pr = draft`); `prepare` makes sure a delivered
branch is pushed and has its PR; `deliver` marks it ready and comments the risk and the review's verdict.
With the local provider every function here does nothing."""

import subprocess
from typing import Any

from . import backlog, providers, util
from .config import Project
from .providers import Provider


def change_of(i: dict[str, Any], head: str) -> dict[str, Any]:
    acceptance: backlog.Acceptance = i.get("acceptance") or {}
    pr: dict[str, Any] = i.get("pr") or {}
    return {
        "id": i["id"],
        "title": i["title"],
        "branch": i["branch"],
        "head": head,
        "claim": acceptance.get("claim", ""),
        "pr": pr.get("number"),
        "merge": i.get("merge", ""),
    }


def _head(p: Project, branch: str) -> str:
    r = subprocess.run(["git", "-C", str(p.repo), "rev-parse", "--verify", branch], capture_output=True, text=True, check=False)
    return r.stdout.strip()


def _commits(p: Project, branch: str) -> int:
    r = subprocess.run(["git", "-C", str(p.repo), "rev-list", "--count", f"{p.get('project', 'main')}..{branch}"], capture_output=True, text=True, check=False)
    return int(r.stdout.strip() or 0)


def _open(p: Project, provider: Provider, i: dict[str, Any], head: str, draft: bool) -> None:
    ref = provider.publish(change_of(i, head), draft)
    backlog.update(p, i["id"], pr=ref, pr_error="")
    backlog.event(p, i["id"], f"opened {'draft ' if draft else ''}PR #{ref['number']}: {ref['url']}")


def open_drafts(p: Project) -> None:
    """Loop step: a task in progress whose branch is pushed (with commits of its own) gets a draft PR. One task's
    failure never stops the others, and none blocks the task (its agent is working fine): the error is noted once and
    the PR is retried next tick."""
    provider = providers.get(p)
    if provider.name != "github" or providers.pr_mode(p) != "draft":
        return
    for i in backlog.issues(backlog.load(p)):
        if i["status"] != "in progress" or i.get("pr") or not i.get("branch") or i.get("blocked"):
            continue
        head = _head(p, i["branch"])
        try:
            if head and _commits(p, i["branch"]) and provider.pushed(change_of(i, head)):
                _open(p, provider, i, head, True)
        except Exception as e:
            if i.get("pr_error") != str(e):
                backlog.update(p, i["id"], pr_error=str(e))
                backlog.event(p, i["id"], f"could not open its PR, will retry: {e}"[:300])


def prepare(p: Project, i: dict[str, Any], head: str) -> tuple[str, str]:
    """For a delivered branch: ('ok', ci) ready to be checked, with the CI result read now; ('wait', why) CI still
    running; ('back', note) the agent must push first."""
    provider = providers.get(p)
    if provider.name != "github":
        return "ok", ""
    if not provider.pushed(change_of(i, head)):
        return "back", f"Push your branch ({i['branch']}) to origin at its tip, then run `fm done` again: the PR is opened and merged from what is pushed."
    if not i.get("pr"):
        _open(p, provider, i, head, False)
    ci = provider.ci(change_of(backlog.get(backlog.load(p), i["id"]), head))
    return ("wait", "CI is still running") if ci == "pending" else ("ok", ci)


def verdict_text(i: dict[str, Any], c: dict[str, Any]) -> str:
    v: dict[str, Any] = i.get("review") or {}
    review = "no review" if not v else f"round {v['round']}: H {v['high']}, M {v['medium']}" + (f" ({v['file']})" if v.get("file") else "")
    reasons = "\n".join(f"- {r}" for r in c.get("reasons", []))
    return f"**fm: merge risk {c['risk']}**\n\n{reasons}\n\nTests: {c['tests']} · CI: {c.get('ci') or 'none'}\nIndependent review, {review}\n"


def deliver(p: Project, i: dict[str, Any], c: dict[str, Any]) -> None:
    """Marks the PR ready for review and posts the risk and verdict (once per checked tip)."""
    provider = providers.get(p)
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
    if pr.get("commented") != c["head"]:
        provider.comment(change, verdict_text(i, c))
        backlog.update(p, i["id"], pr={**pr, "commented": c["head"], "at": util.now()})
