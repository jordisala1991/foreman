"""A resume continues an agent's own session: the session is stopped first, no flag is given, a copy fails the send."""

import json
from typing import Any
from unittest import mock

import pytest
from fakerepo import FakeRepo
from helpers import Env, fm

from foreman import proc, util
from foreman.core.tasks import backlog, questions
from foreman.loop.launch import launch_free
from foreman.providers import registry
from foreman.providers.claude import sessions
from foreman.providers.claude.sessions import Claude
from foreman.providers.runner import RunnerError, SessionRef
from foreman.steps import talk
from foreman.steps.health import checks


def launched(e: Env) -> str:
    tid = registry.backlog(e.p).add("x", status="ready", criteria="y")
    launch_free(e.p)
    return tid


def answer(e: Env, tid: str, text: str = "B") -> None:
    registry.backlog(e.p).answer(registry.backlog(e.p).ask(tid, tid.lower(), "A or B?"), text)


def rows(e: Env) -> list[dict[str, Any]]:
    return json.loads((e.bin / "agents.json").read_text())


class TestResume:
    @pytest.fixture(autouse=True)
    def set_up(self, fake_git: FakeRepo) -> None:
        self.fake = fake_git

    def test_resume_keeps_the_session_and_stops_it_first(self):
        e = Env(repo=self.fake)
        tid = launched(e)
        sid = e.task(tid)["agent"]["session"]
        e.set_state(tid.lower(), "done")  # idle but alive: the fake copies it unless it was stopped first
        answer(e, tid)
        talk.deliver_answers(e.p, registry.rows(e.p))
        assert e.task(tid)["agent"]["session"] == sid
        assert len(rows(e)) == 1
        assert questions.undelivered(registry.backlog(e.p).load()) == []
        assert e.calls()[-1]["argv"][:3] == ["--bg", "--resume", sid]
        assert len(e.calls()[-1]["argv"]) == 4

    def test_a_copy_fails_the_send(self):
        e = Env(repo=self.fake)
        tid = launched(e)
        sid = e.task(tid)["agent"]["session"]
        (e.bin / "copy_next").write_text("")
        e.set_state(tid.lower(), "done")
        answer(e, tid)
        talk.deliver_answers(e.p, registry.rows(e.p))
        i = e.task(tid)
        assert (i["agent"]["session"], i["undelivered"]) == (sid, 1)
        assert any("started a copy" in ev["text"] for ev in util.read_jsonl(e.p.state / "events.jsonl"))

    def test_an_unreadable_listing_does_not_resume(self):
        e = Env(repo=self.fake)
        tid = launched(e)
        e.set_state(tid.lower(), "done")
        (e.bin / "agents.json").unlink()
        with pytest.raises(RunnerError):
            sid = e.task(tid)["agent"]["session"]
            Claude(e.p).send(SessionRef(sid[:8], sid), "hi", e.repo)
        assert len(e.calls()) == 1

    def failed_send(self, flag: str, tries: int = 15):
        e = Env(repo=self.fake)
        tid = launched(e)
        e.set_state(tid.lower(), "done")
        (e.bin / flag).write_text("")
        answer(e, tid)
        old, sessions.STOP_WAIT_TRIES = sessions.STOP_WAIT_TRIES, tries
        try:
            talk.deliver_answers(e.p, registry.rows(e.p))
        finally:
            sessions.STOP_WAIT_TRIES = old
        assert e.task(tid)["undelivered"] == 1
        assert not [c for c in e.calls() if "--resume" in c["argv"]]
        assert any("could not reach" in ev["text"] for ev in util.read_jsonl(e.p.state / "events.jsonl"))

    def test_a_failed_stop_fails_the_send(self):
        def boom(self: Claude, ref: SessionRef) -> None:
            raise RunnerError("claude stop: timed out after 30s")

        with mock.patch.object(Claude, "stop", boom):
            self.failed_send("stop_ok")

    def test_stop_raises_the_sessions_error(self):
        e = Env(repo=self.fake)

        def boom(*a: object, **k: object) -> None:
            raise proc.ProcError("claude stop: timed out after 30s")

        old, proc.run = proc.run, boom
        try:
            with pytest.raises(RunnerError, match="timed out"):
                Claude(e.p).stop(SessionRef("abc", None))
        finally:
            proc.run = old

    def test_a_session_that_does_not_stop_is_not_resumed(self):
        self.failed_send("stop_noop", tries=1)

    def test_an_unreadable_listing_while_waiting_does_not_resume(self):
        self.failed_send("stop_breaks_listing")

    def test_a_failed_nudge_still_counts_so_the_block_fires(self):
        e = Env(repo=self.fake)
        tid = launched(e)
        registry.backlog(e.p).update(tid, agent={**e.task(tid)["agent"], "started": "2000-01-01 00:00:00"})
        (e.bin / "copy_next").write_text("")
        e.set_state(tid.lower(), "done")
        talk.watch_agents(e.p, registry.rows(e.p))
        assert e.task(tid)["agent"]["nudged"]
        registry.backlog(e.p).update(tid, agent={**e.task(tid)["agent"], "nudged": "2000-01-01 00:00:00"})
        talk.watch_agents(e.p, registry.rows(e.p))
        assert "even after a nudge" in e.task(tid)["blocked"]
        assert len([r for r in rows(e) if "-mechanic" not in r["name"]]) == 2  # health files the block: a mechanic follows

    def test_two_answers_go_one_per_tick(self):
        e = Env(repo=self.fake)
        tid = launched(e)
        e.set_state(tid.lower(), "done")
        answer(e, tid, "B")
        answer(e, tid, "C")
        talk.deliver_answers(e.p, registry.rows(e.p))
        assert len(e.calls()) == 2
        assert len(questions.undelivered(registry.backlog(e.p).load())) == 1
        talk.deliver_answers(e.p, registry.rows(e.p))  # the session is working now
        assert len(e.calls()) == 2
        e.set_state(tid.lower(), "done")
        talk.deliver_answers(e.p, registry.rows(e.p))
        assert len(e.calls()) == 3
        assert questions.undelivered(registry.backlog(e.p).load()) == []
        assert len(rows(e)) == 1

    def test_a_session_that_became_busy_since_the_listing_is_not_resumed(self):
        e = Env(repo=self.fake)
        tid = launched(e)
        e.set_state(tid.lower(), "done")
        answer(e, tid)
        stale = registry.rows(e.p)
        e.set_state(tid.lower(), "working")  # the owner opened it meanwhile
        talk.deliver_answers(e.p, stale)
        assert len(e.calls()) == 1
        assert e.task(tid)["undelivered"] == 1

    def test_a_failed_send_also_ends_the_task_for_the_tick(self):
        e = Env(repo=self.fake)
        tid = launched(e)
        e.set_state(tid.lower(), "done")
        (e.bin / "fail_resume").write_text("")
        answer(e, tid, "B")
        answer(e, tid, "C")
        talk.deliver_answers(e.p, registry.rows(e.p))
        assert len(e.calls()) == 2
        assert e.task(tid)["undelivered"] == 1

    def test_answers_that_keep_failing_stay_undelivered_and_health_shows_them(self):
        e = Env(repo=self.fake)
        tid = launched(e)
        e.set_state(tid.lower(), "done")
        (e.bin / "fail_resume").write_text("")
        answer(e, tid)
        for _ in range(4):
            talk.deliver_answers(e.p, registry.rows(e.p))
        assert len(questions.undelivered(registry.backlog(e.p).load())) == 1
        assert e.task(tid)["blocked"] == ""
        with registry.backlog(e.p).edit() as d:
            d["decisions_resolved"][0]["answered_at"] = "2020-01-01 00:00:00"
        assert len(checks.answers_undelivered(e.p)) == 1


class TestUnblock:
    @pytest.fixture(autouse=True)
    def set_up(self, fake_git: FakeRepo) -> None:
        self.fake = fake_git

    def blocked_idle(self, e: Env) -> str:
        tid = launched(e)
        with registry.backlog(e.p).edit() as d:
            i = backlog.get(d, tid)
            i["agent"]["started"] = i["agent"]["nudged"] = "2000-01-01 00:00:00"
            i["blocked"] = "waiting for a dependency"
        e.set_state(tid.lower(), "done")
        return tid

    def test_unblock_tells_an_idle_agent_before_any_nudge(self):
        e = Env(repo=self.fake)
        tid = self.blocked_idle(e)
        fm(e.home, "unblock", tid)
        talk.deliver_answers(e.p, registry.rows(e.p))
        talk.watch_agents(e.p, registry.rows(e.p))
        assert tid in e.calls()[-1]["argv"][-1]
        assert "--resume" in e.calls()[-1]["argv"]
        assert e.task(tid).get("nudges", 0) == 0
        assert not e.task(tid).get("blocked")

    def test_unblock_relaunch_files_no_message(self):
        e = Env(repo=self.fake)
        tid = self.blocked_idle(e)
        fm(e.home, "unblock", tid, "--relaunch")
        assert e.task(tid)["status"] == "ready"
        assert questions.undelivered(registry.backlog(e.p).load()) == []

    def test_unblock_of_a_task_without_a_session_files_no_message(self):
        e = Env(repo=self.fake)
        tid = registry.backlog(e.p).add("x", status="ready", criteria="y")
        registry.backlog(e.p).update(tid, blocked="x")
        fm(e.home, "unblock", tid)
        assert questions.undelivered(registry.backlog(e.p).load()) == []
