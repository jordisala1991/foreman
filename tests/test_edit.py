"""fm edit: change a task's text before its agent starts."""

import json
import os
from unittest import mock

import pytest
from helpers import Env, fm

from foreman.providers import registry


class TestEdit:
    @pytest.fixture(autouse=True)
    def set_up(self, owner: None, env: Env):
        self.e = env
        self.tid = registry.backlog(self.e.p).add("t", detail="d", goal="c", criteria="a\nb")

    def status(self, status: str) -> None:
        registry.backlog(self.e.p).update(self.tid, status=status, plan_file="/x/p.md")

    def events(self) -> str:
        f = self.e.p.state / "events.jsonl"
        return f.read_text() if f.exists() else ""

    def test_edit_changes_only_the_given_fields(self):
        fm(self.e.home, "edit", self.tid, "--title", "new")
        i = self.e.task(self.tid)
        assert (i["title"], i["detail"], i["acceptance"]) == ("new", "d", {"goal": "c", "criteria": "a\nb"})
        assert json.loads(fm(self.e.home, "show", self.tid))["title"] == "new"
        assert any(self.tid in line and "edited" in line for line in self.events().splitlines())
        fm(self.e.home, "edit", self.tid, "--goal", "x", "--criteria", "p\nq", "--detail", "y")
        i = self.e.task(self.tid)
        assert (i["title"], i["detail"], i["acceptance"]) == ("new", "y", {"goal": "x", "criteria": "p\nq"})

    def test_criteria_on_a_triage_task_lets_it_go(self):
        tid = registry.backlog(self.e.p).add("u", status="triage")
        with pytest.raises(SystemExit):
            fm(self.e.home, "go", tid)
        fm(self.e.home, "edit", tid, "--criteria", "a\nb")
        assert self.e.task(tid)["status"] == "triage"
        fm(self.e.home, "go", tid)
        assert self.e.task(tid)["status"] == "backlog"

    def test_criteria_or_detail_drop_the_plan(self):
        for status in ("backlog", "ready"):
            for flag in ("--criteria", "--detail"):
                self.status(status)
                fm(self.e.home, "edit", self.tid, flag, "z")
                i = self.e.task(self.tid)
                assert i["status"] == "backlog"
                assert "plan_file" not in i

    def test_a_blocked_task_is_not_said_to_be_planned_again(self):
        registry.backlog(self.e.p).update(self.tid, status="ready", plan_file="/x/p.md", blocked="x")
        out = fm(self.e.home, "edit", self.tid, "--criteria", "z")
        assert "unblock" in out
        assert "planned again" not in out

    def test_title_or_goal_keep_plan_and_status(self):
        for status in ("backlog", "ready"):
            for flag in ("--title", "--goal"):
                self.status(status)
                fm(self.e.home, "edit", self.tid, flag, "z")
                i = self.e.task(self.tid)
                assert (i["status"], i["plan_file"]) == (status, "/x/p.md")

    def test_refused_when_the_agent_has_it_or_it_is_closed(self):
        for status, word in (
            ("refining", "old text"),
            ("in progress", "old text"),
            ("in review", "old text"),
            ("done", "closed"),
            ("canceled", "closed"),
            ("duplicate", "closed"),
        ):
            self.status(status)
            before, events = self.e.task(self.tid), self.events()
            with pytest.raises(SystemExit) as r:
                fm(self.e.home, "edit", self.tid, "--title", "x")
            assert word in str(r.value.code)
            assert (self.e.task(self.tid), self.events()) == (before, events)

    def test_no_field_or_unknown_id_fails(self):
        before = registry.backlog(self.e.p).load()
        for args in ((self.tid,), (self.tid, "--title", ""), ("FN-99", "--title", "x")):
            with pytest.raises(SystemExit):
                fm(self.e.home, "edit", *args)
        assert registry.backlog(self.e.p).load() == before

    def test_refused_from_an_agent_session(self):
        with mock.patch.dict(os.environ, {"FOREMAN_TASK": "TY-9"}), pytest.raises(SystemExit):
            fm(self.e.home, "edit", self.tid, "--title", "x")
        assert self.e.task(self.tid)["title"] == "t"
