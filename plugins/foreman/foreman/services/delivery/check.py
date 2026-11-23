from dataclasses import asdict
from pathlib import Path
from typing import Any

from ... import proc, util
from ...core.project.config import Project
from ...core.review import reviews
from ...core.rules import decide
from ...core.rules.risk import Facts, Plan, parse_plan, score
from ...core.tasks import backlog
from ...providers import registry
from ...providers.repo import RepoError
from . import git, pulls

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


def tests_at(p: Project, commit: str, name: str, merge: str = "") -> tuple[str, str]:
    """The test command on exactly `commit` (a fresh detached worktree in state/), never on uncommitted files; with
    `merge`, on `commit` with that commit merged in (git refusing the worktree or the merge raises DeliveryError)."""
    if not p.get("merge", "test"):
        return "none", ""
    tmp = p.state / "trees" / name
    r = registry.repo(p)
    try:
        r.remove_worktree(tmp)
        try:
            r.add_detached(tmp, commit)
        except RepoError as e:
            if merge or not e.reason:  # merged check, or git did not run: git's fault, it raises
                raise
            return "fail", e.reason
        try:
            if merge:  # a failure here is git's, not the tests': it raises
                r.merge_in_worktree(tmp, merge)
            return run_tests(p, tmp)
        finally:
            r.remove_worktree(tmp)
    except RepoError as e:  # every git failure here is a DeliveryError, as documented
        raise git.DeliveryError(str(e)) from e


def check(p: Project, tid: str, ci: str | None = None) -> dict[str, Any]:
    """Checks a task's branch and stores the result in its `check`. `ci`: the PR's CI as just read, if the caller has it."""
    d = registry.backlog(p).load()
    i = backlog.get(d, tid)
    branch = i.get("branch", "")
    main = p.get("project", "main")
    commits = pulls.commits_of(p, branch)
    head = pulls.head_of(p, branch)
    tests, out = tests_at(p, head, tid.lower())
    if ci is None:
        ci = registry.get(p).ci(pulls.change_of(i, head)) if i.get("pr") else ""
    failed = registry.get(p).failed_checks(pulls.change_of(i, head)) if ci == "fail" else []
    tests = decide.tests_or_ci(tests, ci)  # no test command: the PR's CI stands in for it
    files = git.diff_stat(p, branch)
    deleted = git.deleted_files(p, branch)
    v: reviews.Review | None = i.get("review")
    review = "" if not v else "clean" if v["open"] == 0 else f"open after round {v['round']}: {v['open']}"
    conflict = git.conflicts(p, branch)
    plan = parse_plan(Path(i["plan_file"]).read_text(encoding="utf-8")) if i.get("plan_file") else Plan()
    risk, reasons = score(p, Facts(tests, review, files, deleted, conflict, commits, ci, p.publishes, plan))
    c = {
        "tests": tests,
        "tests_output": out,
        "ci": ci,
        "failed_checks": [asdict(f) for f in failed],
        "files": len(files),
        "lines": sum(f["added"] + f["deleted"] for f in files),
        "paths": [f["path"] for f in files][:30],
        "commits": commits,
        "risk": risk,
        "review": review,
        "reasons": reasons,
        "head": head,
        "main": registry.repo(p).head(main),
        "conflict": conflict,
        "at": util.now(),
    }
    registry.backlog(p).update(tid, check=c)
    backlog.event(p, tid, f"checked: tests {tests}, {c['files']} files, {c['lines']} lines, risk {risk}")
    return c
