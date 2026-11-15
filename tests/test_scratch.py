import unittest
from unittest.mock import patch

from fm import agents, backlog, loop
from tests.helpers import Env, fm


def launched(e: Env, tid: str) -> str:
    backlog.update(e.p, tid, status="in review", worktree=str(e.repo))
    agents.launch_reviewer(e.p, e.task(tid), "abc123", "hunter")
    agents.launch_reviewer(e.p, e.task(tid), "abc123")
    return tid


class Scratch(unittest.TestCase):
    def test_each_review_has_its_own_folder_told_in_its_brief(self):
        e = Env()
        for _ in range(2):
            launched(e, backlog.add(e.p, "x", status="ready", criteria="y"))
        folders = sorted((e.home / "scratch").iterdir())
        self.assertEqual(len(folders), 4)
        briefs = [c["argv"][-1] for c in e.calls()]
        for f in folders:
            self.assertRegex(f.name, r"^[A-Z]+-\d+-r1-abc123-(hunter|reviewer)$")
            self.assertEqual(sum(str(f) in b for b in briefs), 1)
        self.assertFalse((e.home / "reviews" / "scratch").exists())

    def test_gone_with_its_verdict(self):
        e = Env(test="true")
        tid = backlog.add(e.p, "x", status="ready", criteria="y")
        loop.tick(e.p)
        backlog.set_status(e.p, tid, "in review", report={"summary": "s"})
        e.review(tid)
        loop.tick(e.p)
        loop.tick(e.p)
        self.assertEqual(list((e.home / "scratch").iterdir()), [])

    def test_gone_when_canceled(self):
        e = Env(test="true")
        tid = backlog.add(e.p, "x", status="ready", criteria="y")
        loop.tick(e.p)
        backlog.set_status(e.p, tid, "in review", report={"summary": "s"})
        e.hunt_then_review(tid)
        fm(e.home, "cancel", tid)
        loop.tick(e.p)
        loop.tick(e.p)
        self.assertEqual(list((e.home / "scratch").iterdir()), [])

    def test_a_folder_no_review_owns_is_swept(self):
        e = Env(test="true")
        dead = e.home / "scratch" / "TY-9-r1-abc123-reviewer"
        dead.mkdir(parents=True)
        loop.tick(e.p)
        self.assertFalse(dead.exists())

    def test_a_session_unlisted_at_launch_is_swept_with_its_round(self):
        e = Env(test="true")
        tid = backlog.add(e.p, "x", status="ready", criteria="y")
        backlog.update(e.p, tid, status="in review", worktree=str(e.repo))
        agents.launch_reviewer(e.p, e.task(tid), "abc123", "hunter")
        with patch("fm.sessions.find", return_value=None):
            agents.launch_reviewer(e.p, e.task(tid), "abc123")
        mine = e.home / "scratch" / f"{tid}-r1-abc123-reviewer"
        loop.tick(e.p)
        self.assertTrue(mine.exists())  # round 1 is not over
        backlog.update(e.p, tid, reviews=[{"tip": "abc123"}])
        loop.tick(e.p)
        self.assertFalse(mine.exists())
