"""Tasks and subtasks: filing, showing, the loop skipping parents, closing and canceling together."""

import json
import os
from unittest import mock

import pytest
from helpers import Env, fm, git
from test_github import GH

from foreman.core.project.config import Project
from foreman.core.tasks import backlog, subtasks
from foreman.loop import tick
from foreman.providers import registry
from foreman.services.delivery import merge


def count(e: Env) -> int:
    return len(backlog.tasks(registry.backlog(e.p).load()))


class TestFiling:
    @pytest.fixture(autouse=True)
    def set_up(self, owner: None, env: Env):
        self.e = env
        self.P = registry.backlog(self.e.p).add("parent", status="triage")

    def test_add_parent_files_a_subtask(self):
        for status in ("triage", "backlog"):
            registry.backlog(self.e.p).set_status(self.P, status)
            s = fm(self.e.home, "add", "s", "--parent", self.P, "--criteria", "c").strip()
            assert (self.e.task(s)["parent"], self.e.task(s)["status"]) == (self.P, "backlog")
        s = fm(self.e.home, "add", "s", "--parent", self.P).strip()
        assert self.e.task(s)["status"] == "triage"

    def test_subtask_refused_from_an_agent_session(self):
        with mock.patch.dict(os.environ, {"FOREMAN_TASK": "TY-9"}), pytest.raises(SystemExit):
            fm(self.e.home, "add", "s", "--parent", self.P, "--criteria", "c")
        assert count(self.e) == 1

    def test_subtask_refused_once_the_parent_started(self):
        for status in ("ready", "refining", "in progress", "done"):
            registry.backlog(self.e.p).set_status(self.P, status)
            with pytest.raises(SystemExit):
                fm(self.e.home, "add", "s", "--parent", self.P, "--criteria", "c")
        with pytest.raises(SystemExit):
            fm(self.e.home, "add", "s", "--parent", "TY-99", "--criteria", "c")
        assert count(self.e) == 1

    def test_a_subtask_cannot_have_subtasks(self):
        s = fm(self.e.home, "add", "s", "--parent", self.P, "--criteria", "c").strip()
        with pytest.raises(SystemExit):
            fm(self.e.home, "add", "x", "--parent", s, "--criteria", "c")
        assert count(self.e) == 2


@pytest.mark.usefixtures("owner")
class TestShowing:
    def test_show_lists_subtasks_and_names_the_parent(self):
        e = Env()
        P = registry.backlog(e.p).add("parent", status="triage")
        s1 = fm(e.home, "add", "one", "--parent", P, "--criteria", "c").strip()
        s2 = fm(e.home, "add", "two", "--parent", P).strip()
        shown = json.loads(fm(e.home, "show", P))
        assert [(s["id"], s["status"]) for s in shown["subtasks"]] == [(s1, "backlog"), (s2, "triage")]
        assert json.loads(fm(e.home, "show", s1))["parent"] == P
        assert "subtasks" not in json.loads(fm(e.home, "show", s1))

    def test_list_shows_subtasks_under_their_parent(self):
        e = Env()
        a = registry.backlog(e.p).add("a", status="triage")
        b = registry.backlog(e.p).add("b", status="triage")
        s = fm(e.home, "add", "s", "--parent", a).strip()
        assert [ln.split()[0] for ln in fm(e.home, "list").splitlines()] == [a, s, b]

    def test_a_subtask_of_a_closed_parent_is_not_indented(self):
        e = Env()
        a = registry.backlog(e.p).add("a", status="triage")
        s = fm(e.home, "add", "s", "--parent", a).strip()
        merge.close(e.p, a, "done", "x")
        line = fm(e.home, "list").rstrip("\n")
        assert line == f"{s:8} {'triage':12} {'change':10} {'':14} s  [parent: {a}]"


class TestOutcome:
    @pytest.mark.parametrize(
        ("states", "want"),
        [
            ([], ""),
            (["done", "done"], "done"),
            (["done", "canceled"], "done"),
            (["canceled", "canceled"], "canceled"),
            (["done", "in progress"], ""),
            (["canceled", "triage"], ""),
        ],
    )
    def test_outcome_table(self, states: list[str], want: str):
        assert subtasks.outcome([{"status": s} for s in states]) == want


@pytest.mark.usefixtures("owner")
class TestLoop:
    def test_a_parent_gets_only_a_parent_agent(self):
        e = Env()
        P = registry.backlog(e.p).add("parent", status="backlog", criteria="c")
        s1 = fm(e.home, "add", "s1", "--parent", P, "--triage", "--criteria", "c").strip()
        tick.tick(e.p)
        assert [c["argv"][c["argv"].index("-n") + 1] for c in e.calls()] == ["TY-1 parent · parent"]
        assert (e.task(P)["status"], e.task(P)["agent"]["kind"]) == ("backlog", "parent")
        fm(e.home, "go", s1)
        tick.tick(e.p)
        assert [c["argv"][c["argv"].index("-n") + 1] for c in e.calls()] == ["TY-1 parent · parent", "TY-2 planning · s1"]
        assert (e.task(s1)["status"], e.task(P)["status"]) == ("refining", "backlog")


class TestClosing:
    @pytest.fixture(autouse=True)
    def set_up(self, owner: None, env: Env):
        self.e = env
        self.P = registry.backlog(self.e.p).add("parent", status="triage")
        self.S1 = fm(self.e.home, "add", "one", "--parent", self.P, "--criteria", "c").strip()
        self.S2 = fm(self.e.home, "add", "two", "--parent", self.P).strip()

    def status(self, tid: str) -> str:
        return self.e.task(tid)["status"]

    def test_parent_done_when_every_subtask_is_closed_and_one_is_done(self):
        merge.close(self.e.p, self.S1, "done", "x")
        assert self.status(self.P) == "triage"
        fm(self.e.home, "cancel", self.S2)
        assert self.status(self.P) == "done"

    def test_parent_done_when_its_only_subtask_is_merged(self):
        e = Env(test="true")
        P = registry.backlog(e.p).add("parent", status="triage")
        s = fm(e.home, "add", "one", "--parent", P, "--criteria", "c").strip()
        registry.backlog(e.p).set_status(s, "ready")
        tick.tick(e.p)
        wt = e.task(s)["worktree"]
        with open(os.path.join(wt, "app.py"), "a") as f:
            f.write("# 0\n")
        git(e.repo.parent / wt, "commit", "-qam", "change")
        registry.backlog(e.p).set_status(s, "in review", report={"summary": "s", "confused": ""})
        e.review(s, 0)
        tick.tick(e.p)
        merge.accept(e.p, s)
        tick.tick(e.p)
        assert (e.task(s)["status"], e.task(P)["status"]) == ("done", "done")

    def test_parent_canceled_when_all_subtasks_are(self):
        fm(self.e.home, "cancel", self.S1)
        assert self.status(self.P) == "triage"
        fm(self.e.home, "cancel", self.S2)
        assert self.status(self.P) == "canceled"

    def test_cancel_of_a_parent_cancels_its_open_subtasks(self):
        S3 = fm(self.e.home, "add", "three", "--parent", self.P, "--criteria", "c").strip()
        merge.close(self.e.p, S3, "done", "x")
        fm(self.e.home, "cancel", self.P)
        assert [self.status(i) for i in (self.S1, self.S2, S3, self.P)] == ["canceled", "canceled", "done", "canceled"]

    def test_cancel_of_a_parent_closes_its_subtasks_pull_requests(self, request: pytest.FixtureRequest):
        g = GH()
        request.addfinalizer(g.close)
        P = registry.backlog(g.e.p).add("parent", status="triage")
        tid = g.work()
        registry.backlog(g.e.p).update(tid, parent=P)
        tick.tick(g.e.p)
        fm(g.e.home, "cancel", P)
        assert g.state["prs"]["1"]["state"] == "CLOSED"
        assert (g.e.task(tid)["status"], g.e.task(P)["status"]) == ("canceled", "canceled")


class TestRace:
    def test_cancel_of_a_parent_does_not_overwrite_a_subtask_that_closed_meanwhile(self, owner: None):
        e = Env()
        P = registry.backlog(e.p).add("parent", status="triage")
        S = fm(e.home, "add", "s", "--parent", P, "--criteria", "c").strip()

        def merged(p: Project, i: str) -> None:  # the loop merges the subtask while the cascade closes it
            registry.backlog(p).set_status(S, "done")

        with mock.patch.object(merge, "stop_all", side_effect=merged):
            merge.close(e.p, P, "canceled", "x")
        assert (e.task(S)["status"], e.task(P)["status"]) == ("done", "canceled")
