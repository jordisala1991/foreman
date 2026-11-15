"""The loop pushes the task branches (provider.push) and retries transient provider failures (loop.guarded). Fake `gh`
and a bare origin come from test_github."""

import subprocess

import pytest
from helpers import Env, git
from test_github import GH

from fm import providers
from fm.core.tasks import backlog
from fm.loop import tick
from fm.loop.guarded import guarded


def transient() -> None:
    raise providers.Transient("api graphql")


class TestPushes:
    @pytest.fixture(autouse=True)
    def keep_request(self, request):
        self.request = request  # for the cleanups of helpers

    def make(self, extra: str = "", **kw) -> GH:
        g = GH(extra, **kw)
        self.request.addfinalizer(g.close)
        return g

    def origin_tip(self, g: GH, tid: str) -> str:
        ref = "refs/heads/" + g.e.task(tid)["branch"]
        r = subprocess.run(["git", "--git-dir", str(g.bare), "rev-parse", "--verify", "-q", ref], capture_output=True, text=True)
        return r.stdout.strip()

    def local_tip(self, g: GH, tid: str) -> str:
        return subprocess.run(["git", "-C", str(g.e.repo), "rev-parse", g.e.task(tid)["branch"]], capture_output=True, text=True).stdout.strip()

    def more_commits(self, g: GH, tid: str) -> None:
        wt = g.e.repo.parent / g.e.task(tid)["worktree"]
        with open(wt / "app.py", "a") as f:
            f.write("# more\n")
        git(wt, "commit", "-qam", "more")

    def test_fm_done_on_an_unpushed_branch_is_pushed_and_goes_to_review(self):
        g = self.make(test="true")
        tid = g.work()
        tick.tick(g.e.p)
        self.more_commits(g, tid)
        g.deliver(tid)
        assert self.origin_tip(g, tid) != self.local_tip(g, tid)
        tick.tick(g.e.p)
        assert self.origin_tip(g, tid) == self.local_tip(g, tid)
        assert g.e.task(tid)["status"] == "in review"
        assert not g.e.task(tid).get("blocked")
        assert not g.state["prs"]["1"]["draft"]

    def test_a_failing_push_blocks_the_task_with_git_s_error(self):
        g = self.make(test="true")
        tid = g.work()
        tick.tick(g.e.p)
        self.more_commits(g, tid)
        g.deliver(tid)
        hook = g.bare / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\necho 'no pushing today' >&2\nexit 1\n")
        hook.chmod(0o755)
        self.ticks(g, 3)
        assert "no pushing today" in g.e.task(tid)["blocked"]
        assert g.e.task(tid)["status"] == "in review"

    def flaky_push(self, g: GH, failures: int, error: str = "fatal: unable to access 'https://github.com/o/r.git/': Could not resolve host: github.com"):
        """Patches the push so its first `failures` calls fail with `error`; returns the list of push calls."""
        from unittest import mock

        from fm.services.delivery import pulls

        real, calls = pulls.proc.run, []

        def run(args, **kw):
            if "push" in args:
                calls.append(args)
                if len(calls) <= failures:
                    return pulls.proc.Result(128, "", error)
            return real(args, **kw)

        patcher = mock.patch.object(pulls.proc, "run", run)
        patcher.start()
        self.request.addfinalizer(patcher.stop)
        return calls

    def ticks(self, g: GH, times: int) -> None:
        for _ in range(times):
            tick.tick(g.e.p)

    def test_a_push_that_fails_once_with_a_connection_error_is_retried_and_never_blocks(self):
        g = self.make(test="true")
        tid = g.work(push=False)
        self.flaky_push(g, 1)
        tick.tick(g.e.p)
        assert not g.e.task(tid).get("blocked")
        assert self.origin_tip(g, tid) == ""
        tick.tick(g.e.p)
        assert not g.e.task(tid).get("blocked")
        assert self.origin_tip(g, tid) == self.local_tip(g, tid)

    def test_a_delivered_branch_whose_push_fails_transiently_stays_in_review_and_is_pushed_later(self):
        g = self.make(test="true")
        tid = g.work(push=False)
        g.deliver(tid)
        self.flaky_push(g, 1)
        tick.tick(g.e.p)
        assert not g.e.task(tid).get("blocked")
        assert g.e.task(tid)["status"] == "in review"
        tick.tick(g.e.p)
        assert self.origin_tip(g, tid) == self.local_tip(g, tid)
        assert not g.e.task(tid).get("blocked")

    def test_the_local_provider_pushes_nothing(self):
        e = Env(test="true")
        git(e.repo, "remote", "add", "origin", str(e.tmp / "never.git"))
        tid = backlog.add(e.p, "x", status="ready", criteria="y")
        tick.tick(e.p)
        wt = e.repo.parent / e.task(tid)["worktree"]
        with open(wt / "app.py", "a") as f:
            f.write("# change\n")
        git(wt, "commit", "-qam", "change")
        tick.tick(e.p)
        assert not (e.tmp / "never.git").exists()
        assert not e.task(tid).get("blocked")
        assert not e.task(tid).get("retry_count")

    def test_any_provider_call_failing_transient_three_times_blocks_with_the_error(self):
        e = Env(test="true")
        tid = backlog.add(e.p, "x", status="ready", criteria="y")
        for _ in range(2):
            guarded(e.p, tid, "x", transient)
        i = e.task(tid)
        assert (i["retry_count"], bool(i.get("blocked"))) == (2, False)
        guarded(e.p, tid, "x", transient)
        i = e.task(tid)
        assert "api graphql" in i["blocked"]
        assert i["retry_count"] == 0

    def test_a_provider_call_failing_once_then_working_resets_the_count(self):
        e = Env(test="true")
        tid = backlog.add(e.p, "x", status="ready", criteria="y")
        guarded(e.p, tid, "x", transient)
        assert e.task(tid)["retry_count"] == 1
        guarded(e.p, tid, "x", lambda: None)
        i = e.task(tid)
        assert (i["retry_count"], i["retry_error"], bool(i.get("blocked"))) == (0, "", False)

    def test_a_branch_that_diverged_from_origin_is_not_force_pushed(self):
        g = self.make(test="true")
        tid = g.work()
        remote_tip = self.origin_tip(g, tid)
        git(g.e.repo.parent / g.e.task(tid)["worktree"], "commit", "-q", "--amend", "-m", "rewritten")
        g.deliver(tid)
        self.ticks(g, 3)
        assert self.origin_tip(g, tid) == remote_tip
        assert g.e.task(tid)["blocked"]
