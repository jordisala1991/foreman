import functools
import json
import os
import time
from unittest import mock

import pytest
from helpers import Env, fm, git, install_fm, version_folder

from fm import delivery, launcher, loop, proc, providers, util
from fm.core.tasks import backlog, questions


class TestLaunch:
    @pytest.fixture(autouse=True)
    def set_up(self):
        self.e = Env(slots=2)
        self.p = self.e.p

    def test_plans_backlog_and_launches_ready_within_the_slots(self):
        a = backlog.add(self.p, "vague idea", criteria="c")
        b = backlog.add(self.p, "clear one", status="ready", criteria="tests pass")
        c = backlog.add(self.p, "another", status="ready", criteria="x")
        t = backlog.add(self.p, "a finding", status="triage")
        loop.tick(self.p)
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
        assert json.loads(argv[argv.index("--settings") + 1])["env"]["FM_AGENT"] == b.lower()
        assert "[fm responsible TY-2]" in argv[-1]
        assert calls[0]["cwd"].endswith("worktrees/ty-2")
        assert "fm/ty-2" in git(self.e.repo, "branch")
        assert self.e.task(b)["agent"]["session"]

    def test_a_title_edited_before_launch_shows_in_the_new_agent(self):
        e = Env()
        tid = backlog.add(e.p, "old title", status="ready", criteria="c")
        fm(e.home, "edit", tid, "--title", "new title")
        loop.tick(e.p)
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
        loop.tick(e.p)
        backlog.set_status(e.p, w, "in review", report={"summary": "s"})
        e.hunt_then_review(w)
        return self.flags(e)

    def test_every_kind_launches_with_an_explicit_model_and_effort(self):
        e = Env(test="true")
        assert self.run_all_kinds(e) == {
            "planning": ("sonnet", "high"),
            "responsible": ("sonnet", "medium"),
            "hunter": ("sonnet", "high"),
            "reviewer": ("sonnet", "high"),
        }
        i = e.task("TY-2")
        assert i["reviewer"]["effort"] == "high"
        assert i["agent"]["effort"] == "medium"

    def test_a_configured_model_and_effort_reach_each_command(self):
        e = Env(test="true")
        cfg = e.p.cfg["agents"]
        cfg.update(planning_model="opus", planning_effort="max", model="haiku", effort="low", review_model="opus", review_effort="xhigh")
        assert self.run_all_kinds(e) == {
            "planning": ("opus", "max"),
            "responsible": ("haiku", "low"),
            "hunter": ("opus", "xhigh"),
            "reviewer": ("opus", "xhigh"),
        }

    def test_defining_agent_runs_in_the_repo_and_never_gets_a_worktree(self):
        a = backlog.add(self.p, "vague idea", criteria="c")
        loop.tick(self.p)
        assert self.e.task(a)["status"] == "refining"
        assert os.path.realpath(self.e.calls()[0]["cwd"]) == str(self.e.repo)
        assert "[fm planning TY-1]" in self.e.calls()[0]["argv"][-1]
        assert "worktree" not in self.e.task(a)

    def test_paused_launches_nothing(self):
        backlog.add(self.p, "x", status="ready", criteria="y")
        (self.p.state).mkdir(parents=True, exist_ok=True)
        (self.p.state / "paused").write_text("now")
        loop.tick(self.p)
        assert self.e.calls() == []


class TestAnswers:
    def test_the_answer_goes_back_to_the_agent_that_asked(self):
        e = Env()
        tid = backlog.add(e.p, "x", status="ready", criteria="y")
        loop.tick(e.p)
        qid = questions.ask(e.p, tid, tid.lower(), "A or B?")
        questions.answer(e.p, qid, "B")
        loop.tick(e.p)  # its session is still working: wait
        assert len(e.calls()) == 1
        e.set_state(tid.lower(), "blocked")
        e.set_status(tid.lower(), "active")
        loop.tick(e.p)  # nor while blocked: resuming it would start a copy
        assert len(e.calls()) == 1
        e.set_state(tid.lower(), "done")
        loop.tick(e.p)
        resume = e.calls()[-1]["argv"]
        assert "--resume" in resume
        assert resume[resume.index("--resume") + 1] == e.task(tid)["agent"]["session"]
        assert "«B»" in resume[-1]
        assert questions.undelivered(backlog.load(e.p)) == []

    @pytest.mark.parametrize(("state", "status"), (("blocked", "idle"), ("working", "idle"), ("blocked", None)))
    def test_an_idle_session_gets_the_answer_whatever_state_it_reports(self, state, status):
        self.delivered_to_idle(state, status)

    def delivered_to_idle(self, state, status):
        e = Env()
        tid = backlog.add(e.p, "x", status="ready", criteria="y")
        loop.tick(e.p)
        qid = questions.ask(e.p, tid, tid.lower(), "A or B?")
        questions.answer(e.p, qid, "B")
        e.set_state(tid.lower(), state)
        e.set_status(tid.lower(), status) if status else e.drop_pid(tid.lower())  # no status: its process is gone too
        loop.tick(e.p)
        assert "--resume" in e.calls()[-1]["argv"]
        assert questions.undelivered(backlog.load(e.p)) == []


class TestStopped:
    def test_an_agent_that_stops_without_finishing_is_nudged_once_then_blocked(self):
        e = Env()
        tid = backlog.add(e.p, "x", status="ready", criteria="y")
        loop.tick(e.p)
        backlog.update(e.p, tid, agent={**e.task(tid)["agent"], "started": "2000-01-01 00:00:00"})
        e.set_state(tid.lower(), "done")
        loop.tick(e.p)
        assert "not finished" in e.calls()[-1]["argv"][-1]
        assert e.task(tid)["nudges"] == 1
        backlog.update(e.p, tid, agent={**e.task(tid)["agent"], "nudged": "2000-01-01 00:00:00"})
        e.set_state(tid.lower(), "done")  # the nudge woke it; it ended its turn again
        loop.tick(e.p)
        assert "stopped without finishing" in e.task(tid)["blocked"]
        assert len([c for c in e.calls() if "-mechanic" not in str(c["argv"])]) == 2  # health files the block: a mechanic follows


class TestQueue:
    def deliver(self, e: Env, lines: int = 1, open_findings: int = 0) -> str:
        tid = backlog.add(e.p, "x", status="ready", criteria="y")
        loop.tick(e.p)
        wt = e.task(tid)["worktree"]
        with open(os.path.join(wt, "app.py"), "a") as f:
            f.write("".join(f"# {n}\n" for n in range(lines)))
        git(e.repo.parent / wt, "commit", "-qam", "change")
        backlog.set_status(e.p, tid, "in review", report={"summary": "s", "confused": ""})
        e.review(tid, open_findings)
        return tid

    def test_manual_waits_for_the_owner_who_accepts(self):
        e = Env(test="true")
        tid = self.deliver(e)
        loop.tick(e.p)
        i = e.task(tid)
        assert (i["status"], i["check"]["risk"], i["check"]["tests"]) == ("in review", "low", "pass")
        delivery.accept(e.p, tid)
        assert e.task(tid)["status"] == "in review"  # accepting merges nothing
        loop.tick(e.p)
        assert e.task(tid)["status"] == "done"
        assert "Merge TY-1" in git(e.repo, "log", "--oneline", "-1")
        assert "fm/ty-1" not in git(e.repo, "branch")

    def test_one_tick_checks_one_branch_and_merges_the_accepted_one_first(self, request):
        e = Env(test="true")
        ids = [self.deliver(e) for _ in range(3)]
        loop.tick(e.p)
        delivery.accept(e.p, ids[0])
        (e.repo / "other.py").write_text("x = 1\n")
        git(e.repo, "add", "other.py")
        git(e.repo, "commit", "-qm", "main moves")  # every branch is now stale
        checks: list[str] = []
        real = delivery.check
        delivery.check = lambda p, tid, *a: checks.append(tid) or real(p, tid, *a)
        request.addfinalizer(functools.partial(setattr, delivery, "check", real))
        try:
            loop.tick(e.p)
            assert (e.task(ids[0])["status"], checks) == ("done", [ids[1]])
            loop.tick(e.p)
            assert checks == [ids[1], ids[2]]
        finally:
            delivery.check = real

    def test_a_failure_after_the_check_does_not_start_a_second_check_in_the_tick(self, request):
        e = Env(test="true")
        ids = [self.deliver(e) for _ in range(3)]
        (e.repo / "other.py").write_text("x = 1\n")
        git(e.repo, "add", "other.py")
        git(e.repo, "commit", "-qm", "main moves")
        checks: list[str] = []
        real, deliver = delivery.check, loop.pulls.deliver
        delivery.check = lambda p, tid, *a: checks.append(tid) or real(p, tid, *a)

        def boom(*a):
            raise RuntimeError("boom")

        loop.pulls.deliver = boom
        request.addfinalizer(functools.partial(setattr, delivery, "check", real))
        request.addfinalizer(functools.partial(setattr, loop.pulls, "deliver", deliver))
        loop.tick(e.p)
        assert len(checks) == 1
        assert len(ids) == 3

    def test_a_slow_check_does_not_hold_up_a_launch(self, request):
        e = Env(test="case $PWD in */trees/*) sleep 3;; esac")
        a = self.deliver(e)
        (e.repo / "other.py").write_text("x = 1\n")
        git(e.repo, "add", "other.py")
        git(e.repo, "commit", "-qm", "main moves")
        ready = backlog.add(e.p, "later", status="ready", criteria="z")
        util.BACKGROUND_WAIT_S = 0.2
        request.addfinalizer(functools.partial(setattr, util, "BACKGROUND_WAIT_S", 20))
        loop.tick(e.p)
        assert e.task(ready)["status"] == "in progress"  # launched while the check still runs
        assert (e.task(a).get("check") or {}).get("main") != git(e.repo, "rev-parse", "main").strip()
        for t in list(util.BACKGROUND.values()):
            t.join()
        assert e.task(a)["check"]["main"] == git(e.repo, "rev-parse", "main").strip()

    def test_a_check_that_keeps_failing_with_a_transient_error_blocks_the_task_on_the_third_tick(self, request):
        e = Env(test="true")
        a = self.deliver(e)
        (e.repo / "other.py").write_text("x = 1\n")
        git(e.repo, "add", "other.py")
        git(e.repo, "commit", "-qm", "main moves")

        def down(*args):
            time.sleep(0.3)  # longer than the wait: the tick has gone on when it fails
            raise providers.Transient("net down")

        real = delivery.check
        delivery.check = down
        request.addfinalizer(functools.partial(setattr, delivery, "check", real))
        util.BACKGROUND_WAIT_S = 0.05
        request.addfinalizer(functools.partial(setattr, util, "BACKGROUND_WAIT_S", 20))
        for _ in range(3):
            loop.tick(e.p)
            for t in list(util.BACKGROUND.values()):
                t.join()
        assert "net down" in e.task(a)["blocked"]

    def test_transient_failures_of_the_queue_step_that_are_not_in_a_row_do_not_block(self, request):
        e = Env(test="true")
        a = self.deliver(e)
        loop.tick(e.p)  # checked: up to date, no check runs from now on
        real = loop.pulls.prepare
        calls: list[int] = []

        def flaky(*args):
            calls.append(1)
            if len(calls) != 3:
                raise providers.Transient("gh 502")
            return real(*args)

        loop.pulls.prepare = flaky
        request.addfinalizer(functools.partial(setattr, loop.pulls, "prepare", real))
        for _ in range(4):
            loop.tick(e.p)
        assert (len(calls), e.task(a).get("blocked", "")) == (4, "")

    def test_an_exception_in_a_background_thread_is_written_to_errors_jsonl(self, request):
        e = Env(test="true")

        def boom(*args):
            raise delivery.DeliveryError("git timed out")

        real = delivery.tests_at
        delivery.tests_at = boom
        request.addfinalizer(functools.partial(setattr, delivery, "tests_at", real))
        loop.tick(e.p)
        for t in list(util.BACKGROUND.values()):
            t.join()
        assert "git timed out" in util.read_jsonl(e.p.state / "errors.jsonl")[-1]["error"]

    def test_a_task_canceled_while_the_queue_runs_is_not_checked(self):
        e = Env(test="true")
        a, b = self.deliver(e), self.deliver(e)
        real = loop.queue_one

        def cancel_b_first(p, i, main, rows):
            if i["id"] == a:
                delivery.close(p, b, "canceled", "x")
            real(p, i, main, rows)

        loop.queue_one = cancel_b_first
        try:
            loop.tick(e.p)
        finally:
            loop.queue_one = real
        assert not e.task(b).get("check")

    def test_autonomy_low_merges_low_risk_alone(self):
        e = Env(test="true", autonomy="low")
        tid = self.deliver(e)
        loop.tick(e.p)
        assert e.task(tid)["status"] == "done"

    def test_a_big_diff_alone_does_not_raise_risk(self):
        e = Env(test="true", autonomy="low")
        big = self.deliver(e, lines=400)
        loop.tick(e.p)
        assert (e.task(big)["status"], e.task(big)["check"]["risk"]) == ("done", "low")

    def test_failing_tests_are_high(self):
        e = Env(test="false", autonomy="medium")
        tid = self.deliver(e)
        loop.tick(e.p)
        c = e.task(tid)["check"]
        assert (e.task(tid)["status"], c["risk"]) == ("in progress", "high")  # returned to its agent (FN-67)
        assert any("tests fail" in r for r in c["reasons"])

    def test_the_loop_hunts_first_and_launches_the_reviewer_when_the_hunter_has_written_its_file(self):
        e = Env(test="true")
        tid = backlog.add(e.p, "x", status="ready", criteria="y", detail="DETAIL-OF-THE-TASK")
        loop.tick(e.p)
        backlog.set_status(e.p, tid, "in review", report={"summary": "s"})
        loop.tick(e.p)
        call = e.calls()[-1]
        assert call["argv"][call["argv"].index("-n") + 1] == "TY-1 hunter · x"
        hunter = call["argv"][-1]
        assert "[fm hunter TY-1]" in hunter
        assert call["cwd"] == e.task(tid)["worktree"]
        hunter_file = e.task(tid)["hunter"]["file"]
        assert hunter_file in hunter
        loop.tick(e.p)
        assert len(e.calls()) == 2  # no reviewer while the hunter has no file
        e.hunter_done(tid)
        loop.tick(e.p)
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
        loop.tick(e.p)
        assert len(e.calls()) == 3  # not launched twice

    def test_a_finished_hunter_and_a_reviewer_with_a_verdict_are_stopped(self):
        e = Env(test="true")
        tid = backlog.add(e.p, "x", status="ready", criteria="y")
        loop.tick(e.p)
        backlog.set_status(e.p, tid, "in review", report={"summary": "s"})
        e.hunt_then_review(tid)
        loop.tick(e.p)
        assert e.state_of(e.task(tid)["hunter"]["name"]) == "stopped"
        assert e.state_of(e.task(tid)["reviewer"]["name"]) == "working"
        e.verdict(tid)
        loop.tick(e.p)
        assert e.state_of(e.task(tid)["reviewer"]["name"]) == "stopped"

    def test_cancelling_a_task_in_review_stops_every_session_of_it(self):
        e = Env(test="true")
        tid = backlog.add(e.p, "x", status="ready", criteria="y")
        loop.tick(e.p)
        backlog.set_status(e.p, tid, "in review", report={"summary": "s"})
        e.hunt_then_review(tid)
        i = e.task(tid)
        delivery.close(e.p, tid, "canceled", "no")
        for role in ("agent", "hunter", "reviewer"):
            assert e.state_of(i[role]["name"]) == "stopped", role

    def test_merging_stops_every_session_of_the_task(self):
        e = Env(test="true")
        tid = self.deliver(e)
        e.review(tid)
        i = e.task(tid)
        loop.tick(e.p)
        delivery.accept(e.p, tid)
        loop.tick(e.p)
        assert e.task(tid)["status"] == "done"
        for role in ("agent", "hunter", "reviewer"):
            assert e.state_of(i[role]["name"]) == "stopped", role

    def test_a_blocked_task_still_has_its_finished_hunter_stopped(self):
        e = Env(test="true")
        tid = backlog.add(e.p, "x", status="ready", criteria="y")
        loop.tick(e.p)
        backlog.set_status(e.p, tid, "in review", report={"summary": "s"})
        loop.tick(e.p)
        e.hunter_done(tid)
        backlog.update(e.p, tid, blocked="why")
        loop.tick(e.p)
        assert e.state_of(e.task(tid)["hunter"]["name"]) == "stopped"

    def test_a_failing_stop_does_not_stop_the_cancel(self):
        from unittest import mock

        e = Env(test="true")
        tid = backlog.add(e.p, "x", status="ready", criteria="y")
        loop.tick(e.p)
        with mock.patch("fm.sessions.stop", side_effect=proc.ProcError("timed out")):
            delivery.close(e.p, tid, "canceled", "no")
        assert e.task(tid)["status"] == "canceled"

    def test_cancel_stops_a_session_the_listing_does_not_show(self):
        e = Env(test="true")
        tid = backlog.add(e.p, "x", status="ready", criteria="y")
        loop.tick(e.p)
        (e.bin / "agents.json").write_text("[]")  # unreadable or not yet listed: its agent still runs
        delivery.close(e.p, tid, "canceled", "no")
        assert e.stops() == [e.task(tid)["agent"]["short"]]

    def test_the_review_tick_does_not_stop_again_a_session_the_listing_does_not_show(self):
        e = Env(test="true")
        tid = backlog.add(e.p, "x", status="ready", criteria="y")
        loop.tick(e.p)
        backlog.set_status(e.p, tid, "in review", report={"summary": "s"})
        loop.tick(e.p)
        e.hunter_done(tid)
        (e.bin / "agents.json").write_text("[]")  # gone, or an unreadable listing
        for _ in range(3):
            loop.tick(e.p)
        assert e.stops() == []

    def test_a_cancel_stops_each_session_once(self):
        e = Env(test="true")
        tid = backlog.add(e.p, "x", status="ready", criteria="y")
        loop.tick(e.p)
        name = e.task(tid)["agent"]["name"]
        delivery.close(e.p, tid, "canceled", "no")
        assert e.stops() == [e.task(tid)["agent"]["short"]]  # once, though its id is also in `sessions`
        assert e.state_of(name) == "stopped"

    def test_a_hunter_that_ends_without_its_file_blocks_the_task(self):
        e = Env(test="true")
        tid = backlog.add(e.p, "x", status="ready", criteria="y")
        loop.tick(e.p)
        backlog.set_status(e.p, tid, "in review", report={"summary": "s"})
        loop.tick(e.p)
        e.set_state(e.task(tid)["hunter"]["name"], "done")
        backlog.update(e.p, tid, hunter={**e.task(tid)["hunter"], "started": "2000-01-01 00:00:00"})
        loop.tick(e.p)
        assert "hunter ended without its findings file" in e.task(tid)["blocked"]
        assert len(e.calls()) == 2

    def test_open_findings_go_back_to_the_agent_then_round_two_then_the_owner(self):
        e = Env(test="true", autonomy="medium")
        tid = self.deliver(e, open_findings=1)
        loop.tick(e.p)
        assert e.task(tid)["status"] == "in progress"
        e.set_state(tid.lower(), "done")
        loop.tick(e.p)
        assert "leaves 1 open" in e.calls()[-1]["argv"][-1]
        wt = e.task(tid)["worktree"]
        with open(os.path.join(wt, "app.py"), "a") as f:
            f.write("# fixed\n")
        git(e.repo.parent / wt, "commit", "-qam", "fix")
        backlog.set_status(e.p, tid, "in review")
        e.review(tid, 1)  # round 2 still open: to the owner, whatever the autonomy
        assert "Round: 2" in e.calls()[-1]["argv"][-1]
        loop.tick(e.p)
        i = e.task(tid)
        assert (i["status"], i["check"]["risk"]) == ("in review", "low")
        assert any("open after round 2" in r for r in i["check"]["reasons"])

    def test_a_branch_that_conflicts_after_main_moved_goes_back_to_its_agent_once_accepted(self):
        e = Env(test="true")
        tid = self.deliver(e)
        loop.tick(e.p)
        (e.repo / "app.py").write_text("changed on main\n")
        git(e.repo, "commit", "-qam", "main moves")
        loop.tick(e.p)
        assert e.task(tid)["status"] == "in review"  # nobody wants it merged yet: it waits
        delivery.accept(e.p, tid)
        loop.tick(e.p)
        i = e.task(tid)
        assert (i["status"], i["conflicts"]) == ("in progress", 1)
        e.set_state(tid.lower(), "done")
        loop.tick(e.p)
        assert "conflicts" in e.calls()[-1]["argv"][-1]

    def test_reject_sends_the_note_back_to_the_agent(self):
        e = Env(test="true")
        tid = self.deliver(e)
        fm(e.home, "reject", tid, "name it better")
        assert (e.task(tid)["status"], e.task(tid)["rework"]) == ("in progress", 1)
        e.set_state(tid.lower(), "done")
        loop.tick(e.p)
        assert "name it better" in e.calls()[-1]["argv"][-1]

    def fix_and_resubmit(self, e: Env, tid: str) -> None:
        e.set_state(tid.lower(), "done")
        loop.tick(e.p)
        wt = e.task(tid)["worktree"]
        with open(os.path.join(wt, "app.py"), "a") as f:
            f.write("# fixed\n")
        git(e.repo.parent / wt, "commit", "-qam", "fix")
        backlog.set_status(e.p, tid, "in review")

    def test_open_findings_send_the_review_back(self):
        e = Env(test="true")
        tid = self.deliver(e, open_findings=1)
        loop.tick(e.p)
        assert e.task(tid)["status"] == "in progress"
        e.set_state(tid.lower(), "done")
        loop.tick(e.p)
        assert f"reviews/{tid}.md" in e.calls()[-1]["argv"][-1]

    def test_clean_review_sends_nothing(self):
        e = Env(test="true")
        tid = self.deliver(e)
        loop.tick(e.p)
        i = e.task(tid)
        assert i["status"] == "in review"
        assert "check" in i
        d = backlog.load(e.p)
        assert not [x for x in d["decisions_resolved"] if x.get("kind") == "review"]

    def test_refuted_without_commit_is_not_returned_twice(self):
        e = Env(test="true")
        tid = self.deliver(e, open_findings=1)
        loop.tick(e.p)
        e.set_state(tid.lower(), "done")
        loop.tick(e.p)
        backlog.set_status(e.p, tid, "in review")
        loop.tick(e.p)
        i = e.task(tid)
        assert (i["status"], i["check"]["risk"]) == ("in review", "low")


class TestVersion:
    """A loop started by the launcher (FM_PINNED) exits as soon as another fm@fm version is installed."""

    @pytest.fixture(autouse=True)
    def set_up(self, request, env):
        self.e = env
        self.p = self.e.p
        cache = self.e.tmp / "cache/fm/fm"
        self.a, self.b = version_folder(cache, "aaa"), version_folder(cache, "bbb")
        env = mock.patch.dict(os.environ, {"FM_PINNED": self.a["installPath"]})
        env.start()
        request.addfinalizer(env.stop)

    def test_a_new_install_stops_the_loop_before_its_tick(self, request):
        install_fm(request, self.b)
        with pytest.raises(SystemExit) as e:
            loop.forever([str(self.p.home)], once=True)
        assert str(e.value) == "fm: fm@fm bbb is installed now; this runs aaa"
        assert not (self.p.state / "loop.json").exists()

    def test_the_same_install_ticks_and_the_heartbeat_names_the_version(self, request):
        install_fm(request, self.a)
        loop.forever([str(self.p.home)], once=True)
        hb = util.read_json(self.p.state / "loop.json", {})
        assert hb["version"] == launcher.ROOT.name
