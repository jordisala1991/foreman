"""Main's tests run again once when they fail: fail then pass is a flaky finding, not a broken main (fake repo)."""

import json
from pathlib import Path

from fakerepo import FakeRepo
from helpers import Env

from foreman.core.tasks import backlog
from foreman.providers import registry
from foreman.providers.runner import NO_ROWS
from foreman.steps import watch
from foreman.steps.health import health, main_tests


def flaky_cmd(flag: Path) -> str:
    """Fails (printing boom) the first time it runs, passes after."""
    return f"test -e {flag} || {{ touch {flag}; echo boom >&2; false; }}"


def setup(fake_git: FakeRepo, test: str) -> Env:
    return Env(test=test, repo=fake_git)


def ci(e: Env) -> dict[str, str]:
    return json.loads((e.home / "state/ci.json").read_text())


def test_a_pass_runs_once(fake_git: FakeRepo, tmp_path: Path):
    count = tmp_path / "count"
    e = setup(fake_git, f"echo x >> {count}")
    assert main_tests.main_tests(e.p) == []
    assert len(count.read_text().splitlines()) == 1
    assert ci(e)["result"] == "pass"


def test_fail_then_fail_is_tests_fail_on_main(fake_git: FakeRepo, tmp_path: Path):
    count = tmp_path / "count"
    e = setup(fake_git, f"echo x >> {count}; false")
    assert [f["title"] for f in main_tests.main_tests(e.p)] == ["Tests fail on main"]
    assert len(count.read_text().splitlines()) == 2
    assert ci(e)["result"] == "fail"


def test_fail_then_pass_is_a_flaky_finding(fake_git: FakeRepo, tmp_path: Path):
    flag = tmp_path / "flag"
    cmd = flaky_cmd(flag)
    e = setup(fake_git, cmd)
    (f,) = main_tests.main_tests(e.p)
    assert f["title"] == f"Flaky check on main: {cmd}"
    assert f["key"] == f"flaky:{cmd}"
    assert "boom" in f["detail"]
    assert ci(e)["result"] == "flaky"
    assert "boom" in ci(e)["tail"]


def test_a_flaky_result_is_not_a_need_of_the_owner(fake_git: FakeRepo, tmp_path: Path):
    e = setup(fake_git, flaky_cmd(tmp_path / "flag"))
    main_tests.main_tests(e.p)
    assert ci(e)["result"] == "flaky"
    assert watch.needs(e.p) == {}


def test_a_flaky_step_is_filed_as_one_task(fake_git: FakeRepo, tmp_path: Path):
    e = setup(fake_git, flaky_cmd(tmp_path / "flag"))
    health.run(e.p, NO_ROWS)
    health.run(e.p, NO_ROWS)
    (t,) = [i for i in backlog.tasks(registry.backlog(e.p).load()) if i["status"] == "triage"]
    assert t["title"].startswith("Flaky check on main: ")
