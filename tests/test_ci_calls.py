"""How the loop reads PR CI (one batched call, settled results kept per commit) and who spends GitHub's quota. Uses the
fake `gh` of test_github."""

import json
import os
import subprocess
import unittest

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

    def test_a_pr_github_does_not_show_is_asked_once_per_poll_not_once_per_task(self):
        g = self.make(test="true")
        g.work()
        github.POLL = 3600
        gh = registry.get(g.e.p)
        before = self.graphql(g)
        for pr in ("98", "99"):
            self.assertEqual(gh.ci({"pr": pr, "head": "x"}), "pending")
        self.assertEqual(self.graphql(g) - before, 1)

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

    def test_gh_calls_are_counted_by_origin(self):
        from fm import ghcalls, guard

        g = self.make(test="true")
        g.work()
        loop.tick(g.e.p)
        self.assertGreater(ghcalls.by_origin(g.e.p)["loop"]["last_hour"], 0)
        os.environ.update(FM_HOME=str(g.e.home), FM_ISSUE="FN-9")
        self.addCleanup(lambda: [os.environ.pop(k) for k in ("FM_HOME", "FM_ISSUE")])

        def run(cmd):
            guard.hook(json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": str(g.e.repo)}))
            return ghcalls.by_origin(g.e.p).get("FN-9", {"last_hour": 0})["last_hour"]

        self.assertEqual(run("gh issue view 3 | head && gh api x"), 2)
        self.assertEqual(run("for i in 1 2; do gh api x; done"), 3)
        self.assertEqual(run("bash -c 'gh api y' ; gh --version"), 4)
        self.assertEqual(run("gh pr merge 3"), 4)  # the guard denies it: it never ran

    def test_gh_run_outside_the_loop_is_not_counted_as_the_loop(self):
        from fm import ghcalls

        g = self.make(test="true")
        g.work()
        loop.tick(g.e.p)
        before = ghcalls.by_origin(g.e.p)["loop"]["last_hour"]
        os.environ["FM_ISSUE"] = "FN-7"
        self.addCleanup(lambda: os.environ.pop("FM_ISSUE", None))
        registry.get(g.e.p).refresh()
        after = ghcalls.by_origin(g.e.p)
        self.assertEqual((after["loop"]["last_hour"], after["FN-7"]["last_hour"]), (before, 1))
