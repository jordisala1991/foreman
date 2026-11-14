"""A task waits for the tasks in `blocked_by`: filing, editing, the loop holding and releasing it, refusals."""

import json
import unittest

from helpers import Env, fm
from test_subtasks import owner

from fm import backlog, blockers, integrate, loop, util


def events(e: Env, iid: str) -> list[str]:
    return [x["text"] for x in util.read_jsonl(e.p.state / "events.jsonl") if x["issue"] == iid]


class Blockers(unittest.TestCase):
    def setUp(self):
        owner(self)
        self.e = Env()
        self.X1 = backlog.add(self.e.p, "x1", status="triage")
        self.X2 = backlog.add(self.e.p, "x2", status="triage")

    def y(self, *extra: str) -> str:
        return fm(self.e.home, "add", "y", "--criteria", "c", *extra).strip()

    def ids(self, iid: str) -> list[str]:
        return self.e.issue(iid).get("blocked_by", [])

    def test_add_and_edit_take_blockers_and_show_them(self):
        a = self.y("--blocked-by", self.X1, self.X2)
        b = self.y("--blocked-by", self.X1, "--blocked-by", self.X2)
        self.assertEqual((self.ids(a), self.ids(b)), ([self.X1, self.X2],) * 2)
        fm(self.e.home, "edit", a, "--blocked-by", self.X1)
        self.assertEqual(self.ids(a), [self.X1])
        self.assertEqual(json.loads(fm(self.e.home, "show", a))["waiting_for"], [self.X1])
        line = next(x for x in fm(self.e.home, "list").splitlines() if x.startswith(a))
        self.assertIn(self.X1, line)
        integrate.close(self.e.p, self.X1, "done", "x")
        line = next(x for x in fm(self.e.home, "list").splitlines() if x.startswith(a))
        self.assertNotIn(self.X1, line)
        fm(self.e.home, "edit", a, "--blocked-by", "none")
        self.assertEqual(self.ids(a), [])
        fm(self.e.home, "edit", a, "--blocked-by", self.X2)
        fm(self.e.home, "cancel", a)
        line = next(x for x in fm(self.e.home, "list", "--all").splitlines() if x.startswith(a))
        self.assertNotIn(self.X2, line)
        self.assertNotIn("waiting_for", json.loads(fm(self.e.home, "show", a)))

    def test_not_launched_until_the_last_blocker_is_done(self):
        for status, started in (("backlog", "refining"), ("ready", "in progress")):
            with self.subTest(status=status):
                self.setUp()
                y = self.y("--blocked-by", self.X1, self.X2)
                backlog.set_status(self.e.p, y, status)
                loop.tick(self.e.p)
                self.assertEqual((self.e.calls(), self.e.issue(y)["status"]), ([], status))
                integrate.close(self.e.p, self.X1, "done", "x")
                loop.tick(self.e.p)
                self.assertEqual(self.e.calls(), [])
                integrate.close(self.e.p, self.X2, "done", "x")
                loop.tick(self.e.p)
                self.assertEqual((len(self.e.calls()), self.e.issue(y)["status"]), (1, started))
                self.assertEqual(len([t for t in events(self.e, y) if "unblocked" in t]), 1)
                loop.tick(self.e.p)
                self.assertEqual(len([t for t in events(self.e, y) if "unblocked" in t]), 1)

    def test_a_task_filed_with_a_done_blocker_launches_at_once(self):
        integrate.close(self.e.p, self.X1, "done", "x")
        y = self.y("--blocked-by", self.X1)
        loop.tick(self.e.p)
        self.assertEqual(len(self.e.calls()), 1)
        self.assertEqual(self.e.issue(y)["status"], "refining")

    def test_a_canceled_blocker_blocks_the_task_for_the_owner(self):
        for status in ("canceled", "duplicate"):
            with self.subTest(status=status):
                self.setUp()
                y = self.y("--blocked-by", self.X1)
                backlog.set_status(self.e.p, self.X1, status)
                loop.tick(self.e.p)
                self.assertEqual(self.e.calls(), [])
                self.assertIn(self.X1, self.e.issue(y)["blocked"])
                n = len(events(self.e, y))
                loop.tick(self.e.p)
                self.assertEqual(len(events(self.e, y)), n)
                fm(self.e.home, "edit", y, "--blocked-by", "none")
                fm(self.e.home, "unblock", y)
                loop.tick(self.e.p)
                self.assertEqual(len(self.e.calls()), 1)
        self.setUp()
        y = self.y("--blocked-by", self.X1)
        backlog.set_status(self.e.p, self.X1, "canceled")
        loop.tick(self.e.p)
        fm(self.e.home, "cancel", y)
        self.assertEqual(self.e.issue(y)["status"], "canceled")

    def test_refused_changes_nothing(self):
        y = self.y("--blocked-by", self.X1)
        a, b, c = (backlog.add(self.e.p, n, status="triage") for n in "abc")
        backlog.update(self.e.p, b, blocked_by=[a])
        backlog.update(self.e.p, c, blocked_by=[b])
        cases = [
            ("add", "t", "--criteria", "c", "--blocked-by", "TY-99"),
            ("edit", y, "--blocked-by", y),
            ("edit", y, "--blocked-by", "TY-99"),
            ("edit", y, "--blocked-by", "none", self.X2),
            ("edit", a, "--blocked-by", b),
            ("edit", a, "--blocked-by", c),
        ]
        wants = ["TY-99", y, "TY-99", "none", b, c]
        before = (backlog.load(self.e.p), (self.e.p.state / "events.jsonl").read_text())
        for args, want in zip(cases, wants, strict=True):
            with self.subTest(args=args), self.assertRaises(SystemExit) as r:
                fm(self.e.home, *args)
            self.assertIn(want, str(r.exception.code))
        self.assertEqual((backlog.load(self.e.p), (self.e.p.state / "events.jsonl").read_text()), before)

    def test_blockers_follow_the_edit_rules(self):
        y = self.y()
        for status in ("triage", "backlog", "ready"):
            with self.subTest(status=status):
                backlog.update(self.e.p, y, plan_file="/x/p.md")
                backlog.set_status(self.e.p, y, status)
                fm(self.e.home, "edit", y, "--blocked-by", self.X1)
                self.assertEqual((self.e.issue(y)["status"], self.e.issue(y)["plan_file"]), (status, "/x/p.md"))
        for status in ("refining", "in progress", "in review", "done", "canceled", "duplicate"):
            with self.subTest(status=status):
                backlog.set_status(self.e.p, y, status)
                before = self.e.issue(y)
                with self.assertRaises(SystemExit):
                    fm(self.e.home, "edit", y, "--blocked-by", self.X2)
                self.assertEqual(self.e.issue(y), before)

    def test_pending_and_dead_by_status(self):
        for status, want in (("done", ([], [])), ("canceled", (["B"], ["B"])), ("in progress", (["B"], []))):
            issues = [{"id": "A", "status": "backlog", "blocked_by": ["B"]}, {"id": "B", "status": status}]
            self.assertEqual((blockers.pending(issues[0], issues), blockers.dead(issues[0], issues)), want)
