from dataclasses import replace
from typing import Any

from ... import providers, util
from ...core.project.config import Project
from ...core.tasks import backlog, questions, subtasks
from ...providers import Change, registry
from ...providers.runner import ref_from_tab, ref_of, stop_live
from ..agents import worktree
from . import git, pulls


def accept(p: Project, tid: str, by: str = "owner") -> str:
    """Records the owner's accept; it merges nothing. The loop's merge step does the merge (`merge_accepted`)."""
    i = backlog.get(registry.backlog(p).load(), tid)
    p = p.for_task(i)
    registry.get(p)  # a bad [merge] provider/merge fails here, clearly
    if i["status"] != "in review":
        raise git.DeliveryError(f"{tid} is {i['status']}, not in review")
    head = registry.repo(p).head(i["branch"])
    checked: backlog.Check = i.get("check") or {}
    if checked.get("head") != head:
        raise git.DeliveryError(f"{i['branch']} moved since it was checked: wait for the next check")
    registry.backlog(p).update(tid, accepted={"by": by, "head": head, "at": util.now()})
    backlog.event(p, tid, f"accepted by {by}: waiting for the merge")
    return head


def adopt_merge(p: Project, i: dict[str, Any]) -> str:
    """After a crash between the merge and storing its ref: the merge commit already in main, or ''."""
    if not i.get("merging"):
        return ""
    found = registry.repo(p).merges_of(i["id"], i["merging"], p.get("project", "main"))
    if found:
        registry.backlog(p).update(i["id"], merge=found[0])
    return found[0] if found else ""


def request_merge(p: Project, i: dict[str, Any], provider: providers.Provider, change: Change) -> str:
    """Asks the provider to merge (marking `merging` first) and returns the merge ref. A refusal for a conflict that
    the local check confirms is a `MergeConflict`; any other platform error is a `DeliveryError`."""
    registry.backlog(p).update(i["id"], merging=registry.repo(p).head(p.get("project", "main")))
    try:
        return provider.merge(change, providers.merge_strategy(p))
    except (providers.ConfigError, providers.Transient):
        raise
    except providers.Conflict as e:
        if git.conflicts(p, i["branch"]):
            raise git.MergeConflict(str(e)) from None
        raise git.DeliveryError(str(e)) from None
    except (providers.Permanent, providers.ProviderError) as e:
        raise git.DeliveryError(str(e)) from None


def merge_accepted(p: Project, tid: str) -> str:
    """The one place that merges: asks the provider to merge an accepted task (once), then closes the task, removing
    its worktree and branch, only when the provider shows the merge done in main. Returns what happened."""
    provider = registry.get(p)
    i = backlog.get(registry.backlog(p).load(), tid)
    acc = i.get("accepted")
    if i["status"] != "in review" or not acc:
        raise git.DeliveryError(f"{tid} is not accepted")
    head = registry.repo(p).head(i["branch"])
    if head != acc["head"]:
        registry.backlog(p).update(tid, accepted=None)
        raise git.DeliveryError(f"{i['branch']} moved after it was accepted: accept again after the next check")
    change = pulls.change_of(i, head)
    if not change.merge:
        change = replace(change, merge=adopt_merge(p, i))
    if not change.merge:
        pr: dict[str, Any] = i.get("pr") or {}
        if pr.get("draft"):  # a PR from the running phase is still a draft: only pulls.deliver readies it
            provider.ready(change)
        ref = request_merge(p, i, provider, change)
        registry.backlog(p).update(tid, merge=ref)
        backlog.event(p, tid, f"merged as {ref[:9]} ({providers.merge_strategy(p)}); waiting to see it in {p.get('project', 'main')}")
        change = replace(change, merge=ref)
    seen = provider.state(change)
    if seen.closed and not seen.merged:
        raise git.DeliveryError("its pull request was closed without merging: reopen it, or reject the task")
    if not seen.merged:
        return "merging"
    stop_all(p, i)
    if i.get("worktree"):
        registry.repo(p).remove_worktree(i["worktree"])
    registry.repo(p).delete_branch(i["branch"])
    by = acc["by"]
    report: backlog.Report = i.get("report") or {}
    acceptance: backlog.Acceptance = i.get("acceptance") or {}
    registry.backlog(p).set_status(
        tid,
        "done",
        f"merged, accepted by {by}, as {(seen.commit or change.merge)[:9]}",
        result=report.get("summary", ""),
        commit=seen.commit or change.merge,
        acceptance={**acceptance, "verdict": f"accepted by {by}"},
    )
    settle_parent(p, tid)
    return "done"


def close_pull(p: Project, i: dict[str, Any], why: str) -> None:
    """Closes the task's PR with the reason, unless it is already closed or merged."""
    provider, change = registry.get(p), pulls.change_of(i, "")
    try:
        if provider.is_open(change):
            provider.close(change, f"Task canceled: {why}")
    except (providers.Transient, providers.Permanent, providers.ProviderError) as e:
        raise git.DeliveryError(f"could not close its pull request: {e}") from e


def stop_all(p: Project, i: dict[str, Any]) -> None:
    """None of the task's sessions stays running, whatever its status was: its agent, hunter and reviewer, and every
    session it ever started (a replaced review keeps no record but its id in `sessions`)."""
    rows = registry.rows(p)
    runner = registry.runner(p)
    refs = [ref_of(i.get(role)) for role in ("agent", "hunter", "reviewer", "mechanic")]
    tabs: dict[str, list[str | None]] = i.get("session_tabs", {})
    refs += [ref_from_tab(rows.short(sid) or "", sid, tabs.get(sid)) for sid in i.get("sessions", [])]
    done: set[str] = set()
    for ref in refs:
        if ref and ref.short not in done:
            done.add(ref.short)
            stop_live(p, i["id"], ref, runner, rows)
    worktree.remove_agent_folders(p, i["id"])


def settle_parent(p: Project, tid: str) -> None:
    """A parent closes by itself once its subtasks have: done if any is done, else canceled. One with an agent closes
    only by that agent's `foreman done`."""
    d = registry.backlog(p).load()
    parent = backlog.get(d, tid).get("parent")
    if not parent or backlog.get(d, parent)["status"] in backlog.CLOSED or backlog.get(d, parent).get("agent"):
        return
    if status := subtasks.outcome(subtasks.of(backlog.tasks(d), parent)):
        registry.backlog(p).set_status(parent, status, "its subtasks are closed")


def close(p: Project, tid: str, status: str, why: str, *, settle: bool = True) -> None:
    """Closes an open task as canceled or done (merged outside fm): stops its sessions, answers its open questions,
    removes its worktree (the branch stays: the work is not lost); a canceled task's PR is closed with the reason.
    `foreman cancel` and the sources' signals use it. Canceling a parent first cancels its open subtasks."""
    d = registry.backlog(p).load()
    i = backlog.get(d, tid)
    p = p.for_task(i)
    if status == "canceled":
        for s in subtasks.of(backlog.tasks(d), tid):
            if backlog.get(registry.backlog(p).load(), s["id"])["status"] not in backlog.CLOSED:  # re-read: the loop may have merged it
                close(p, s["id"], "canceled", f"its parent {tid} was canceled: {why}", settle=False)
    if status == "canceled" and i.get("pr"):  # first: if the platform fails, nothing is half-done
        close_pull(p, i, why)
    stop_all(p, i)
    for q in questions.open_questions(registry.backlog(p).load(), tid):
        registry.backlog(p).answer(q["id"], f"(the task was {status})")
        registry.backlog(p).mark_delivered(q["id"])
    if i.get("worktree"):
        registry.repo(p).remove_worktree(i["worktree"])
    registry.backlog(p).set_status(tid, status, why)
    if settle:
        settle_parent(p, tid)
