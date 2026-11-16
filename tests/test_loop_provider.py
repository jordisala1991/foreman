import os

import pytest
import test_loop
from helpers import Env, git

from foreman.core.tasks import backlog
from foreman.loop import queue, tick
from foreman.providers import ChangeState, registry
from foreman.services.delivery import merge
from foreman.steps import talk


class TestProvider:
    def delivered(self, e):
        return test_loop.TestQueue().deliver(e)  # via the module: an imported Queue would be collected and run twice

    def test_accept_only_marks_and_the_task_closes_when_the_merge_is_seen_in_main(self):
        from foreman.providers import local

        e = Env(test="true")
        tid = self.delivered(e)
        tick.tick(e.p)
        before = git(e.repo, "rev-parse", "main").strip()
        merge.accept(e.p, tid)
        i = e.task(tid)
        assert (i["status"], i["accepted"]["by"]) == ("in review", "owner")
        assert git(e.repo, "rev-parse", "main").strip() == before
        wt = i["worktree"]
        # the provider merges but main does not show it yet: the task stays open, worktree and branch stay
        orig = local.Local.state
        local.Local.state = lambda self, change: ChangeState(False, False)
        try:
            tick.tick(e.p)
        finally:
            local.Local.state = orig
        assert e.task(tid)["status"] == "in review"
        assert e.task(tid)["merge"]
        assert "foreman/ty-1" in git(e.repo, "branch")
        assert os.path.isdir(e.repo.parent / wt)
        tick.tick(e.p)  # seen done in main: closes and cleans, without merging twice
        assert e.task(tid)["status"] == "done"
        assert "foreman/ty-1" not in git(e.repo, "branch")
        assert not os.path.isdir(e.repo.parent / wt)
        assert git(e.repo, "rev-list", "--count", f"{before}..main").strip() == "1"

    def test_squash_by_default_merge_is_no_ff(self):
        for strategy, parents in (("", 1), ("merge", 2)):
            e = Env(test="true", autonomy="low")
            if strategy:
                e.home.joinpath("foreman.toml").write_text(e.home.joinpath("foreman.toml").read_text().replace("[merge]", f'[merge]\nstrategy = "{strategy}"'))
                e.p = type(e.p).load(e.home)
            tid = self.delivered(e)
            tick.tick(e.p)
            assert e.task(tid)["status"] == "done"
            assert len(git(e.repo, "log", "-1", "--format=%P", "main").split()) == parents

    def test_unknown_merge_or_provider_is_a_clear_config_error(self):
        from foreman import providers

        e = Env(test="true")
        tid = self.delivered(e)
        tick.tick(e.p)
        for key, val in (("strategy", "rebase"), ("provider", "gitlab"), ("pr", "never")):  # each message: TestProviderError
            before = e.p.cfg["merge"][key]
            e.p.cfg["merge"][key] = val
            with pytest.raises(providers.ConfigError, match=f"{key} = '{val}'"):
                registry.get(e.p)
            with pytest.raises(providers.ConfigError):
                merge.accept(e.p, tid)
            e.p.cfg["merge"][key] = before

    def test_rejection_forgets_the_merge_of_the_old_tip(self):
        e = Env(test="true")
        tid = self.delivered(e)
        tick.tick(e.p)
        backlog.update(e.p, tid, accepted={"by": "o", "head": "x", "at": ""}, merge="abc", merging="def")
        talk.return_to_agent(e.p, tid, "rejection", "again")
        i = e.task(tid)
        assert not {"accepted", "merge", "merging"} & set(i)

    def test_a_crash_after_the_merge_adopts_it_instead_of_merging_twice(self):
        e = Env(test="true")
        tid = self.delivered(e)
        tick.tick(e.p)
        merge.accept(e.p, tid)
        base = git(e.repo, "rev-parse", "main").strip()
        git(e.repo, "merge", "--squash", "foreman/ty-1")
        git(e.repo, "commit", "-qm", f"Merge {tid}: x")  # the merge happened; its ref was never stored
        backlog.update(e.p, tid, merging=base)
        merged = git(e.repo, "rev-parse", "main").strip()
        tick.tick(e.p)
        assert e.task(tid)["status"] == "done"
        assert git(e.repo, "rev-parse", "main").strip() == merged

    def test_a_failing_merge_blocks_the_task_with_the_reason(self):
        e = Env(test="true")
        tid = self.delivered(e)
        tick.tick(e.p)
        merge.accept(e.p, tid)
        (e.repo / "app.py").write_text("dirty\n")  # the main checkout has uncommitted changes
        tick.tick(e.p)
        i = e.task(tid)
        assert i["status"] == "in review"
        assert "uncommitted changes" in i["blocked"]
        assert "merge" not in i

    def test_a_main_checkout_on_another_branch_blocks_the_merge(self):
        e = Env(test="true")
        tid = self.delivered(e)
        tick.tick(e.p)
        merge.accept(e.p, tid)
        git(e.repo, "checkout", "-q", "-b", "elsewhere")
        tick.tick(e.p)
        assert "is not on main" in e.task(tid)["blocked"]
        assert "merge" not in e.task(tid)

    def accepted_then_main_moves(self, e):
        tid = self.delivered(e)
        tick.tick(e.p)
        merge.accept(e.p, tid)
        (e.repo / "app.py").write_text("changed on main\n")
        git(e.repo, "commit", "-qam", "main moved")
        return tid

    def test_an_accepted_branch_that_conflicts_at_merge_goes_back_as_a_conflict(self):
        for strategy in ("squash", "merge"):
            e = Env(test="true")
            e.p.cfg["merge"]["strategy"] = strategy
            tid = self.accepted_then_main_moves(e)
            main = git(e.repo, "rev-parse", "main").strip()
            tick.tick(e.p)
            tick.tick(e.p)
            i = e.task(tid)
            assert i["status"] == "in progress"
            assert i["conflicts"] == 1
            assert backlog.load(e.p)["decisions_resolved"][-1]["kind"] == "conflict"
            assert not i.get("blocked")
            assert "accepted" not in i
            assert git(e.repo, "rev-parse", "main").strip() == main
            assert git(e.repo, "status", "--porcelain").strip() == ""

    def test_an_accepted_branch_whose_merge_fails_otherwise_is_still_blocked(self):
        e = Env(test="true")
        tid = self.delivered(e)
        tick.tick(e.p)
        merge.accept(e.p, tid)
        (e.repo / "app.py").write_text("dirty\n")
        tick.tick(e.p)
        i = e.task(tid)
        assert i["blocked"]
        assert i["status"] == "in review"
        assert "accepted" in i
        assert "conflicts" not in i

    def test_after_the_fix_the_task_merges_without_a_new_accept(self):
        e = Env(test="true")
        tid = self.accepted_then_main_moves(e)
        tick.tick(e.p)
        wt = e.repo.parent / e.task(tid)["worktree"]
        git(wt, "merge", "-X", "ours", "main", "-m", "fix")
        backlog.set_status(e.p, tid, "in review", report={"summary": "s", "confused": ""})
        e.review(tid)
        tick.tick(e.p)
        assert e.task(tid)["status"] == "done"

    def test_a_bad_provider_blocks_at_the_merge_step(self):
        e = Env(test="true")
        tid = self.delivered(e)
        tick.tick(e.p)
        merge.accept(e.p, tid)
        e.p.cfg["merge"]["strategy"] = "rebase"
        queue.run_queue(e.p, {})
        assert "strategy = 'rebase'" in e.task(tid)["blocked"]

    def test_defaults_and_nothing_is_published(self):

        e = Env()
        assert (e.p.get("merge", "provider"), e.p.get("merge", "strategy")) == ("auto", "squash")
        assert registry.get(e.p).name == "local"  # no GitHub remote: local
        import inspect

        from foreman.providers import local

        src = inspect.getsource(local)
        for word in ('"push"', '"tag"', "release"):
            assert word not in src
        e = Env(test="true", autonomy="low")
        self.delivered(e)
        tick.tick(e.p)
        assert git(e.repo, "tag").strip() == ""
        assert git(e.repo, "remote").strip() == ""


class TestRequestMerge:
    def test_request_merge_sorts_platform_errors(self):
        from foreman import providers
        from foreman.services.delivery.git import DeliveryError

        e = Env(test="true")
        tid = backlog.add(e.p, "x", status="ready", criteria="y")

        class Refuses:
            def __init__(self, error):
                self.error = error

            def merge(self, change, strategy):
                raise self.error

        def ask(error):
            i = {"id": tid, "branch": "main"}  # main does not conflict with itself
            merge.request_merge(e.p, i, Refuses(error), providers.Change(tid))

        with pytest.raises(providers.Transient):
            ask(providers.Transient("hiccup"))
        for error in (providers.Permanent("no"), providers.ProviderError("odd"), providers.Conflict("claims a conflict")):
            with pytest.raises(DeliveryError):
                ask(error)
