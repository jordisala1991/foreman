"""A parent task's own agent: launched on its Go, outside the slots; told only of subtasks merged or canceled; never
closes the parent alone; files and edits only its own subtasks; `fm show` gives it real data per subtask."""

import json
from typing import Any

import pytest
from helpers import Env, fm
from test_misled import run_as

from foreman.core.tasks import backlog, questions, subtasks
from foreman.loop import tick
from foreman.providers import registry
from foreman.providers.runner import NO_ROWS
from foreman.steps import parents, watch


def resumes(e: Env, sid: str) -> list[dict[str, Any]]:
    return [c for c in e.calls() if c["argv"][:3] == ["--bg", "--resume", sid]]


@pytest.mark.usefixtures("owner")
class TestLaunch:
    def test_go_launches_its_agent(self):
        e = Env()
        P = registry.backlog(e.p).add("parent", status="triage", criteria="c")
        fm(e.home, "add", "s", "--parent", P, "--triage", "--criteria", "c")
        tick.tick(e.p)
        assert e.calls() == []
        fm(e.home, "go", P)
        tick.tick(e.p)
        assert len(e.calls()) == 1
        argv = e.calls()[0]["argv"]
        assert argv[argv.index("-n") + 1] == "TY-1 parent · parent"
        t = e.task(P)
        assert (t["agent"]["kind"], t["status"], t["agent"]["cwd"]) == ("parent", "backlog", str(e.repo))
        assert "worktree" not in t
        assert "branch" not in t

    def test_an_existing_parent_in_backlog_gets_its_agent(self):
        e = Env()
        P = registry.backlog(e.p).add("parent", status="backlog", criteria="c")
        fm(e.home, "add", "s", "--parent", P, "--triage", "--criteria", "c")
        tick.tick(e.p)
        assert (e.task(P)["agent"]["kind"], e.task(P)["status"]) == ("parent", "backlog")

    def test_the_parent_agent_takes_no_slot(self):
        e = Env(slots=1)
        P = registry.backlog(e.p).add("parent", status="backlog", criteria="c")
        s = fm(e.home, "add", "s", "--parent", P, "--triage", "--criteria", "c").strip()
        tick.tick(e.p)
        registry.backlog(e.p).set_status(s, "ready")
        tick.tick(e.p)
        assert e.task(s)["status"] == "in progress"
        assert len(e.calls()) == 2


class TestNotices:
    @pytest.fixture(autouse=True)
    def set_up(self, owner, env):
        self.e = env
        self.P = registry.backlog(self.e.p).add("parent", status="backlog", criteria="c")
        self.S0 = fm(self.e.home, "add", "zero", "--parent", self.P, "--triage", "--criteria", "c").strip()
        self.S1 = fm(self.e.home, "add", "one", "--parent", self.P, "--triage", "--criteria", "c").strip()
        self.S2 = fm(self.e.home, "add", "two", "--parent", self.P, "--triage", "--criteria", "c").strip()
        fm(self.e.home, "cancel", self.S0)  # closed before the agent: no notice for the past
        tick.tick(self.e.p)
        self.sid = self.e.task(self.P)["agent"]["session"]
        self.e.set_state("ty-1", "done")

    def idle_ticks(self, n: int = 2) -> None:
        for _ in range(n):
            tick.tick(self.e.p)
            self.e.set_state("ty-1", "done")

    def test_a_canceled_subtask_is_told_and_nothing_else(self):
        self.idle_ticks()
        assert resumes(self.e, self.sid) == []
        fm(self.e.home, "cancel", self.S1)
        self.idle_ticks()
        told = resumes(self.e, self.sid)
        assert len(told) == 1
        assert self.S1 in told[0]["argv"][3]
        assert "canceled" in told[0]["argv"][3]
        registry.backlog(self.e.p).ask(self.S2, "ty-3", "q?")
        registry.backlog(self.e.p).update(self.S2, blocked="why")
        self.idle_ticks(3)
        assert len(resumes(self.e, self.sid)) == 1

    def test_the_parent_does_not_close_alone(self):
        fm(self.e.home, "cancel", self.S1)
        fm(self.e.home, "cancel", self.S2)
        self.idle_ticks(4)
        assert self.e.task(self.P)["status"] == "backlog"
        assert "goal" in resumes(self.e, self.sid)[-1]["argv"][3]

    def test_the_parent_asks_with_foreman_ask_and_gets_the_answer(self):
        run_as(self.e, self.P, "ty-1", "ask", "Which subtask first?")
        qs = questions.open_questions(registry.backlog(self.e.p).load(), self.P)
        assert len(qs) == 1
        assert qs[0]["text"] == "Which subtask first?"
        assert qs[0]["id"] in fm(self.e.home, "list")
        fm(self.e.home, "answer", qs[0]["id"], "zero-two")
        self.idle_ticks()
        assert "zero-two" in resumes(self.e, self.sid)[-1]["argv"][3]


@pytest.mark.usefixtures("owner")
class TestRelaunched:
    """A parent whose session ended or is gone gets a new one on the next pass, told where it stands."""

    @pytest.fixture(autouse=True)
    def set_up(self, owner, env):
        self.e = env
        self.P = registry.backlog(env.p).add("parent", status="backlog", criteria="c")
        self.S0 = fm(env.home, "add", "zero", "--parent", self.P, "--triage", "--criteria", "c").strip()
        self.S1 = fm(env.home, "add", "one", "--parent", self.P, "--triage", "--criteria", "c").strip()
        tick.tick(env.p)
        self.old = env.task(self.P)["agent"]
        registry.backlog(env.p).set_status(self.S0, "done", "x")

    def undelivered(self) -> list[str]:
        return [q["answer"] for q in questions.undelivered(registry.backlog(self.e.p).load())]

    def test_an_ended_session_gets_a_new_one_told_where_it_stands(self):
        self.e.set_state(self.old["name"], "stopped")
        tick.tick(self.e.p)
        assert len(self.e.calls()) == 2
        text = self.e.calls()[-1]["argv"][-1]
        assert f"- {self.S0} done: zero" in text
        assert f"- {self.S1} " in text
        assert "Notices you missed:" in text
        assert f"Subtask {self.S0} «zero» was merged" in text
        assert "$standing" not in text
        t = self.e.task(self.P)
        assert t["agent"]["session"] != self.old["session"]
        assert {self.old["session"], t["agent"]["session"]} <= set(t["sessions"])
        assert resumes(self.e, self.old["session"]) == []

    def test_the_notices_it_missed_are_in_the_brief_and_not_sent_again(self):
        self.e.set_state(self.old["name"], "stopped")
        tick.tick(self.e.p)
        text = self.e.calls()[-1]["argv"][-1]
        assert f"Subtask {self.S0} «zero» was merged" in text
        assert self.undelivered() == []
        tick.tick(self.e.p)
        assert len(self.e.calls()) == 2

    def test_a_session_gone_from_the_listing_gets_a_new_one(self):
        self.e.set_state(self.old["name"], "stopped")
        (self.e.bin / "agents.json").write_text(json.dumps([{"id": "other", "sessionId": "other-0000-0000", "name": "other", "state": "working", "pid": 1}]))
        tick.tick(self.e.p)
        assert len(self.e.calls()) == 2

    def test_an_idle_session_is_not_replaced(self):
        self.e.set_state(self.old["name"], "done")
        parents.run(self.e.p, registry.rows(self.e.p))
        assert len(self.e.calls()) == 1

    def test_an_unreadable_listing_launches_nothing(self):
        parents.run(self.e.p, NO_ROWS)
        assert len(self.e.calls()) == 1

    def test_an_agent_with_no_session_yet_launches_nothing(self):
        registry.backlog(self.e.p).update(self.P, agent={**self.old, "session": None})
        self.e.set_state(self.old["name"], "stopped")
        parents.run(self.e.p, registry.rows(self.e.p))
        assert len(self.e.calls()) == 1

    def test_the_owner_is_no_longer_waited_on_once_it_runs(self):
        self.e.set_state(self.old["name"], "stopped")
        tick.tick(self.e.p)
        assert f"parent:{self.P}" not in [n.key for n in watch.found(self.e.p, registry.rows(self.e.p))]


class TestClosing:
    @pytest.fixture(autouse=True)
    def set_up(self, owner, env):
        self.e = env
        self.P = registry.backlog(self.e.p).add("parent", status="backlog", criteria="c")
        self.S1 = fm(self.e.home, "add", "one", "--parent", self.P, "--triage", "--criteria", "c").strip()
        self.S2 = fm(self.e.home, "add", "two", "--parent", self.P, "--triage", "--criteria", "c").strip()
        tick.tick(self.e.p)

    def done(self) -> None:
        run_as(self.e, self.P, "ty-1", "done", "--summary", "s")

    def test_refused_while_a_subtask_is_open(self):
        registry.backlog(self.e.p).set_status(self.S1, "done")
        with pytest.raises(SystemExit):
            self.done()
        assert self.e.task(self.P)["status"] == "backlog"

    def test_done_on_the_owners_yes_and_its_session_stops(self):
        registry.backlog(self.e.p).set_status(self.S1, "done")
        registry.backlog(self.e.p).set_status(self.S2, "canceled")
        self.done()
        assert self.e.task(self.P)["status"] == "done"
        tick.tick(self.e.p)
        assert self.e.task(self.P)["agent"]["short"] in self.e.stops()

    def test_canceled_when_every_subtask_is(self):
        registry.backlog(self.e.p).set_status(self.S1, "canceled")
        registry.backlog(self.e.p).set_status(self.S2, "canceled")
        self.done()
        assert self.e.task(self.P)["status"] == "canceled"


class TestAuthority:
    @pytest.fixture(autouse=True)
    def set_up(self, owner, env):
        self.e = env
        self.P = registry.backlog(self.e.p).add("parent", status="backlog", criteria="c")
        self.Q = registry.backlog(self.e.p).add("other", status="backlog", criteria="c")
        self.S = fm(self.e.home, "add", "s", "--parent", self.P, "--triage", "--criteria", "c").strip()
        self.T = fm(self.e.home, "add", "t", "--parent", self.Q, "--triage", "--criteria", "c").strip()
        tick.tick(self.e.p)

    def test_its_own_subtasks_only(self):
        run_as(self.e, self.P, "ty-1", "add", "new", "--parent", self.P, "--triage", "--criteria", "c")
        assert len(subtasks.of(backlog.tasks(registry.backlog(self.e.p).load()), self.P)) == 2
        run_as(self.e, self.P, "ty-1", "edit", self.S, "--criteria", "x")
        assert self.e.task(self.S)["acceptance"]["criteria"] == "x"
        registry.backlog(self.e.p).set_status(self.S, "in progress")
        for args in (("edit", self.S, "--criteria", "y"), ("edit", self.P, "--criteria", "y"), ("edit", self.T, "--criteria", "y")):
            with pytest.raises(SystemExit):
                run_as(self.e, self.P, "ty-1", *args)
        with pytest.raises(SystemExit):
            run_as(self.e, self.P, "ty-1", "add", "x", "--parent", self.Q, "--triage", "--criteria", "c")
        assert self.e.task(self.S)["acceptance"]["criteria"] == "x"

    def test_a_subtask_it_files_waits_for_a_go(self):
        for flags in ((), ("--ready",)):
            run_as(self.e, self.P, "ty-1", "add", "new", "--parent", self.P, "--criteria", "c", *flags)
            new = subtasks.of(backlog.tasks(registry.backlog(self.e.p).load()), self.P)[-1]
            tick.tick(self.e.p)
            assert self.e.task(new["id"])["status"] == "triage"

    def test_refused_from_a_planning_agent(self):
        registry.backlog(self.e.p).update(self.T, agent={"kind": "planning", "name": "ty-4-planning"})
        with pytest.raises(SystemExit):
            run_as(self.e, self.T, "ty-4-planning", "add", "x", "--parent", self.T, "--criteria", "c")
        with pytest.raises(SystemExit):
            run_as(self.e, self.T, "ty-4-planning", "edit", self.S, "--criteria", "z")


class TestRealData:
    def test_show_gives_each_subtasks_state(self, owner):
        e = Env()
        P = registry.backlog(e.p).add("parent", status="triage", criteria="c")
        s = fm(e.home, "add", "s", "--parent", P, "--triage", "--criteria", "c").strip()
        registry.backlog(e.p).update(s, pr={"url": "http://x/1"}, reviews=[{}, {}], blocked="why")
        registry.backlog(e.p).ask(s, "ty-2", "q?")
        shown = json.loads(fm(e.home, "show", P))["subtasks"][0]
        qs = [{"id": f"{s} Q1", "text": "q?"}]
        want = {"id": s, "status": "triage", "title": "s", "pr": "http://x/1", "review_rounds": 2, "questions": qs, "blocked": "why", "waiting_for": []}
        assert shown == want


class TestPure:
    def test_agent_refusal_table(self):
        tasks = [
            {"id": "P", "status": "backlog", "agent": {"kind": "parent"}},
            {"id": "Q", "status": "backlog", "agent": {"kind": "parent"}},
            {"id": "X", "status": "refining", "agent": {"kind": "planning"}},
        ]
        for caller, parent, ok in [("", "P", True), ("", "", True), ("P", "P", True), ("X", "X", False), ("Q", "P", False), ("P", "", False)]:
            assert (subtasks.agent_refusal(tasks, caller, parent, "w") == "") == ok

    def test_notice(self):
        a = {"id": "S1", "title": "a", "status": "done"}
        b = {"id": "S2", "title": "b", "status": "in progress"}
        c = {"id": "S2", "title": "b", "status": "canceled"}
        assert "merged" in subtasks.notice(a, [a, b], "P")
        assert "goal" not in subtasks.notice(a, [a, b], "P")
        assert "canceled" in subtasks.notice(c, [a, c], "P")
        assert "goal" in subtasks.notice(c, [a, c], "P")
