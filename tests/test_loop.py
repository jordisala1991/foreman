import functools
import json
import os
import threading
from unittest import mock

import pytest
from fakerepo import FakeRepo
from helpers import Env, fm, git

from foreman import providers, util
from foreman.core.rules import decide, risk
from foreman.core.tasks import backlog, questions
from foreman.loop import queue, tick
from foreman.loop.launch import launch_free
from foreman.providers.repo import FileDiff
from foreman.services.claude import sessions
from foreman.services.delivery import check, merge, pulls
from foreman.services.delivery.git import DeliveryError
from foreman.steps import talk


class TestLaunch:
    def test_plans_backlog_and_launches_ready_within_the_slots(self):
        self.e = Env(slots=2)
        self.p = self.e.p
        a = backlog.add(self.p, "vague idea", criteria="c")
        b = backlog.add(self.p, "clear one", status="ready", criteria="tests pass")
        c = backlog.add(self.p, "another", status="ready", criteria="x")
        t = backlog.add(self.p, "a finding", status="triage")
        tick.tick(self.p)
        assert self.e.task(b)["status"] == "in progress"
        assert self.e.task(c)["status"] == "in progress"  # ready before backlog, both slots used
        assert self.e.task(a)["status"] == "backlog"
        assert self.e.task(t)["status"] == "triage"  # triage waits for the owner
        calls = self.e.calls()
        assert len(calls) == 2
        argv = calls[0]["argv"]
        assert "--bg" in argv
        assert argv[argv.index("-n") + 1] == "TY-2 responsible · clear one"
        assert self.e.task(b)["agent"]["name"] == b.lower()  # identity unchanged
        assert json.loads(argv[argv.index("--settings") + 1])["env"]["FOREMAN_AGENT"] == b.lower()
        assert "[fm responsible TY-2]" in argv[-1]
        assert calls[0]["cwd"].endswith("worktrees/ty-2")
        assert "foreman/ty-2" in git(self.e.repo, "branch")
        assert self.e.task(b)["agent"]["session"]

    def test_a_title_edited_before_launch_shows_in_the_new_agent(self, fake_git):
        e = Env(repo=fake_git)
        tid = backlog.add(e.p, "old title", status="ready", criteria="c")
        fm(e.home, "edit", tid, "--title", "new title")
        launch_free(e.p)
        argv = e.calls()[0]["argv"]
        assert argv[argv.index("-n") + 1] == "TY-1 responsible · new title"

    def flags(self, e: Env) -> dict[str, tuple[str, str]]:
        """(model, effort) of every launched command, by the kind in its prompt."""
        out = {}
        for c in e.calls():
            a = c["argv"]
            out[a[-1].split(" ")[1]] = (a[a.index("--model") + 1], a[a.index("--effort") + 1])
        return out

    def run_all_kinds(self, e: Env) -> dict[str, tuple[str, str]]:
        backlog.add(e.p, "vague idea", criteria="c")
        w = backlog.add(e.p, "clear one", status="ready", criteria="x")
        tick.tick(e.p)
        backlog.set_status(e.p, w, "in review", report={"summary": "s"})
        e.hunt_then_review(w)
        return self.flags(e)

    def test_every_kind_launches_with_an_explicit_model_and_effort(self, fake_git):
        e = Env(test="true", repo=fake_git)
        assert self.run_all_kinds(e) == {
            "planning": ("sonnet", "high"),
            "responsible": ("sonnet", "medium"),
            "hunter": ("sonnet", "high"),
            "reviewer": ("sonnet", "high"),
        }
        i = e.task("TY-2")
        assert i["reviewer"]["effort"] == "high"
        assert i["agent"]["effort"] == "medium"

    def test_a_configured_model_and_effort_reach_each_command(self, fake_git):
        e = Env(test="true", repo=fake_git)
        cfg = e.p.cfg["agents"]
        cfg.update(planning_model="opus", planning_effort="max", model="haiku", effort="low", review_model="opus", review_effort="xhigh")
        assert self.run_all_kinds(e) == {
            "planning": ("opus", "max"),
            "responsible": ("haiku", "low"),
            "hunter": ("opus", "xhigh"),
            "reviewer": ("opus", "xhigh"),
        }

    def test_defining_agent_runs_in_the_repo_and_never_gets_a_worktree(self, fake_git):
        self.e = Env(slots=2, repo=fake_git)
        a = backlog.add(self.e.p, "vague idea", criteria="c")
        launch_free(self.e.p)
        assert self.e.task(a)["status"] == "refining"
        assert os.path.realpath(self.e.calls()[0]["cwd"]) == str(self.e.repo)
        assert "[fm planning TY-1]" in self.e.calls()[0]["argv"][-1]
        assert "worktree" not in self.e.task(a)

    def test_paused_launches_nothing(self, fake_git):
        self.e = Env(slots=2, repo=fake_git)
        self.p = self.e.p
        backlog.add(self.p, "x", status="ready", criteria="y")
        (self.p.state).mkdir(parents=True, exist_ok=True)
        (self.p.state / "paused").write_text("now")
        launch_free(self.p)
        assert self.e.calls() == []


class TestAnswers:
    def test_the_answer_goes_back_to_the_agent_that_asked(self, fake_git):
        e = Env(repo=fake_git)
        tid = backlog.add(e.p, "x", status="ready", criteria="y")
        launch_free(e.p)
        qid = questions.ask(e.p, tid, tid.lower(), "A or B?")
        questions.answer(e.p, qid, "B")
        talk.deliver_answers(e.p, sessions.listing(e.p))  # its session is still working: wait
        assert len(e.calls()) == 1
        e.set_state(tid.lower(), "blocked")
        e.set_status(tid.lower(), "active")
        talk.deliver_answers(e.p, sessions.listing(e.p))  # nor while blocked: resuming it would start a copy
        assert len(e.calls()) == 1
        e.set_state(tid.lower(), "done")
        talk.deliver_answers(e.p, sessions.listing(e.p))
        resume = e.calls()[-1]["argv"]
        assert "--resume" in resume
        assert resume[resume.index("--resume") + 1] == e.task(tid)["agent"]["session"]
        assert "«B»" in resume[-1]
        assert questions.undelivered(backlog.load(e.p)) == []

    @pytest.mark.parametrize(("state", "status"), (("blocked", "idle"), ("working", "idle"), ("blocked", None)))
    def test_an_idle_session_gets_the_answer_whatever_state_it_reports(self, state, status, fake_git):
        self.delivered_to_idle(state, status, fake_git)

    def delivered_to_idle(self, state, status, fake: FakeRepo):
        e = Env(repo=fake)
        tid = backlog.add(e.p, "x", status="ready", criteria="y")
        launch_free(e.p)
        qid = questions.ask(e.p, tid, tid.lower(), "A or B?")
        questions.answer(e.p, qid, "B")
        e.set_state(tid.lower(), state)
        e.set_status(tid.lower(), status) if status else e.drop_pid(tid.lower())  # no status: its process is gone too
        talk.deliver_answers(e.p, sessions.listing(e.p))
        assert "--resume" in e.calls()[-1]["argv"]
        assert questions.undelivered(backlog.load(e.p)) == []


class TestStopped:
    def test_an_agent_that_stops_without_finishing_is_nudged_once_then_blocked(self, fake_git):
        e = Env(repo=fake_git)
        tid = backlog.add(e.p, "x", status="ready", criteria="y")
        launch_free(e.p)
        backlog.update(e.p, tid, agent={**e.task(tid)["agent"], "started": "2000-01-01 00:00:00"})
        e.set_state(tid.lower(), "done")
        talk.watch_agents(e.p, sessions.listing(e.p))
        assert "not finished" in e.calls()[-1]["argv"][-1]
        assert e.task(tid)["nudges"] == 1
        backlog.update(e.p, tid, agent={**e.task(tid)["agent"], "nudged": "2000-01-01 00:00:00"})
        e.set_state(tid.lower(), "done")  # the nudge woke it; it ended its turn again
        talk.watch_agents(e.p, sessions.listing(e.p))
        assert "stopped without finishing" in e.task(tid)["blocked"]
        assert len([c for c in e.calls() if "-mechanic" not in str(c["argv"])]) == 2  # health files the block: a mechanic follows


class TestQueue:
    def deliver(self, e: Env, lines: int = 1, open_findings: int = 0) -> str:
        tid = backlog.add(e.p, "x", status="ready", criteria="y")
        tick.tick(e.p)
        e.change(tid, lines)
        backlog.set_status(e.p, tid, "in review", report={"summary": "s", "confused": ""})
        e.review(tid, open_findings)
        return tid

    def test_manual_waits_for_the_owner_who_accepts(self):
        e = Env(test="true")
        tid = self.deliver(e)
        tick.tick(e.p)
        i = e.task(tid)
        assert (i["status"], i["check"]["risk"], i["check"]["tests"]) == ("in review", "low", "pass")
        assert not [x for x in backlog.load(e.p)["decisions_resolved"] if x.get("kind") == "review"]  # a clean review sends nothing
        merge.accept(e.p, tid)
        assert e.task(tid)["status"] == "in review"  # accepting merges nothing
        tick.tick(e.p)
        assert e.task(tid)["status"] == "done"
        assert "Merge TY-1" in git(e.repo, "log", "--oneline", "-1")
        assert "foreman/ty-1" not in git(e.repo, "branch")

    def test_one_tick_checks_one_branch_and_merges_the_accepted_one_first(self, request, fake_git):
        e = Env(test="true", repo=fake_git)
        ids = [self.deliver(e) for _ in range(3)]
        tick.tick(e.p)
        merge.accept(e.p, ids[0])
        e.fake.commit("main", FileDiff("other.py", 1, 0))  # main moves: every branch is now stale
        checks: list[str] = []
        real = check.check
        check.check = lambda p, tid, *a: checks.append(tid) or real(p, tid, *a)
        request.addfinalizer(functools.partial(setattr, check, "check", real))
        try:
            tick.tick(e.p)
            assert (e.task(ids[0])["status"], checks) == ("done", [ids[1]])
            tick.tick(e.p)
            assert checks == [ids[1], ids[2]]
        finally:
            check.check = real

    def test_the_merged_check_of_a_merge_does_not_count_as_the_ticks_branch_check(self, request, fake_git):
        e = Env(test="true", repo=fake_git)
        ids = [self.deliver(e) for _ in range(3)]
        tick.tick(e.p)
        merge.accept(e.p, ids[0])  # its merged check starts in the tick
        e.fake.commit("main", FileDiff("other.py", 1, 0))  # main moves
        checks: list[str] = []
        real, step = check.check, queue.queue_one
        check.check = lambda p, tid, *a: checks.append(tid) or real(p, tid, *a)
        queue.queue_one = lambda p, i, *a: None if i["id"] == ids[1] else step(p, i, *a)  # its turn starts nothing
        request.addfinalizer(functools.partial(setattr, check, "check", real))
        request.addfinalizer(functools.partial(setattr, queue, "queue_one", step))
        tick.tick(e.p)
        assert (e.task(ids[0])["status"], checks) == ("done", [ids[2]])

    def test_a_failure_after_the_check_does_not_start_a_second_check_in_the_tick(self, request, fake_git):
        e = Env(test="true", repo=fake_git)
        ids = [self.deliver(e) for _ in range(3)]
        e.fake.commit("main", FileDiff("other.py", 1, 0))  # main moves
        checks: list[str] = []
        real, deliver = check.check, pulls.deliver
        check.check = lambda p, tid, *a: checks.append(tid) or real(p, tid, *a)

        def boom(*a):
            raise RuntimeError("boom")

        pulls.deliver = boom
        request.addfinalizer(functools.partial(setattr, check, "check", real))
        request.addfinalizer(functools.partial(setattr, pulls, "deliver", deliver))
        tick.tick(e.p)
        assert len(checks) == 1
        assert len(ids) == 3

    def test_a_slow_check_does_not_hold_up_a_launch(self, request, monkeypatch, tmp_path, fake_git):
        gate, armed = tmp_path / "gate", tmp_path / "armed"  # once armed, a check blocks reading the gate until the test writes a line
        os.mkfifo(gate)
        fd = os.open(gate, os.O_RDWR)  # held open for reading and writing: a write never blocks, the check's open never waits
        request.addfinalizer(functools.partial(os.close, fd))
        request.addfinalizer(functools.partial(os.write, fd, b"release\n" * 2))  # a failing assertion never leaves the check hanging
        e = Env(test=f"case $PWD in */trees/*) if [ -e {armed} ]; then read line < {gate}; fi;; esac", repo=fake_git)
        a = self.deliver(e)
        e.fake.commit("main", FileDiff("other.py", 1, 0))  # main moves
        ready = backlog.add(e.p, "later", status="ready", criteria="z")
        monkeypatch.setattr(util, "BACKGROUND_WAIT_S", 0)
        armed.touch()
        tick.tick(e.p)
        assert e.task(ready)["status"] == "in progress"  # launched while the check still runs
        assert (e.task(a).get("check") or {}).get("main") != e.fake.head("main")
        os.write(fd, b"release\n" * 2)  # one line for each check blocked: main's tests and the branch's
        for t in list(util.BACKGROUND.values()):
            t.join()
        assert e.task(a)["check"]["main"] == e.fake.head("main")

    def test_a_check_that_keeps_failing_with_a_transient_error_blocks_the_task_on_the_third_tick(self, request, monkeypatch, fake_git):
        e = Env(test="true", repo=fake_git)
        a = self.deliver(e)
        e.fake.commit("main", FileDiff("other.py", 1, 0))  # main moves

        tick_over = threading.Event()

        def down(*args):
            tick_over.wait(5)  # fails only once the tick has gone on without it (a guard of 5 s, the test sets it)
            raise providers.Transient("net down")

        real = check.check
        check.check = down
        request.addfinalizer(functools.partial(setattr, check, "check", real))
        monkeypatch.setattr(util, "BACKGROUND_WAIT_S", 0)
        for _ in range(3):
            tick_over.clear()
            tick.tick(e.p)
            tick_over.set()
            for t in list(util.BACKGROUND.values()):
                t.join()
        assert "net down" in e.task(a)["blocked"]

    def test_transient_failures_of_the_queue_step_that_are_not_in_a_row_do_not_block(self, request, fake_git):
        e = Env(test="true", repo=fake_git)
        a = self.deliver(e)
        tick.tick(e.p)  # checked: up to date, no check runs from now on
        real = pulls.prepare
        calls: list[int] = []

        def flaky(*args):
            calls.append(1)
            if len(calls) != 3:
                raise providers.Transient("gh 502")
            return real(*args)

        pulls.prepare = flaky
        request.addfinalizer(functools.partial(setattr, pulls, "prepare", real))
        for _ in range(4):
            tick.tick(e.p)
        assert (len(calls), e.task(a).get("blocked", "")) == (4, "")

    def test_an_exception_in_a_background_thread_is_written_to_errors_jsonl(self, request, fake_git):
        e = Env(test="true", repo=fake_git)

        def boom(*args):
            raise DeliveryError("git timed out")

        real = check.tests_at
        check.tests_at = boom
        request.addfinalizer(functools.partial(setattr, check, "tests_at", real))
        tick.tick(e.p)
        for t in list(util.BACKGROUND.values()):
            t.join()
        assert "git timed out" in util.read_jsonl(e.p.state / "errors.jsonl")[-1]["error"]

    def test_a_task_canceled_while_the_queue_runs_is_not_checked(self, fake_git):
        e = Env(test="true", repo=fake_git)
        a, b = self.deliver(e), self.deliver(e)
        real = queue.queue_one

        def cancel_b_first(p, i, rows):
            if i["id"] == a:
                merge.close(p, b, "canceled", "x")
            real(p, i, rows)

        queue.queue_one = cancel_b_first
        try:
            tick.tick(e.p)
        finally:
            queue.queue_one = real
        assert not e.task(b).get("check")

    def test_a_big_diff_alone_does_not_raise_risk(self, fake_git):
        e = Env(test="true", autonomy="low", repo=fake_git)
        big = self.deliver(e, lines=400)
        tick.tick(e.p)
        assert (e.task(big)["status"], e.task(big)["check"]["risk"]) == ("done", "low")

    def test_failing_tests_are_high(self):
        level, reasons = risk.score(Env().p, risk.Facts("fail", "clean", [{"path": "app.py", "added": 1, "deleted": 0}], [], False, 1, ""))
        assert level == "high"
        assert any("tests fail" in r for r in reasons)
        c = {"risk": "high", "review": "clean", "tests": "fail", "ci": "", "conflict": False}
        assert decide.after_check(c, "medium", False) == "failing"  # returned to its agent (FN-67)

    def test_the_loop_hunts_first_and_launches_the_reviewer_when_the_hunter_has_written_its_file(self, fake_git):
        e = Env(test="true", repo=fake_git)
        tid = backlog.add(e.p, "x", status="ready", criteria="y", detail="DETAIL-OF-THE-TASK")
        tick.tick(e.p)
        backlog.set_status(e.p, tid, "in review", report={"summary": "s"})
        tick.tick(e.p)
        call = e.calls()[-1]
        assert call["argv"][call["argv"].index("-n") + 1] == "TY-1 hunter · x"
        hunter = call["argv"][-1]
        assert "[fm hunter TY-1]" in hunter
        assert call["cwd"] == e.task(tid)["worktree"]
        hunter_file = e.task(tid)["hunter"]["file"]
        assert hunter_file in hunter
        tick.tick(e.p)
        assert len(e.calls()) == 2  # no reviewer while the hunter has no file
        e.hunter_done(tid)
        tick.tick(e.p)
        call = e.calls()[-1]
        assert call["argv"][call["argv"].index("-n") + 1] == "TY-1 review · x"
        reviewer = call["argv"][-1]
        assert "[fm reviewer TY-1]" in reviewer
        assert "Round: 1" in reviewer
        assert hunter_file in reviewer  # it reads the file, nothing else of the hunter
        assert "DETAIL-OF-THE-TASK" in hunter
        assert "DETAIL-OF-THE-TASK" in reviewer
        assert "a finding leaves only with a cited refutation" in reviewer  # no cited refutation: it stays in To fix
        assert call["cwd"] == e.task(tid)["worktree"]
        assert "check" not in e.task(tid)  # no check before the verdict
        tick.tick(e.p)
        assert len(e.calls()) == 3  # not launched twice
        assert e.state_of(e.task(tid)["hunter"]["name"]) == "stopped"
        assert e.state_of(e.task(tid)["reviewer"]["name"]) == "working"
        e.verdict(tid)
        tick.tick(e.p)
        assert e.state_of(e.task(tid)["reviewer"]["name"]) == "stopped"

    def test_cancelling_a_task_in_review_stops_every_session_of_it(self, fake_git):
        e = Env(test="true", repo=fake_git)
        tid = backlog.add(e.p, "x", status="ready", criteria="y")
        tick.tick(e.p)
        backlog.set_status(e.p, tid, "in review", report={"summary": "s"})
        e.hunt_then_review(tid)
        i = e.task(tid)
        merge.close(e.p, tid, "canceled", "no")
        for role in ("agent", "hunter", "reviewer"):
            assert e.state_of(i[role]["name"]) == "stopped", role

    def test_merging_stops_every_session_of_the_task(self, fake_git):
        e = Env(test="true", repo=fake_git)
        tid = self.deliver(e)
        e.review(tid)
        i = e.task(tid)
        tick.tick(e.p)
        merge.accept(e.p, tid)
        tick.tick(e.p)
        assert e.task(tid)["status"] == "done"
        for role in ("agent", "hunter", "reviewer"):
            assert e.state_of(i[role]["name"]) == "stopped", role

    def test_a_blocked_task_still_has_its_finished_hunter_stopped(self, fake_git):
        e = Env(test="true", repo=fake_git)
        tid = backlog.add(e.p, "x", status="ready", criteria="y")
        tick.tick(e.p)
        backlog.set_status(e.p, tid, "in review", report={"summary": "s"})
        tick.tick(e.p)
        e.hunter_done(tid)
        backlog.update(e.p, tid, blocked="why")
        tick.tick(e.p)
        assert e.state_of(e.task(tid)["hunter"]["name"]) == "stopped"

    def test_a_failing_stop_does_not_stop_the_cancel(self, fake_git):
        e = Env(test="true", repo=fake_git)
        tid = backlog.add(e.p, "x", status="ready", criteria="y")
        tick.tick(e.p)
        with mock.patch("foreman.services.claude.sessions.stop", side_effect=sessions.SessionError("timed out")):
            merge.close(e.p, tid, "canceled", "no")
        assert e.task(tid)["status"] == "canceled"

    def test_cancel_stops_a_session_the_listing_does_not_show(self, fake_git):
        e = Env(test="true", repo=fake_git)
        tid = backlog.add(e.p, "x", status="ready", criteria="y")
        tick.tick(e.p)
        (e.bin / "agents.json").write_text("[]")  # unreadable or not yet listed: its agent still runs
        merge.close(e.p, tid, "canceled", "no")
        assert e.stops() == [e.task(tid)["agent"]["short"]]

    def test_the_review_tick_does_not_stop_again_a_session_the_listing_does_not_show(self, fake_git):
        e = Env(test="true", repo=fake_git)
        tid = backlog.add(e.p, "x", status="ready", criteria="y")
        tick.tick(e.p)
        backlog.set_status(e.p, tid, "in review", report={"summary": "s"})
        tick.tick(e.p)
        e.hunter_done(tid)
        (e.bin / "agents.json").write_text("[]")  # gone, or an unreadable listing
        for _ in range(3):
            tick.tick(e.p)
        assert e.stops() == []

    def test_a_cancel_stops_each_session_once(self, fake_git):
        e = Env(test="true", repo=fake_git)
        tid = backlog.add(e.p, "x", status="ready", criteria="y")
        tick.tick(e.p)
        name = e.task(tid)["agent"]["name"]
        merge.close(e.p, tid, "canceled", "no")
        assert e.stops() == [e.task(tid)["agent"]["short"]]  # once, though its id is also in `sessions`
        assert e.state_of(name) == "stopped"

    def test_a_hunter_that_ends_without_its_file_blocks_the_task(self, fake_git):
        e = Env(test="true", repo=fake_git)
        tid = backlog.add(e.p, "x", status="ready", criteria="y")
        tick.tick(e.p)
        backlog.set_status(e.p, tid, "in review", report={"summary": "s"})
        tick.tick(e.p)
        e.set_state(e.task(tid)["hunter"]["name"], "done")
        backlog.update(e.p, tid, hunter={**e.task(tid)["hunter"], "started": "2000-01-01 00:00:00"})
        tick.tick(e.p)
        assert "hunter ended without its findings file" in e.task(tid)["blocked"]
        assert len(e.calls()) == 2

    def test_open_findings_go_back_to_the_agent_then_round_two_then_the_owner(self, fake_git):
        e = Env(test="true", autonomy="medium", repo=fake_git)
        tid = self.deliver(e, open_findings=1)
        tick.tick(e.p)
        assert e.task(tid)["status"] == "in progress"
        e.set_state(tid.lower(), "done")
        tick.tick(e.p)
        assert "leaves 1 open" in e.calls()[-1]["argv"][-1]
        assert f"reviews/{tid}.md" in e.calls()[-1]["argv"][-1]
        e.change(tid)  # the fix
        backlog.set_status(e.p, tid, "in review")
        e.review(tid, 1)  # round 2 still open: to the owner, whatever the autonomy
        assert "Round: 2" in e.calls()[-1]["argv"][-1]
        tick.tick(e.p)
        i = e.task(tid)
        assert (i["status"], i["check"]["risk"]) == ("in review", "low")
        assert any("open after round 2" in r for r in i["check"]["reasons"])

    def test_reject_sends_the_note_back_to_the_agent(self, fake_git):
        e = Env(test="true", repo=fake_git)
        tid = self.deliver(e)
        fm(e.home, "reject", tid, "name it better")
        assert (e.task(tid)["status"], e.task(tid)["rework"]) == ("in progress", 1)
        e.set_state(tid.lower(), "done")
        tick.tick(e.p)
        assert "name it better" in e.calls()[-1]["argv"][-1]

    def test_refuted_without_commit_is_not_returned_twice(self, fake_git):
        e = Env(test="true", repo=fake_git)
        tid = self.deliver(e, open_findings=1)
        tick.tick(e.p)
        e.set_state(tid.lower(), "done")
        tick.tick(e.p)
        backlog.set_status(e.p, tid, "in review")
        tick.tick(e.p)
        i = e.task(tid)
        assert (i["status"], i["check"]["risk"]) == ("in review", "low")
