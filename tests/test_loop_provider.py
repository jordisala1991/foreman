import os
import unittest

from helpers import Env, git
from test_loop import Queue

from fm import backlog, delivery, loop
from fm.providers import registry


class Provider(unittest.TestCase):
    def delivered(self, e):
        return Queue().deliver(e)

    def test_accept_only_marks_and_the_task_closes_when_the_merge_is_seen_in_main(self):
        from fm.providers import local

        e = Env(test="true")
        tid = self.delivered(e)
        loop.tick(e.p)
        before = git(e.repo, "rev-parse", "main").strip()
        delivery.accept(e.p, tid)
        i = e.task(tid)
        self.assertEqual((i["status"], i["accepted"]["by"]), ("in review", "owner"))
        self.assertEqual(git(e.repo, "rev-parse", "main").strip(), before)
        wt = i["worktree"]
        # the provider merges but main does not show it yet: the task stays open, worktree and branch stay
        orig = local.Local.state
        local.Local.state = lambda self, change: {"merged": False, "closed": False}
        try:
            loop.tick(e.p)
        finally:
            local.Local.state = orig
        self.assertEqual(e.task(tid)["status"], "in review")
        self.assertTrue(e.task(tid)["merge"])
        self.assertIn("fm/ty-1", git(e.repo, "branch"))
        self.assertTrue(os.path.isdir(e.repo.parent / wt))
        loop.tick(e.p)  # seen done in main: closes and cleans, without merging twice
        self.assertEqual(e.task(tid)["status"], "done")
        self.assertNotIn("fm/ty-1", git(e.repo, "branch"))
        self.assertFalse(os.path.isdir(e.repo.parent / wt))
        self.assertEqual(git(e.repo, "rev-list", "--count", f"{before}..main").strip(), "1")

    def test_squash_by_default_merge_is_no_ff(self):
        for strategy, parents in (("", 1), ("merge", 2)):
            e = Env(test="true", autonomy="low")
            if strategy:
                e.home.joinpath("fm.toml").write_text(e.home.joinpath("fm.toml").read_text().replace("[merge]", f'[merge]\nstrategy = "{strategy}"'))
                e.p = type(e.p).load(e.home)
            tid = self.delivered(e)
            loop.tick(e.p)
            self.assertEqual(e.task(tid)["status"], "done")
            self.assertEqual(len(git(e.repo, "log", "-1", "--format=%P", "main").split()), parents)

    def test_unknown_merge_or_provider_is_a_clear_config_error(self):
        from fm import providers

        for key, val in (("strategy", "rebase"), ("provider", "gitlab"), ("pr", "never")):
            e = Env(test="true")
            e.p.cfg["merge"][key] = val
            with self.assertRaisesRegex(providers.ConfigError, f"{key} = '{val}'"):
                registry.get(e.p)
            tid = self.delivered(e)
            loop.tick(e.p)
            with self.assertRaises(providers.ConfigError):
                delivery.accept(e.p, tid)

    def test_rejection_forgets_the_merge_of_the_old_tip(self):
        e = Env(test="true")
        tid = self.delivered(e)
        loop.tick(e.p)
        backlog.update(e.p, tid, accepted={"by": "o", "head": "x", "at": ""}, merge="abc", merging="def")
        loop.return_to_agent(e.p, tid, "rejection", "again")
        i = e.task(tid)
        self.assertFalse({"accepted", "merge", "merging"} & set(i))

    def test_a_crash_after_the_merge_adopts_it_instead_of_merging_twice(self):
        e = Env(test="true")
        tid = self.delivered(e)
        loop.tick(e.p)
        delivery.accept(e.p, tid)
        base = git(e.repo, "rev-parse", "main").strip()
        git(e.repo, "merge", "--squash", "fm/ty-1")
        git(e.repo, "commit", "-qm", f"Merge {tid}: x")  # the merge happened; its ref was never stored
        backlog.update(e.p, tid, merging=base)
        merged = git(e.repo, "rev-parse", "main").strip()
        loop.tick(e.p)
        self.assertEqual(e.task(tid)["status"], "done")
        self.assertEqual(git(e.repo, "rev-parse", "main").strip(), merged)

    def test_a_failing_merge_blocks_the_task_with_the_reason(self):
        e = Env(test="true")
        tid = self.delivered(e)
        loop.tick(e.p)
        delivery.accept(e.p, tid)
        (e.repo / "app.py").write_text("dirty\n")  # the main checkout has uncommitted changes
        loop.tick(e.p)
        i = e.task(tid)
        self.assertEqual(i["status"], "in review")
        self.assertIn("uncommitted changes", i["blocked"])
        self.assertNotIn("merge", i)

    def test_a_main_checkout_on_another_branch_blocks_the_merge(self):
        e = Env(test="true")
        tid = self.delivered(e)
        loop.tick(e.p)
        delivery.accept(e.p, tid)
        git(e.repo, "checkout", "-q", "-b", "elsewhere")
        loop.tick(e.p)
        self.assertIn("is not on main", e.task(tid)["blocked"])
        self.assertNotIn("merge", e.task(tid))

    def accepted_then_main_moves(self, e):
        tid = self.delivered(e)
        loop.tick(e.p)
        delivery.accept(e.p, tid)
        (e.repo / "app.py").write_text("changed on main\n")
        git(e.repo, "commit", "-qam", "main moved")
        return tid

    def test_an_accepted_branch_that_conflicts_at_merge_goes_back_as_a_conflict(self):
        for strategy in ("squash", "merge"):
            e = Env(test="true")
            e.p.cfg["merge"]["strategy"] = strategy
            tid = self.accepted_then_main_moves(e)
            main = git(e.repo, "rev-parse", "main").strip()
            loop.tick(e.p)
            loop.tick(e.p)
            i = e.task(tid)
            self.assertEqual(i["status"], "in progress")
            self.assertEqual(i["conflicts"], 1)
            self.assertEqual(backlog.load(e.p)["decisions_resolved"][-1]["kind"], "conflict")
            self.assertFalse(i.get("blocked"))
            self.assertNotIn("accepted", i)
            self.assertEqual(git(e.repo, "rev-parse", "main").strip(), main)
            self.assertEqual(git(e.repo, "status", "--porcelain").strip(), "")

    def test_an_accepted_branch_whose_merge_fails_otherwise_is_still_blocked(self):
        e = Env(test="true")
        tid = self.delivered(e)
        loop.tick(e.p)
        delivery.accept(e.p, tid)
        (e.repo / "app.py").write_text("dirty\n")
        loop.tick(e.p)
        i = e.task(tid)
        self.assertTrue(i["blocked"])
        self.assertEqual(i["status"], "in review")
        self.assertIn("accepted", i)
        self.assertNotIn("conflicts", i)

    def test_after_the_fix_the_task_is_accepted_again_and_merges(self):
        e = Env(test="true")
        tid = self.accepted_then_main_moves(e)
        loop.tick(e.p)
        wt = e.repo.parent / e.task(tid)["worktree"]
        git(wt, "merge", "-X", "ours", "main", "-m", "fix")
        backlog.set_status(e.p, tid, "in review", report={"summary": "s", "confused": ""})
        e.review(tid)
        loop.tick(e.p)
        delivery.accept(e.p, tid)
        loop.tick(e.p)
        self.assertEqual(e.task(tid)["status"], "done")

    def test_a_bad_provider_blocks_at_the_merge_step(self):
        e = Env(test="true")
        tid = self.delivered(e)
        loop.tick(e.p)
        delivery.accept(e.p, tid)
        e.p.cfg["merge"]["strategy"] = "rebase"
        loop.run_queue(e.p, {})
        self.assertIn("strategy = 'rebase'", e.task(tid)["blocked"])

    def test_defaults_and_nothing_is_published(self):

        e = Env()
        self.assertEqual((e.p.get("merge", "provider"), e.p.get("merge", "strategy")), ("auto", "squash"))
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
