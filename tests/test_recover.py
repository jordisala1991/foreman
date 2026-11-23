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
from foreman.core.tasks import backlog, questions, recovery, runs
from foreman.core.tasks.recovery import Facts
from foreman.loop import tick
from foreman.loop.launch import launch_free
from foreman.providers import registry
from foreman.providers.runner import NO_ROWS, State
from foreman.services.agents.launch import launch_reviewer
from foreman.steps import parents, recover, talk, watch
from foreman.ui.panel import views

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


def test_turn_over_table():
    idle = WORKING._replace(idle=True)
    assert not recovery.turn_over(idle._replace(quiet_s=59))
    assert recovery.turn_over(idle._replace(quiet_s=60))
    assert not recovery.turn_over(idle._replace(quiet_s=60, ended=True))
    assert not recovery.turn_over(idle._replace(quiet_s=60, known=False))


def a_parent(e: Env, r: FakeRunner, sub: str = "triage") -> str:
    """A parent whose agent runs its first turn (WORKING), with one subtask in the status `sub`."""
    P = registry.backlog(e.p).add("parent", status="backlog", criteria="c")
    S = registry.backlog(e.p).add("s", parent=P, criteria="c", status="triage")
    if sub != "triage":
        registry.backlog(e.p).update(S, status=sub)  # closed before the agent: no wakeup owed
    parents.run(e.p, r.rows())
    r.states[str(task(e, P)["agent"]["session"])] = State.WORKING
    return P


def idles(r: FakeRunner, e: Env, P: str) -> None:
    """Its session idles, started long ago and with an old transcript."""
    a = task(e, P)["agent"]
    r.states[str(a["session"])] = State.IDLE
    registry.backlog(e.p).update(P, agent={**a, "started": OLD})


def test_a_parent_whose_session_idles_ends_its_run_done(e: Env, fake_runner: FakeRunner):
    P = a_parent(e, fake_runner)
    idles(fake_runner, e, P)
    transcript(e, str(task(e, P)["agent"]["session"]), "done for now")
    os.utime(next(Path(os.environ["FOREMAN_CLAUDE_PROJECTS"]).rglob("*.jsonl")), (0, 0))
    for _ in range(2):
        recover.run(e.p, fake_runner.rows())
    assert [(r.outcome, r.failure_reason) for r in of(e, P, "parent")] == [("done", "")]
    assert fake_runner.sent == [] and not task(e, P).get("blocked")


def test_a_parent_waiting_for_the_owner_ends_its_run_done_once_idle(e: Env, fake_runner: FakeRunner):
    P = a_parent(e, fake_runner)
    registry.backlog(e.p).ask(P, "agent", "which first?")
    idles(fake_runner, e, P)
    recover.run(e.p, fake_runner.rows())
    assert [r.outcome for r in of(e, P, "parent")] == ["done"]


def test_a_parent_whose_session_ended_mid_turn_is_resumed(e: Env, fake_runner: FakeRunner):
    P = a_parent(e, fake_runner)
    end(fake_runner, e, P)
    recover.run(e.p, fake_runner.rows())
    first, second = of(e, P, "parent")
    assert (first.outcome, first.failure_reason, second.active) == ("failed", "session_ended", True)
    [(_, message, _)] = fake_runner.sent
    assert "session_ended" in message and "end your turn" in message
    assert len(fake_runner.started) == 1


def test_a_parent_that_cannot_be_resumed_gets_a_new_session_and_stays_in_backlog(e: Env, fake_runner: FakeRunner):
    P = a_parent(e, fake_runner)
    old = task(e, P)["agent"]["session"]
    end(fake_runner, e, P)
    fake_runner.fail_send = True
    recover.run(e.p, fake_runner.rows())
    assert len(fake_runner.started) == 2 and task(e, P)["agent"]["session"] != old
    assert task(e, P)["status"] == "backlog"
    assert [(r.outcome, r.active) for r in of(e, P, "parent")] == [("failed", False), (None, True)]


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
    assert [n.key for n in watch.found(e.p) if n.key.startswith("blocked:")] == [f"blocked:{tid}"]
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


def test_a_session_an_answer_resumed_in_the_same_tick_is_not_judged_on_the_old_listing(e: Env, fake_runner: FakeRunner):
    tid = launched(e, fake_runner)
    qid = registry.backlog(e.p).ask(tid, "x", "A or B?")
    registry.backlog(e.p).answer(qid, "B")
    end(fake_runner, e, tid)
    tick.tick(e.p)
    assert [m for _, m, _ in fake_runner.sent] == ["The owner answered your question TY-1 Q1: «B». Carry on with the task."]
    assert [r.outcome for r in of(e, tid)] == [None]
    assert not task(e, tid).get("blocked")


def test_an_unblock_after_a_hand_over_lets_the_agent_run(e: Env, fake_runner: FakeRunner):
    tid = launched(e, fake_runner)
    for _ in range(2):
        end(fake_runner, e, tid)
        recover.run(e.p, fake_runner.rows())
    assert task(e, tid)["blocked"].startswith("its agent stopped")
    sent = len(fake_runner.sent)
    talk.unblock(e.p, tid)
    tick.tick(e.p)
    assert len(fake_runner.sent) == sent + 1
    assert [r.outcome for r in of(e, tid)] == ["failed", "failed", None]
    assert not task(e, tid).get("blocked")


def test_a_hunter_whose_file_is_written_is_not_judged_dead(e: Env, fake_runner: FakeRunner):
    tid = registry.backlog(e.p).add("y", status="ready", criteria="y")
    registry.backlog(e.p).set_status(tid, "in review", worktree=str(e.repo), branch="b")
    launch_reviewer(e.p, task(e, tid), "tip", "hunter")
    Path(task(e, tid)["hunter"]["file"]).write_text("findings\n")
    end(fake_runner, e, tid, "hunter")
    tick.tick(e.p)
    assert [(r.outcome, r.failure_reason) for r in of(e, tid, "hunter")] == [("done", "")]
    assert fake_runner.sent == []


def ended_idle(e: Env, r: FakeRunner, P: str, said: str) -> None:
    """The parent's session idles after its last words, and the loop recovers it twice."""
    idles(r, e, P)
    transcript(e, str(task(e, P)["agent"]["session"]), said)
    os.utime(next(Path(os.environ["FOREMAN_CLAUDE_PROJECTS"]).rglob("*.jsonl")), (0, 0))
    for _ in range(2):
        recover.run(e.p, r.rows())


def test_a_parent_that_ends_with_every_subtask_done_and_no_ask_is_a_need(e: Env, fake_runner: FakeRunner):
    P = a_parent(e, fake_runner, sub="done")
    ended_idle(e, fake_runner, P, "all subtasks done, waiting for your yes  ")
    [need] = watch.found(e.p)
    assert (need.kind, need.task, need.text) == ("stopped", P, "all subtasks done, waiting for your yes")


def test_a_parent_that_asked_leaves_only_its_question(e: Env, fake_runner: FakeRunner):
    P = a_parent(e, fake_runner, sub="done")
    registry.backlog(e.p).ask(P, "agent", "which first?")
    ended_idle(e, fake_runner, P, "waiting for your yes")
    assert [n.kind for n in watch.found(e.p)] == ["question"]


def test_a_parent_with_an_open_subtask_leaves_no_need(e: Env, fake_runner: FakeRunner):
    P = a_parent(e, fake_runner)
    ended_idle(e, fake_runner, P, "waiting for your yes")
    assert watch.found(e.p) == []


def test_answering_the_stopped_need_wakes_the_parent(e: Env, fake_runner: FakeRunner):
    P = a_parent(e, fake_runner, sub="done")
    ended_idle(e, fake_runner, P, "waiting for your yes")
    [need] = watch.found(e.p)
    registry.backlog(e.p).answer(need.key.removeprefix("q:"), "carry on with the report")
    tick.tick(e.p)
    assert any("carry on with the report" in m for _, m, _ in fake_runner.sent)


@pytest.mark.parametrize("session", [State.IDLE, State.ENDED])
def test_waking_the_parent_by_an_edit_closes_the_stopped_need(e: Env, fake_runner: FakeRunner, session: State):
    P = a_parent(e, fake_runner, sub="done")
    ended_idle(e, fake_runner, P, "waiting for your yes")
    fake_runner.states[str(task(e, P)["agent"]["session"])] = session  # ENDED: a new session is launched for the wakeup
    with registry.backlog(e.p).edit() as d:
        questions.message(d, backlog.get(d, P), "E", ("edit",), kind="edit", text="edit", answer="do the report")
    tick.tick(e.p)
    assert watch.found(e.p) == []
    assert [x for x in registry.backlog(e.p).load()["decisions_resolved"] if x.get("kind") == "stopped"][0]["by"].startswith("loop")
    assert "stopped" not in [q["kind"] for q in views.task(e.p, P)["questions"]]  # no card to answer in the panel for the closed need
    events = [x["text"] for x in util.read_jsonl(e.p.state / "events.jsonl") if x["task"] == P]
    assert "closed: parent woken" in " ".join(events) and not any("owner answered" in t for t in events)
