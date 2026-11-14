"""Tasks and subtasks: filing, showing, the loop skipping parents, closing and canceling together."""

import json
import os
import unittest
from unittest import mock

from helpers import Env, fm, git
from test_github import GH

from fm import backlog, integrate, loop, subtasks


def owner(case: unittest.TestCase) -> None:
    """The owner's shell: no agent session (FM_ISSUE) leaks in, whatever the caller's environment."""
    patch = mock.patch.dict(os.environ)
    patch.start()
    case.addCleanup(patch.stop)
    os.environ.pop("FM_ISSUE", None)


def count(e: Env) -> int:
    return len(backlog.issues(backlog.load(e.p)))


class Filing(unittest.TestCase):
    def setUp(self):
        owner(self)
        self.e = Env()
        self.P = backlog.add(self.e.p, "parent", status="triage")

    def test_add_parent_files_a_subtask(self):
        for status in ("triage", "backlog"):
            with self.subTest(status=status):
                backlog.set_status(self.e.p, self.P, status)
                s = fm(self.e.home, "add", "s", "--parent", self.P, "--criteria", "c").strip()
                self.assertEqual((self.e.issue(s)["parent"], self.e.issue(s)["status"]), (self.P, "backlog"))
        s = fm(self.e.home, "add", "s", "--parent", self.P).strip()
        self.assertEqual(self.e.issue(s)["status"], "triage")

    def test_subtask_refused_from_an_agent_session(self):
        with mock.patch.dict(os.environ, {"FM_ISSUE": "TY-9"}), self.assertRaises(SystemExit):
            fm(self.e.home, "add", "s", "--parent", self.P, "--criteria", "c")
        self.assertEqual(count(self.e), 1)

    def test_subtask_refused_once_the_parent_started(self):
        for status in ("ready", "refining", "in progress", "done"):
            with self.subTest(status=status):
                backlog.set_status(self.e.p, self.P, status)
                with self.assertRaises(SystemExit):
                    fm(self.e.home, "add", "s", "--parent", self.P, "--criteria", "c")
        with self.assertRaises(SystemExit):
            fm(self.e.home, "add", "s", "--parent", "TY-99", "--criteria", "c")
        self.assertEqual(count(self.e), 1)

    def test_a_subtask_cannot_have_subtasks(self):
        s = fm(self.e.home, "add", "s", "--parent", self.P, "--criteria", "c").strip()
        with self.assertRaises(SystemExit):
            fm(self.e.home, "add", "x", "--parent", s, "--criteria", "c")
        self.assertEqual(count(self.e), 2)


class Showing(unittest.TestCase):
    def setUp(self):
        owner(self)

    def test_show_lists_subtasks_and_names_the_parent(self):
        e = Env()
        P = backlog.add(e.p, "parent", status="triage")
        s1 = fm(e.home, "add", "one", "--parent", P, "--criteria", "c").strip()
        s2 = fm(e.home, "add", "two", "--parent", P).strip()
        shown = json.loads(fm(e.home, "show", P))
        self.assertEqual([(s["id"], s["status"]) for s in shown["subtasks"]], [(s1, "backlog"), (s2, "triage")])
        self.assertEqual(json.loads(fm(e.home, "show", s1))["parent"], P)
        self.assertNotIn("subtasks", json.loads(fm(e.home, "show", s1)))

    def test_list_shows_subtasks_under_their_parent(self):
        e = Env()
        a = backlog.add(e.p, "a", status="triage")
        b = backlog.add(e.p, "b", status="triage")
        s = fm(e.home, "add", "s", "--parent", a).strip()
        self.assertEqual([ln.split()[0] for ln in fm(e.home, "list").splitlines()], [a, s, b])

    def test_a_subtask_of_a_closed_parent_is_not_indented(self):
        e = Env()
        a = backlog.add(e.p, "a", status="triage")
        s = fm(e.home, "add", "s", "--parent", a).strip()
        integrate.close(e.p, a, "done", "x")
        line = fm(e.home, "list").rstrip("\n")
        self.assertEqual(line, f"{s:8} {'triage':12} {'':14} s")


class Outcome(unittest.TestCase):
    def test_outcome_table(self):
        for states, want in [
            ([], ""),
            (["done", "done"], "done"),
            (["done", "canceled"], "done"),
            (["canceled", "canceled"], "canceled"),
            (["done", "in progress"], ""),
            (["canceled", "triage"], ""),
        ]:
            with self.subTest(states=states):
                self.assertEqual(subtasks.outcome([{"status": s} for s in states]), want)


class Loop(unittest.TestCase):
    def setUp(self):
        owner(self)

    def test_a_parent_never_gets_an_agent(self):
        e = Env()
        P = backlog.add(e.p, "parent", status="backlog", criteria="c")
        s1 = fm(e.home, "add", "s1", "--parent", P, "--triage", "--criteria", "c").strip()
        loop.tick(e.p)
        self.assertEqual(e.calls(), [])
        self.assertEqual(e.issue(P)["status"], "backlog")
        fm(e.home, "go", s1)
        loop.tick(e.p)
        self.assertEqual(len(e.calls()), 1)
        self.assertEqual((e.issue(s1)["status"], e.issue(P)["status"]), ("refining", "backlog"))


class Closing(unittest.TestCase):
    def setUp(self):
        owner(self)
        self.e = Env()
        self.P = backlog.add(self.e.p, "parent", status="triage")
        self.S1 = fm(self.e.home, "add", "one", "--parent", self.P, "--criteria", "c").strip()
        self.S2 = fm(self.e.home, "add", "two", "--parent", self.P).strip()

    def status(self, iid: str) -> str:
        return self.e.issue(iid)["status"]

    def test_parent_done_when_every_subtask_is_closed_and_one_is_done(self):
        integrate.close(self.e.p, self.S1, "done", "x")
        self.assertEqual(self.status(self.P), "triage")
        fm(self.e.home, "cancel", self.S2)
        self.assertEqual(self.status(self.P), "done")

    def test_parent_done_when_its_only_subtask_is_merged(self):
        e = Env(test="true")
        P = backlog.add(e.p, "parent", status="triage")
        s = fm(e.home, "add", "one", "--parent", P, "--criteria", "c").strip()
        backlog.set_status(e.p, s, "ready")
        loop.tick(e.p)
        wt = e.issue(s)["worktree"]
        with open(os.path.join(wt, "app.py"), "a") as f:
            f.write("# 0\n")
        git(e.repo.parent / wt, "commit", "-qam", "change")
        backlog.set_status(e.p, s, "in review", report={"summary": "s", "confused": ""})
        e.review(s, 0)
        loop.tick(e.p)
        integrate.accept(e.p, s)
        loop.tick(e.p)
        self.assertEqual((e.issue(s)["status"], e.issue(P)["status"]), ("done", "done"))

    def test_parent_canceled_when_all_subtasks_are(self):
        fm(self.e.home, "cancel", self.S1)
        self.assertEqual(self.status(self.P), "triage")
        fm(self.e.home, "cancel", self.S2)
        self.assertEqual(self.status(self.P), "canceled")

    def test_cancel_of_a_parent_cancels_its_open_subtasks(self):
        S3 = fm(self.e.home, "add", "three", "--parent", self.P, "--criteria", "c").strip()
        integrate.close(self.e.p, S3, "done", "x")
        fm(self.e.home, "cancel", self.P)
        self.assertEqual([self.status(i) for i in (self.S1, self.S2, S3, self.P)], ["canceled", "canceled", "done", "canceled"])

    def test_cancel_of_a_parent_closes_its_subtasks_pull_requests(self):
        g = GH()
        self.addCleanup(g.close)
        P = backlog.add(g.e.p, "parent", status="triage")
        iid = g.work()
        backlog.update(g.e.p, iid, parent=P)
        loop.tick(g.e.p)
        fm(g.e.home, "cancel", P)
        self.assertEqual(g.state["prs"]["1"]["state"], "CLOSED")
        self.assertEqual((g.e.issue(iid)["status"], g.e.issue(P)["status"]), ("canceled", "canceled"))


class Race(unittest.TestCase):
    def test_cancel_of_a_parent_does_not_overwrite_a_subtask_that_closed_meanwhile(self):
        owner(self)
        e = Env()
        P = backlog.add(e.p, "parent", status="triage")
        S = fm(e.home, "add", "s", "--parent", P, "--criteria", "c").strip()

        def merged(p, i):  # the loop merges the subtask while the cascade closes it
            backlog.set_status(p, S, "done")

        with mock.patch.object(integrate, "stop_all", side_effect=merged):
            integrate.close(e.p, P, "canceled", "x")
        self.assertEqual((e.issue(S)["status"], e.issue(P)["status"]), ("done", "canceled"))
