"""Delivery: a task in review. When a responsible agent says `fm done`, the loop checks its branch (tests in the worktree,
the paths it touches, conflicts with main) and scores the merge risk: low, medium or high, with the reasons.
The project's autonomy decides which risks merge without the owner: `manual` (none: the default), `low`, or
`medium`. High never merges alone. Everything else waits for the owner's accept in the panel (`fm accept`), or is
rejected: it goes back to its agent with his note (`fm reject`).

Accepting only records the decision; the merge step (`merge_accepted`, through the project's provider, fm/providers/) merges,
and the task closes once the merge is seen in main. The local provider merges into the main checkout (`squash` by
default, or `merge` = `--no-ff`); nothing is ever pushed or released."""

from pathlib import Path
from typing import Any

from . import backlog, proc, providers, pulls, sessions, subtasks, util
from .config import Project
from .providers import registry
from .risk import Facts, allows, score


class DeliveryError(Exception):
    pass


class MergeConflict(DeliveryError):
    """The accepted branch conflicts with main: it goes back to its agent."""


def git(repo: Path, *args: str, check: bool = True) -> proc.Result:
    try:
        r = proc.run(["git", "-C", str(repo), *args])
    except proc.ProcError as e:
        raise DeliveryError(str(e)) from e
    if check and r.code != 0:
        raise DeliveryError(f"git {' '.join(args)}: {(r.err or r.out).strip()[:300]}")
    return r


TAIL_LINES = 20


def run_tests(p: Project, cwd: Path) -> tuple[str, str]:
    """(pass | fail | none (no test command), the last TAIL_LINES lines of the output of a failure)."""
    cmd = p.get("merge", "test")
    if not cmd:
        return "none", ""
    try:
        r = proc.run(["sh", "-c", cmd], cwd=cwd, timeout=900)
    except proc.ProcError as e:
        return "fail", str(e)
    if r.code == 0:
        return "pass", ""
    return "fail", "\n".join(f"{r.out}\n{r.err}".strip().splitlines()[-TAIL_LINES:])


def tests_at(p: Project, commit: str, name: str) -> tuple[str, str]:
    """The test command on exactly `commit` (a fresh detached worktree in state/), never on uncommitted files."""
    if not p.get("merge", "test"):
        return "none", ""
    tmp = p.state / "trees" / name
    git(p.repo, "worktree", "remove", "--force", str(tmp), check=False)
    added = git(p.repo, "worktree", "add", "--detach", str(tmp), commit, check=False)
    if added.code != 0:
        return "fail", added.err.strip()
    try:
        return run_tests(p, tmp)
    finally:
        git(p.repo, "worktree", "remove", "--force", str(tmp), check=False)


def diff_stat(p: Project, branch: str) -> list[dict[str, Any]]:
    main = p.get("project", "main")
    out = git(p.repo, "diff", "--numstat", "-M", f"{main}...{branch}").out
    rows: list[dict[str, Any]] = []
    for ln in out.splitlines():
        a, d, path = ln.split("\t", 2)
        rows.append({"path": path, "added": int(a) if a.isdigit() else 0, "deleted": int(d) if d.isdigit() else 0})
    return rows


def deleted_files(p: Project, branch: str) -> list[str]:
    main = p.get("project", "main")
    return git(p.repo, "diff", "--name-only", "--diff-filter=D", f"{main}...{branch}").out.split()


def conflicts(p: Project, branch: str) -> bool:
    r = git(p.repo, "merge-tree", "--write-tree", p.get("project", "main"), branch, check=False)
    return r.code != 0


def check(p: Project, tid: str, ci: str | None = None) -> dict[str, Any]:
    """Checks a task's branch and stores the result in its `check`. `ci`: the PR's CI as just read, if the caller has it."""
    d = backlog.load(p)
    i = backlog.get(d, tid)
    branch = i.get("branch", "")
    main = p.get("project", "main")
    commits = pulls.commits_of(p, branch)
    head = pulls.head_of(p, branch)
    tests, _ = tests_at(p, head, tid.lower())
    if ci is None:
        ci = registry.get(p).ci(pulls.change_of(i, head)) if i.get("pr") else ""
    if tests == "none" and ci in ("pass", "fail"):  # no test command: the PR's CI stands in for it
        tests = ci
    files = diff_stat(p, branch)
    deleted = deleted_files(p, branch)
    v: backlog.Review | None = i.get("review")
    review = "" if not v else "clean" if v["open"] == 0 else f"open after round {v['round']}: {v['open']}"
    conflict = conflicts(p, branch)
    risk, reasons = score(p, Facts(tests, review, files, deleted, conflict, commits, ci))
    c = {
        "tests": tests,
        "ci": ci,
        "files": len(files),
        "lines": sum(f["added"] + f["deleted"] for f in files),
        "paths": [f["path"] for f in files][:30],
        "commits": commits,
        "risk": risk,
        "review": review,
        "reasons": reasons,
        "head": head,
        "main": git(p.repo, "rev-parse", main).out.strip(),
        "conflict": conflict,
        "at": util.now(),
    }
    backlog.update(p, tid, check=c)
    backlog.event(p, tid, f"checked: tests {tests}, {c['files']} files, {c['lines']} lines, risk {risk}")
    return c


def merges_alone(p: Project, c: dict[str, Any]) -> bool:
    """Autonomy lets this risk in alone; when the provider has CI that must be green, only on a pass; open findings
    never merge alone (quality is not risk, but unresolved quality waits for the owner)."""
    if str(c.get("review", "")).startswith("open"):
        return False
    if registry.get(p).has_ci and c.get("ci") != "pass":
        return False
    return allows(p.get("merge", "autonomy"), c["risk"])


def accept(p: Project, tid: str, by: str = "owner") -> str:
    """Records the owner's accept; it merges nothing. The loop's merge step does the merge (`merge_accepted`)."""
    registry.get(p)  # a bad [merge] provider/merge fails here, clearly
    d = backlog.load(p)
    i = backlog.get(d, tid)
    if i["status"] != "in review":
        raise DeliveryError(f"{tid} is {i['status']}, not in review")
    head = git(p.repo, "rev-parse", i["branch"]).out.strip()
    checked: backlog.Check = i.get("check") or {}
    if checked.get("head") != head:
        raise DeliveryError(f"{i['branch']} moved since it was checked: wait for the next check")
    backlog.update(p, tid, accepted={"by": by, "head": head, "at": util.now()})
    backlog.event(p, tid, f"accepted by {by}: waiting for the merge")
    return head


def adopt_merge(p: Project, i: dict[str, Any]) -> str:
    """After a crash between the merge and storing its ref: the merge commit already in main, or ''."""
    if not i.get("merging"):
        return ""
    found = git(p.repo, "log", "--format=%H", f"--grep=^Merge {i['id']}: ", f"{i['merging']}..{p.get('project', 'main')}", check=False).out.split()
    if found:
        backlog.update(p, i["id"], merge=found[0])
    return found[0] if found else ""


def request_merge(p: Project, i: dict[str, Any], provider: providers.Provider, change: dict[str, Any]) -> str:
    """Asks the provider to merge (marking `merging` first) and returns the merge ref. A refusal for a conflict that
    the local check confirms is a `MergeConflict`; any other platform error is a `DeliveryError`."""
    backlog.update(p, i["id"], merging=git(p.repo, "rev-parse", p.get("project", "main")).out.strip())
    try:
        return provider.merge(change, providers.merge_strategy(p))
    except Exception as e:  # a platform's own errors are a delivery error
        if isinstance(e, providers.ConfigError | providers.Transient):
            raise
        if isinstance(e, providers.Conflict) and conflicts(p, i["branch"]):
            raise MergeConflict(str(e)) from None
        raise DeliveryError(str(e)) from None


def merge_accepted(p: Project, tid: str) -> str:
    """The one place that merges: asks the provider to merge an accepted task (once), then closes the task, removing
    its worktree and branch, only when the provider shows the merge done in main. Returns what happened."""
    provider = registry.get(p)
    i = backlog.get(backlog.load(p), tid)
    acc = i.get("accepted")
    if i["status"] != "in review" or not acc:
        raise DeliveryError(f"{tid} is not accepted")
    head = git(p.repo, "rev-parse", i["branch"]).out.strip()
    if head != acc["head"]:
        backlog.update(p, tid, accepted=None)
        raise DeliveryError(f"{i['branch']} moved after it was accepted: accept again after the next check")
    change = pulls.change_of(i, head)
    if not change["merge"]:
        change["merge"] = adopt_merge(p, i)
    if not change["merge"]:
        pr: dict[str, Any] = i.get("pr") or {}
        if pr.get("draft"):  # a PR from the running phase is still a draft: only pulls.deliver readies it
            provider.ready(change)
        ref = request_merge(p, i, provider, change)
        backlog.update(p, tid, merge=ref)
        backlog.event(p, tid, f"merged as {ref[:9]} ({providers.merge_strategy(p)}); waiting to see it in {p.get('project', 'main')}")
        change["merge"] = ref
    seen = provider.state(change)
    if seen["closed"] and not seen["merged"]:
        raise DeliveryError("its pull request was closed without merging: reopen it, or reject the task")
    if not seen["merged"]:
        return "merging"
    stop_all(p, i)
    if i.get("worktree"):
        git(p.repo, "worktree", "remove", "--force", i["worktree"], check=False)
    git(p.repo, "branch", "-D", i["branch"], check=False)
    by = acc["by"]
    report: backlog.Report = i.get("report") or {}
    acceptance: backlog.Acceptance = i.get("acceptance") or {}
    backlog.set_status(
        p,
        tid,
        "done",
        f"merged, accepted by {by}, as {(seen.get('commit') or change['merge'])[:9]}",
        result=report.get("summary", ""),
        commit=seen.get("commit") or change["merge"],
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
        raise DeliveryError(f"could not close its pull request: {e}") from e


def stop_all(p: Project, i: dict[str, Any]) -> None:
    """None of the task's sessions stays running, whatever its status was: its agent, hunter and reviewer, and every
    session it ever started (a replaced review keeps no record but its id in `sessions`)."""
    rows = sessions.listing(p)
    every: list[dict[str, Any]] = [i.get(role) or {} for role in ("agent", "hunter", "reviewer", "mechanic")]
    every += [{"short": rows.get(sid, {}).get("id"), "session": sid} for sid in i.get("sessions", [])]
    done: set[str] = set()
    for a in every:
        if a.get("short") not in done:
            done.add(str(a.get("short")))
            sessions.stop_live(p, i["id"], a, rows)


def settle_parent(p: Project, tid: str) -> None:
    """A parent closes by itself once its subtasks have: done if any is done, else canceled."""
    d = backlog.load(p)
    parent = backlog.get(d, tid).get("parent")
    if not parent or backlog.get(d, parent)["status"] in backlog.CLOSED:
        return
    if status := subtasks.outcome(subtasks.of(backlog.tasks(d), parent)):
        backlog.set_status(p, parent, status, "its subtasks are closed")


def close(p: Project, tid: str, status: str, why: str, *, settle: bool = True) -> None:
    """Closes an open task as canceled or done (merged outside fm): stops its sessions, answers its open questions,
    removes its worktree (the branch stays: the work is not lost); a canceled task's PR is closed with the reason.
    `fm cancel` and the sources' signals use it. Canceling a parent first cancels its open subtasks."""
    d = backlog.load(p)
    i = backlog.get(d, tid)
    if status == "canceled":
        for s in subtasks.of(backlog.tasks(d), tid):
            if backlog.get(backlog.load(p), s["id"])["status"] not in backlog.CLOSED:  # re-read: the loop may have merged it
                close(p, s["id"], "canceled", f"its parent {tid} was canceled: {why}", settle=False)
    if status == "canceled" and i.get("pr"):  # first: if the platform fails, nothing is half-done
        close_pull(p, i, why)
    stop_all(p, i)
    for q in backlog.open_questions(backlog.load(p), tid):
        backlog.answer(p, q["id"], f"(the task was {status})")
        backlog.mark_delivered(p, q["id"])
    if i.get("worktree"):
        git(p.repo, "worktree", "remove", "--force", i["worktree"], check=False)
    backlog.set_status(p, tid, status, why)
    if settle:
        settle_parent(p, tid)
