import re

from foreman.loop import tick
from foreman.providers import registry
from foreman.services.agents import launch
from foreman.steps.reviewing import sweep_scratch
from tests.fakerepo import FakeRepo
from tests.helpers import Env, fm


def launched(e: Env, tid: str) -> str:
    registry.backlog(e.p).update(tid, status="in review", worktree=str(e.repo))
    launch.launch_reviewer(e.p, e.task(tid), "abc123", "hunter")
    launch.launch_reviewer(e.p, e.task(tid), "abc123")
    return tid


class TestScratch:
    def test_each_review_has_its_own_folder_told_in_its_brief(self, fake_git: FakeRepo):
        e = Env(repo=fake_git)
        for _ in range(2):
            launched(e, registry.backlog(e.p).add("x", status="ready", criteria="y"))
        folders = sorted((e.home / "scratch").iterdir())
        assert len(folders) == 4
        briefs = [c["argv"][-1] for c in e.calls()]
        for f in folders:
            assert re.search(r"^[A-Z]+-\d+-r1-abc123-(hunter|reviewer)$", f.name)
            assert sum(str(f) in b for b in briefs) == 1
        assert not (e.home / "reviews" / "scratch").exists()

    def test_gone_with_its_verdict(self, fake_git: FakeRepo):
        e = Env(test="true", repo=fake_git)
        tid = registry.backlog(e.p).add("x", status="ready", criteria="y")
        tick.tick(e.p)
        registry.backlog(e.p).set_status(tid, "in review", report={"summary": "s"})
        e.review(tid)
        sweep_scratch(e.p)
        sweep_scratch(e.p)
        assert list((e.home / "scratch").iterdir()) == []

    def test_gone_when_canceled(self, fake_git: FakeRepo):
        e = Env(test="true", repo=fake_git)
        tid = registry.backlog(e.p).add("x", status="ready", criteria="y")
        tick.tick(e.p)
        registry.backlog(e.p).set_status(tid, "in review", report={"summary": "s"})
        e.hunt_then_review(tid)
        fm(e.home, "cancel", tid)
        sweep_scratch(e.p)
        sweep_scratch(e.p)
        assert list((e.home / "scratch").iterdir()) == []

    def test_a_folder_no_review_owns_is_swept(self, fake_git: FakeRepo):
        e = Env(test="true", repo=fake_git)
        dead = e.home / "scratch" / "TY-9-r1-abc123-reviewer"
        dead.mkdir(parents=True)
        sweep_scratch(e.p)
        assert not dead.exists()

    def test_a_session_unlisted_at_launch_is_swept_with_its_round(self, fake_git: FakeRepo):
        e = Env(test="true", repo=fake_git)
        tid = registry.backlog(e.p).add("x", status="ready", criteria="y")
        registry.backlog(e.p).update(tid, status="in review", worktree=str(e.repo))
        launch.launch_reviewer(e.p, e.task(tid), "abc123", "hunter")
        launch.launch_reviewer(e.p, e.task(tid), "abc123")
        mine = e.home / "scratch" / f"{tid}-r1-abc123-reviewer"
        sweep_scratch(e.p)
        assert mine.exists()  # round 1 is not over
        registry.backlog(e.p).update(tid, reviews=[{"tip": "abc123"}])
        sweep_scratch(e.p)
        assert not mine.exists()
