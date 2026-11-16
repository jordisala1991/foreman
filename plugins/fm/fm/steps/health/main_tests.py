import re
from typing import Any

from ... import util
from ...core.project.config import Project
from ...core.rules import decide
from ...core.tasks import backlog
from ...services.delivery import check, git
from .checks import Finding, agents, answers_undelivered, finding, login_check, loop_dead, loop_errors, orphan_worktrees


def failing_step(tail: str, command: str) -> str:
    """The target of the first make error line in `tail`; the command itself when there is none."""
    m = re.search(r"\*\*\* \[(?:[^\]\s]*:\d+: )?([^\]]+)\] Error", tail)
    return m.group(1) if m else command


def record_main_tests(p: Project, head: str) -> None:
    """Runs the tests on `head` and writes ci.json. `since` stamps when the current failing episode began."""
    result, tail = check.tests_at(p, head, "main")
    before = util.read_json(p.state / "ci.json", {})
    since = before.get("since") if before.get("result") == "fail" == result and before.get("since") else util.now()
    util.write_json(p.state / "ci.json", {"head": head, "at": util.now(), "result": result, "tail": tail, "since": since})


def main_tests(p: Project, run: bool = True) -> list[Finding]:
    """With `run` False (the panel, `foreman health`) it only reads the last result; the loop runs the tests."""
    cmd = p.get("merge", "test")
    if not cmd:
        return []
    head = git.git(p.repo, "rev-parse", p.get("project", "main"), check=False).out.strip()
    ci = util.read_json(p.state / "ci.json", {})
    due = decide.main_tests_due(ci.get("head"), head, util.age_s(ci.get("at")), int(p.get("health", "ci_every_minutes")))
    if due and run:  # slow: in the background, the tick waits a little and goes on; the result lands in ci.json
        t = util.start_background(f"{p.state}:main-tests", p.state / "errors.jsonl", lambda: record_main_tests(p, head))
        if t:
            t.join(util.BACKGROUND_WAIT_S)
            ci = util.read_json(p.state / "ci.json", {})
    if ci.get("result") != "fail":
        return []
    head, tail = ci.get("head") or head, ci.get("tail") or "(no output recorded)"
    meaning = "The project's test command fails on main: what merges next lands on a broken base."
    evidence = f"Command: `{cmd}`\nCommit: {head[:12]}\nFailing step: `{failing_step(tail, cmd)}`\nLast lines of its output:\n{tail}"
    return [finding(f"ci:{ci.get('since') or head[:12]}", "Tests fail on main", meaning, evidence)]


def findings(p: Project, rows: dict[str, Any], outside: bool = False) -> list[Finding]:
    out = [*loop_dead(p), *login_check(p)] if outside else []
    checks = (
        lambda: agents(p, rows),
        lambda: answers_undelivered(p),
        lambda: orphan_worktrees(p),
        lambda: main_tests(p, run=not outside),
        lambda: loop_errors(p),
    )
    for step in checks:
        try:
            out += step()
        except Exception as e:
            meaning = "A health check raised an exception, so its findings are missing."
            out.append(finding(f"health:{e!r}"[:80], "A health check fails", meaning, repr(e)))
    return out


def run(p: Project, rows: dict[str, Any], outside: bool = False) -> list[str]:
    """Files the new findings; the ids filed."""
    found = {f["key"]: f for f in findings(p, rows, outside)}
    filed: list[str] = []
    with util.locked_json(p.state / "seen.json", {}) as seen:
        for key, f in found.items():
            if key not in seen:
                tid = backlog.add(p, f["title"], detail=f["detail"], status="triage", source=f"health:{key}")
                if tid:
                    filed.append(tid)
            seen[key] = util.now()
        for key in [k for k in seen if k not in found]:
            del seen[key]
    return filed
