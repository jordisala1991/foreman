"""The runner port (foreman/providers/runner.py): what fm asks of a Runner, checked with a fake one, and the claude adapter
behind the default `[agents] runner = "claude"`."""

import pytest
from fakerepo import FakeRepo
from fakerunner import FakeRows, FakeRunner
from helpers import Env, fm

from foreman import providers, util
from foreman.core.project.config import Project
from foreman.core.tasks import backlog
from foreman.loop import tick
from foreman.loop.launch import launch_free
from foreman.providers import registry
from foreman.providers.claude.rows import ClaudeRows
from foreman.providers.runner import NO_ROWS, SessionRef
from foreman.services.agents import launch, running
from foreman.services.delivery import merge
from foreman.steps import mechanic


def launched(e: Env) -> str:
    tid = backlog.add(e.p, status="backlog", criteria="y", title="x")
    launch_free(e.p)
    return tid


def task(e: Env, tid: str) -> backlog.Task:
    return backlog.get(backlog.load(e.p), tid)


def test_claude_launch_writes_no_tab(fake_git: FakeRepo):
    e = Env(repo=fake_git)
    tid = launched(e)
    i = task(e, tid)
    assert "session_tabs" not in i
    assert "terminal" not in i["agent"] and "pane" not in i["agent"]
    assert i["sessions"] == [i["agent"]["session"]]


def test_launch_records_the_ref(fake_git: FakeRepo, fake_runner: FakeRunner):
    e = Env(repo=fake_git)
    tid = launched(e)
    [(agent, _, _)] = fake_runner.started
    assert agent.tid == tid
    i = task(e, tid)
    assert (i["agent"]["short"], i["agent"]["session"], i["agent"]["terminal"], i["agent"]["pane"]) == ("fk1", "fk1-sid", "T1", "P1")
    assert i["sessions"] == ["fk1-sid"]
    assert i["session_kinds"] == {"fk1-sid": "planning"}
    assert i["session_tabs"] == {"fk1-sid": ["T1", "P1"]}


def test_send_goes_through_the_port(fake_git: FakeRepo, fake_runner: FakeRunner):
    e = Env(repo=fake_git)
    tid = launched(e)
    assert running.send(e.p, task(e, tid), "hi")
    [(ref, message, cwd)] = fake_runner.sent
    assert ref == SessionRef("fk1", "fk1-sid", "T1", "P1", task(e, tid)["agent"]["started"])
    assert (message, cwd) == ("hi", e.repo)


def test_a_failed_send_is_an_event(fake_git: FakeRepo, fake_runner: FakeRunner):
    e = Env(repo=fake_git)
    tid = launched(e)
    fake_runner.fail_send = True
    assert not running.send(e.p, task(e, tid), "hi")
    texts = [ev["text"] for ev in util.read_jsonl(e.p.state / "events.jsonl")]
    assert f"could not reach {task(e, tid)['agent']['name']}: boom" in texts


def test_stop_all_gives_each_old_session_its_tab(fake_git: FakeRepo, fake_runner: FakeRunner):
    e = Env(repo=fake_git)
    tid = launched(e)
    backlog.update(e.p, tid, sessions=["old1", "fk1-sid"], session_tabs={"old1": ["T9", "P9"], "fk1-sid": ["T1", "P1"]})
    merge.stop_all(e.p, task(e, tid))
    assert sorted((r.short, r.terminal, r.pane) for r in fake_runner.stopped) == [("fk1", "T1", "P1"), ("old1", "T9", "P9")]


def test_missing_why_waits_for_a_readable_listing():
    m = {"session": "s", "short": "s", "started": ""}
    assert mechanic.missing_why(m, NO_ROWS) == ""
    assert mechanic.missing_why(m, FakeRows()) == "ended without writing a diagnosis"


def with_runner(e: Env, value: str) -> Project:
    f = e.home / "foreman.toml"
    f.write_text(f.read_text().replace("[agents]\n", f'[agents]\nrunner = "{value}"\n'))
    return Project.load(e.home)


def test_unknown_runner_names_it():
    p = with_runner(Env(), "nope")
    with pytest.raises(providers.ConfigError, match="'nope'"):
        registry.runner(p)


def test_a_tick_with_an_unknown_runner_goes_on_recorded():
    p = with_runner(Env(), "nope")
    tick.tick(p)
    rows = [r for r in util.read_jsonl(p.state / "errors.jsonl") if r["step"] == "runner"]
    assert len(rows) == 1 and "nope" in rows[0]["error"]
    assert any("nope" in str(i) for i in backlog.tasks(backlog.load(p)))  # health filed it


def test_a_session_found_late_keeps_its_tab(fake_git: FakeRepo):
    e = Env(repo=fake_git)
    tid = backlog.add(e.p, status="ready", criteria="y", title="x")
    backlog.update(e.p, tid, agent={"short": "aa", "terminal": "T1", "pane": "P1", "kind": "responsible"})
    launch.fill_sessions(e.p, ClaudeRows({"aa-sid": {"id": "aa"}}))
    assert task(e, tid)["session_tabs"] == {"aa-sid": ["T1", "P1"]}


def test_health_command_names_an_unknown_runner():
    e = Env()
    with_runner(e, "nope")
    with pytest.raises(SystemExit, match="nope"):
        fm(e.home, "health")
