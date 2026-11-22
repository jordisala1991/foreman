"""Recovery (steps/recover.py, core/tasks/recovery.py): an active run whose session ended, never started or went silent is
closed as failed with its reason, then resumed or started again up to two runs in a row, then handed to the owner."""

import json
import os
from pathlib import Path

import pytest
from fakerepo import FakeRepo
from fakerunner import FakeRunner
from helpers import Env

from foreman import util
from foreman.core.tasks import backlog, recovery, runs
from foreman.core.tasks.recovery import Facts
from foreman.loop import tick
from foreman.loop.launch import launch_free
from foreman.providers import registry
from foreman.providers.runner import NO_ROWS, State
from foreman.services.agents.launch import launch_reviewer
from foreman.steps import parents, recover, watch

OLD = "2000-01-01 00:00:00"


WORKING = Facts(known=True, ended=False, restarted=False, idle=False, quiet_s=0, age_s=0)


def run_of(outcome: str | None) -> runs.Run:
    return runs.Run("R-1", "TY-1", "responsible", 1, "s", "", "m", "e", OLD, OLD if outcome else None, outcome)


def test_reason_table():
    assert recovery.reason("responsible", WORKING._replace(ended=True)) == "session_ended"
    assert recovery.reason("responsible", WORKING._replace(ended=True, restarted=True)) == "runner_unavailable"
    assert recovery.reason("planning", WORKING._replace(idle=True, quiet_s=600)) == "silent_too_long"
    assert recovery.reason("parent", WORKING._replace(idle=True, quiet_s=600)) == ""
    assert recovery.reason("responsible", WORKING._replace(known=False, age_s=121)) == "launch_failed"
    assert recovery.reason("responsible", WORKING._replace(known=False, age_s=10)) == ""
    assert recovery.reason("responsible", WORKING) == ""


def test_streak_and_next_step():
    assert recovery.streak([run_of("failed"), run_of("failed")]) == 2
    assert recovery.streak([run_of("done"), run_of("failed")]) == 1
    assert recovery.streak([run_of("failed"), run_of("canceled")]) == 0
    assert recovery.next_step("session_ended", 1) == "retry"
    assert recovery.next_step("session_ended", 2) == "owner"
    assert recovery.next_step("launch_failed", 1) == "owner"


def task(e: Env, tid: str) -> backlog.Task:
    return backlog.get(registry.backlog(e.p).load(), tid)


def launched(e: Env, r: FakeRunner, status: str = "ready") -> str:
    """A task whose agent runs (WORKING)."""
    tid = registry.backlog(e.p).add("x", status=status, criteria="y")
    launch_free(e.p)
    r.states[str(task(e, tid)["agent"]["session"])] = State.WORKING
    return tid


def of(e: Env, tid: str, kind: str = "responsible") -> list[runs.Run]:
    return [r for r in runs.load(e.p) if r.task == tid and r.kind == kind]


def end(r: FakeRunner, e: Env, tid: str, role: str = "agent") -> None:
    r.states[str(task(e, tid)[role]["session"])] = State.ENDED


@pytest.fixture
def e(fake_git: FakeRepo, fake_runner: FakeRunner) -> Env:
    return Env(slots=2, repo=fake_git)


def test_a_dead_run_is_closed_failed_with_its_reason(e: Env, fake_runner: FakeRunner):
    tid = launched(e, fake_runner)
    end(fake_runner, e, tid)
    recover.run(e.p, fake_runner.rows())
    first = of(e, tid)[0]
    assert (first.outcome, first.failure_reason) == ("failed", "session_ended") and first.ended


def test_an_unreadable_listing_closes_no_run(e: Env, fake_runner: FakeRunner):
    tid = launched(e, fake_runner)
    end(fake_runner, e, tid)
    recover.run(e.p, NO_ROWS)
    assert [r.active for r in of(e, tid)] == [True]


def test_a_blocked_or_waiting_task_is_left_alone(e: Env, fake_runner: FakeRunner):
    blocked, waiting = launched(e, fake_runner), launched(e, fake_runner)
    registry.backlog(e.p).update(blocked, blocked="by hand")
    registry.backlog(e.p).ask(waiting, "x", "A or B?")
    end(fake_runner, e, blocked)
    end(fake_runner, e, waiting)
    recover.run(e.p, fake_runner.rows())
    assert [r.active for r in runs.load(e.p)] == [True, True]


def test_a_restarted_runner_says_runner_unavailable(e: Env, fake_runner: FakeRunner):
    tid = launched(e, fake_runner)
    fake_runner.epoch = "1"
    recover.run(e.p, fake_runner.rows())
    fake_runner.epoch = "2"
    end(fake_runner, e, tid)
    recover.run(e.p, fake_runner.rows())
    assert of(e, tid)[0].failure_reason == "runner_unavailable"


def test_a_session_the_runner_cannot_resume_is_started_again(e: Env, fake_runner: FakeRunner):
    planned = launched(e, fake_runner, "backlog")
    assert task(e, planned)["status"] == "refining"
    reviewed = registry.backlog(e.p).add("y", status="ready", criteria="y")
    registry.backlog(e.p).set_status(reviewed, "in review", worktree=str(e.repo), branch="b")
    launch_reviewer(e.p, task(e, reviewed), "tip", "hunter")
    fake_runner.fail_send = True
    end(fake_runner, e, planned)
    end(fake_runner, e, reviewed, "hunter")
    recover.run(e.p, fake_runner.rows())
    assert task(e, planned)["status"] == "backlog"
    assert "hunter" not in task(e, reviewed)
    assert [r.failure_reason for r in of(e, reviewed, "hunter")] == ["session_ended"]


def test_a_run_that_fails_after_a_done_one_is_retried(e: Env, fake_runner: FakeRunner):
    tid = launched(e, fake_runner)
    runs.end(e.p, tid, ("responsible",), "done")
    sid = str(task(e, tid)["agent"]["session"])
    registry.backlog(e.p).set_status(tid, "in progress")
    runs.start(e.p, tid, "responsible", runs.Session(sid, "", "m", "e"))
    end(fake_runner, e, tid)
    recover.run(e.p, fake_runner.rows())
    assert len(fake_runner.sent) == 1
    assert [(r.outcome, r.attempt) for r in of(e, tid)] == [("done", 1), ("failed", 2), (None, 3)]


def test_a_launch_that_never_listed_goes_to_the_owner_at_once(e: Env, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch):
    tid = launched(e, fake_runner)
    registry.backlog(e.p).update(tid, agent={**task(e, tid)["agent"], "session": ""})
    monkeypatch.setattr(recovery, "LAUNCH_GRACE_S", -1)  # the run started longer ago than the grace
    recover.run(e.p, fake_runner.rows())
    assert "launch_failed" in task(e, tid)["blocked"]
    assert fake_runner.sent == []


def test_a_dead_parent_is_not_resumed(e: Env, fake_runner: FakeRunner):
    P = registry.backlog(e.p).add("parent", status="backlog", criteria="c")
    registry.backlog(e.p).add("s", parent=P, criteria="c", status="triage")
    parents.run(e.p, fake_runner.rows())
    end(fake_runner, e, P)
    recover.run(e.p, fake_runner.rows())
    assert (of(e, P, "parent")[0].failure_reason, fake_runner.sent) == ("session_ended", [])
    parents.run(e.p, fake_runner.rows())
    assert len(fake_runner.started) == 2
    assert [(r.attempt, r.active) for r in of(e, P, "parent")] == [(1, False), (2, True)]


def transcript(e: Env, sid: str, said: str) -> None:
    f = Path(os.environ["FOREMAN_CLAUDE_PROJECTS"]) / "proj" / f"{sid}.jsonl"
    f.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "an  earlier\nthought"}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": said}]}},
        {"type": "user", "message": {"content": "a tool result"}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash"}]}},
    ]
    f.write_text("\n".join(json.dumps(x) for x in lines) + "\n{not json")


def test_a_killed_responsible_session_is_retried_once_then_handed_to_the_owner(e: Env, fake_runner: FakeRunner):
    tid = launched(e, fake_runner)
    sid = str(task(e, tid)["agent"]["session"])
    assert [r.attempt for r in of(e, tid)] == [1]
    end(fake_runner, e, tid)
    recover.run(e.p, fake_runner.rows())
    first, second = of(e, tid)
    assert (first.outcome, first.failure_reason) == ("failed", "session_ended")
    assert (second.attempt, second.active, second.session) == (2, True, sid)
    [(_, message, _)] = fake_runner.sent
    assert "session_ended" in message and not task(e, tid).get("blocked")
    transcript(e, sid, "I was   halfway through\nthe tests")
    end(fake_runner, e, tid)
    recover.run(e.p, fake_runner.rows())
    assert [(r.outcome, r.failure_reason) for r in of(e, tid)] == [("failed", "session_ended")] * 2
    blocked = task(e, tid)["blocked"]
    assert blocked.startswith("its agent stopped") and "session_ended" in blocked and "I was halfway through the tests" in blocked
    assert [n.key for n in watch.found(e.p, fake_runner.rows()) if n.key.startswith("blocked:")] == [f"blocked:{tid}"]
    events = len(util.read_jsonl(e.p.state / "events.jsonl"))
    for _ in range(2):
        recover.run(e.p, fake_runner.rows())
    assert len(util.read_jsonl(e.p.state / "events.jsonl")) == events and len(fake_runner.sent) == 1


def test_a_loop_restart_recovers_both_open_runs(e: Env, fake_runner: FakeRunner):
    a, b = launched(e, fake_runner), launched(e, fake_runner)
    end(fake_runner, e, a)
    end(fake_runner, e, b)
    tick.tick(e.p)
    for tid in (a, b):
        assert [(r.outcome, r.failure_reason, r.attempt) for r in of(e, tid)] == [("failed", "session_ended", 1), (None, "", 2)]
