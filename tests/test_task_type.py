"""Task types: set with --type, default change, shown by show/list/panel, none for a parent, one paragraph in the agents' instructions."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import Env, fm
from test_subtasks import owner

from fm import agents, backlog, cli_parser, panel_views, tasktypes

KINDS = ("responsible", "planning", "reviewer", "hunter")
BUG = ("reproduce the bug with a test that fails", "name its cause in the report")
MECHANICAL = ("No design decisions", "the diff is the proof")
DEFINITION = ("changes only documents", "DESIGN.md", "without filing them")


class TaskType(unittest.TestCase):
    def setUp(self):
        owner(self)
        self.e = Env()
        self.tid = backlog.add(self.e.p, "t", criteria="a")

    def shown(self, tid: str) -> dict:
        return json.loads(fm(self.e.home, "show", tid))

    def line(self, tid: str) -> str:
        return next(ln for ln in fm(self.e.home, "list").splitlines() if ln.startswith(tid))

    def test_add_and_edit_set_the_type(self):
        for t in tasktypes.TYPES:
            with self.subTest(t=t):
                added = fm(self.e.home, "add", "n", "--criteria", "a", "--type", t).strip()
                self.assertEqual(self.e.task(added)["type"], t)
                fm(self.e.home, "edit", self.tid, "--type", t)
                self.assertEqual(self.e.task(self.tid)["type"], t)

    def test_an_invalid_type_lists_the_four_and_changes_nothing(self):
        before = backlog.load(self.e.p)
        for argv in (["edit", self.tid, "--type", "x"], ["add", "n", "--type", "x"]):
            with self.subTest(argv=argv):
                err = io.StringIO()
                with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
                    cli_parser.parser(None).parse_args(argv)
                for t in tasktypes.TYPES:
                    self.assertIn(t, err.getvalue())
                with self.assertRaises(SystemExit):
                    fm(self.e.home, *argv)
                self.assertEqual(backlog.load(self.e.p), before)

    def test_without_a_type_it_is_change(self):
        added = fm(self.e.home, "add", "n", "--criteria", "a").strip()
        self.assertNotIn("type", self.e.task(added))
        for tid in (self.tid, added):
            self.assertEqual(self.shown(tid)["type"], "change")
            self.assertIn("change", self.line(tid))

    def test_show_list_and_panel_carry_the_type(self):
        fm(self.e.home, "edit", self.tid, "--type", "bug")
        self.assertEqual(self.shown(self.tid)["type"], "bug")
        self.assertIn("bug", self.line(self.tid))
        self.assertEqual(panel_views.task(self.e.p, self.tid)["task"]["type"], "bug")
        self.assertEqual({i["id"]: i["type"] for i in panel_views.state(self.e.p)["tasks"]}, {self.tid: "bug"})

    def test_a_parent_has_no_type(self):
        sub = fm(self.e.home, "add", "s", "--criteria", "a", "--parent", self.tid).strip()
        before = self.e.task(self.tid)
        with self.assertRaises(SystemExit):
            fm(self.e.home, "edit", self.tid, "--type", "bug")
        self.assertEqual(self.e.task(self.tid), before)
        backlog.update(self.e.p, self.tid, type="bug")  # stored earlier: ignored on read
        self.assertNotIn("type", self.shown(self.tid))
        self.assertNotIn("bug", self.line(self.tid))
        by_id = {i["id"]: i for i in panel_views.state(self.e.p)["tasks"]}
        self.assertNotIn("type", by_id[self.tid])
        self.assertNotIn("type", panel_views.task(self.e.p, self.tid)["task"])
        self.assertEqual((self.shown(sub)["type"], by_id[sub]["type"]), ("change", "change"))


class AgentsInstructions(unittest.TestCase):
    def setUp(self):
        self.e = Env()
        self.tid = backlog.add(self.e.p, "t", criteria="a")

    def brief(self, kind: str, **extra: str) -> str:
        return agents.brief(self.e.p, {**self.e.task(self.tid), **extra}, kind, {"round": 1})

    def test_each_type_has_its_paragraph_for_every_agent_of_the_task(self):
        for t, words in (("bug", BUG), ("mechanical", MECHANICAL), ("definition", DEFINITION)):
            for kind in KINDS:
                with self.subTest(t=t, kind=kind):
                    text = self.brief(kind, type=t)
                    for w in words:
                        self.assertIn(w, text)
                    self.assertIn(f"# Task type: {t}", text)

    def test_change_is_the_template_as_it_was(self):
        every = (*BUG, *MECHANICAL, *DEFINITION)
        for kind in KINDS:
            with self.subTest(kind=kind):
                plain = self.brief(kind)
                self.assertEqual(plain, self.brief(kind, type="change"))
                self.assertFalse([w for w in every if w in plain])
                with tempfile.TemporaryDirectory() as tmp:
                    src = (agents.BRIEFS / f"{kind}.md").read_text(encoding="utf-8")
                    self.assertIn("$type_section", src)
                    (Path(tmp) / f"{kind}.md").write_text(src.replace("$type_section", ""), encoding="utf-8")
                    with mock.patch.object(agents, "BRIEFS", Path(tmp)):
                        self.assertEqual(plain, self.brief(kind))


if __name__ == "__main__":
    unittest.main()
