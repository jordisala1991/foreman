"""A resume continues the agent's own session; when claude starts a copy anyway, fm records the copy."""

import json
import unittest

from helpers import Env

from fm import backlog, loop


def launched(e: Env) -> str:
    iid = backlog.add(e.p, "x", status="ready", criterion="y")
    loop.tick(e.p)
    return iid


def answer(e: Env, iid: str, text: str = "B") -> None:
    backlog.answer(e.p, backlog.ask(e.p, iid, iid.lower(), "A or B?"), text)


def rows(e: Env, fn: str = "agents.json") -> list[dict]:
    return json.loads((e.bin / fn).read_text())


class Resume(unittest.TestCase):
    def test_resume_keeps_the_session(self):
        e = Env()
        iid = launched(e)
        sid = e.issue(iid)["agent"]["session"]
        e.set_state(iid.lower(), "done")
        answer(e, iid)
        loop.tick(e.p)
        self.assertEqual(e.issue(iid)["agent"]["session"], sid)
        self.assertEqual(len(rows(e)), 1)
        self.assertEqual(backlog.undelivered(backlog.load(e.p)), [])
        self.assertEqual(e.calls()[-1]["argv"][:3], ["--bg", "--resume", sid])
        self.assertEqual(len(e.calls()[-1]["argv"]), 4)

    def test_next_send_back_reaches_the_copy(self):
        e = Env()
        iid = launched(e)
        (e.bin / "copy_next").write_text("")
        e.set_state(iid.lower(), "done")
        answer(e, iid)
        loop.tick(e.p)
        new = rows(e)[1]
        a = e.issue(iid)["agent"]
        self.assertEqual((a["session"], a["short"], a["nudged"]), (new["sessionId"], new["id"], None))
        self.assertEqual(e.issue(iid)["session_kinds"][new["sessionId"]], "work")
        (e.bin / "copy_next").unlink()
        (e.bin / "agents.json").write_text(json.dumps([{**r, "state": "done"} for r in rows(e)]))
        loop.send_back(e.p, iid, "review", "X")
        loop.tick(e.p)
        argv = e.calls()[-1]["argv"]
        self.assertEqual(argv[argv.index("--resume") + 1], new["sessionId"])

    def test_copy_not_listed_yet_is_found_later(self):
        e = Env()
        iid = launched(e)
        (e.bin / "copy_next").write_text("")
        (e.bin / "copy_unlisted").write_text("")
        e.set_state(iid.lower(), "done")
        answer(e, iid)
        loop.tick(e.p)
        new = rows(e, "pending.json")[0]
        a = e.issue(iid)["agent"]
        self.assertEqual((a["session"], a["short"]), (None, new["id"]))
        (e.bin / "agents.json").write_text(json.dumps([*rows(e), new]))
        loop.tick(e.p)
        self.assertEqual(e.issue(iid)["agent"]["session"], new["sessionId"])

    def test_nudge_into_a_copy_is_followed(self):
        e = Env()
        iid = launched(e)
        backlog.update(e.p, iid, agent={**e.issue(iid)["agent"], "started": "2000-01-01 00:00:00"})
        (e.bin / "copy_next").write_text("")
        e.set_state(iid.lower(), "done")
        loop.tick(e.p)
        new = rows(e)[1]
        i = e.issue(iid)
        self.assertEqual(i["agent"]["session"], new["sessionId"])
        self.assertTrue(i["agent"]["nudged"])
        self.assertEqual(i["nudges"], 1)

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
        self.assertEqual(e.calls()[-1]["argv"][1:3], ["--resume", e.issue(iid)["agent"]["session"]])
