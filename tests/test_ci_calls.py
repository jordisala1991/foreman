"""How the loop reads PR CI (one batched call, settled results kept per commit) and who spends GitHub's quota. Uses the
fake `gh` of test_github."""

import os
import subprocess
import unittest
from pathlib import Path

from helpers import git
from test_github import GH

from fm import loop
from fm.providers import github, registry


def backlog_change(g, iid: str) -> dict:
    i = g.e.issue(iid)
    r = subprocess.run(["git", "-C", str(g.e.repo), "rev-parse", i["branch"]], capture_output=True, text=True)
    return {"pr": i["pr"]["number"], "head": r.stdout.strip()}


class CiCalls(unittest.TestCase):
    def make(self, extra: str = "", **kw) -> GH:
        g = GH(extra, **kw)
        self.addCleanup(g.close)
        return g

    def graphql(self, g: GH) -> int:
        return len(g.calls("api", "graphql"))

    def test_a_settled_ci_is_not_read_again_until_the_branch_gets_a_commit(self):
        g = self.make(test="true", autonomy="manual")
        iid = g.work()
        g.deliver(iid)
        loop.tick(g.e.p)
        self.assertEqual((g.e.issue(iid)["check"]["ci"], self.graphql(g)), ("pass", 1))
        github.POLL = 3600
        g.set(ci="fail")  # a re-run on the same commit: not looked at
        for _ in range(3):
            loop.tick(g.e.p)
        self.assertEqual((g.e.issue(iid)["check"]["ci"], self.graphql(g)), ("pass", 1))
        self.assertEqual(g.calls("pr", "checks"), [])

    def test_a_new_commit_reads_the_ci_again(self):
        g = self.make(test="true", autonomy="manual")
        iid = g.work()
        g.deliver(iid)
        loop.tick(g.e.p)
        g.set(ci="fail")
        wt = g.e.repo.parent / g.e.issue(iid)["worktree"]
        with open(wt / "app.py", "a") as f:
            f.write("# fix\n")
        git(wt, "commit", "-qam", "fix")
        g.e.review(iid)  # the new tip is reviewed first
        loop.tick(g.e.p)
        self.assertEqual((g.e.issue(iid)["check"]["ci"], self.graphql(g)), ("fail", 2))

    def test_pending_ci_of_several_tasks_is_one_call_per_poll(self):
        g = self.make(test="true", autonomy="manual")
        g.set(ci="pending")
        github.POLL = 3600
        ids = [g.work(), g.work()]
        for iid in ids:
            g.deliver(iid)
        loop.tick(g.e.p)
        loop.tick(g.e.p)
        self.assertEqual(self.graphql(g), 1)
        self.assertTrue(all("check" not in g.e.issue(iid) for iid in ids))

    def test_a_settled_ci_stays_when_another_task_causes_a_refresh(self):
        g = self.make(test="true", autonomy="manual")
        a = g.work()
        g.deliver(a)
        loop.tick(g.e.p)
        self.assertEqual(g.e.issue(a)["check"]["ci"], "pass")
        g.set(ci="pending")  # GitHub now shows every PR as running, A's commit has not changed
        b = g.work()
        g.deliver(b)
        loop.tick(g.e.p)
        self.assertEqual(registry.get(g.e.p).ci(backlog_change(g, a)), "pass")
        self.assertEqual(registry.get(g.e.p).ci(backlog_change(g, b)), "pending")

    def test_a_pr_missing_from_the_open_answer_is_read_once_and_kept(self):
        g = self.make(test="true", autonomy="manual")
        iid = g.work()
        g.deliver(iid)
        loop.tick(g.e.p)  # its PR is open and its CI read
        (g.e.p.state / "pr_ci.json").unlink()  # nothing known about it yet
        g.set(hidden=["1"])  # still open, but not in the first page of open PRs
        github.POLL = 3600
        github.NONE_TTL = 3600
        gh, change = registry.get(g.e.p), backlog_change(g, iid)
        before = self.graphql(g)
        self.assertEqual([gh.ci(change) for _ in range(3)], ["pass"] * 3)
        self.assertEqual(self.graphql(g) - before, 2)  # the open answer, then that PR; not again

    def test_a_pr_missing_from_the_open_answer_because_it_is_not_open_fails_with_its_state(self):
        g = self.make(test="true", autonomy="manual")
        iid = g.work()
        g.deliver(iid)
        loop.tick(g.e.p)
        (g.e.p.state / "pr_ci.json").unlink()
        g.set(prs={**g.state["prs"], "1": {**g.state["prs"]["1"], "state": "MERGED"}})
        with self.assertRaises(github.PermanentError) as cm:
            registry.get(g.e.p).ci(backlog_change(g, iid))
        self.assertIn("merged", str(cm.exception))

    def test_a_pr_without_checks_is_read_again_only_after_the_ttl(self):
        g = self.make(test="true")
        g.set(ci="none")
        iid = g.work()
        g.deliver(iid)
        loop.tick(g.e.p)
        github.NONE_TTL = 3600
        gh, change = registry.get(g.e.p), backlog_change(g, iid)
        self.assertEqual(gh.ci(change), "none")
        before = self.graphql(g)
        for _ in range(3):
            gh.ci(change)
        self.assertEqual(self.graphql(g), before)

    def test_the_loops_gh_calls_are_counted_as_the_loop(self):
        from fm import ghcalls

        g = self.make(test="true")
        g.work()
        loop.tick(g.e.p)
        self.assertGreater(ghcalls.by_origin(g.e.p)["loop"]["last_hour"], 0)

    def test_an_agents_gh_goes_through_the_shim_which_counts_it_and_runs_the_real_gh(self):
        from fm import ghcalls

        g = self.make(test="true")
        shim = Path(__file__).resolve().parent.parent / "bin"
        env = {**os.environ, "FM_HOME": str(g.e.home), "FM_ISSUE": "FN-9", "PATH": f"{shim}{os.pathsep}{os.environ['PATH']}"}
        for _ in range(2):
            r = subprocess.run(["gh", "api", "x"], env=env, capture_output=True, text=True)
        self.assertEqual((r.stdout.strip(), ghcalls.by_origin(g.e.p)["FN-9"]["last_hour"]), ("null", 2))
        self.assertEqual(len(g.calls("api", "x")), 2)
