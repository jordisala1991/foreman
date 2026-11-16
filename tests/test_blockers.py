"""A task waits for the tasks in `blocked_by`: filing, editing, the loop holding and releasing it, refusals."""

import json

import pytest
from helpers import Env, fm

from foreman import util
from foreman.core.tasks import backlog, blockers
from foreman.loop.launch import launch_free
from foreman.services.delivery import merge
from foreman.steps import release


def events(e: Env, tid: str) -> list[str]:
    return [x["text"] for x in util.read_jsonl(e.p.state / "events.jsonl") if x["task"] == tid]


class TestBlockers:
    @pytest.fixture(autouse=True)
    def set_up(self, owner, fake_git):
        self.fake = fake_git
        self.fresh()

    def fresh(self) -> None:
        """A new project with two triage tasks (a test that needs several calls it again)."""
        self.e = Env(repo=self.fake)
        self.X1 = backlog.add(self.e.p, "x1", status="triage")
        self.X2 = backlog.add(self.e.p, "x2", status="triage")

    def y(self, *extra: str) -> str:
        return fm(self.e.home, "add", "y", "--criteria", "c", *extra).strip()

    def step(self) -> None:
        """The tick's steps a blocker is about, in its order: release, then launch."""
        release.run(self.e.p)
        launch_free(self.e.p)

    def ids(self, tid: str) -> list[str]:
        return self.e.task(tid).get("blocked_by", [])

    def test_add_and_edit_take_blockers_and_show_them(self):
        a = self.y("--blocked-by", self.X1, self.X2)
        b = self.y("--blocked-by", self.X1, "--blocked-by", self.X2)
        assert (self.ids(a), self.ids(b)) == ([self.X1, self.X2],) * 2
        fm(self.e.home, "edit", a, "--blocked-by", self.X1)
        assert self.ids(a) == [self.X1]
        assert json.loads(fm(self.e.home, "show", a))["waiting_for"] == [self.X1]
        line = next(x for x in fm(self.e.home, "list").splitlines() if x.startswith(a))
        assert self.X1 in line
        merge.close(self.e.p, self.X1, "done", "x")
        line = next(x for x in fm(self.e.home, "list").splitlines() if x.startswith(a))
        assert self.X1 not in line
        fm(self.e.home, "edit", a, "--blocked-by", "none")
        assert self.ids(a) == []
        fm(self.e.home, "edit", a, "--blocked-by", self.X2)
        fm(self.e.home, "cancel", a)
        line = next(x for x in fm(self.e.home, "list", "--all").splitlines() if x.startswith(a))
        assert self.X2 not in line
        assert "waiting_for" not in json.loads(fm(self.e.home, "show", a))

    @pytest.mark.parametrize(("status", "started"), (("backlog", "refining"), ("ready", "in progress")))
    def test_not_launched_until_the_last_blocker_is_done(self, status, started):
        y = self.y("--blocked-by", self.X1, self.X2)
        backlog.set_status(self.e.p, y, status)
        self.step()
        assert (self.e.calls(), self.e.task(y)["status"]) == ([], status)
        merge.close(self.e.p, self.X1, "done", "x")
        self.step()
        assert self.e.calls() == []
        merge.close(self.e.p, self.X2, "done", "x")
        self.step()
        assert (len(self.e.calls()), self.e.task(y)["status"]) == (1, started)
        assert len([t for t in events(self.e, y) if "unblocked" in t]) == 1
        self.step()
        assert len([t for t in events(self.e, y) if "unblocked" in t]) == 1

    def test_a_task_filed_with_a_done_blocker_launches_at_once(self):
        merge.close(self.e.p, self.X1, "done", "x")
        y = self.y("--blocked-by", self.X1)
        self.step()
        assert len(self.e.calls()) == 1
        assert self.e.task(y)["status"] == "refining"

    def test_a_canceled_blocker_blocks_the_task_for_the_owner(self):
        for status in ("canceled", "duplicate"):
            self.fresh()
            y = self.y("--blocked-by", self.X1)
            backlog.set_status(self.e.p, self.X1, status)
            self.step()
            assert self.e.calls() == []
            assert self.X1 in self.e.task(y)["blocked"]
            n = len(events(self.e, y))
            self.step()
            assert len(events(self.e, y)) == n
            fm(self.e.home, "edit", y, "--blocked-by", "none")
            fm(self.e.home, "unblock", y)
            self.step()
            assert len(self.e.calls()) == 1
        self.fresh()
        y = self.y("--blocked-by", self.X1)
        backlog.set_status(self.e.p, self.X1, "canceled")
        self.step()
        fm(self.e.home, "cancel", y)
        assert self.e.task(y)["status"] == "canceled"

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
            with pytest.raises(SystemExit) as r:
                fm(self.e.home, *args)
            assert want in str(r.value.code)
        assert (backlog.load(self.e.p), (self.e.p.state / "events.jsonl").read_text()) == before

    def test_blockers_follow_the_edit_rules(self):
        y = self.y()
        for status in ("triage", "backlog", "ready"):
            backlog.update(self.e.p, y, plan_file="/x/p.md")
            backlog.set_status(self.e.p, y, status)
            fm(self.e.home, "edit", y, "--blocked-by", self.X1)
            assert (self.e.task(y)["status"], self.e.task(y)["plan_file"]) == (status, "/x/p.md")
        for status in ("refining", "in progress", "in review", "done", "canceled", "duplicate"):
            backlog.set_status(self.e.p, y, status)
            before = self.e.task(y)
            with pytest.raises(SystemExit):
                fm(self.e.home, "edit", y, "--blocked-by", self.X2)
            assert self.e.task(y) == before

    def test_pending_and_dead_by_status(self):
        for status, want in (("done", ([], [])), ("canceled", (["B"], ["B"])), ("in progress", (["B"], []))):
            tasks = [{"id": "A", "status": "backlog", "blocked_by": ["B"]}, {"id": "B", "status": status}]
            assert (blockers.pending(tasks[0], tasks), blockers.dead(tasks[0], tasks)) == want
