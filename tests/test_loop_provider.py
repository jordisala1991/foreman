import os
import unittest

from helpers import Env, git
from test_loop import Queue

from fm import backlog, integrate, loop
from fm.providers import registry


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

    def test_a_main_checkout_on_another_branch_blocks_the_merge(self):
        e = Env(test="true")
        iid = self.delivered(e)
        loop.tick(e.p)
        integrate.accept(e.p, iid)
        git(e.repo, "checkout", "-q", "-b", "elsewhere")
        loop.tick(e.p)
        self.assertIn("is not on main", e.issue(iid)["blocked"])
        self.assertNotIn("merge", e.issue(iid))

    def accepted_then_main_moves(self, e):
        iid = self.delivered(e)
        loop.tick(e.p)
        integrate.accept(e.p, iid)
        (e.repo / "app.py").write_text("changed on main\n")
        git(e.repo, "commit", "-qam", "main moved")
        return iid

    def test_an_accepted_branch_that_conflicts_at_merge_goes_back_as_a_conflict(self):
        for strategy in ("squash", "merge"):
            e = Env(test="true")
            e.p.cfg["integrate"]["merge"] = strategy
            iid = self.accepted_then_main_moves(e)
            main = git(e.repo, "rev-parse", "main").strip()
            loop.tick(e.p)
            loop.tick(e.p)
            i = e.issue(iid)
            self.assertEqual(i["status"], "in progress")
            self.assertEqual(i["conflicts"], 1)
            self.assertEqual(backlog.load(e.p)["decisions_resolved"][-1]["kind"], "conflict")
            self.assertFalse(i.get("blocked"))
            self.assertNotIn("accepted", i)
            self.assertEqual(git(e.repo, "rev-parse", "main").strip(), main)
            self.assertEqual(git(e.repo, "status", "--porcelain").strip(), "")

    def test_an_accepted_branch_whose_merge_fails_otherwise_is_still_blocked(self):
        e = Env(test="true")
        iid = self.delivered(e)
        loop.tick(e.p)
        integrate.accept(e.p, iid)
        (e.repo / "app.py").write_text("dirty\n")
        loop.tick(e.p)
        i = e.issue(iid)
        self.assertTrue(i["blocked"])
        self.assertEqual(i["status"], "in review")
        self.assertIn("accepted", i)
        self.assertNotIn("conflicts", i)

    def test_after_the_fix_the_task_is_accepted_again_and_merges(self):
        e = Env(test="true")
        iid = self.accepted_then_main_moves(e)
        loop.tick(e.p)
        wt = e.repo.parent / e.issue(iid)["worktree"]
        git(wt, "merge", "-X", "ours", "main", "-m", "fix")
        backlog.set_status(e.p, iid, "in review", report={"summary": "s", "confused": ""})
        e.review(iid)
        loop.tick(e.p)
        integrate.accept(e.p, iid)
        loop.tick(e.p)
        self.assertEqual(e.issue(iid)["status"], "done")

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
