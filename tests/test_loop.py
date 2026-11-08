import os
import unittest

from helpers import Env, git

from fm import backlog, integrate, loop


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
        loop.tick(e.p)  # nor while blocked: resuming it would start a copy
        self.assertEqual(len(e.calls()), 1)
        e.set_state(iid.lower(), "done")
        loop.tick(e.p)
        resume = e.calls()[-1]["argv"]
        self.assertIn("--resume", resume)
        self.assertEqual(resume[resume.index("--resume") + 1], e.issue(iid)["agent"]["session"])
        self.assertIn("«B»", resume[-1])
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
    def deliver(self, e: Env, lines: int = 1, high: int = 0, medium: int = 0) -> str:
        iid = backlog.add(e.p, "x", status="ready", criterion="y")
        loop.tick(e.p)
        wt = e.issue(iid)["worktree"]
        with open(os.path.join(wt, "app.py"), "a") as f:
            f.write("".join(f"# {n}\n" for n in range(lines)))
        git(e.repo.parent / wt, "commit", "-qam", "change")
        backlog.set_status(e.p, iid, "in review", report={"summary": "s", "confused": ""})
        e.review(iid, high, medium)
        return iid

    def test_manual_waits_for_the_owner_who_accepts(self):
        e = Env(test="true")
        iid = self.deliver(e)
        loop.tick(e.p)
        i = e.issue(iid)
        self.assertEqual((i["status"], i["check"]["risk"], i["check"]["tests"]), ("in review", "low", "pass"))
        integrate.accept(e.p, iid)
        self.assertEqual(e.issue(iid)["status"], "done")
        self.assertIn("Integrate TY-1", git(e.repo, "log", "--oneline", "-1"))
        self.assertNotIn("fm/ty-1", git(e.repo, "branch"))

    def test_autonomy_low_merges_low_risk_alone(self):
        e = Env(test="true", autonomy="low")
        iid = self.deliver(e)
        loop.tick(e.p)
        self.assertEqual(e.issue(iid)["status"], "done")

    def test_autonomy_low_leaves_medium_and_high(self):
        e = Env(test="true", autonomy="low")
        big = self.deliver(e, lines=120)
        loop.tick(e.p)
        self.assertEqual((e.issue(big)["status"], e.issue(big)["check"]["risk"]), ("in review", "medium"))

    def test_failing_tests_are_high(self):
        e = Env(test="false", autonomy="medium")
        iid = self.deliver(e)
        loop.tick(e.p)
        c = e.issue(iid)["check"]
        self.assertEqual((e.issue(iid)["status"], c["risk"]), ("in review", "high"))
        self.assertTrue(any("tests fail" in r for r in c["reasons"]))

    def test_the_loop_launches_an_independent_reviewer_in_the_worktree(self):
        e = Env(test="true")
        iid = backlog.add(e.p, "x", status="ready", criterion="y")
        loop.tick(e.p)
        backlog.set_status(e.p, iid, "in review", report={"summary": "s"})
        loop.tick(e.p)
        call = e.calls()[-1]
        self.assertEqual(call["argv"][call["argv"].index("-n") + 1], f"{iid.lower()}-review")
        self.assertIn("[fm reviewer TY-1]", call["argv"][-1])
        self.assertIn("Round: 1", call["argv"][-1])
        self.assertEqual(call["cwd"], e.issue(iid)["worktree"])
        self.assertNotIn("check", e.issue(iid))  # no check before the verdict
        loop.tick(e.p)
        self.assertEqual(len(e.calls()), 2)  # not launched twice

    def test_open_findings_go_back_to_the_agent_then_round_two_then_the_owner(self):
        e = Env(test="true", autonomy="medium")
        iid = self.deliver(e, high=1)
        loop.tick(e.p)
        self.assertEqual(e.issue(iid)["status"], "in progress")
        e.set_state(iid.lower(), "done")
        loop.tick(e.p)
        self.assertIn("H 1, M 0", e.calls()[-1]["argv"][-1])
        wt = e.issue(iid)["worktree"]
        with open(os.path.join(wt, "app.py"), "a") as f:
            f.write("# fixed\n")
        git(e.repo.parent / wt, "commit", "-qam", "fix")
        backlog.set_status(e.p, iid, "in review")
        e.review(iid, medium=1)  # round 2 still open: to the owner, as high risk
        self.assertIn("Round: 2", e.calls()[-1]["argv"][-1])
        loop.tick(e.p)
        i = e.issue(iid)
        self.assertEqual((i["status"], i["check"]["risk"]), ("in review", "high"))
        self.assertTrue(any("open after round 2" in r for r in i["check"]["reasons"]))

    def test_open_or_missing_review_is_high(self):
        e = Env(test="true", autonomy="medium")
        f = [{"path": "a.py", "added": 1, "deleted": 0}]
        kw = dict(tests="pass", files=f, deleted=[], conflict=False, commits=1)
        self.assertEqual(integrate.score(e.p, review="open after round 2: H 0, M 1", **kw)[0], "high")
        self.assertEqual(integrate.score(e.p, review="", **kw)[0], "high")
        self.assertEqual(integrate.score(e.p, review="clean", **kw)[0], "low")

    def test_sensitive_path_is_high(self):
        e = Env(test="true")
        self.assertTrue(integrate.is_sensitive(".github/workflows/ci.yml", e.p.get("integrate", "sensitive")))
        self.assertTrue(integrate.is_sensitive("conf/fm.toml", e.p.get("integrate", "sensitive")))
        self.assertFalse(integrate.is_sensitive("src/app.py", e.p.get("integrate", "sensitive")))

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
        from fm import cli

        e = Env(test="true")
        iid = self.deliver(e)
        cli.main(["--home", str(e.home), "reject", iid, "name it better"])
        self.assertEqual((e.issue(iid)["status"], e.issue(iid)["rework"]), ("in progress", 1))
        e.set_state(iid.lower(), "done")
        loop.tick(e.p)
        self.assertIn("name it better", e.calls()[-1]["argv"][-1])


if __name__ == "__main__":
    unittest.main()
