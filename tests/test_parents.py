"""A parent task's own agent: launched on its Go, outside the slots; told only of subtasks merged or canceled; never
closes the parent alone; files and edits only its own subtasks; `fm show` gives it real data per subtask."""

import json
import unittest

from helpers import Env, fm
from test_misled import run_as
from test_subtasks import owner

from fm import backlog, loop, subtasks


def resumes(e: Env, sid: str) -> list[dict]:
    return [c for c in e.calls() if c["argv"][:3] == ["--bg", "--resume", sid]]


class Launch(unittest.TestCase):
    def setUp(self):
        owner(self)

    def test_go_launches_its_agent(self):
        e = Env()
        P = backlog.add(e.p, "parent", status="triage", criteria="c")
        fm(e.home, "add", "s", "--parent", P, "--triage", "--criteria", "c")
        loop.tick(e.p)
        self.assertEqual(e.calls(), [])
        fm(e.home, "go", P)
        loop.tick(e.p)
        self.assertEqual(len(e.calls()), 1)
        argv = e.calls()[0]["argv"]
        self.assertEqual(argv[argv.index("-n") + 1], "ty-1")
        t = e.task(P)
        self.assertEqual((t["agent"]["kind"], t["status"], t["agent"]["cwd"]), ("parent", "backlog", str(e.repo)))
        self.assertNotIn("worktree", t)
        self.assertNotIn("branch", t)

    def test_an_existing_parent_in_backlog_gets_its_agent(self):
        e = Env()
        P = backlog.add(e.p, "parent", status="backlog", criteria="c")
        fm(e.home, "add", "s", "--parent", P, "--triage", "--criteria", "c")
        loop.tick(e.p)
        self.assertEqual((e.task(P)["agent"]["kind"], e.task(P)["status"]), ("parent", "backlog"))

    def test_the_parent_agent_takes_no_slot(self):
        e = Env(slots=1)
        P = backlog.add(e.p, "parent", status="backlog", criteria="c")
        s = fm(e.home, "add", "s", "--parent", P, "--triage", "--criteria", "c").strip()
        loop.tick(e.p)
        backlog.set_status(e.p, s, "ready")
        loop.tick(e.p)
        self.assertEqual(e.task(s)["status"], "in progress")
        self.assertEqual(len(e.calls()), 2)


class Notices(unittest.TestCase):
    def setUp(self):
        owner(self)
        self.e = Env()
        self.P = backlog.add(self.e.p, "parent", status="backlog", criteria="c")
        self.S0 = fm(self.e.home, "add", "zero", "--parent", self.P, "--triage", "--criteria", "c").strip()
        self.S1 = fm(self.e.home, "add", "one", "--parent", self.P, "--triage", "--criteria", "c").strip()
        self.S2 = fm(self.e.home, "add", "two", "--parent", self.P, "--triage", "--criteria", "c").strip()
        fm(self.e.home, "cancel", self.S0)  # closed before the agent: no notice for the past
        loop.tick(self.e.p)
        self.sid = self.e.task(self.P)["agent"]["session"]
        self.e.set_state("ty-1", "done")

    def idle_ticks(self, n: int = 2) -> None:
        for _ in range(n):
            loop.tick(self.e.p)
            self.e.set_state("ty-1", "done")

    def test_a_canceled_subtask_is_told_and_nothing_else(self):
        self.idle_ticks()
        self.assertEqual(resumes(self.e, self.sid), [])
        fm(self.e.home, "cancel", self.S1)
        self.idle_ticks()
        told = resumes(self.e, self.sid)
        self.assertEqual(len(told), 1)
        self.assertIn(self.S1, told[0]["argv"][3])
        self.assertIn("canceled", told[0]["argv"][3])
        backlog.ask(self.e.p, self.S2, "ty-3", "q?")
        backlog.update(self.e.p, self.S2, blocked="why")
        self.idle_ticks(3)
        self.assertEqual(len(resumes(self.e, self.sid)), 1)

    def test_the_parent_does_not_close_alone(self):
        fm(self.e.home, "cancel", self.S1)
        fm(self.e.home, "cancel", self.S2)
        self.idle_ticks(4)
        self.assertEqual(self.e.task(self.P)["status"], "backlog")
        self.assertIn("goal", resumes(self.e, self.sid)[-1]["argv"][3])


class Closing(unittest.TestCase):
    def setUp(self):
        owner(self)
        self.e = Env()
        self.P = backlog.add(self.e.p, "parent", status="backlog", criteria="c")
        self.S1 = fm(self.e.home, "add", "one", "--parent", self.P, "--triage", "--criteria", "c").strip()
        self.S2 = fm(self.e.home, "add", "two", "--parent", self.P, "--triage", "--criteria", "c").strip()
        loop.tick(self.e.p)

    def done(self) -> None:
        run_as(self.e, self.P, "ty-1", "done", "--summary", "s")

    def test_refused_while_a_subtask_is_open(self):
        backlog.set_status(self.e.p, self.S1, "done")
        with self.assertRaises(SystemExit):
            self.done()
        self.assertEqual(self.e.task(self.P)["status"], "backlog")

    def test_done_on_the_owners_yes_and_its_session_stops(self):
        backlog.set_status(self.e.p, self.S1, "done")
        backlog.set_status(self.e.p, self.S2, "canceled")
        self.done()
        self.assertEqual(self.e.task(self.P)["status"], "done")
        loop.tick(self.e.p)
        self.assertIn(self.e.task(self.P)["agent"]["short"], self.e.stops())

    def test_canceled_when_every_subtask_is(self):
        backlog.set_status(self.e.p, self.S1, "canceled")
        backlog.set_status(self.e.p, self.S2, "canceled")
        self.done()
        self.assertEqual(self.e.task(self.P)["status"], "canceled")


class Authority(unittest.TestCase):
    def setUp(self):
        owner(self)
        self.e = Env()
        self.P = backlog.add(self.e.p, "parent", status="backlog", criteria="c")
        self.Q = backlog.add(self.e.p, "other", status="backlog", criteria="c")
        self.S = fm(self.e.home, "add", "s", "--parent", self.P, "--triage", "--criteria", "c").strip()
        self.T = fm(self.e.home, "add", "t", "--parent", self.Q, "--triage", "--criteria", "c").strip()
        loop.tick(self.e.p)

    def test_its_own_subtasks_only(self):
        run_as(self.e, self.P, "ty-1", "add", "new", "--parent", self.P, "--triage", "--criteria", "c")
        self.assertEqual(len(subtasks.of(backlog.tasks(backlog.load(self.e.p)), self.P)), 2)
        run_as(self.e, self.P, "ty-1", "edit", self.S, "--criteria", "x")
        self.assertEqual(self.e.task(self.S)["acceptance"]["criteria"], "x")
        backlog.set_status(self.e.p, self.S, "in progress")
        for args in (("edit", self.S, "--criteria", "y"), ("edit", self.P, "--criteria", "y"), ("edit", self.T, "--criteria", "y")):
            with self.subTest(args=args), self.assertRaises(SystemExit):
                run_as(self.e, self.P, "ty-1", *args)
        with self.assertRaises(SystemExit):
            run_as(self.e, self.P, "ty-1", "add", "x", "--parent", self.Q, "--triage", "--criteria", "c")
        self.assertEqual(self.e.task(self.S)["acceptance"]["criteria"], "x")

    def test_a_subtask_it_files_waits_for_a_go(self):
        for flags in ((), ("--ready",)):
            with self.subTest(flags=flags):
                run_as(self.e, self.P, "ty-1", "add", "new", "--parent", self.P, "--criteria", "c", *flags)
                new = subtasks.of(backlog.tasks(backlog.load(self.e.p)), self.P)[-1]
                loop.tick(self.e.p)
                self.assertEqual(self.e.task(new["id"])["status"], "triage")

    def test_refused_from_a_planning_agent(self):
        backlog.update(self.e.p, self.T, agent={"kind": "planning", "name": "ty-4-planning"})
        with self.assertRaises(SystemExit):
            run_as(self.e, self.T, "ty-4-planning", "add", "x", "--parent", self.T, "--criteria", "c")
        with self.assertRaises(SystemExit):
            run_as(self.e, self.T, "ty-4-planning", "edit", self.S, "--criteria", "z")


class RealData(unittest.TestCase):
    def test_show_gives_each_subtasks_state(self):
        owner(self)
        e = Env()
        P = backlog.add(e.p, "parent", status="triage", criteria="c")
        s = fm(e.home, "add", "s", "--parent", P, "--triage", "--criteria", "c").strip()
        backlog.update(e.p, s, pr={"url": "http://x/1"}, reviews=[{}, {}], blocked="why")
        backlog.ask(e.p, s, "ty-2", "q?")
        shown = json.loads(fm(e.home, "show", P))["subtasks"][0]
        questions = [{"id": f"{s} Q1", "text": "q?"}]
        want = {"id": s, "status": "triage", "title": "s", "pr": "http://x/1", "review_rounds": 2, "questions": questions, "blocked": "why", "waiting_for": []}
        self.assertEqual(shown, want)


class Pure(unittest.TestCase):
    def test_agent_refusal_table(self):
        tasks = [
            {"id": "P", "status": "backlog", "agent": {"kind": "parent"}},
            {"id": "Q", "status": "backlog", "agent": {"kind": "parent"}},
            {"id": "X", "status": "refining", "agent": {"kind": "planning"}},
        ]
        for caller, parent, ok in [("", "P", True), ("", "", True), ("P", "P", True), ("X", "X", False), ("Q", "P", False), ("P", "", False)]:
            with self.subTest(caller=caller, parent=parent):
                self.assertEqual(subtasks.agent_refusal(tasks, caller, parent, "w") == "", ok)

    def test_notice(self):
        a = {"id": "S1", "title": "a", "status": "done"}
        b = {"id": "S2", "title": "b", "status": "in progress"}
        c = {"id": "S2", "title": "b", "status": "canceled"}
        self.assertIn("merged", subtasks.notice(a, [a, b], "P"))
        self.assertNotIn("goal", subtasks.notice(a, [a, b], "P"))
        self.assertIn("canceled", subtasks.notice(c, [a, c], "P"))
        self.assertIn("goal", subtasks.notice(c, [a, c], "P"))


if __name__ == "__main__":
    unittest.main()
