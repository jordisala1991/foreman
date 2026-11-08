"""Integration: a queue. When a responsible agent says `fm done`, the loop checks its branch (tests in the worktree,
diff size, sensitive paths, conflicts with main) and scores the merge risk: low, medium or high, with the reasons.
The project's autonomy level decides which risks merge without the owner: `manual` (none: the default), `low`, or
`medium`. High never merges alone. Everything else waits for the owner's accept in the panel (`fm accept`), or goes
back to its agent with his note (`fm reject`).

Accepting only records the decision; the merge step (`merge_accepted`, through the project's provider, fm/providers/) merges,
and the issue closes once the merge is seen in main. The local provider merges into the main checkout (`squash` by
default, or `merge` = `--no-ff`); nothing is ever pushed or released."""

import fnmatch
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import backlog, providers, sessions, util
from .config import Project

RISKS = ("low", "medium", "high")
AUTONOMY = {"manual": (), "low": ("low",), "medium": ("low", "medium")}
DEPENDENCY_FILES = ("requirements*.txt", "pyproject.toml", "package.json", "package-lock.json", "go.mod", "Cargo.toml")


class IntegrateError(Exception):
    pass


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=False)
    if check and r.returncode != 0:
        raise IntegrateError(f"git {' '.join(args)}: {(r.stderr or r.stdout).strip()[:300]}")
    return r


def run_tests(p: Project, cwd: Path) -> str:
    """pass | fail | none (no test command); the tail of a failure goes to the events."""
    cmd = p.get("integrate", "test")
    if not cmd:
        return "none"
    try:
        r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=900, check=False)
    except subprocess.TimeoutExpired:
        return "fail"
    return "pass" if r.returncode == 0 else "fail"


def tests_at(p: Project, commit: str, name: str) -> str:
    """The test command on exactly `commit` (a fresh detached worktree in state/), never on uncommitted files."""
    if not p.get("integrate", "test"):
        return "none"
    tmp = p.state / "trees" / name
    git(p.repo, "worktree", "remove", "--force", str(tmp), check=False)
    if git(p.repo, "worktree", "add", "--detach", str(tmp), commit, check=False).returncode != 0:
        return "fail"
    try:
        return run_tests(p, tmp)
    finally:
        git(p.repo, "worktree", "remove", "--force", str(tmp), check=False)


def diff_stat(p: Project, branch: str) -> list[dict[str, Any]]:
    main = p.get("project", "main")
    out = git(p.repo, "diff", "--numstat", "-M", f"{main}...{branch}").stdout
    rows: list[dict[str, Any]] = []
    for ln in out.splitlines():
        a, d, path = ln.split("\t", 2)
        rows.append({"path": path, "added": int(a) if a.isdigit() else 0, "deleted": int(d) if d.isdigit() else 0})
    return rows


def deleted_files(p: Project, branch: str) -> list[str]:
    main = p.get("project", "main")
    return git(p.repo, "diff", "--name-only", "--diff-filter=D", f"{main}...{branch}").stdout.split()


def conflicts(p: Project, branch: str) -> bool:
    r = git(p.repo, "merge-tree", "--write-tree", p.get("project", "main"), branch, check=False)
    return r.returncode != 0


def is_sensitive(path: str, patterns: list[str]) -> bool:
    for pat in patterns:
        if pat.endswith("/"):
            if path.startswith(pat) or f"/{pat}" in f"/{path}":
                return True
        elif fnmatch.fnmatch(path, pat) or fnmatch.fnmatch(Path(path).name, pat):
            return True
    return False


@dataclass
class Facts:
    """What the checks found about a branch: the input of its risk score."""

    tests: str
    review: str
    files: list[dict[str, Any]]
    deleted: list[str]
    conflict: bool
    commits: int


def _high_risks(p: Project, f: Facts, lines: int, paths: list[str]) -> list[str]:
    sensitive = [x for x in paths if is_sensitive(x, p.get("integrate", "sensitive"))]
    reasons = [
        (f.commits == 0 or not f.files, "the branch has no changes"),
        (f.tests == "fail", "tests fail"),
        (f.conflict, "conflicts with main"),
        (bool(sensitive), "touches sensitive paths: " + ", ".join(sensitive[:5])),
        (not f.review.strip().lower().startswith("clean"), "review not clean: " + (f.review.strip()[:120] or "no review reported")),
        (lines > p.get("integrate", "medium_lines"), f"{lines} lines changed"),
    ]
    return [why for hit, why in reasons if hit]


def _medium_risks(p: Project, f: Facts, lines: int, paths: list[str]) -> list[str]:
    reasons = [
        (p.get("integrate", "small_lines") < lines <= p.get("integrate", "medium_lines"), f"{lines} lines changed"),
        (f.tests == "none", "no test command to run"),
        (bool(f.deleted), "deletes files: " + ", ".join(f.deleted[:5])),
        (any(fnmatch.fnmatch(Path(x).name, pat) for x in paths for pat in DEPENDENCY_FILES), "changes dependencies"),
        (len(paths) > 8, f"{len(paths)} files"),
    ]
    return [why for hit, why in reasons if hit]


def score(p: Project, f: Facts) -> tuple[str, list[str]]:
    """(risk, reasons): high beats medium beats low; each reason says what raised it."""
    lines = sum(x["added"] + x["deleted"] for x in f.files)
    paths = [x["path"] for x in f.files]
    high, medium = _high_risks(p, f, lines, paths), _medium_risks(p, f, lines, paths)
    risk = "high" if high else "medium" if medium else "low"
    return risk, high + medium or ["small, tests green, review clean"]


def check(p: Project, iid: str) -> dict[str, Any]:
    """Checks an issue's branch and stores the result in its `check`."""
    d = backlog.load(p)
    i = backlog.get(d, iid)
    branch = i.get("branch", "")
    main = p.get("project", "main")
    commits = int(git(p.repo, "rev-list", "--count", f"{main}..{branch}", check=False).stdout.strip() or 0)
    tests = tests_at(p, git(p.repo, "rev-parse", branch).stdout.strip(), iid.lower())
    files = diff_stat(p, branch)
    deleted = deleted_files(p, branch)
    v: backlog.Review | None = i.get("review")
    review = "" if not v else "clean" if v["high"] + v["medium"] == 0 else f"open after round {v['round']}: H {v['high']}, M {v['medium']}"
    risk, reasons = score(p, Facts(tests, review, files, deleted, conflicts(p, branch), commits))
    c = {
        "tests": tests,
        "files": len(files),
        "lines": sum(f["added"] + f["deleted"] for f in files),
        "paths": [f["path"] for f in files][:30],
        "commits": commits,
        "risk": risk,
        "reasons": reasons,
        "head": git(p.repo, "rev-parse", branch).stdout.strip(),
        "main": git(p.repo, "rev-parse", main).stdout.strip(),
        "conflict": conflicts(p, branch),
        "at": util.now(),
    }
    backlog.update(p, iid, check=c)
    backlog.event(p, iid, f"checked: tests {tests}, {c['files']} files, {c['lines']} lines, risk {risk}")
    return c


def merges_alone(p: Project, risk: str) -> bool:
    return risk in AUTONOMY.get(p.get("integrate", "autonomy"), ())


def accept(p: Project, iid: str, by: str = "owner") -> str:
    """Records the decision to integrate; it merges nothing. The loop's merge step does the merge (`merge_accepted`)."""
    providers.get(p)  # a bad [integrate] provider/merge fails here, clearly
    d = backlog.load(p)
    i = backlog.get(d, iid)
    if i["status"] != "in review":
        raise IntegrateError(f"{iid} is {i['status']}, not in the integration queue")
    head = git(p.repo, "rev-parse", i["branch"]).stdout.strip()
    checked: backlog.Check = i.get("check") or {}
    if checked.get("head") != head:
        raise IntegrateError(f"{i['branch']} moved since it was checked: wait for the next check")
    backlog.update(p, iid, accepted={"by": by, "head": head, "at": util.now()})
    backlog.event(p, iid, f"accepted by {by}: waiting for the merge")
    return head


def merge_accepted(p: Project, iid: str) -> str:
    """The one place that merges: asks the provider to merge an accepted issue (once), then closes the issue, removing
    its worktree and branch, only when the provider shows the merge done in main. Returns what happened."""
    provider = providers.get(p)
    i = backlog.get(backlog.load(p), iid)
    acc = i.get("accepted")
    if i["status"] != "in review" or not acc:
        raise IntegrateError(f"{iid} is not accepted")
    head = git(p.repo, "rev-parse", i["branch"]).stdout.strip()
    if head != acc["head"]:
        backlog.update(p, iid, accepted=None)
        raise IntegrateError(f"{i['branch']} moved after it was accepted: accept again after the next check")
    change = {"id": iid, "title": i["title"], "branch": i["branch"], "head": head, "merge": i.get("merge", "")}
    if not change["merge"] and i.get("merging"):  # a crash after the merge, before its ref was stored: adopt it
        found = git(p.repo, "log", "--format=%H", f"--grep=^Integrate {iid}: ", f"{i['merging']}..{p.get('project', 'main')}", check=False).stdout.split()
        if found:
            change["merge"] = found[0]
            backlog.update(p, iid, merge=found[0])
    if not change["merge"]:
        backlog.update(p, iid, merging=git(p.repo, "rev-parse", p.get("project", "main")).stdout.strip())
        try:
            ref = provider.merge(change, providers.merge_strategy(p))
        except Exception as e:  # a platform's own errors are an integration error
            if isinstance(e, providers.ConfigError):
                raise
            raise IntegrateError(str(e)) from None
        backlog.update(p, iid, merge=ref)
        backlog.event(p, iid, f"merged as {ref[:9]} ({providers.merge_strategy(p)}); waiting to see it in {p.get('project', 'main')}")
        change["merge"] = ref
    if not provider.state(change)["merged"]:
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
        f"integrated, accepted by {by}, as {change['merge'][:9]}",
        result=report.get("summary", ""),
        commit=change["merge"],
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
