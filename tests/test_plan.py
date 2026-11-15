import os
from unittest import mock

import pytest
from helpers import Env, fm

from fm.core.tasks import backlog
from fm.services.agents import brief, launch
from fm.ui.panel import actions

READY = ("ready",)


class TestPlan:
    @pytest.fixture(autouse=True)
    def set_up(self, request, env):
        self.e = env
        self.p = self.e.p
        self.tid = backlog.add(self.p, "vague idea")
        backlog.update(self.p, self.tid, status="refining")
        self.env = mock.patch.dict(os.environ, {"FM_TASK": self.tid})
        self.env.start()
        request.addfinalizer(self.env.stop)
        self.plan = self.p.home / "plans" / f"{self.tid}.md"

    def ready(self, *extra):
        return fm(self.e.home, *READY, *extra)

    def test_neither_flag_fails_and_leaves_it_refining(self):
        with pytest.raises(SystemExit) as r:
            self.ready()
        assert r.value.code != 0
        assert self.e.task(self.tid)["status"] == "refining"

    def test_missing_or_empty_plan_file_fails(self):
        with pytest.raises(SystemExit):
            self.ready("--plan", str(self.plan))
        self.plan.parent.mkdir(parents=True)
        self.plan.write_text("  \n")
        with pytest.raises(SystemExit):
            self.ready("--plan", str(self.plan))
        assert self.e.task(self.tid)["status"] == "refining"
        assert "plan_file" not in self.e.task(self.tid)

    def test_valid_plan_sets_plan_file_and_ready(self):
        self.plan.parent.mkdir(parents=True)
        self.plan.write_text("# Files touched\nx\n")
        self.ready("--plan", str(self.plan))
        i = self.e.task(self.tid)
        assert (i["status"], i["plan_file"]) == ("ready", str(self.plan))

    def test_no_plan_with_reason_is_ready_without_plan_file(self):
        self.ready("--no-plan", "one-line typo")
        i = self.e.task(self.tid)
        assert i["status"] == "ready"
        assert "plan_file" not in i

    def test_work_brief_with_and_without_plan(self):
        with_plan = {"id": "TY-1", "title": "t", "plan_file": "/x/plans/TY-1.md"}
        text = brief.brief(self.p, with_plan, "responsible")
        assert "/x/plans/TY-1.md" in text
        without = brief.brief(self.p, {"id": "TY-2", "title": "t"}, "responsible")
        assert "/plans/" not in without

    def test_plan_and_no_plan_together_fail(self):
        self.plan.parent.mkdir(parents=True)
        self.plan.write_text("x")
        with pytest.raises(SystemExit) as r:
            self.ready("--plan", str(self.plan), "--no-plan", "why")
        assert r.value.code != 0
        assert self.e.task(self.tid)["status"] == "refining"

    def test_no_plan_after_a_plan_clears_plan_file(self):
        self.plan.parent.mkdir(parents=True)
        self.plan.write_text("x")
        self.ready("--plan", str(self.plan))
        backlog.update(self.p, self.tid, status="refining")
        self.ready("--no-plan", "trivial after all")
        i = self.e.task(self.tid)
        assert "plan_file" not in i
        assert str(self.plan) not in brief.brief(self.p, i, "responsible")

    def test_fm_add_with_criteria_goes_to_planning(self):
        fm(self.e.home, "add", "t", "--criteria", "c", "--ready")
        i = next(x for x in backlog.load(self.p)["tasks"] if x["title"] == "t")
        assert i["status"] == "backlog"

    def test_fm_add_without_criteria_stays_in_triage_even_ready(self):
        fm(self.e.home, "add", "t", "--ready")
        i = next(x for x in backlog.load(self.p)["tasks"] if x["title"] == "t")
        assert i["status"] == "triage"

    def test_panel_filed_without_criteria_stays_in_triage(self):
        new = actions.add(self.p, '{"title": "p"}')["id"]
        assert self.e.task(new)["status"] == "triage"

    def test_go_refuses_without_criteria(self):
        tid = backlog.add(self.p, "no criteria")
        with pytest.raises(SystemExit) as r:
            fm(self.e.home, "go", tid)
        assert "define its acceptance criteria with the owner first" in str(r.value.code)
        assert self.e.task(tid)["status"] == "triage"

    def test_go_with_criteria_sends_it_to_planning(self):
        tid = backlog.add(self.p, "t", status="triage", criteria="c")
        fm(self.e.home, "go", tid)
        assert self.e.task(tid)["status"] == "backlog"


class TestReviewerPlan:
    def brief_of_reviewer(self, **keys):
        e = Env()
        tid = backlog.add(e.p, "x", status="ready", criteria="y")
        backlog.update(e.p, tid, status="in review", worktree=str(e.repo), **keys)
        launch.launch_reviewer(e.p, e.task(tid), "abc123", "hunter")
        launch.launch_reviewer(e.p, e.task(tid), "abc123")
        return e, e.calls()[-1]["argv"][-1]

    def test_reviewer_gets_the_registered_plan_file(self):
        e, text = self.brief_of_reviewer(plan_file="/elsewhere/custom-plan.md")
        assert "/elsewhere/custom-plan.md" in text
        assert str(e.p.home / "plans") not in text

    def test_reviewer_gets_no_plan_path_when_the_task_has_none(self):
        e, text = self.brief_of_reviewer()
        assert str(e.p.home / "plans") not in text
        assert "plan_file" not in text
