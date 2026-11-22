"""Task types: set with --type, default change, shown by show/list/panel, none for a parent, one paragraph in the agents' instructions."""

import contextlib
import io
import json
import tempfile
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from helpers import Env, fm

from foreman.core.tasks import tasktypes
from foreman.providers import registry
from foreman.services.agents import brief
from foreman.ui.cli import parser
from foreman.ui.panel import views

KINDS = ("responsible", "planning", "reviewer", "hunter")
BUG = ("reproduce the bug with a test that fails", "name its cause in the report")
MECHANICAL = ("No design decisions", "the diff is the proof")
DEFINITION = ("changes only documents", "the definition goes in DESIGN.md", "without filing them")


class TestTaskType:
    @pytest.fixture(autouse=True)
    def set_up(self, owner: None, env: Env):
        self.e = env
        self.tid = registry.backlog(self.e.p).add("t", criteria="a")

    def shown(self, tid: str) -> dict[str, Any]:
        return json.loads(fm(self.e.home, "show", tid))

    def line(self, tid: str) -> str:
        return next(ln for ln in fm(self.e.home, "list").splitlines() if ln.startswith(tid))

    def test_add_and_edit_set_the_type(self):
        for t in tasktypes.TYPES:
            added = fm(self.e.home, "add", "n", "--criteria", "a", "--type", t).strip()
            assert self.e.task(added)["type"] == t
            fm(self.e.home, "edit", self.tid, "--type", t)
            assert self.e.task(self.tid)["type"] == t

    def test_an_invalid_type_lists_the_four_and_changes_nothing(self):
        before = registry.backlog(self.e.p).load()
        for argv in (["edit", self.tid, "--type", "x"], ["add", "n", "--type", "x"]):
            err = io.StringIO()
            with contextlib.redirect_stderr(err), pytest.raises(SystemExit):
                parser.parser(None).parse_args(argv)
            for t in tasktypes.TYPES:
                assert t in err.getvalue()
            with pytest.raises(SystemExit):
                fm(self.e.home, *argv)
            assert registry.backlog(self.e.p).load() == before

    def test_without_a_type_it_is_change(self):
        added = fm(self.e.home, "add", "n", "--criteria", "a").strip()
        assert "type" not in self.e.task(added)
        for tid in (self.tid, added):
            assert self.shown(tid)["type"] == "change"
            assert "change" in self.line(tid)

    def test_show_list_and_panel_carry_the_type(self):
        fm(self.e.home, "edit", self.tid, "--type", "bug")
        assert self.shown(self.tid)["type"] == "bug"
        assert "bug" in self.line(self.tid)
        assert views.task(self.e.p, self.tid)["task"]["type"] == "bug"
        assert {i["id"]: i["type"] for i in views.state(self.e.p)["tasks"]} == {self.tid: "bug"}

    def test_a_parent_has_no_type(self):
        sub = fm(self.e.home, "add", "s", "--criteria", "a", "--parent", self.tid).strip()
        before = self.e.task(self.tid)
        with pytest.raises(SystemExit):
            fm(self.e.home, "edit", self.tid, "--type", "bug")
        assert self.e.task(self.tid) == before
        registry.backlog(self.e.p).update(self.tid, type="bug")  # stored earlier: ignored on read
        assert "type" not in self.shown(self.tid)
        assert "bug" not in self.line(self.tid)
        by_id = {i["id"]: i for i in views.state(self.e.p)["tasks"]}
        assert "type" not in by_id[self.tid]
        assert "type" not in views.task(self.e.p, self.tid)["task"]
        assert (self.shown(sub)["type"], by_id[sub]["type"]) == ("change", "change")


class TestAgentsInstructions:
    @pytest.fixture(autouse=True)
    def set_up(self, env: Env):
        self.e = env
        self.tid = registry.backlog(self.e.p).add("t", criteria="a")

    def brief(self, kind: str, **extra: str) -> str:
        return brief.brief(self.e.p, {**self.e.task(self.tid), **extra}, kind, {"round": 1})

    @pytest.mark.parametrize(("t", "words"), (("bug", BUG), ("mechanical", MECHANICAL), ("definition", DEFINITION)))
    def test_each_type_has_its_paragraph_for_every_agent_of_the_task(self, t: str, words: tuple[str, ...]):
        for kind in KINDS:
            text = self.brief(kind, type=t)
            for w in words:
                assert w in text
            assert f"# Task type: {t}" in text

    def test_change_is_the_template_as_it_was(self):
        every = (*BUG, *MECHANICAL, *DEFINITION)
        for kind in KINDS:
            plain = self.brief(kind)
            assert plain == self.brief(kind, type="change")
            assert not [w for w in every if w in plain]
            with tempfile.TemporaryDirectory() as tmp:
                src = (brief.BRIEFS / f"{kind}.md").read_text(encoding="utf-8")
                assert "$type_section" in src
                (Path(tmp) / f"{kind}.md").write_text(src.replace("$type_section", ""), encoding="utf-8")
                with mock.patch.object(brief, "BRIEFS", Path(tmp)):
                    assert plain == self.brief(kind)
