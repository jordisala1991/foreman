"""Pull requests for a GitHub project (the loop's side; the agent only pushes its branch). `open_drafts` gives a
running task a draft PR once its first commit is pushed (`[integrate] pr = draft`); `prepare` makes sure a delivered
branch is pushed and has its PR; `deliver` marks it ready and comments the risk and the review's verdict.
With the local provider every function here does nothing."""

from pathlib import Path
from typing import Any

from . import backlog, describe, evidence, proc, providers, util
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
    backlog.update(p, i["id"], pr=ref, pr_error="")
    backlog.event(p, i["id"], f"opened {'draft ' if draft else ''}PR #{ref['number']}: {ref['url']}")


def open_drafts(p: Project) -> None:
    """Loop step: a task in progress whose branch is pushed (with commits of its own) gets a draft PR. One task's
    failure never stops the others, and none blocks the task (its agent is working fine): the error is noted once and
    the PR is retried next tick."""
    provider = registry.get(p)
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
    provider = registry.get(p)
    if provider.name != "github":
        return "ok", ""
    if not provider.pushed(change_of(i, head)):
        return "back", f"Push your branch ({i['branch']}) to origin at its tip, then run `fm done` again: the PR is opened and merged from what is pushed."
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


def evidence_files(p: Project, iid: str, text: str) -> dict[str, Path]:
    """The images `text` names that exist in `reports/ID/` (a missing one is left as written)."""
    folder = p.home / "reports" / iid
    return {n: folder / n for n in evidence.names(text) if (folder / n).is_file()}


def _failed(p: Project, i: dict[str, Any], what: str, e: Exception) -> None:
    if i.get("pr_error") != str(e):
        backlog.update(p, i["id"], pr_error=str(e))
        backlog.event(p, i["id"], f"could not {what}, will retry: {e}"[:300])


def _describe(p: Project, provider: Provider, i: dict[str, Any], head: str, pr: dict[str, Any]) -> dict[str, Any]:
    """Sets the PR description for this tip. A failure never holds back the verdict comment or the merge: it is noted
    once on the task (`pr_error`) and retried next tick. If only the images could not be published the body is still
    written, with them unlinked, and it is retried."""
    change = change_of(i, head)
    text = description(p, i, head, util.now())
    urls: dict[str, str] = {}
    retry = False
    try:
        files = evidence_files(p, i["id"], text)
        urls = provider.evidence(change, files) if files else {}
    except Exception as e:
        retry = True
        _failed(p, i, "publish the evidence images", e)
    try:
        provider.describe(change, describe.merge_body(provider.body(change), evidence.relink(text, urls)))
    except Exception as e:
        _failed(p, i, "set the PR description", e)
        return pr
    if retry:
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
