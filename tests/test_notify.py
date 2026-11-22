"""[owner] notify: the loop's last step runs the owner's command once per need `watch.needs` finds new, the line as its last
argument, in the background, with a time limit. Needs are the ones `foreman watch` prints (tests/test_watch.py)."""

from pathlib import Path

import pytest
from fakerepo import FakeRepo
from helpers import Env
from test_watch import in_review, parent, row, task

from foreman import util
from foreman.core.project.config import Project, ProjectError
from foreman.core.tasks import backlog
from foreman.loop import tick
from foreman.providers import registry
from foreman.providers.claude.rows import ClaudeRows
from foreman.providers.runner import NO_ROWS, Rows
from foreman.steps import notify, watch
from foreman.steps.health import health


def script(e: Env, name: str, body: str) -> Path:
    f = e.bin / name
    f.write_text(f"#!/bin/sh\n{body}\n")
    f.chmod(0o755)
    return f


def notifying(e: Env, command: str) -> Project:
    with (e.home / "foreman.toml").open("a") as f:
        f.write(f"[owner]\nnotify = {command!r}\n")
    e.p = Project.load(e.home)
    return e.p


def recorder(e: Env) -> Path:
    return script(e, "notify", f'echo "$(printf \'%s\\t\' "$@")" >> {e.bin / "notes.txt"}')  # one write: the commands run at once


def notes(e: Env) -> list[list[str]]:
    f = e.bin / "notes.txt"
    return [ln.rstrip("\t").split("\t") for ln in f.read_text().splitlines()] if f.exists() else []


def settle() -> None:
    for t in list(util.BACKGROUND.values()):
        t.join(timeout=10)


def first_run(e: Env, command: str | None = None) -> Project:
    """A project with the command, and the baseline run on an empty backlog."""
    p = notifying(e, command or str(recorder(e)))
    notify.run(p, NO_ROWS)
    return p


def errors(p: Project) -> list[dict[str, object]]:
    return util.read_jsonl(p.state / "errors.jsonl")


def test_a_new_need_runs_the_command_once_with_its_line_last(fake_git: FakeRepo):
    e = Env(repo=fake_git)
    p = first_run(e, f"{recorder(e)} --to bot")
    tid = task(e, blocked="x")
    notify.run(p, NO_ROWS)
    settle()
    assert notes(e) == [["--to", "bot", watch.needs(p, NO_ROWS)[f"blocked:{tid}"]]]


def test_every_kind_of_need_is_notified(fake_git: FakeRepo):
    e = Env(repo=fake_git)
    p = first_run(e)
    t = task(e)
    registry.backlog(p).ask(t, "agent", "which way?")
    in_review(e)
    task(e, blocked="x")
    util.write_json(p.state / "ci.json", {"result": "fail", "since": "t1"})
    parent(e)
    rows = ClaudeRows({"s1": row()})
    found = watch.needs(p, rows)
    assert len(found) == 5
    notify.run(p, rows)
    settle()
    assert {n[-1] for n in notes(e)} == set(found.values()) and len(notes(e)) == 5


def test_each_need_is_notified_once_across_restarts(fake_git: FakeRepo):
    e = Env(repo=fake_git)
    p = first_run(e)
    tid = task(e, blocked="x")
    notify.run(p, NO_ROWS)
    settle()
    p = Project.load(e.home)  # a restart
    notify.run(p, NO_ROWS)
    notify.run(p, NO_ROWS)
    settle()
    assert len(notes(e)) == 1
    registry.backlog(p).update(tid, blocked="")
    notify.run(p, NO_ROWS)
    registry.backlog(p).update(tid, blocked="x")
    notify.run(p, NO_ROWS)
    settle()
    assert len(notes(e)) == 2


def test_only_the_accepts_waiting_at_first_start_are_notified(fake_git: FakeRepo):
    e = Env(repo=fake_git)
    tid = in_review(e)
    task(e, blocked="x")
    p = notifying(e, str(recorder(e)))
    notify.run(p, NO_ROWS)
    settle()
    assert [n[-1] for n in notes(e)] == [watch.needs(p, NO_ROWS)[f"accept:{tid}"]]


def test_an_unreadable_listing_does_not_notify_a_parent_again(fake_git: FakeRepo):
    e = Env(repo=fake_git)
    p = first_run(e)
    parent(e)
    idle: Rows = ClaudeRows({"s1": row()})
    for rows in (idle, NO_ROWS, idle):
        notify.run(p, rows)
        settle()
        assert len(notes(e)) == 1


@pytest.mark.parametrize("failing", ["exit 3", None])
def test_a_failing_or_missing_command_is_one_error_line_and_not_retried(fake_git: FakeRepo, failing: str | None):
    e = Env(repo=fake_git)
    cmd = str(script(e, "fails", f"echo x >> {e.bin / 'ran.txt'}; {failing}")) if failing else str(e.bin / "nope")
    p = first_run(e, cmd)
    task(e, blocked="x")
    notify.run(p, NO_ROWS)
    settle()
    assert len(errors(p)) == 1
    notify.run(p, NO_ROWS)
    settle()
    assert len(errors(p)) == 1
    if failing:
        assert (e.bin / "ran.txt").read_text() == "x\n"


def test_a_slow_command_is_cut_at_the_limit_and_does_not_hold_the_step(fake_git: FakeRepo, monkeypatch: pytest.MonkeyPatch):
    e = Env(repo=fake_git)
    monkeypatch.setattr(notify, "LIMIT_S", 1)
    p = first_run(e, str(script(e, "slow", "sleep 5")))
    task(e, blocked="x")
    notify.run(p, NO_ROWS)
    t = next(t for k, t in util.BACKGROUND.items() if k.startswith(f"{p.state}:notify:"))
    assert t.is_alive()
    settle()
    assert len(errors(p)) == 1


def test_the_tick_runs_the_notify_step(fake_git: FakeRepo):
    e = Env(repo=fake_git)
    p = notifying(e, str(recorder(e)))
    tick.tick(p)
    task(e, blocked="x")
    tick.tick(p)
    settle()
    assert len(notes(e)) == 1


@pytest.mark.parametrize("owner", ["", "[owner]\nnotify = ''\n"])
def test_without_the_key_no_command_runs(fake_git: FakeRepo, owner: str):
    e = Env(repo=fake_git)
    with (e.home / "foreman.toml").open("a") as f:
        f.write(owner)
    p = Project.load(e.home)
    task(e, blocked="x")
    notify.run(p, NO_ROWS)
    notify.run(p, NO_ROWS)
    settle()
    assert notes(e) == [] and not (p.state / "notified.json").exists()


@pytest.mark.parametrize("value", ["5", '["a"]', "true"])
def test_a_notify_that_is_not_a_text_is_refused_at_load(fake_git: FakeRepo, value: str):
    e = Env(repo=fake_git)
    with (e.home / "foreman.toml").open("a") as f:
        f.write(f"[owner]\nnotify = {value}\n")
    with pytest.raises(ProjectError, match="notify"):
        Project.load(e.home)


def test_a_need_that_returns_while_its_previous_command_runs_is_notified_again(fake_git: FakeRepo):
    e = Env(repo=fake_git)
    p = first_run(e, str(script(e, "slow", f"sleep 1; echo ran >> {e.bin / 'ran.txt'}")))
    tid = task(e, blocked="x")
    notify.run(p, NO_ROWS)
    registry.backlog(p).update(tid, blocked="")
    notify.run(p, NO_ROWS)
    registry.backlog(p).update(tid, blocked="x")
    notify.run(p, NO_ROWS)
    settle()
    assert (e.bin / "ran.txt").read_text() == "ran\nran\n"


def findings(p: Project) -> list[dict[str, object]]:
    return [i for i in backlog.tasks(registry.backlog(p).load()) if i["status"] == "triage" and "notify" in str(i["title"])]


def test_a_command_failing_on_every_need_is_one_health_finding(fake_git: FakeRepo):
    e = Env(repo=fake_git)
    cmd = str(script(e, "fails", 'for a; do last=$a; done; echo "no --from for $last" >&2; exit 3'))
    p = first_run(e, cmd)
    for _ in range(3):
        task(e, blocked="x")
    notify.run(p, NO_ROWS)
    settle()
    health.run(p, NO_ROWS)
    [found] = findings(p)
    assert cmd in str(found) and "exited 3" in str(found)
    task(e, blocked="x")
    notify.run(p, NO_ROWS)
    settle()
    health.run(p, NO_ROWS)
    assert len(findings(p)) == 1


@pytest.mark.parametrize("kind", ["codes", "unsplittable"])
def test_failures_of_another_kind_do_not_file_another_finding(fake_git: FakeRepo, kind: str):
    e = Env(repo=fake_git)
    cmd = str(script(e, "fails", f"echo . >> {e.bin / 'n.txt'}; exit $(wc -l < {e.bin / 'n.txt'})")) if kind == "codes" else 'say "hello'
    p = first_run(e, cmd)
    for _ in range(3):
        task(e, blocked="x")
    notify.run(p, NO_ROWS)
    settle()
    health.run(p, NO_ROWS)
    assert len(findings(p)) == 1
