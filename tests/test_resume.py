"""A resume continues an agent's own session: the session is stopped first, no flag is given, a copy fails the send."""

import json

import pytest
from helpers import Env

from foreman import proc, util
from foreman.core.tasks import backlog, questions
from foreman.loop import tick
from foreman.services.claude import sessions
from foreman.steps import talk
from foreman.steps.health import checks


def launched(e: Env) -> str:
    tid = backlog.add(e.p, "x", status="ready", criteria="y")
    tick.tick(e.p)
    return tid


def answer(e: Env, tid: str, text: str = "B") -> None:
    questions.answer(e.p, questions.ask(e.p, tid, tid.lower(), "A or B?"), text)


def rows(e: Env) -> list[dict]:
    return json.loads((e.bin / "agents.json").read_text())


class TestResume:
    def test_resume_keeps_the_session_and_stops_it_first(self):
        e = Env()
        tid = launched(e)
        sid = e.task(tid)["agent"]["session"]
        e.set_state(tid.lower(), "done")  # idle but alive: the fake copies it unless it was stopped first
        answer(e, tid)
        tick.tick(e.p)
        assert e.task(tid)["agent"]["session"] == sid
        assert len(rows(e)) == 1
        assert questions.undelivered(backlog.load(e.p)) == []
        assert e.calls()[-1]["argv"][:3] == ["--bg", "--resume", sid]
        assert len(e.calls()[-1]["argv"]) == 4

    def test_a_copy_fails_the_send(self):
        e = Env()
        tid = launched(e)
        sid = e.task(tid)["agent"]["session"]
        (e.bin / "copy_next").write_text("")
        e.set_state(tid.lower(), "done")
        answer(e, tid)
        tick.tick(e.p)
        i = e.task(tid)
        assert (i["agent"]["session"], i["undelivered"]) == (sid, 1)
        assert any("started a copy" in ev["text"] for ev in util.read_jsonl(e.p.state / "events.jsonl"))

    def test_an_unreadable_listing_does_not_resume(self):
        e = Env()
        tid = launched(e)
        e.set_state(tid.lower(), "done")
        (e.bin / "agents.json").unlink()
        with pytest.raises(sessions.SessionError):
            sessions.resume(e.p, e.task(tid)["agent"]["session"], "hi", e.repo)
        assert len(e.calls()) == 1

    def failed_send(self, flag: str, tries: int = 15):
        e = Env()
        tid = launched(e)
        e.set_state(tid.lower(), "done")
        (e.bin / flag).write_text("")
        answer(e, tid)
        old, sessions.STOP_WAIT_TRIES = sessions.STOP_WAIT_TRIES, tries
        try:
            tick.tick(e.p)
        finally:
            sessions.STOP_WAIT_TRIES = old
        assert e.task(tid)["undelivered"] == 1
        assert not [c for c in e.calls() if "--resume" in c["argv"]]
        assert any("could not reach" in ev["text"] for ev in util.read_jsonl(e.p.state / "events.jsonl"))

    def test_a_failed_stop_fails_the_send(self):
        def boom(p, short):
            raise sessions.SessionError("claude stop: timed out after 30s")

        old, sessions.stop = sessions.stop, boom
        try:
            self.failed_send("stop_ok")
        finally:
            sessions.stop = old

    def test_stop_raises_the_sessions_error(self):
        e = Env()

        def boom(*a, **k):
            raise proc.ProcError("claude stop: timed out after 30s")

        old, proc.run = proc.run, boom
        try:
            with pytest.raises(sessions.SessionError, match="timed out"):
                sessions.stop(e.p, "abc")
        finally:
            proc.run = old

    def test_a_session_that_does_not_stop_is_not_resumed(self):
        self.failed_send("stop_noop", tries=1)

    def test_an_unreadable_listing_while_waiting_does_not_resume(self):
        self.failed_send("stop_breaks_listing")

    def test_a_failed_nudge_still_counts_so_the_block_fires(self):
        e = Env()
        tid = launched(e)
        backlog.update(e.p, tid, agent={**e.task(tid)["agent"], "started": "2000-01-01 00:00:00"})
        (e.bin / "copy_next").write_text("")
        e.set_state(tid.lower(), "done")
        tick.tick(e.p)
        assert e.task(tid)["agent"]["nudged"]
        backlog.update(e.p, tid, agent={**e.task(tid)["agent"], "nudged": "2000-01-01 00:00:00"})
        tick.tick(e.p)
        assert "even after a nudge" in e.task(tid)["blocked"]
        assert len([r for r in rows(e) if "-mechanic" not in r["name"]]) == 2  # health files the block: a mechanic follows

    def test_two_answers_go_one_per_tick(self):
        e = Env()
        tid = launched(e)
        e.set_state(tid.lower(), "done")
        answer(e, tid, "B")
        answer(e, tid, "C")
        tick.tick(e.p)
        assert len(e.calls()) == 2
        assert len(questions.undelivered(backlog.load(e.p))) == 1
        tick.tick(e.p)  # the session is working now
        assert len(e.calls()) == 2
        e.set_state(tid.lower(), "done")
        tick.tick(e.p)
        assert len(e.calls()) == 3
        assert questions.undelivered(backlog.load(e.p)) == []
        assert len(rows(e)) == 1

    def test_a_session_that_became_busy_since_the_listing_is_not_resumed(self):
        e = Env()
        tid = launched(e)
        e.set_state(tid.lower(), "done")
        answer(e, tid)
        stale = sessions.listing(e.p)
        e.set_state(tid.lower(), "working")  # the owner opened it meanwhile
        talk.deliver_answers(e.p, stale)
        assert len(e.calls()) == 1
        assert e.task(tid)["undelivered"] == 1

    def test_a_failed_send_also_ends_the_task_for_the_tick(self):
        e = Env()
        tid = launched(e)
        e.set_state(tid.lower(), "done")
        (e.bin / "fail_resume").write_text("")
        answer(e, tid, "B")
        answer(e, tid, "C")
        tick.tick(e.p)
        assert len(e.calls()) == 2
        assert e.task(tid)["undelivered"] == 1

    def test_answers_that_keep_failing_stay_undelivered_and_health_shows_them(self):
        e = Env()
        tid = launched(e)
        e.set_state(tid.lower(), "done")
        (e.bin / "fail_resume").write_text("")
        answer(e, tid)
        for _ in range(4):
            tick.tick(e.p)
        assert len(questions.undelivered(backlog.load(e.p))) == 1
        assert e.task(tid)["blocked"] == ""
        with backlog.edit(e.p) as d:
            d["decisions_resolved"][0]["answered_at"] = "2020-01-01 00:00:00"
        assert len(checks.answers_undelivered(e.p)) == 1
