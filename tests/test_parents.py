"""A parent task's own agent: launched on its Go, outside the slots; woken by a subtask merged or canceled, the owner's
answer or edit, one run per wakeup; never closes the parent alone; files and edits only its own subtasks; `fm show` gives it
real data per subtask."""

import json
from typing import Any
from unittest import mock

import pytest
from helpers import Env, fm
from test_misled import run_as

from foreman.core.tasks import backlog, questions, runs, subtasks
from foreman.loop import tick
from foreman.providers import registry
from foreman.providers.claude.sessions import Claude
from foreman.providers.runner import NO_ROWS
from foreman.services.agents.launch import launch_parent
from foreman.steps import parents

OLD = "2000-01-01 00:00:00"


def resumes(e: Env, sid: str) -> list[dict[str, Any]]:
    return [c for c in e.calls() if c["argv"][:3] == ["--bg", "--resume", sid]]


def of(e: Env, tid: str) -> list[runs.Run]:
    return [r for r in runs.load(e.p) if r.task == tid]


def settle(e: Env, tid: str) -> None:
    """Its session idles past the settle time; a tick ends its turn (run done)."""
    a = e.task(tid)["agent"]
    e.set_state(a["name"], "done")
    registry.backlog(e.p).update(tid, agent={**a, "started": OLD})
    tick.tick(e.p)


def parent_needs(e: Env) -> list[dict[str, Any]]:
    return [n for n in json.loads(fm(e.home, "needs", "--json")) if n["kind"] == "parent"]


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
    def set_up(self, owner: None, env: Env):
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


class TestWakeups:
    """A wakeup is a message of the parent not yet delivered: a subtask closing, the owner's answer, an owner edit. The loop
    resumes the idle session with it, or starts a new session told where it stands when the old one ended."""

    @pytest.fixture(autouse=True)
    def set_up(self, owner: None, env: Env):
        self.e = env
        self.P = registry.backlog(env.p).add("parent", status="backlog", criteria="c")
        self.S0 = fm(env.home, "add", "zero", "--parent", self.P, "--triage", "--criteria", "c").strip()
        self.S1 = fm(env.home, "add", "one", "--parent", self.P, "--triage", "--criteria", "c").strip()
        tick.tick(env.p)
        self.old = env.task(self.P)["agent"]
        settle(env, self.P)

    def pending(self) -> list[dict[str, Any]]:
        return [q for q in questions.undelivered(registry.backlog(self.e.p).load()) if q["task"] == self.P]

    def subtask_closes(self) -> str:
        registry.backlog(self.e.p).set_status(self.S0, "done", "x")
        parents.wake(self.e.p)
        return f"Subtask {self.S0} «zero» was merged"

    def owner_answers(self) -> str:
        run_as(self.e, self.P, self.old["name"], "ask", "Which first?")
        (q,) = questions.open_questions(registry.backlog(self.e.p).load(), self.P)
        fm(self.e.home, "answer", q["id"], "zero first")
        return "zero first"

    def owner_edits(self) -> str:
        fm(self.e.home, "edit", self.P, "--detail", "new detail")
        return "«new detail»"

    def test_a_subtask_closing_is_one_wakeup_named_by_its_subtask(self):
        registry.backlog(self.e.p).set_status(self.S0, "done", "x")
        for _ in range(2):
            parents.wake(self.e.p)
            assert [(q["kind"], q["subtask"]) for q in self.pending()] == [("subtask", self.S0)]
        registry.backlog(self.e.p).mark_delivered(self.pending()[0]["id"])
        parents.wake(self.e.p)
        assert self.pending() == []
        assert "notified" not in self.e.task(self.P)

    def test_the_owners_answer_and_edit_are_wakeups(self):
        self.owner_answers()
        assert len(self.pending()) == 1
        self.owner_edits()
        assert [q["kind"] for q in self.pending()][-1] == "edit"

    @pytest.mark.parametrize("event", ["subtask_closes", "owner_answers", "owner_edits"])
    def test_an_idle_session_is_resumed_with_it_as_a_new_run(self, event: str):
        text = getattr(self, event)()
        tick.tick(self.e.p)
        (call,) = resumes(self.e, self.old["session"])
        assert text in call["argv"][3]
        first, second = of(self.e, self.P)
        assert (first.kind, first.outcome) == ("parent", "done")
        assert (second.kind, second.active, second.session) == ("parent", True, self.old["session"])
        assert self.pending() == []

    @pytest.mark.parametrize("event", ["subtask_closes", "owner_answers", "owner_edits"])
    def test_an_ended_session_is_replaced_by_one_told_where_it_stands(self, event: str):
        self.e.set_state(self.old["name"], "stopped")
        text = getattr(self, event)()
        tick.tick(self.e.p)
        assert resumes(self.e, self.old["session"]) == []
        brief = self.e.calls()[-1]["argv"][-1]
        assert "Notices you missed:" in brief and text in brief
        new = self.e.task(self.P)["agent"]
        assert new["session"] != self.old["session"]
        first, second = of(self.e, self.P)
        assert (first.outcome, second.attempt, second.active, second.session) == ("done", 2, True, new["session"])
        assert self.pending() == []
        tick.tick(self.e.p)
        assert len(self.e.calls()) == 2

    def test_an_ended_session_with_nothing_pending_is_not_replaced(self):
        self.e.set_state(self.old["name"], "stopped")
        for _ in range(2):
            tick.tick(self.e.p)
        assert len(self.e.calls()) == 1 and parent_needs(self.e) == []

    def test_an_idle_session_is_not_replaced(self):
        parents.run(self.e.p, registry.rows(self.e.p))
        assert len(self.e.calls()) == 1

    def test_an_unreadable_listing_launches_nothing(self):
        self.e.set_state(self.old["name"], "stopped")
        self.owner_edits()
        parents.run(self.e.p, NO_ROWS)
        assert len(self.e.calls()) == 1

    def test_an_agent_with_no_session_yet_launches_nothing(self):
        registry.backlog(self.e.p).update(self.P, agent={**self.old, "session": None})
        self.e.set_state(self.old["name"], "stopped")
        self.owner_edits()
        parents.run(self.e.p, registry.rows(self.e.p))
        assert len(self.e.calls()) == 1

    def test_a_permission_answer_it_missed_keeps_its_wording_in_the_brief(self):
        qid = registry.backlog(self.e.p).ask(self.P, self.old["name"], "may I push?", kind="permission", action="push")
        registry.backlog(self.e.p).answer(qid, "go on")
        launch_parent(self.e.p, self.e.task(self.P))
        assert f"The owner answered your request {qid} (push)" in self.e.calls()[-1]["argv"][-1]
        assert self.pending() == []

    def test_a_subtask_closed_while_the_session_starts_is_still_a_wakeup(self):
        start = Claude.start

        def closing(runner: Claude, *args: Any) -> Any:
            registry.backlog(self.e.p).set_status(self.S1, "done", "x")
            return start(runner, *args)

        self.e.set_state(self.old["name"], "stopped")
        self.subtask_closes()
        with mock.patch.object(Claude, "start", closing):
            parents.run(self.e.p, registry.rows(self.e.p))
        assert self.pending() == []
        parents.wake(self.e.p)
        assert [q["subtask"] for q in self.pending()] == [self.S1]

    def test_ds14_a_subtask_closed_while_a_notice_is_in_flight_raises_no_alarm(self):
        """DS-14: S1 closes and its notice resumes the parent; S2 closes before it idles. Both reach it, no run fails."""
        registry.backlog(self.e.p).set_status(self.S0, "done", "x")
        tick.tick(self.e.p)  # the wakeup is recorded
        tick.tick(self.e.p)  # and resumes the idle session: it works
        assert parent_needs(self.e) == []
        registry.backlog(self.e.p).set_status(self.S1, "done", "x")
        tick.tick(self.e.p)  # busy: the second wakeup waits
        assert parent_needs(self.e) == []
        self.e.set_state(self.old["name"], "done")
        tick.tick(self.e.p)  # idle: resumed with the second notice
        assert parent_needs(self.e) == []
        settle(self.e, self.P)
        assert parent_needs(self.e) == []
        assert len(resumes(self.e, self.old["session"])) == 2
        assert [r.outcome for r in of(self.e, self.P)] == ["done", "done", "done"]
        assert not self.e.task(self.P).get("blocked")


class TestClosing:
    @pytest.fixture(autouse=True)
    def set_up(self, owner: None, env: Env):
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
        assert [r.outcome for r in runs.load(self.e.p) if r.task == self.P] == ["done"]
        tick.tick(self.e.p)
        assert self.e.task(self.P)["agent"]["short"] in self.e.stops()

    def test_canceled_when_every_subtask_is(self):
        registry.backlog(self.e.p).set_status(self.S1, "canceled")
        registry.backlog(self.e.p).set_status(self.S2, "canceled")
        self.done()
        assert self.e.task(self.P)["status"] == "canceled"

    def closed(self) -> None:
        registry.backlog(self.e.p).set_status(self.S1, "done")
        registry.backlog(self.e.p).set_status(self.S2, "done")

    def proposes_done(self) -> str:
        sid = self.e.task(self.P)["agent"]["session"]
        self.e.set_state("ty-1", "done")
        run_as(self.e, self.P, "ty-1", "ask", "Proposal: foreman done --summary s")
        (q,) = questions.open_questions(registry.backlog(self.e.p).load(), self.P)
        self.sid = sid
        return q["id"]

    def delivered(self) -> str:
        for _ in range(3):
            tick.tick(self.e.p)
            self.e.set_state("ty-1", "done")
        (text,) = [c["argv"][-1] for c in resumes(self.e, self.sid) if "The owner" in c["argv"][-1]]
        return text

    def test_a_proposal_waits_as_an_open_question_in_needs(self):
        self.closed()
        qid = self.proposes_done()
        (n,) = [n for n in json.loads(fm(self.e.home, "needs", "--json")) if n["task"] == self.P]
        assert (n["kind"], n["key"], n["text"]) == ("question", f"q:{qid}", "Proposal: foreman done --summary s")

    def test_an_answered_yes_runs_done(self):
        self.closed()
        fm(self.e.home, "answer", self.proposes_done(), "yes")
        assert "Proposal: foreman done --summary s" in self.delivered()
        self.done()
        assert self.e.task(self.P)["status"] == "done"

    def test_any_other_answer_runs_nothing(self):
        self.closed()
        fm(self.e.home, "answer", self.proposes_done(), "wait, not yet")
        text = self.delivered()
        assert "wait, not yet" in text
        assert "Proposal: foreman done" not in text
        assert self.e.task(self.P)["status"] == "backlog"


class TestAuthority:
    @pytest.fixture(autouse=True)
    def set_up(self, owner: None, env: Env):
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
    def test_show_gives_each_subtasks_state(self, owner: None):
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

    def test_is_yes(self):
        for a in ("yes", "Yes.", " YES! "):
            assert questions.is_yes(a)
        for a in ("yes, but change the summary", "no", ""):
            assert not questions.is_yes(a)

    def test_message_for_a_parent(self):
        i = {"id": "P", "agent": {"kind": "parent"}}
        q = {"id": "P Q1", "text": "Proposal: foreman done", "answer": "yes"}
        assert "Proposal: foreman done" in questions.message_for(i, q)
        text = questions.message_for(i, {**q, "answer": "not yet"})
        assert "not yet" in text
        assert "Proposal: foreman done" not in text
