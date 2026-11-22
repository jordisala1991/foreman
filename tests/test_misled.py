import os

import pytest
from helpers import PLUGIN, Env, fm

from foreman.loop import tick
from foreman.providers import registry
from foreman.services.agents import brief

BRIEFS = PLUGIN / "foreman" / "briefs"


def run_as(e: Env, tid: str, agent: str, *args: str) -> None:
    os.environ.update(FOREMAN_TASK=tid, FOREMAN_AGENT=agent)
    try:
        fm(e.home, *args)
    finally:
        os.environ.pop("FOREMAN_TASK")
        os.environ.pop("FOREMAN_AGENT")


class TestMisled:
    @pytest.fixture(autouse=True)
    def set_up(self, env: Env) -> None:
        self.e = env
        self.tid = registry.backlog(self.e.p).add("x", status="ready", criteria="y")
        tick.tick(self.e.p)

    def misled(self) -> object:
        return self.e.task(self.tid).get("misled")

    def test_each_agent_has_its_text(self) -> None:
        run_as(self.e, self.tid, "ty-1", "done", "--summary", "s", "--misled", "a")
        run_as(self.e, self.tid, "ty-1-planning", "ready", "--no-plan", "t", "--misled", "b")
        run_as(self.e, self.tid, "ty-1-review", "verdict", "--open", "0", "--file", "r", "--misled", "c")
        run_as(self.e, self.tid, "ty-1-hunt", "verdict", "--open", "0", "--file", "r", "--misled", "d")
        assert self.misled() == {"responsible": ["a"], "planning": ["b"], "reviewer": ["c"], "hunter": ["d"]}

    def test_none_is_not_kept(self) -> None:
        run_as(self.e, self.tid, "ty-1", "done", "--summary", "s", "--misled", "none")
        run_as(self.e, self.tid, "ty-1", "done", "--summary", "s", "--misled", "None.")
        assert self.misled() is None

    def test_same_text_shows_once(self) -> None:
        for text in ("x", "x", "y"):
            run_as(self.e, self.tid, "ty-1", "done", "--summary", "s", "--misled", text)
        assert self.misled() == {"responsible": ["x", "y"]}

    def test_no_lessons_file_and_no_brief_mentions_it(self) -> None:
        run_as(self.e, self.tid, "ty-1", "done", "--summary", "s", "--misled", "a")
        brief.brief(self.e.p, self.e.task(self.tid), "responsible")
        assert not (self.e.home / "LESSONS.md").exists()
        for f in BRIEFS.glob("*.md"):
            assert "lesson" not in f.read_text().lower(), f.name
