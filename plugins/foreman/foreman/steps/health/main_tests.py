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
    """Runs the tests on `head` and writes ci.json (`ci-KEY.json` for a repo of several): `result` is
    pass | fail | flaky | none. A failure is run once more on the same head: fail then pass is `flaky` (the first
    run's tail is kept), fail twice is `fail`. `since` stamps when the current failing episode began."""
    name = f"main-{key}" if key else "main"
    result, tail = check.tests_at(p, head, name)
    if result == "fail" and check.tests_at(p, head, name)[0] == "pass":
        result = "flaky"
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
    if ci.get("result") not in ("fail", "flaky"):
        return []
    return [_finding(ci, cmd, key, head)]


def _finding(ci: dict[str, str], cmd: str, key: str, head: str) -> Finding:
    """The finding of a `fail` or `flaky` result: `Tests fail on main` per failing episode, `Flaky check on main` per step."""
    head, tail = ci.get("head") or head, ci.get("tail") or "(no output recorded)"
    step = failing_step(tail, cmd)
    evidence = f"Command: `{cmd}`\nCommit: {head[:12]}\nFailing step: `{step}`\nLast lines of its output:\n{tail}"
    repo = f" (repo {key})" if key else ""
    where = f"{key}:" if key else ""
    if key:
        evidence = f"Repo: {key}\n{evidence}"
    if ci.get("result") == "flaky":
        meaning = (
            "The project's test command failed on main and passed when the same commit ran again: "
            "a check that depends on the machine or on timing, not a broken main."
        )
        return finding(f"flaky:{where}{step}", f"Flaky check on main{repo}: {step}", meaning, evidence, repo=key)
    meaning = "The project's test command fails on main: what merges next lands on a broken base."
    return finding(f"ci:{where}{ci.get('since') or head[:12]}", f"Tests fail on main{repo}", meaning, evidence, repo=key)
