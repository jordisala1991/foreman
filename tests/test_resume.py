"""A resume continues the agent's own session: the session is stopped first, no flag is given, a copy fails the send."""

import json
import unittest

from helpers import Env

from fm import backlog, loop, sessions, talk, util


def launched(e: Env) -> str:
    iid = backlog.add(e.p, "x", status="ready", criterion="y")
    loop.tick(e.p)
    return iid


def answer(e: Env, iid: str, text: str = "B") -> None:
    backlog.answer(e.p, backlog.ask(e.p, iid, iid.lower(), "A or B?"), text)


def rows(e: Env) -> list[dict]:
    return json.loads((e.bin / "agents.json").read_text())


class Resume(unittest.TestCase):
    def test_resume_keeps_the_session_and_stops_it_first(self):
        e = Env()
        iid = launched(e)
        sid = e.issue(iid)["agent"]["session"]
        e.set_state(iid.lower(), "done")  # idle but alive: the fake copies it unless it was stopped first
        answer(e, iid)
        loop.tick(e.p)
        self.assertEqual(e.issue(iid)["agent"]["session"], sid)
        self.assertEqual(len(rows(e)), 1)
        self.assertEqual(backlog.undelivered(backlog.load(e.p)), [])
        self.assertEqual(e.calls()[-1]["argv"][:3], ["--bg", "--resume", sid])
        self.assertEqual(len(e.calls()[-1]["argv"]), 4)

    def test_a_copy_fails_the_send(self):
        e = Env()
        iid = launched(e)
        sid = e.issue(iid)["agent"]["session"]
        (e.bin / "copy_next").write_text("")
        e.set_state(iid.lower(), "done")
        answer(e, iid)
        loop.tick(e.p)
        i = e.issue(iid)
        self.assertEqual((i["agent"]["session"], i["undelivered"]), (sid, 1))
        self.assertTrue(any("started a copy" in ev["text"] for ev in util.read_jsonl(e.p.state / "events.jsonl")))

    def test_an_unreadable_listing_does_not_resume(self):
        e = Env()
        iid = launched(e)
        e.set_state(iid.lower(), "done")
        (e.bin / "agents.json").unlink()
        with self.assertRaises(sessions.SessionError):
            sessions.resume(e.p, e.issue(iid)["agent"]["session"], "hi", e.repo)
        self.assertEqual(len(e.calls()), 1)

    def test_two_answers_go_one_per_tick(self):
        e = Env()
        iid = launched(e)
        e.set_state(iid.lower(), "done")
        answer(e, iid, "B")
        answer(e, iid, "C")
        loop.tick(e.p)
        self.assertEqual(len(e.calls()), 2)
        self.assertEqual(len(backlog.undelivered(backlog.load(e.p))), 1)
        loop.tick(e.p)  # the session is working now
        self.assertEqual(len(e.calls()), 2)
        e.set_state(iid.lower(), "done")
        loop.tick(e.p)
        self.assertEqual(len(e.calls()), 3)
        self.assertEqual(backlog.undelivered(backlog.load(e.p)), [])
        self.assertEqual(len(rows(e)), 1)

    def test_a_session_that_became_busy_since_the_listing_is_not_resumed(self):
        e = Env()
        iid = launched(e)
        e.set_state(iid.lower(), "done")
        answer(e, iid)
        stale = sessions.listing(e.p)
        e.set_state(iid.lower(), "working")  # the owner opened it meanwhile
        talk.deliver_answers(e.p, stale)
        self.assertEqual(len(e.calls()), 1)
        self.assertEqual(e.issue(iid)["undelivered"], 1)

    def test_a_failed_send_also_ends_the_task_for_the_tick(self):
        e = Env()
        iid = launched(e)
        e.set_state(iid.lower(), "done")
        (e.bin / "fail_resume").write_text("")
        answer(e, iid, "B")
        answer(e, iid, "C")
        loop.tick(e.p)
        self.assertEqual(len(e.calls()), 2)
        self.assertEqual(e.issue(iid)["undelivered"], 1)
