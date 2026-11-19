import re

from ... import util
from ...core.project.config import Project
from ...core.rules import decide
from ...providers import registry
from ...services.delivery import check
from .checks import Finding, finding, usable


def failing_step(tail: str, command: str) -> str:
    """The target of the first make error line in `tail`; the command itself when there is none."""
    m = re.search(r"\*\*\* \[(?:[^\]\s]*:\d+: )?([^\]]+)\] Error", tail)
    return m.group(1) if m else command


def record_main_tests(p: Project, head: str, key: str = "") -> None:
    """Runs the tests on `head` and writes ci.json (`ci-KEY.json` for a repo of several). `since` stamps when the
    current failing episode began."""
    result, tail = check.tests_at(p, head, f"main-{key}" if key else "main")
    file = p.state / (f"ci-{key}.json" if key else "ci.json")
    before = util.read_json(file, {})
    since = before.get("since") if before.get("result") == "fail" == result and before.get("since") else util.now()
    util.write_json(file, {"head": head, "at": util.now(), "result": result, "tail": tail, "since": since})


def main_tests(p: Project, run: bool = True) -> list[Finding]:
    """With `run` False (the panel, `foreman health`) it only reads the last result; the loop runs the tests."""
    return [f for key, v in usable(p) for f in _repo_tests(p, v, key, run)]


def _repo_tests(p: Project, v: Project, key: str, run: bool) -> list[Finding]:
    cmd = v.get("merge", "test")
    if not cmd:
        return []
    file = p.state / (f"ci-{key}.json" if key else "ci.json")
    head = registry.repo(v).find(v.get("project", "main"))
    ci = util.read_json(file, {})
    due = decide.main_tests_due(ci.get("head"), head, util.age_s(ci.get("at")), int(p.get("health", "ci_every_minutes")))
    if due and run:  # slow: in the background, the tick waits a little and goes on; the result lands in the file
        t = util.start_background(f"{p.state}:main-tests" + (f"-{key}" if key else ""), p.state / "errors.jsonl", lambda: record_main_tests(v, head, key))
        if t:
            t.join(util.BACKGROUND_WAIT_S)
            ci = util.read_json(file, {})
    if ci.get("result") != "fail":
        return []
    head, tail = ci.get("head") or head, ci.get("tail") or "(no output recorded)"
    meaning = "The project's test command fails on main: what merges next lands on a broken base."
    evidence = f"Command: `{cmd}`\nCommit: {head[:12]}\nFailing step: `{failing_step(tail, cmd)}`\nLast lines of its output:\n{tail}"
    since = ci.get("since") or head[:12]
    if not key:
        return [finding(f"ci:{since}", "Tests fail on main", meaning, evidence)]
    return [finding(f"ci:{key}:{since}", f"Tests fail on main (repo {key})", meaning, f"Repo: {key}\n{evidence}", repo=key)]
