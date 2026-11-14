import os
import unittest
from pathlib import Path

from helpers import Env, fm

from fm import agents, backlog, loop

BRIEFS = Path(__file__).resolve().parent.parent / "fm" / "briefs"


def run_as(e, tid, agent, *args):
    os.environ.update(FM_TASK=tid, FM_AGENT=agent)
    try:
        fm(e.home, *args)
    finally:
        os.environ.pop("FM_TASK")
        os.environ.pop("FM_AGENT")


class Misled(unittest.TestCase):
    def setUp(self):
        self.e = Env()
        self.tid = backlog.add(self.e.p, "x", status="ready", criteria="y")
        loop.tick(self.e.p)

    def misled(self):
        return self.e.task(self.tid).get("misled")

    def test_each_agent_has_its_text(self):
        run_as(self.e, self.tid, "ty-1", "done", "--summary", "s", "--misled", "a")
        run_as(self.e, self.tid, "ty-1-planning", "ready", "--no-plan", "t", "--misled", "b")
        run_as(self.e, self.tid, "ty-1-review", "verdict", "--open", "0", "--file", "r", "--misled", "c")
        run_as(self.e, self.tid, "ty-1-hunt", "verdict", "--open", "0", "--file", "r", "--misled", "d")
        self.assertEqual(self.misled(), {"responsible": ["a"], "planning": ["b"], "reviewer": ["c"], "hunter": ["d"]})

    def test_none_is_not_kept(self):
        run_as(self.e, self.tid, "ty-1", "done", "--summary", "s", "--misled", "none")
        run_as(self.e, self.tid, "ty-1", "done", "--summary", "s", "--misled", "None.")
        self.assertIsNone(self.misled())

    def test_same_text_shows_once(self):
        for text in ("x", "x", "y"):
            run_as(self.e, self.tid, "ty-1", "done", "--summary", "s", "--misled", text)
        self.assertEqual(self.misled(), {"responsible": ["x", "y"]})

    def test_no_lessons_file_and_no_brief_mentions_it(self):
        run_as(self.e, self.tid, "ty-1", "done", "--summary", "s", "--misled", "a")
        agents.brief(self.e.p, self.e.task(self.tid), "responsible")
        self.assertFalse((self.e.home / "LESSONS.md").exists())
        for f in BRIEFS.glob("*.md"):
            self.assertNotIn("lesson", f.read_text().lower(), f.name)
