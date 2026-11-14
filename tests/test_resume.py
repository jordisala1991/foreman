"""A resume continues an agent's own session: the session is stopped first, no flag is given, a copy fails the send."""

import json
import unittest

from helpers import Env

from fm import backlog, health, loop, proc, sessions, talk, util


def launched(e: Env) -> str:
    tid = backlog.add(e.p, "x", status="ready", criteria="y")
    loop.tick(e.p)
    return tid


def answer(e: Env, tid: str, text: str = "B") -> None:
    backlog.answer(e.p, backlog.ask(e.p, tid, tid.lower(), "A or B?"), text)


def rows(e: Env) -> list[dict]:
    return json.loads((e.bin / "agents.json").read_text())


class Resume(unittest.TestCase):
    def test_resume_keeps_the_session_and_stops_it_first(self):
        e = Env()
        tid = launched(e)
        sid = e.task(tid)["agent"]["session"]
        e.set_state(tid.lower(), "done")  # idle but alive: the fake copies it unless it was stopped first
        answer(e, tid)
        loop.tick(e.p)
        self.assertEqual(e.task(tid)["agent"]["session"], sid)
        self.assertEqual(len(rows(e)), 1)
        self.assertEqual(backlog.undelivered(backlog.load(e.p)), [])
        self.assertEqual(e.calls()[-1]["argv"][:3], ["--bg", "--resume", sid])
        self.assertEqual(len(e.calls()[-1]["argv"]), 4)

    def test_a_copy_fails_the_send(self):
        e = Env()
        tid = launched(e)
        sid = e.task(tid)["agent"]["session"]
        (e.bin / "copy_next").write_text("")
        e.set_state(tid.lower(), "done")
        answer(e, tid)
        loop.tick(e.p)
        i = e.task(tid)
        self.assertEqual((i["agent"]["session"], i["undelivered"]), (sid, 1))
        self.assertTrue(any("started a copy" in ev["text"] for ev in util.read_jsonl(e.p.state / "events.jsonl")))

    def test_an_unreadable_listing_does_not_resume(self):
        e = Env()
        tid = launched(e)
        e.set_state(tid.lower(), "done")
        (e.bin / "agents.json").unlink()
        with self.assertRaises(sessions.SessionError):
            sessions.resume(e.p, e.task(tid)["agent"]["session"], "hi", e.repo)
        self.assertEqual(len(e.calls()), 1)

    def failed_send(self, flag: str, tries: int = 15):
        e = Env()
        tid = launched(e)
        e.set_state(tid.lower(), "done")
        (e.bin / flag).write_text("")
        answer(e, tid)
        old, sessions.STOP_WAIT_TRIES = sessions.STOP_WAIT_TRIES, tries
        try:
            loop.tick(e.p)
        finally:
            sessions.STOP_WAIT_TRIES = old
        self.assertEqual(e.task(tid)["undelivered"], 1)
        self.assertFalse([c for c in e.calls() if "--resume" in c["argv"]])
        self.assertTrue(any("could not reach" in ev["text"] for ev in util.read_jsonl(e.p.state / "events.jsonl")))

    def test_a_failed_stop_fails_the_send(self):
        def boom(p, short):
            raise proc.ProcError("claude stop: timed out after 30s")

        old, sessions.stop = sessions.stop, boom
        try:
            self.failed_send("stop_ok")
        finally:
            sessions.stop = old

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
        loop.tick(e.p)
        self.assertTrue(e.task(tid)["agent"]["nudged"])
        backlog.update(e.p, tid, agent={**e.task(tid)["agent"], "nudged": "2000-01-01 00:00:00"})
        loop.tick(e.p)
        self.assertIn("even after a nudge", e.task(tid)["blocked"])
        self.assertEqual(len([r for r in rows(e) if "-mechanic" not in r["name"]]), 2)  # health files the block: a mechanic follows

    def test_two_answers_go_one_per_tick(self):
        e = Env()
        tid = launched(e)
        e.set_state(tid.lower(), "done")
        answer(e, tid, "B")
        answer(e, tid, "C")
        loop.tick(e.p)
        self.assertEqual(len(e.calls()), 2)
        self.assertEqual(len(backlog.undelivered(backlog.load(e.p))), 1)
        loop.tick(e.p)  # the session is working now
        self.assertEqual(len(e.calls()), 2)
        e.set_state(tid.lower(), "done")
        loop.tick(e.p)
        self.assertEqual(len(e.calls()), 3)
        self.assertEqual(backlog.undelivered(backlog.load(e.p)), [])
        self.assertEqual(len(rows(e)), 1)

    def test_a_session_that_became_busy_since_the_listing_is_not_resumed(self):
        e = Env()
        tid = launched(e)
        e.set_state(tid.lower(), "done")
        answer(e, tid)
        stale = sessions.listing(e.p)
        e.set_state(tid.lower(), "working")  # the owner opened it meanwhile
        talk.deliver_answers(e.p, stale)
        self.assertEqual(len(e.calls()), 1)
        self.assertEqual(e.task(tid)["undelivered"], 1)

    def test_a_failed_send_also_ends_the_task_for_the_tick(self):
        e = Env()
        tid = launched(e)
        e.set_state(tid.lower(), "done")
        (e.bin / "fail_resume").write_text("")
        answer(e, tid, "B")
        answer(e, tid, "C")
        loop.tick(e.p)
        self.assertEqual(len(e.calls()), 2)
        self.assertEqual(e.task(tid)["undelivered"], 1)

    def test_answers_that_keep_failing_stay_undelivered_and_health_shows_them(self):
        e = Env()
        tid = launched(e)
        e.set_state(tid.lower(), "done")
        (e.bin / "fail_resume").write_text("")
        answer(e, tid)
        for _ in range(4):
            loop.tick(e.p)
        self.assertEqual(len(backlog.undelivered(backlog.load(e.p))), 1)
        self.assertEqual(e.task(tid)["blocked"], "")
        with backlog.edit(e.p) as d:
            d["decisions_resolved"][0]["answered_at"] = "2020-01-01 00:00:00"
        self.assertEqual(len(health.answers_undelivered(e.p)), 1)
