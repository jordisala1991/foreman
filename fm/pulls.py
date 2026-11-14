"""Pull requests (the loop's side: the loop pushes the task branches, the agents never do).
`running` and `sync` push a running task's new commits and, once it has some, gives it a draft PR (`[merge] pr = draft`);
`prepare` pushes a delivered branch if needed and makes sure it has its PR; `deliver` marks it ready and posts the verdict as a PR review.
Every function calls the provider's operations whatever the platform; the local provider answers each as a no-op, and
a task with no PR (`publish` returned nothing) has nothing to deliver to."""

import re
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from . import backlog, decide, describe, evidence, inline, proc, providers, util, verdict
from .config import Project
from .inline import Review
from .providers import Change, Permanent, Provider, ProviderError, Transient, registry
from .verdict import Case, Finding


def change_of(i: dict[str, Any], head: str) -> Change:
    acceptance: backlog.Acceptance = i.get("acceptance") or {}
    pr: dict[str, Any] = i.get("pr") or {}
    return Change(i["id"], i["title"], i["branch"], head, acceptance.get("goal", ""), "", pr.get("number"), i.get("merge", ""))


def head_of(p: Project, branch: str) -> str:
    return proc.run(["git", "-C", str(p.repo), "rev-parse", "--verify", branch]).out.strip()


def commits_of(p: Project, branch: str) -> int:
    r = proc.run(["git", "-C", str(p.repo), "rev-list", "--count", f"{p.get('project', 'main')}..{branch}"])
    return int(r.out.strip() or 0)


def _open(p: Project, provider: Provider, i: dict[str, Any], head: str, draft: bool) -> None:
    change = change_of(i, head)
    ref = provider.publish(replace(change, body=describe.wrap(change.goal or change.title)), draft)
    if ref is None:  # a provider with no pull requests
        return
    backlog.update(p, i["id"], pr=asdict(ref))
    backlog.event(p, i["id"], f"opened {'draft ' if draft else ''}PR #{ref.number}: {ref.url}")


def running(p: Project) -> list[dict[str, Any]]:
    """The tasks whose branch the loop keeps on the platform: in progress, with a branch, not blocked."""
    return [i for i in backlog.tasks(backlog.load(p)) if i["status"] == "in progress" and i.get("branch") and not i.get("blocked")]


def sync(p: Project, i: dict[str, Any]) -> None:
    """One running task: with commits of its own its branch is pushed, and (`pr = draft`) it gets its draft PR. Failures
    are the caller's (`loop.guarded`)."""
    provider = registry.get(p)
    head = head_of(p, i["branch"])
    if not head or not commits_of(p, i["branch"]):
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
    return ("wait", "CI is still running") if decide.waits_for_ci(ci) else ("ok", ci)


def description(p: Project, i: dict[str, Any], head: str, at: str) -> str:
    """The task's report as its description, with fm's footer; the goal (or title) when there is no usable report."""
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
        text = acceptance.get("goal") or i["title"]
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
    counted = int(v["open"]) if v.get("tip") == c["head"] else None
    report: dict[str, Any] = i.get("report") or {}
    return Case(c["risk"], c.get("reasons", []), c["tests"], c.get("ci", ""), c["head"], report.get("summary") or i["title"], c["files"], c["lines"], counted)


def _visible(p: Project, provider: Provider, i: dict[str, Any], change: Change, found: list[Finding]) -> dict[str, set[int]]:
    if not any(":" in f.place for f in found):
        return {}
    try:
        return inline.visible_lines(provider.diff(change))
    except (ProviderError, Transient, Permanent) as e:
        backlog.event(p, i["id"], f"could not read the diff, review posted without inline comments: {e}"[:300])
        return {}


def _post_review(p: Project, provider: Provider, i: dict[str, Any], c: dict[str, Any]) -> None:
    change = change_of(i, c["head"])
    found = findings_of(i, c["head"])
    comments = inline.line_comments(found, _visible(p, provider, i, change, found))
    body = verdict.render(case_of(i, c), found, {x.place for x in comments})
    review = Review(f"review-{c['head']}", body, comments)
    try:
        provider.review(change, review)
    except (Transient, Permanent) as e:
        if not comments:
            raise
        backlog.event(p, i["id"], f"inline comments rejected, review posted without them: {e}"[:300])
        provider.review(change, replace(review, comments=[]))


def evidence_files(p: Project, tid: str, text: str) -> dict[str, Path]:
    """The images `text` names that exist in `reports/ID/` (a missing one is left as written)."""
    found = {n: evidence_file(p, tid, n) for n in evidence.names(text)}
    return {n: f for n, f in found.items() if f}


def evidence_file(p: Project, tid: str, name: str) -> Path | None:
    """`reports/ID/name` when it is a regular file in that real folder (a symlink, file or folder, could point anywhere)."""
    d = p.home / "reports" / tid
    f = d / name
    real = d.is_dir() and not d.is_symlink() and f.is_file() and not f.is_symlink()
    return f if evidence.bare(name) and re.fullmatch(r"[A-Za-z0-9_-]+", tid) and real else None


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
