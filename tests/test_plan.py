import os
import unittest
from unittest import mock

from helpers import Env, fm

from fm import agents, backlog, panel_actions

READY = ("ready", "--claim", "g", "--criterion", "c")


class Plan(unittest.TestCase):
    def setUp(self):
        self.e = Env()
        self.p = self.e.p
        self.iid = backlog.add(self.p, "vague idea")
        backlog.update(self.p, self.iid, status="refining")
        self.env = mock.patch.dict(os.environ, {"FM_ISSUE": self.iid})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.plan = self.p.home / "plans" / f"{self.iid}.md"

    def ready(self, *extra):
        return fm(self.e.home, *READY, *extra)

    def test_neither_flag_fails_and_leaves_it_refining(self):
        with self.assertRaises(SystemExit) as r:
            self.ready()
        self.assertNotEqual(r.exception.code, 0)
        self.assertEqual(self.e.issue(self.iid)["status"], "refining")

    def test_missing_or_empty_plan_file_fails(self):
        with self.assertRaises(SystemExit):
            self.ready("--plan", str(self.plan))
        self.plan.parent.mkdir(parents=True)
        self.plan.write_text("  \n")
        with self.assertRaises(SystemExit):
            self.ready("--plan", str(self.plan))
        self.assertEqual(self.e.issue(self.iid)["status"], "refining")
        self.assertNotIn("plan_file", self.e.issue(self.iid))

    def test_valid_plan_sets_plan_file_and_ready(self):
        self.plan.parent.mkdir(parents=True)
        self.plan.write_text("# Files touched\nx\n")
        self.ready("--plan", str(self.plan))
        i = self.e.issue(self.iid)
        self.assertEqual((i["status"], i["plan_file"]), ("ready", str(self.plan)))

    def test_no_plan_with_reason_is_ready_without_plan_file(self):
        self.ready("--no-plan", "one-line typo")
        i = self.e.issue(self.iid)
        self.assertEqual(i["status"], "ready")
        self.assertNotIn("plan_file", i)

    def test_work_brief_with_and_without_plan(self):
        with_plan = {"id": "TY-1", "title": "t", "plan_file": "/x/plans/TY-1.md"}
        text = agents.brief(self.p, with_plan, "work")
        self.assertIn("/x/plans/TY-1.md", text)
        without = agents.brief(self.p, {"id": "TY-2", "title": "t"}, "work")
        self.assertNotIn("/plans/", without)

    def test_plan_and_no_plan_together_fail(self):
        self.plan.parent.mkdir(parents=True)
        self.plan.write_text("x")
        with self.assertRaises(SystemExit) as r:
            self.ready("--plan", str(self.plan), "--no-plan", "why")
        self.assertNotEqual(r.exception.code, 0)
        self.assertEqual(self.e.issue(self.iid)["status"], "refining")

    def test_no_plan_after_a_plan_clears_plan_file(self):
        self.plan.parent.mkdir(parents=True)
        self.plan.write_text("x")
        self.ready("--plan", str(self.plan))
        backlog.update(self.p, self.iid, status="refining")
        self.ready("--no-plan", "trivial after all")
        i = self.e.issue(self.iid)
        self.assertNotIn("plan_file", i)
        self.assertNotIn(str(self.plan), agents.brief(self.p, i, "work"))

    def assert_planless_ready(self, iid):
        i = self.e.issue(iid)
        self.assertEqual(i["status"], "ready")
        self.assertNotIn("plan_file", i)
        self.assertNotIn("/plans/", agents.brief(self.p, i, "work"))

    def test_fm_add_ready_needs_no_plan(self):
        fm(self.e.home, "add", "t", "--claim", "g", "--criterion", "c", "--ready")
        self.assert_planless_ready(max(x["id"] for x in backlog.load(self.p)["issues"] if x["title"] == "t"))

    def test_panel_filed_ready_needs_no_plan(self):
        new = panel_actions.add(self.p, '{"title": "p", "criterion": "c"}')["id"]
        self.assert_planless_ready(new)


class ReviewerPlan(unittest.TestCase):
    def brief_of_reviewer(self, **keys):
        e = Env()
        iid = backlog.add(e.p, "x", status="ready", criterion="y")
        backlog.update(e.p, iid, status="in review", worktree=str(e.repo), **keys)
        agents.launch_reviewer(e.p, e.issue(iid), "abc123", "hunter")
        agents.launch_reviewer(e.p, e.issue(iid), "abc123")
        return e, e.calls()[-1]["argv"][-1]

    def test_reviewer_gets_the_registered_plan_file(self):
        e, text = self.brief_of_reviewer(plan_file="/elsewhere/custom-plan.md")
        self.assertIn("/elsewhere/custom-plan.md", text)
        self.assertNotIn(str(e.p.home / "plans"), text)

    def test_reviewer_gets_no_plan_path_when_the_task_has_none(self):
        e, text = self.brief_of_reviewer()
        self.assertNotIn(str(e.p.home / "plans"), text)
        self.assertNotIn("plan_file", text)
