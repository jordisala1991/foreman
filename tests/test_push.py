"""The loop pushes the task branches (provider.push) and retries transient provider failures (loop.guarded). Fake `gh`
and a bare origin come from test_github."""

import subprocess
import unittest

from helpers import Env, git
from test_github import GH

from fm import backlog, loop


class Pushes(unittest.TestCase):
    def make(self, extra: str = "", **kw) -> GH:
        g = GH(extra, **kw)
        self.addCleanup(g.close)
        return g

    def origin_tip(self, g: GH, iid: str) -> str:
        ref = "refs/heads/" + g.e.issue(iid)["branch"]
        r = subprocess.run(["git", "--git-dir", str(g.bare), "rev-parse", "--verify", "-q", ref], capture_output=True, text=True)
        return r.stdout.strip()

    def local_tip(self, g: GH, iid: str) -> str:
        return subprocess.run(["git", "-C", str(g.e.repo), "rev-parse", g.e.issue(iid)["branch"]], capture_output=True, text=True).stdout.strip()

    def more_commits(self, g: GH, iid: str) -> None:
        wt = g.e.repo.parent / g.e.issue(iid)["worktree"]
        with open(wt / "app.py", "a") as f:
            f.write("# more\n")
        git(wt, "commit", "-qam", "more")

    def test_fm_done_on_an_unpushed_branch_is_pushed_and_goes_to_review(self):
        g = self.make(test="true")
        iid = g.work()
        loop.tick(g.e.p)
        self.more_commits(g, iid)
        g.deliver(iid)
        self.assertNotEqual(self.origin_tip(g, iid), self.local_tip(g, iid))
        loop.tick(g.e.p)
        self.assertEqual(self.origin_tip(g, iid), self.local_tip(g, iid))
        self.assertEqual(g.e.issue(iid)["status"], "in review")
        self.assertFalse(g.e.issue(iid).get("blocked"))
        self.assertFalse(g.state["prs"]["1"]["draft"])

    def test_a_failing_push_blocks_the_task_with_git_s_error(self):
        g = self.make(test="true")
        iid = g.work()
        loop.tick(g.e.p)
        self.more_commits(g, iid)
        g.deliver(iid)
        hook = g.bare / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\necho 'no pushing today' >&2\nexit 1\n")
        hook.chmod(0o755)
        self.ticks(g, 3)
        self.assertIn("no pushing today", g.e.issue(iid)["blocked"])
        self.assertEqual(g.e.issue(iid)["status"], "in review")

    def flaky_push(self, g: GH, failures: int, error: str = "fatal: unable to access 'https://github.com/o/r.git/': Could not resolve host: github.com"):
        """Patches the push so its first `failures` calls fail with `error`; returns the list of push calls."""
        from unittest import mock

        from fm import pulls

        real, calls = pulls.proc.run, []

        def run(args, **kw):
            if "push" in args:
                calls.append(args)
                if len(calls) <= failures:
                    return pulls.proc.Result(128, "", error)
            return real(args, **kw)

        patcher = mock.patch.object(pulls.proc, "run", run)
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls

    def ticks(self, g: GH, times: int) -> None:
        for _ in range(times):
            loop.tick(g.e.p)

    def test_a_push_that_fails_once_with_a_connection_error_is_retried_and_never_blocks(self):
        g = self.make(test="true")
        iid = g.work(push=False)
        self.flaky_push(g, 1)
        loop.tick(g.e.p)
        self.assertFalse(g.e.issue(iid).get("blocked"))
        self.assertEqual(self.origin_tip(g, iid), "")
        loop.tick(g.e.p)
        self.assertFalse(g.e.issue(iid).get("blocked"))
        self.assertEqual(self.origin_tip(g, iid), self.local_tip(g, iid))

    def test_a_delivered_branch_whose_push_fails_transiently_stays_in_review_and_is_pushed_later(self):
        g = self.make(test="true")
        iid = g.work(push=False)
        g.deliver(iid)
        self.flaky_push(g, 1)
        loop.tick(g.e.p)
        self.assertFalse(g.e.issue(iid).get("blocked"))
        self.assertEqual(g.e.issue(iid)["status"], "in review")
        loop.tick(g.e.p)
        self.assertEqual(self.origin_tip(g, iid), self.local_tip(g, iid))
        self.assertFalse(g.e.issue(iid).get("blocked"))

    def test_the_local_provider_pushes_nothing(self):
        e = Env(test="true")
        git(e.repo, "remote", "add", "origin", str(e.tmp / "never.git"))
        iid = backlog.add(e.p, "x", status="ready", criteria="y")
        loop.tick(e.p)
        wt = e.repo.parent / e.issue(iid)["worktree"]
        with open(wt / "app.py", "a") as f:
            f.write("# change\n")
        git(wt, "commit", "-qam", "change")
        loop.tick(e.p)
        self.assertFalse((e.tmp / "never.git").exists())
        self.assertFalse(e.issue(iid).get("blocked"))
        self.assertFalse(e.issue(iid).get("retry_count"))

    def test_any_provider_call_failing_transient_three_times_blocks_with_the_error(self):
        g = self.make(test="true")
        g.set(ci="pending")  # not settled: it is asked for at every tick
        iid = g.work()
        g.deliver(iid)
        g.set(fail_always=["api", "graphql"])
        self.ticks(g, 2)
        i = g.e.issue(iid)
        self.assertEqual((i["retry_count"], bool(i.get("blocked"))), (2, False))
        self.ticks(g, 1)
        i = g.e.issue(iid)
        self.assertIn("api graphql", i["blocked"])
        self.assertEqual(i["retry_count"], 0)

    def test_a_provider_call_failing_once_then_working_resets_the_count(self):
        g = self.make(test="true")
        g.set(ci="pending")
        iid = g.work()
        g.deliver(iid)
        g.set(fail_once=["api", "graphql"])
        self.ticks(g, 1)
        self.assertEqual(g.e.issue(iid)["retry_count"], 1)
        self.ticks(g, 1)
        i = g.e.issue(iid)
        self.assertEqual((i["retry_count"], i["retry_error"], bool(i.get("blocked"))), (0, "", False))

    def test_a_branch_that_diverged_from_origin_is_not_force_pushed(self):
        g = self.make(test="true")
        iid = g.work()
        remote_tip = self.origin_tip(g, iid)
        git(g.e.repo.parent / g.e.issue(iid)["worktree"], "commit", "-q", "--amend", "-m", "rewritten")
        g.deliver(iid)
        self.ticks(g, 3)
        self.assertEqual(self.origin_tip(g, iid), remote_tip)
        self.assertTrue(g.e.issue(iid)["blocked"])


if __name__ == "__main__":
    unittest.main()
