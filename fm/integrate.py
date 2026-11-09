"""Integration: a queue. When a responsible agent says `fm done`, the loop checks its branch (tests in the worktree,
the paths it touches, conflicts with main) and scores the merge risk: low, medium or high, with the reasons.
The project's autonomy level decides which risks merge without the owner: `manual` (none: the default), `low`, or
`medium`. High never merges alone. Everything else waits for the owner's accept in the panel (`fm accept`), or goes
back to its agent with his note (`fm reject`).

Accepting only records the decision; the merge step (`merge_accepted`, through the project's provider, fm/providers/) merges,
and the issue closes once the merge is seen in main. The local provider merges into the main checkout (`squash` by
default, or `merge` = `--no-ff`); nothing is ever pushed or released."""

from pathlib import Path
from typing import Any

from . import backlog, proc, providers, pulls, sessions, util
from .config import Project
from .providers import registry
from .risk import Facts, allows, score


class IntegrateError(Exception):
    pass


def git(repo: Path, *args: str, check: bool = True) -> proc.Result:
    try:
        r = proc.run(["git", "-C", str(repo), *args])
    except proc.ProcError as e:
        raise IntegrateError(str(e)) from e
    if check and r.code != 0:
        raise IntegrateError(f"git {' '.join(args)}: {(r.err or r.out).strip()[:300]}")
    return r


def run_tests(p: Project, cwd: Path) -> str:
    """pass | fail | none (no test command); the tail of a failure goes to the events."""
    cmd = p.get("integrate", "test")
    if not cmd:
        return "none"
    try:
        r = proc.run(["sh", "-c", cmd], cwd=cwd, timeout=900)
    except proc.ProcError:
        return "fail"
    return "pass" if r.code == 0 else "fail"


def tests_at(p: Project, commit: str, name: str) -> str:
    """The test command on exactly `commit` (a fresh detached worktree in state/), never on uncommitted files."""
    if not p.get("integrate", "test"):
        return "none"
    tmp = p.state / "trees" / name
    git(p.repo, "worktree", "remove", "--force", str(tmp), check=False)
    if git(p.repo, "worktree", "add", "--detach", str(tmp), commit, check=False).code != 0:
        return "fail"
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


def check(p: Project, iid: str, ci: str | None = None) -> dict[str, Any]:
    """Checks an issue's branch and stores the result in its `check`. `ci`: the PR's CI as just read, if the caller has it."""
    d = backlog.load(p)
    i = backlog.get(d, iid)
    branch = i.get("branch", "")
    main = p.get("project", "main")
    commits = int(git(p.repo, "rev-list", "--count", f"{main}..{branch}", check=False).out.strip() or 0)
    head = git(p.repo, "rev-parse", branch).out.strip()
    tests = tests_at(p, head, iid.lower())
    if ci is None:
        ci = registry.get(p).ci(pulls.change_of(i, head)) if i.get("pr") else ""
    if tests == "none" and ci in ("pass", "fail"):  # no test command: the PR's CI stands in for it
        tests = ci
    files = diff_stat(p, branch)
    deleted = deleted_files(p, branch)
    v: backlog.Review | None = i.get("review")
    review = "" if not v else "clean" if v["high"] + v["medium"] == 0 else f"open after round {v['round']}: H {v['high']}, M {v['medium']}"
    risk, reasons = score(p, Facts(tests, review, files, deleted, conflicts(p, branch), commits, ci))
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
        "head": git(p.repo, "rev-parse", branch).out.strip(),
        "main": git(p.repo, "rev-parse", main).out.strip(),
        "conflict": conflicts(p, branch),
        "at": util.now(),
    }
    backlog.update(p, iid, check=c)
    backlog.event(p, iid, f"checked: tests {tests}, {c['files']} files, {c['lines']} lines, risk {risk}")
    return c


def merges_alone(p: Project, c: dict[str, Any]) -> bool:
    """Autonomy lets this risk in alone; when the provider has CI that must be green, only on a pass; open H/M findings
    never merge alone (quality is not risk, but unresolved quality waits for the owner)."""
    if str(c.get("review", "")).startswith("open"):
        return False
    if registry.get(p).has_ci and c.get("ci") != "pass":
        return False
    return allows(p.get("integrate", "autonomy"), c["risk"])


def accept(p: Project, iid: str, by: str = "owner") -> str:
    """Records the decision to integrate; it merges nothing. The loop's merge step does the merge (`merge_accepted`)."""
    registry.get(p)  # a bad [integrate] provider/merge fails here, clearly
    d = backlog.load(p)
    i = backlog.get(d, iid)
    if i["status"] != "in review":
        raise IntegrateError(f"{iid} is {i['status']}, not in the integration queue")
    head = git(p.repo, "rev-parse", i["branch"]).out.strip()
    checked: backlog.Check = i.get("check") or {}
    if checked.get("head") != head:
        raise IntegrateError(f"{i['branch']} moved since it was checked: wait for the next check")
    backlog.update(p, iid, accepted={"by": by, "head": head, "at": util.now()})
    backlog.event(p, iid, f"accepted by {by}: waiting for the merge")
    return head


def adopt_merge(p: Project, i: dict[str, Any]) -> str:
    """After a crash between the merge and storing its ref: the merge commit already in main, or ''."""
    if not i.get("merging"):
        return ""
    found = git(p.repo, "log", "--format=%H", f"--grep=^Integrate {i['id']}: ", f"{i['merging']}..{p.get('project', 'main')}", check=False).out.split()
    if found:
        backlog.update(p, i["id"], merge=found[0])
    return found[0] if found else ""


def merge_accepted(p: Project, iid: str) -> str:
    """The one place that merges: asks the provider to merge an accepted issue (once), then closes the issue, removing
    its worktree and branch, only when the provider shows the merge done in main. Returns what happened."""
    provider = registry.get(p)
    i = backlog.get(backlog.load(p), iid)
    acc = i.get("accepted")
    if i["status"] != "in review" or not acc:
        raise IntegrateError(f"{iid} is not accepted")
    head = git(p.repo, "rev-parse", i["branch"]).out.strip()
    if head != acc["head"]:
        backlog.update(p, iid, accepted=None)
        raise IntegrateError(f"{i['branch']} moved after it was accepted: accept again after the next check")
    change = pulls.change_of(i, head)
    if not change["merge"]:
        change["merge"] = adopt_merge(p, i)
    if not change["merge"]:
        backlog.update(p, iid, merging=git(p.repo, "rev-parse", p.get("project", "main")).out.strip())
        try:
            ref = provider.merge(change, providers.merge_strategy(p))
        except Exception as e:  # a platform's own errors are an integration error
            if isinstance(e, providers.ConfigError | providers.Transient):
                raise
            raise IntegrateError(str(e)) from None
        backlog.update(p, iid, merge=ref)
        backlog.event(p, iid, f"merged as {ref[:9]} ({providers.merge_strategy(p)}); waiting to see it in {p.get('project', 'main')}")
        change["merge"] = ref
    seen = provider.state(change)
    if seen["closed"] and not seen["merged"]:
        raise IntegrateError("its pull request was closed without merging: reopen it, or reject the task")
    if not seen["merged"]:
        return "merging"
    if i.get("worktree"):
        git(p.repo, "worktree", "remove", "--force", i["worktree"], check=False)
    git(p.repo, "branch", "-D", i["branch"], check=False)
    by = acc["by"]
    report: backlog.Report = i.get("report") or {}
    acceptance: backlog.Acceptance = i.get("acceptance") or {}
    backlog.set_status(
        p,
        iid,
        "done",
        f"integrated, accepted by {by}, as {(seen.get('commit') or change['merge'])[:9]}",
        result=report.get("summary", ""),
        commit=seen.get("commit") or change["merge"],
        acceptance={**acceptance, "verdict": f"accepted by {by}"},
    )
    return "done"


def close(p: Project, iid: str, status: str, why: str) -> None:
    """Closes an open issue as canceled or done (merged outside fm): stops its agent, answers its open questions,
    removes its worktree (the branch stays: the work is not lost). `fm cancel` and the sources' signals use it."""
    i = backlog.get(backlog.load(p), iid)
    agent: backlog.Agent = i.get("agent") or {}
    short = agent.get("short")
    if short and i["status"] in backlog.RUNNING:
        sessions.stop(p, short)
    for q in backlog.open_questions(backlog.load(p), iid):
        backlog.answer(p, q["id"], f"(the task was {status})")
        backlog.mark_delivered(p, q["id"])
    if i.get("worktree"):
        git(p.repo, "worktree", "remove", "--force", i["worktree"], check=False)
    backlog.set_status(p, iid, status, why)
