"""fm edit: change a task's text before its agent starts."""

import json
import os
import unittest
from unittest import mock

from helpers import Env, fm
from test_subtasks import owner

from fm import backlog


class Edit(unittest.TestCase):
    def setUp(self):
        owner(self)
        self.e = Env()
        self.tid = backlog.add(self.e.p, "t", detail="d", goal="c", criteria="a\nb")

    def status(self, status: str) -> None:
        backlog.update(self.e.p, self.tid, status=status, plan_file="/x/p.md")

    def events(self) -> str:
        f = self.e.p.state / "events.jsonl"
        return f.read_text() if f.exists() else ""

    def test_edit_changes_only_the_given_fields(self):
        fm(self.e.home, "edit", self.tid, "--title", "new")
        i = self.e.task(self.tid)
        self.assertEqual((i["title"], i["detail"], i["acceptance"]), ("new", "d", {"goal": "c", "criteria": "a\nb"}))
        self.assertEqual(json.loads(fm(self.e.home, "show", self.tid))["title"], "new")
        self.assertTrue(any(self.tid in line and "edited" in line for line in self.events().splitlines()))
        fm(self.e.home, "edit", self.tid, "--goal", "x", "--criteria", "p\nq", "--detail", "y")
        i = self.e.task(self.tid)
        self.assertEqual((i["title"], i["detail"], i["acceptance"]), ("new", "y", {"goal": "x", "criteria": "p\nq"}))

    def test_criteria_on_a_triage_task_lets_it_go(self):
        tid = backlog.add(self.e.p, "u", status="triage")
        with self.assertRaises(SystemExit):
            fm(self.e.home, "go", tid)
        fm(self.e.home, "edit", tid, "--criteria", "a\nb")
        self.assertEqual(self.e.task(tid)["status"], "triage")
        fm(self.e.home, "go", tid)
        self.assertEqual(self.e.task(tid)["status"], "backlog")

    def test_criteria_or_detail_drop_the_plan(self):
        for status in ("backlog", "ready"):
            for flag in ("--criteria", "--detail"):
                with self.subTest(status=status, flag=flag):
                    self.status(status)
                    fm(self.e.home, "edit", self.tid, flag, "z")
                    i = self.e.task(self.tid)
                    self.assertEqual(i["status"], "backlog")
                    self.assertNotIn("plan_file", i)

    def test_a_blocked_task_is_not_said_to_be_planned_again(self):
        backlog.update(self.e.p, self.tid, status="ready", plan_file="/x/p.md", blocked="x")
        out = fm(self.e.home, "edit", self.tid, "--criteria", "z")
        self.assertIn("unblock", out)
        self.assertNotIn("planned again", out)

    def test_title_or_goal_keep_plan_and_status(self):
        for status in ("backlog", "ready"):
            for flag in ("--title", "--goal"):
                with self.subTest(status=status, flag=flag):
                    self.status(status)
                    fm(self.e.home, "edit", self.tid, flag, "z")
                    i = self.e.task(self.tid)
                    self.assertEqual((i["status"], i["plan_file"]), (status, "/x/p.md"))

    def test_refused_when_the_agent_has_it_or_it_is_closed(self):
        for status, word in (
            ("refining", "old text"),
            ("in progress", "old text"),
            ("in review", "old text"),
            ("done", "closed"),
            ("canceled", "closed"),
            ("duplicate", "closed"),
        ):
            with self.subTest(status=status):
                self.status(status)
                before, events = self.e.task(self.tid), self.events()
                with self.assertRaises(SystemExit) as r:
                    fm(self.e.home, "edit", self.tid, "--title", "x")
                self.assertIn(word, str(r.exception.code))
                self.assertEqual((self.e.task(self.tid), self.events()), (before, events))

    def test_no_field_or_unknown_id_fails(self):
        before = backlog.load(self.e.p)
        for args in ((self.tid,), (self.tid, "--title", ""), ("FN-99", "--title", "x")):
            with self.subTest(args=args), self.assertRaises(SystemExit):
                fm(self.e.home, "edit", *args)
        self.assertEqual(backlog.load(self.e.p), before)

    def test_refused_from_an_agent_session(self):
        with mock.patch.dict(os.environ, {"FM_TASK": "TY-9"}), self.assertRaises(SystemExit):
            fm(self.e.home, "edit", self.tid, "--title", "x")
        self.assertEqual(self.e.task(self.tid)["title"], "t")
