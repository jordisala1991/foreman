import os
import unittest

from helpers import Env, fm, git

from fm import backlog, integrate, loop, risk
from fm.providers import registry


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
        loop.tick(e.p)
        return self.flags(e)

    def test_every_kind_launches_with_an_explicit_model_and_effort(self):
        e = Env(test="true")
        self.assertEqual(
            self.run_all_kinds(e),
            {"define": ("sonnet", "high"), "work": ("sonnet", "medium"), "reviewer": ("sonnet", "high")},
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
            {"define": ("opus", "max"), "work": ("haiku", "low"), "reviewer": ("opus", "xhigh")},
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
        loop.tick(e.p)  # nor while blocked: resuming it would start a copy
        self.assertEqual(len(e.calls()), 1)
        e.set_state(iid.lower(), "done")
        loop.tick(e.p)
        resume = e.calls()[-1]["argv"]
        self.assertIn("--resume", resume)
        self.assertEqual(resume[resume.index("--resume") + 1], e.issue(iid)["agent"]["session"])
        self.assertIn("«B»", resume[-1])
        self.assertEqual(backlog.undelivered(backlog.load(e.p)), [])


class Provider(unittest.TestCase):
    def delivered(self, e):
        return Queue().deliver(e)

    def test_accept_only_marks_and_the_issue_closes_when_the_merge_is_seen_in_main(self):
        from fm.providers import local

        e = Env(test="true")
        iid = self.delivered(e)
        loop.tick(e.p)
        before = git(e.repo, "rev-parse", "main").strip()
        integrate.accept(e.p, iid)
        i = e.issue(iid)
        self.assertEqual((i["status"], i["accepted"]["by"]), ("in review", "owner"))
        self.assertEqual(git(e.repo, "rev-parse", "main").strip(), before)
        wt = i["worktree"]
        # the provider merges but main does not show it yet: the issue stays open, worktree and branch stay
        orig = local.Local.state
        local.Local.state = lambda self, change: {"merged": False, "closed": False}
        try:
            loop.tick(e.p)
        finally:
            local.Local.state = orig
        self.assertEqual(e.issue(iid)["status"], "in review")
        self.assertTrue(e.issue(iid)["merge"])
        self.assertIn("fm/ty-1", git(e.repo, "branch"))
        self.assertTrue(os.path.isdir(e.repo.parent / wt))
        loop.tick(e.p)  # seen done in main: closes and cleans, without merging twice
        self.assertEqual(e.issue(iid)["status"], "done")
        self.assertNotIn("fm/ty-1", git(e.repo, "branch"))
        self.assertFalse(os.path.isdir(e.repo.parent / wt))
        self.assertEqual(git(e.repo, "rev-list", "--count", f"{before}..main").strip(), "1")

    def test_squash_by_default_merge_is_no_ff(self):
        for strategy, parents in (("", 1), ("merge", 2)):
            e = Env(test="true", autonomy="low")
            if strategy:
                e.home.joinpath("fm.toml").write_text(e.home.joinpath("fm.toml").read_text().replace("[integrate]", f'[integrate]\nmerge = "{strategy}"'))
                e.p = type(e.p).load(e.home)
            iid = self.delivered(e)
            loop.tick(e.p)
            self.assertEqual(e.issue(iid)["status"], "done")
            self.assertEqual(len(git(e.repo, "log", "-1", "--format=%P", "main").split()), parents)

    def test_unknown_merge_or_provider_is_a_clear_config_error(self):
        from fm import providers

        for key, val in (("merge", "rebase"), ("provider", "gitlab"), ("pr", "never")):
            e = Env(test="true")
            e.p.cfg["integrate"][key] = val
            with self.assertRaisesRegex(providers.ConfigError, f"{key} = '{val}'"):
                registry.get(e.p)
            iid = self.delivered(e)
            loop.tick(e.p)
            with self.assertRaises(providers.ConfigError):
                integrate.accept(e.p, iid)

    def test_the_old_forge_key_is_a_clear_config_error(self):
        from fm import providers

        e = Env()
        e.home.joinpath("fm.toml").write_text(e.home.joinpath("fm.toml").read_text().replace("[integrate]", '[integrate]\nforge = "github"'))
        with self.assertRaisesRegex(providers.ConfigError, "forge was renamed to provider"):
            registry.get(type(e.p).load(e.home))

    def test_send_back_forgets_the_merge_of_the_old_tip(self):
        e = Env(test="true")
        iid = self.delivered(e)
        loop.tick(e.p)
        backlog.update(e.p, iid, accepted={"by": "o", "head": "x", "at": ""}, merge="abc", merging="def")
        loop.send_back(e.p, iid, "rejection", "again")
        i = e.issue(iid)
        self.assertFalse({"accepted", "merge", "merging"} & set(i))

    def test_a_crash_after_the_merge_adopts_it_instead_of_merging_twice(self):
        e = Env(test="true")
        iid = self.delivered(e)
        loop.tick(e.p)
        integrate.accept(e.p, iid)
        base = git(e.repo, "rev-parse", "main").strip()
        git(e.repo, "merge", "--squash", "fm/ty-1")
        git(e.repo, "commit", "-qm", f"Integrate {iid}: x")  # the merge happened; its ref was never stored
        backlog.update(e.p, iid, merging=base)
        merged = git(e.repo, "rev-parse", "main").strip()
        loop.tick(e.p)
        self.assertEqual(e.issue(iid)["status"], "done")
        self.assertEqual(git(e.repo, "rev-parse", "main").strip(), merged)

    def test_a_failing_merge_blocks_the_issue_with_the_reason(self):
        e = Env(test="true")
        iid = self.delivered(e)
        loop.tick(e.p)
        integrate.accept(e.p, iid)
        (e.repo / "app.py").write_text("dirty\n")  # the main checkout has uncommitted changes
        loop.tick(e.p)
        i = e.issue(iid)
        self.assertEqual(i["status"], "in review")
        self.assertIn("uncommitted changes", i["blocked"])
        self.assertNotIn("merge", i)

    def test_a_bad_provider_blocks_at_the_merge_step(self):
        e = Env(test="true")
        iid = self.delivered(e)
        loop.tick(e.p)
        integrate.accept(e.p, iid)
        e.p.cfg["integrate"]["merge"] = "rebase"
        loop.run_queue(e.p, {})
        self.assertIn("merge = 'rebase'", e.issue(iid)["blocked"])

    def test_defaults_and_nothing_is_published(self):

        e = Env()
        self.assertEqual((e.p.get("integrate", "provider"), e.p.get("integrate", "merge")), ("auto", "squash"))
        self.assertEqual(registry.get(e.p).name, "local")  # no GitHub remote: local
        import inspect

        from fm.providers import local

        src = inspect.getsource(local)
        for word in ('"push"', '"tag"', "release"):
            self.assertNotIn(word, src)
        e = Env(test="true", autonomy="low")
        self.delivered(e)
        loop.tick(e.p)
        self.assertEqual(git(e.repo, "tag").strip(), "")
        self.assertEqual(git(e.repo, "remote").strip(), "")


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
    def deliver(self, e: Env, lines: int = 1, high: int = 0, medium: int = 0, low: int = 0) -> str:
        iid = backlog.add(e.p, "x", status="ready", criterion="y")
        loop.tick(e.p)
        wt = e.issue(iid)["worktree"]
        with open(os.path.join(wt, "app.py"), "a") as f:
            f.write("".join(f"# {n}\n" for n in range(lines)))
        git(e.repo.parent / wt, "commit", "-qam", "change")
        backlog.set_status(e.p, iid, "in review", report={"summary": "s", "confused": ""})
        e.review(iid, high, medium, low)
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
        e.review(iid, medium=1)  # round 2 still open: to the owner, whatever the autonomy
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

    def test_review_with_h_m_open_sends_back_with_low_included(self):
        e = Env(test="true")
        iid = self.deliver(e, high=1, low=2)
        loop.tick(e.p)
        self.assertEqual(e.issue(iid)["status"], "in progress")
        e.set_state(iid.lower(), "done")
        loop.tick(e.p)
        self.assertIn(f"reviews/{iid}.md", e.calls()[-1]["argv"][-1])

    def test_l_only_review_gives_one_pass_then_the_queue(self):
        e = Env(test="true")
        iid = self.deliver(e, low=2)
        loop.tick(e.p)
        self.assertEqual(e.issue(iid)["status"], "in progress")
        self.fix_and_resubmit(e, iid)
        e.review(iid, low=2)
        n = len(e.calls())
        loop.tick(e.p)
        i = e.issue(iid)
        self.assertEqual(i["status"], "in review")
        self.assertIn("check", i)
        self.assertEqual(len(e.calls()), n)

    def test_clean_review_sends_nothing(self):
        e = Env(test="true")
        iid = self.deliver(e)
        loop.tick(e.p)
        i = e.issue(iid)
        self.assertEqual(i["status"], "in review")
        self.assertIn("check", i)
        d = backlog.load(e.p)
        self.assertFalse([x for x in d["decisions_resolved"] if x.get("kind") == "review"])

    def test_low_findings_leave_the_risk_low(self):
        e = Env(test="true")
        iid = self.deliver(e, low=2)
        loop.tick(e.p)
        self.fix_and_resubmit(e, iid)
        e.review(iid, low=2)
        loop.tick(e.p)
        self.assertEqual(e.issue(iid)["check"]["risk"], "low")

    def test_l_only_pass_answered_without_a_commit_goes_to_the_queue(self):
        e = Env(test="true")
        iid = self.deliver(e, low=1)
        loop.tick(e.p)
        e.set_state(iid.lower(), "done")
        loop.tick(e.p)
        backlog.set_status(e.p, iid, "in review")
        n = len(e.calls())
        loop.tick(e.p)
        i = e.issue(iid)
        self.assertEqual(i["status"], "in review")
        self.assertIn("check", i)
        self.assertEqual(len(e.calls()), n)

    def test_refuted_without_commit_is_not_sent_back_twice(self):
        e = Env(test="true")
        iid = self.deliver(e, high=1)
        loop.tick(e.p)
        e.set_state(iid.lower(), "done")
        loop.tick(e.p)
        backlog.set_status(e.p, iid, "in review")
        loop.tick(e.p)
        i = e.issue(iid)
        self.assertEqual((i["status"], i["check"]["risk"]), ("in review", "low"))

    def test_only_low_findings_open_still_merge_alone(self):
        e = Env(test="true", autonomy="medium")
        iid = self.deliver(e, low=1)
        loop.tick(e.p)
        self.fix_and_resubmit(e, iid)
        e.review(iid, low=1)
        loop.tick(e.p)
        self.assertEqual(e.issue(iid)["status"], "done")

    def test_review_step_table(self):
        rows = [
            (1, 0, 0, 1, False, "back"),
            (0, 1, 3, 1, False, "back"),
            (0, 0, 2, 1, False, "back"),
            (0, 0, 0, 1, False, "queue"),
            (0, 0, 2, 2, False, "queue"),
            (1, 0, 2, 2, False, "queue"),
            (0, 0, 2, 1, True, "queue"),
            (1, 0, 0, 1, True, "queue"),
        ]
        for h, m, low, rnd, sent, want in rows:
            v = {"high": h, "medium": m, "low": low, "round": rnd, "sent_back": sent}
            self.assertEqual(risk.review_step(v, 2), want, v)
        self.assertEqual(risk.review_step({"high": 0, "medium": 0, "round": 1}, 2), "queue")


if __name__ == "__main__":
    unittest.main()
