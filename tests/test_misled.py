import os

import pytest
from helpers import PLUGIN, fm

from foreman.core.tasks import backlog
from foreman.loop import tick
from foreman.services.agents import brief

BRIEFS = PLUGIN / "foreman" / "briefs"


def run_as(e, tid, agent, *args):
    os.environ.update(FM_TASK=tid, FM_AGENT=agent)
    try:
        fm(e.home, *args)
    finally:
        os.environ.pop("FM_TASK")
        os.environ.pop("FM_AGENT")


class TestMisled:
    @pytest.fixture(autouse=True)
    def set_up(self, env):
        self.e = env
        self.tid = backlog.add(self.e.p, "x", status="ready", criteria="y")
        tick.tick(self.e.p)

    def misled(self):
        return self.e.task(self.tid).get("misled")

    def test_each_agent_has_its_text(self):
        run_as(self.e, self.tid, "ty-1", "done", "--summary", "s", "--misled", "a")
        run_as(self.e, self.tid, "ty-1-planning", "ready", "--no-plan", "t", "--misled", "b")
        run_as(self.e, self.tid, "ty-1-review", "verdict", "--open", "0", "--file", "r", "--misled", "c")
        run_as(self.e, self.tid, "ty-1-hunt", "verdict", "--open", "0", "--file", "r", "--misled", "d")
        assert self.misled() == {"responsible": ["a"], "planning": ["b"], "reviewer": ["c"], "hunter": ["d"]}

    def test_none_is_not_kept(self):
        run_as(self.e, self.tid, "ty-1", "done", "--summary", "s", "--misled", "none")
        run_as(self.e, self.tid, "ty-1", "done", "--summary", "s", "--misled", "None.")
        assert self.misled() is None

    def test_same_text_shows_once(self):
        for text in ("x", "x", "y"):
            run_as(self.e, self.tid, "ty-1", "done", "--summary", "s", "--misled", text)
        assert self.misled() == {"responsible": ["x", "y"]}

    def test_no_lessons_file_and_no_brief_mentions_it(self):
        run_as(self.e, self.tid, "ty-1", "done", "--summary", "s", "--misled", "a")
        brief.brief(self.e.p, self.e.task(self.tid), "responsible")
        assert not (self.e.home / "LESSONS.md").exists()
        for f in BRIEFS.glob("*.md"):
            assert "lesson" not in f.read_text().lower(), f.name
