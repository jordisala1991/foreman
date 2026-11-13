import os
import unittest

from helpers import Env, fm, git

from fm import backlog, integrate, loop, proc, risk, util


class Launch(unittest.TestCase):
    def setUp(self):
        self.e = Env(slots=2)
        self.p = self.e.p

    def test_defines_to_define_and_launches_ready_within_the_slots(self):
        a = backlog.add(self.p, "vague idea")
        b = backlog.add(self.p, "clear one", status="ready", criterion="tests pass")
        c = backlog.add(self.p, "another", status="ready", criterion="x")
        t = backlog.add(self.p, "a finding", status="triage")
        loop.tick(self.p)
        self.assertEqual(self.e.issue(b)["status"], "in progress")
        self.assertEqual(self.e.issue(c)["status"], "in progress")  # ready before to-define, both slots used
        self.assertEqual(self.e.issue(a)["status"], "backlog")
        self.assertEqual(self.e.issue(t)["status"], "triage")  # triage waits for the owner
        calls = self.e.calls()
        self.assertEqual(len(calls), 2)
        argv = calls[0]["argv"]
        self.assertIn("--bg", argv)
        self.assertEqual(argv[argv.index("-n") + 1], b.lower())
        self.assertIn("[fm work TY-2]", argv[-1])
        self.assertTrue(calls[0]["cwd"].endswith("worktrees/ty-2"))
        self.assertIn("fm/ty-2", git(self.e.repo, "branch"))
        self.assertTrue(self.e.issue(b)["agent"]["session"])

    def flags(self, e: Env) -> dict[str, tuple[str, str]]:
        """(model, effort) of every launched command, by the kind in its prompt."""
        out = {}
        for c in e.calls():
            a = c["argv"]
            out[a[-1].split(" ")[1]] = (a[a.index("--model") + 1], a[a.index("--effort") + 1])
        return out

    def run_all_kinds(self, e: Env) -> dict[str, tuple[str, str]]:
        backlog.add(e.p, "vague idea")
        w = backlog.add(e.p, "clear one", status="ready", criterion="x")
        loop.tick(e.p)
        backlog.set_status(e.p, w, "in review", report={"summary": "s"})
        e.hunt_then_review(w)
        return self.flags(e)

    def test_every_kind_launches_with_an_explicit_model_and_effort(self):
        e = Env(test="true")
        self.assertEqual(
            self.run_all_kinds(e),
            {"define": ("sonnet", "high"), "work": ("sonnet", "medium"), "hunter": ("sonnet", "high"), "reviewer": ("sonnet", "high")},
        )
        i = e.issue("TY-2")
        self.assertEqual(i["reviewer"]["effort"], "high")
        self.assertEqual(i["agent"]["effort"], "medium")

    def test_a_configured_model_and_effort_reach_each_command(self):
        e = Env(test="true")
        cfg = e.p.cfg["agents"]
        cfg.update(define_model="opus", define_effort="max", model="haiku", effort="low", review_model="opus", review_effort="xhigh")
        self.assertEqual(
            self.run_all_kinds(e),
            {"define": ("opus", "max"), "work": ("haiku", "low"), "hunter": ("opus", "xhigh"), "reviewer": ("opus", "xhigh")},
        )

    def test_defining_agent_runs_in_the_repo_and_never_gets_a_worktree(self):
        a = backlog.add(self.p, "vague idea")
        loop.tick(self.p)
        self.assertEqual(self.e.issue(a)["status"], "refining")
        self.assertEqual(os.path.realpath(self.e.calls()[0]["cwd"]), str(self.e.repo))
        self.assertIn("[fm define TY-1]", self.e.calls()[0]["argv"][-1])
        self.assertNotIn("worktree", self.e.issue(a))

    def test_paused_launches_nothing(self):
        backlog.add(self.p, "x", status="ready", criterion="y")
        (self.p.state).mkdir(parents=True, exist_ok=True)
        (self.p.state / "paused").write_text("now")
        loop.tick(self.p)
        self.assertEqual(self.e.calls(), [])


class Answers(unittest.TestCase):
    def test_the_answer_goes_back_to_the_agent_that_asked(self):
        e = Env()
        iid = backlog.add(e.p, "x", status="ready", criterion="y")
        loop.tick(e.p)
        qid = backlog.ask(e.p, iid, iid.lower(), "A or B?")
        backlog.answer(e.p, qid, "B")
        loop.tick(e.p)  # its session is still working: wait
        self.assertEqual(len(e.calls()), 1)
        e.set_state(iid.lower(), "blocked")
        e.set_status(iid.lower(), "active")
        loop.tick(e.p)  # nor while blocked: resuming it would start a copy
        self.assertEqual(len(e.calls()), 1)
        e.set_state(iid.lower(), "done")
        loop.tick(e.p)
        resume = e.calls()[-1]["argv"]
        self.assertIn("--resume", resume)
        self.assertEqual(resume[resume.index("--resume") + 1], e.issue(iid)["agent"]["session"])
        self.assertIn("«B»", resume[-1])
        self.assertEqual(backlog.undelivered(backlog.load(e.p)), [])

    def test_an_idle_session_gets_the_answer_whatever_state_it_reports(self):
        for state, status in (("blocked", "idle"), ("working", "idle"), ("blocked", None)):
            with self.subTest(state=state, status=status):
                self.delivered_to_idle(state, status)

    def delivered_to_idle(self, state, status):
        e = Env()
        iid = backlog.add(e.p, "x", status="ready", criterion="y")
        loop.tick(e.p)
        qid = backlog.ask(e.p, iid, iid.lower(), "A or B?")
        backlog.answer(e.p, qid, "B")
        e.set_state(iid.lower(), state)
        if status:
            e.set_status(iid.lower(), status)
        loop.tick(e.p)
        self.assertIn("--resume", e.calls()[-1]["argv"])
        self.assertEqual(backlog.undelivered(backlog.load(e.p)), [])


class Stopped(unittest.TestCase):
    def test_an_agent_that_stops_without_finishing_is_nudged_once_then_blocked(self):
        e = Env()
        iid = backlog.add(e.p, "x", status="ready", criterion="y")
        loop.tick(e.p)
        backlog.update(e.p, iid, agent={**e.issue(iid)["agent"], "started": "2000-01-01 00:00:00"})
        e.set_state(iid.lower(), "done")
        loop.tick(e.p)
        self.assertIn("not finished", e.calls()[-1]["argv"][-1])
        self.assertEqual(e.issue(iid)["nudges"], 1)
        backlog.update(e.p, iid, agent={**e.issue(iid)["agent"], "nudged": "2000-01-01 00:00:00"})
        loop.tick(e.p)
        self.assertIn("stopped without finishing", e.issue(iid)["blocked"])
        self.assertEqual(len(e.calls()), 2)


class Queue(unittest.TestCase):
    def deliver(self, e: Env, lines: int = 1, open_findings: int = 0) -> str:
        iid = backlog.add(e.p, "x", status="ready", criterion="y")
        loop.tick(e.p)
        wt = e.issue(iid)["worktree"]
        with open(os.path.join(wt, "app.py"), "a") as f:
            f.write("".join(f"# {n}\n" for n in range(lines)))
        git(e.repo.parent / wt, "commit", "-qam", "change")
        backlog.set_status(e.p, iid, "in review", report={"summary": "s", "confused": ""})
        e.review(iid, open_findings)
        return iid

    def test_manual_waits_for_the_owner_who_accepts(self):
        e = Env(test="true")
        iid = self.deliver(e)
        loop.tick(e.p)
        i = e.issue(iid)
        self.assertEqual((i["status"], i["check"]["risk"], i["check"]["tests"]), ("in review", "low", "pass"))
        integrate.accept(e.p, iid)
        self.assertEqual(e.issue(iid)["status"], "in review")  # accepting merges nothing
        loop.tick(e.p)
        self.assertEqual(e.issue(iid)["status"], "done")
        self.assertIn("Integrate TY-1", git(e.repo, "log", "--oneline", "-1"))
        self.assertNotIn("fm/ty-1", git(e.repo, "branch"))

    def test_one_tick_checks_one_branch_and_merges_the_accepted_one_first(self):
        e = Env(test="true")
        ids = [self.deliver(e) for _ in range(3)]
        loop.tick(e.p)
        integrate.accept(e.p, ids[0])
        (e.repo / "other.py").write_text("x = 1\n")
        git(e.repo, "add", "other.py")
        git(e.repo, "commit", "-qm", "main moves")  # every branch is now stale
        checks: list[str] = []
        real = integrate.check
        integrate.check = lambda p, iid, *a: checks.append(iid) or real(p, iid, *a)
        self.addCleanup(setattr, integrate, "check", real)
        try:
            loop.tick(e.p)
            self.assertEqual((e.issue(ids[0])["status"], checks), ("done", [ids[1]]))
            loop.tick(e.p)
            self.assertEqual(checks, [ids[1], ids[2]])
        finally:
            integrate.check = real

    def test_a_failure_after_the_check_does_not_start_a_second_check_in_the_tick(self):
        e = Env(test="true")
        ids = [self.deliver(e) for _ in range(3)]
        (e.repo / "other.py").write_text("x = 1\n")
        git(e.repo, "add", "other.py")
        git(e.repo, "commit", "-qm", "main moves")
        checks: list[str] = []
        real, deliver = integrate.check, loop.pulls.deliver
        integrate.check = lambda p, iid, *a: checks.append(iid) or real(p, iid, *a)

        def boom(*a):
            raise RuntimeError("boom")

        loop.pulls.deliver = boom
        self.addCleanup(setattr, integrate, "check", real)
        self.addCleanup(setattr, loop.pulls, "deliver", deliver)
        loop.tick(e.p)
        self.assertEqual(len(checks), 1)
        self.assertEqual(len(ids), 3)

    def test_a_slow_check_does_not_hold_up_a_launch(self):
        e = Env(test="case $PWD in */trees/*) sleep 3;; esac")
        a = self.deliver(e)
        (e.repo / "other.py").write_text("x = 1\n")
        git(e.repo, "add", "other.py")
        git(e.repo, "commit", "-qm", "main moves")
        ready = backlog.add(e.p, "later", status="ready", criterion="z")
        util.BACKGROUND_WAIT_S = 0.2
        self.addCleanup(setattr, util, "BACKGROUND_WAIT_S", 20)
        loop.tick(e.p)
        self.assertEqual(e.issue(ready)["status"], "in progress")  # launched while the check still runs
        self.assertNotEqual((e.issue(a).get("check") or {}).get("main"), git(e.repo, "rev-parse", "main").strip())
        for t in list(util.BACKGROUND.values()):
            t.join()
        self.assertEqual(e.issue(a)["check"]["main"], git(e.repo, "rev-parse", "main").strip())

    def test_a_task_canceled_while_the_queue_runs_is_not_checked(self):
        e = Env(test="true")
        a, b = self.deliver(e), self.deliver(e)
        real = loop.queue_one

        def cancel_b_first(p, i, main, rows):
            if i["id"] == a:
                integrate.close(p, b, "canceled", "x")
            real(p, i, main, rows)

        loop.queue_one = cancel_b_first
        try:
            loop.tick(e.p)
        finally:
            loop.queue_one = real
        self.assertFalse(e.issue(b).get("check"))

    def test_autonomy_low_merges_low_risk_alone(self):
        e = Env(test="true", autonomy="low")
        iid = self.deliver(e)
        loop.tick(e.p)
        self.assertEqual(e.issue(iid)["status"], "done")

    def test_a_big_diff_alone_does_not_raise_risk(self):
        e = Env(test="true", autonomy="low")
        big = self.deliver(e, lines=400)
        loop.tick(e.p)
        self.assertEqual((e.issue(big)["status"], e.issue(big)["check"]["risk"]), ("done", "low"))

    def test_failing_tests_are_high(self):
        e = Env(test="false", autonomy="medium")
        iid = self.deliver(e)
        loop.tick(e.p)
        c = e.issue(iid)["check"]
        self.assertEqual((e.issue(iid)["status"], c["risk"]), ("in review", "high"))
        self.assertTrue(any("tests fail" in r for r in c["reasons"]))

    def test_the_loop_hunts_first_and_launches_the_reviewer_when_the_hunter_has_written_its_file(self):
        e = Env(test="true")
        iid = backlog.add(e.p, "x", status="ready", criterion="y", detail="DETAIL-OF-THE-TASK")
        loop.tick(e.p)
        backlog.set_status(e.p, iid, "in review", report={"summary": "s"})
        loop.tick(e.p)
        call = e.calls()[-1]
        self.assertEqual(call["argv"][call["argv"].index("-n") + 1], f"{iid.lower()}-hunt")
        hunter = call["argv"][-1]
        self.assertIn("[fm hunter TY-1]", hunter)
        self.assertEqual(call["cwd"], e.issue(iid)["worktree"])
        hunter_file = e.issue(iid)["hunter"]["file"]
        self.assertIn(hunter_file, hunter)
        loop.tick(e.p)
        self.assertEqual(len(e.calls()), 2)  # no reviewer while the hunter has no file
        e.hunter_done(iid)
        loop.tick(e.p)
        call = e.calls()[-1]
        self.assertEqual(call["argv"][call["argv"].index("-n") + 1], f"{iid.lower()}-review")
        reviewer = call["argv"][-1]
        self.assertIn("[fm reviewer TY-1]", reviewer)
        self.assertIn("Round: 1", reviewer)
        self.assertIn(hunter_file, reviewer)  # it reads the file, nothing else of the hunter
        self.assertIn("DETAIL-OF-THE-TASK", hunter)
        self.assertIn("DETAIL-OF-THE-TASK", reviewer)
        self.assertIn("a finding leaves only with a cited refutation", reviewer)  # no cited refutation: it stays in To fix
        self.assertEqual(call["cwd"], e.issue(iid)["worktree"])
        self.assertNotIn("check", e.issue(iid))  # no check before the verdict
        loop.tick(e.p)
        self.assertEqual(len(e.calls()), 3)  # not launched twice

    def test_a_finished_hunter_and_a_reviewer_with_a_verdict_are_stopped(self):
        e = Env(test="true")
        iid = backlog.add(e.p, "x", status="ready", criterion="y")
        loop.tick(e.p)
        backlog.set_status(e.p, iid, "in review", report={"summary": "s"})
        e.hunt_then_review(iid)
        loop.tick(e.p)
        self.assertEqual(e.state_of(e.issue(iid)["hunter"]["name"]), "stopped")
        self.assertEqual(e.state_of(e.issue(iid)["reviewer"]["name"]), "working")
        e.verdict(iid)
        loop.tick(e.p)
        self.assertEqual(e.state_of(e.issue(iid)["reviewer"]["name"]), "stopped")

    def test_cancelling_a_task_in_review_stops_every_session_of_it(self):
        e = Env(test="true")
        iid = backlog.add(e.p, "x", status="ready", criterion="y")
        loop.tick(e.p)
        backlog.set_status(e.p, iid, "in review", report={"summary": "s"})
        e.hunt_then_review(iid)
        i = e.issue(iid)
        integrate.close(e.p, iid, "canceled", "no")
        for role in ("agent", "hunter", "reviewer"):
            self.assertEqual(e.state_of(i[role]["name"]), "stopped", role)

    def test_merging_stops_every_session_of_the_task(self):
        e = Env(test="true")
        iid = self.deliver(e)
        e.review(iid)
        i = e.issue(iid)
        loop.tick(e.p)
        integrate.accept(e.p, iid)
        loop.tick(e.p)
        self.assertEqual(e.issue(iid)["status"], "done")
        for role in ("agent", "hunter", "reviewer"):
            self.assertEqual(e.state_of(i[role]["name"]), "stopped", role)

    def test_a_blocked_task_still_has_its_finished_hunter_stopped(self):
        e = Env(test="true")
        iid = backlog.add(e.p, "x", status="ready", criterion="y")
        loop.tick(e.p)
        backlog.set_status(e.p, iid, "in review", report={"summary": "s"})
        loop.tick(e.p)
        e.hunter_done(iid)
        backlog.update(e.p, iid, blocked="why")
        loop.tick(e.p)
        self.assertEqual(e.state_of(e.issue(iid)["hunter"]["name"]), "stopped")

    def test_a_failing_stop_does_not_stop_the_cancel(self):
        from unittest import mock

        e = Env(test="true")
        iid = backlog.add(e.p, "x", status="ready", criterion="y")
        loop.tick(e.p)
        with mock.patch("fm.sessions.stop", side_effect=proc.ProcError("timed out")):
            integrate.close(e.p, iid, "canceled", "no")
        self.assertEqual(e.issue(iid)["status"], "canceled")

    def test_cancel_stops_a_session_the_listing_does_not_show(self):
        e = Env(test="true")
        iid = backlog.add(e.p, "x", status="ready", criterion="y")
        loop.tick(e.p)
        (e.bin / "agents.json").write_text("[]")  # unreadable or not yet listed: the agent still runs
        integrate.close(e.p, iid, "canceled", "no")
        self.assertEqual(e.stops(), [e.issue(iid)["agent"]["short"]])

    def test_the_review_tick_does_not_stop_again_a_session_the_listing_does_not_show(self):
        e = Env(test="true")
        iid = backlog.add(e.p, "x", status="ready", criterion="y")
        loop.tick(e.p)
        backlog.set_status(e.p, iid, "in review", report={"summary": "s"})
        loop.tick(e.p)
        e.hunter_done(iid)
        (e.bin / "agents.json").write_text("[]")  # gone, or an unreadable listing
        for _ in range(3):
            loop.tick(e.p)
        self.assertEqual(e.stops(), [])

    def test_a_cancel_stops_each_session_once(self):
        e = Env(test="true")
        iid = backlog.add(e.p, "x", status="ready", criterion="y")
        loop.tick(e.p)
        name = e.issue(iid)["agent"]["name"]
        integrate.close(e.p, iid, "canceled", "no")
        self.assertEqual(e.stops(), [e.issue(iid)["agent"]["short"]])  # once, though its id is also in `sessions`
        self.assertEqual(e.state_of(name), "stopped")

    def test_a_hunter_that_ends_without_its_file_blocks_the_task(self):
        e = Env(test="true")
        iid = backlog.add(e.p, "x", status="ready", criterion="y")
        loop.tick(e.p)
        backlog.set_status(e.p, iid, "in review", report={"summary": "s"})
        loop.tick(e.p)
        e.set_state(e.issue(iid)["hunter"]["name"], "done")
        backlog.update(e.p, iid, hunter={**e.issue(iid)["hunter"], "started": "2000-01-01 00:00:00"})
        loop.tick(e.p)
        self.assertIn("hunter ended without its findings file", e.issue(iid)["blocked"])
        self.assertEqual(len(e.calls()), 2)

    def test_open_findings_go_back_to_the_agent_then_round_two_then_the_owner(self):
        e = Env(test="true", autonomy="medium")
        iid = self.deliver(e, open_findings=1)
        loop.tick(e.p)
        self.assertEqual(e.issue(iid)["status"], "in progress")
        e.set_state(iid.lower(), "done")
        loop.tick(e.p)
        self.assertIn("leaves 1 open", e.calls()[-1]["argv"][-1])
        wt = e.issue(iid)["worktree"]
        with open(os.path.join(wt, "app.py"), "a") as f:
            f.write("# fixed\n")
        git(e.repo.parent / wt, "commit", "-qam", "fix")
        backlog.set_status(e.p, iid, "in review")
        e.review(iid, 1)  # round 2 still open: to the owner, whatever the autonomy
        self.assertIn("Round: 2", e.calls()[-1]["argv"][-1])
        loop.tick(e.p)
        i = e.issue(iid)
        self.assertEqual((i["status"], i["check"]["risk"]), ("in review", "low"))
        self.assertTrue(any("open after round 2" in r for r in i["check"]["reasons"]))

    def test_a_branch_that_conflicts_after_main_moved_goes_back_to_its_agent(self):
        e = Env(test="true")
        iid = self.deliver(e)
        loop.tick(e.p)
        (e.repo / "app.py").write_text("changed on main\n")
        git(e.repo, "commit", "-qam", "main moves")
        loop.tick(e.p)
        i = e.issue(iid)
        self.assertEqual((i["status"], i["conflicts"]), ("in progress", 1))
        e.set_state(iid.lower(), "done")
        loop.tick(e.p)
        self.assertIn("conflicts", e.calls()[-1]["argv"][-1])

    def test_reject_sends_the_note_back_to_the_agent(self):
        e = Env(test="true")
        iid = self.deliver(e)
        fm(e.home, "reject", iid, "name it better")
        self.assertEqual((e.issue(iid)["status"], e.issue(iid)["rework"]), ("in progress", 1))
        e.set_state(iid.lower(), "done")
        loop.tick(e.p)
        self.assertIn("name it better", e.calls()[-1]["argv"][-1])

    def fix_and_resubmit(self, e: Env, iid: str) -> None:
        e.set_state(iid.lower(), "done")
        loop.tick(e.p)
        wt = e.issue(iid)["worktree"]
        with open(os.path.join(wt, "app.py"), "a") as f:
            f.write("# fixed\n")
        git(e.repo.parent / wt, "commit", "-qam", "fix")
        backlog.set_status(e.p, iid, "in review")

    def test_open_findings_send_the_review_back(self):
        e = Env(test="true")
        iid = self.deliver(e, open_findings=1)
        loop.tick(e.p)
        self.assertEqual(e.issue(iid)["status"], "in progress")
        e.set_state(iid.lower(), "done")
        loop.tick(e.p)
        self.assertIn(f"reviews/{iid}.md", e.calls()[-1]["argv"][-1])

    def test_clean_review_sends_nothing(self):
        e = Env(test="true")
        iid = self.deliver(e)
        loop.tick(e.p)
        i = e.issue(iid)
        self.assertEqual(i["status"], "in review")
        self.assertIn("check", i)
        d = backlog.load(e.p)
        self.assertFalse([x for x in d["decisions_resolved"] if x.get("kind") == "review"])

    def test_refuted_without_commit_is_not_sent_back_twice(self):
        e = Env(test="true")
        iid = self.deliver(e, open_findings=1)
        loop.tick(e.p)
        e.set_state(iid.lower(), "done")
        loop.tick(e.p)
        backlog.set_status(e.p, iid, "in review")
        loop.tick(e.p)
        i = e.issue(iid)
        self.assertEqual((i["status"], i["check"]["risk"]), ("in review", "low"))

    def test_review_step_table(self):
        rows = [
            ({"open": 1, "round": 1}, "back"),
            ({"open": 0, "round": 1}, "queue"),
            ({"open": 1, "round": 2}, "queue"),
            ({"open": 1, "round": 1, "sent_back": True}, "queue"),
            ({"high": 1, "medium": 0, "low": 2, "round": 1}, "back"),  # a verdict from before the single count
            ({"high": 0, "medium": 0, "low": 2, "round": 1}, "queue"),
        ]
        for v, want in rows:
            self.assertEqual(risk.review_step(v, 2), want, v)


if __name__ == "__main__":
    unittest.main()
