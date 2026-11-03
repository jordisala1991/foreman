"""The owner's answer with a button in the panel resolves the backlog decision at once (/api/decide, panel.decide,
Backlog.answer_decision): under the backlog's lock, with who and when (`by`), and an inbox message for the orchestrator
with did, resolved and freed; the older page's `d="ID, title"`; the 400s; the fallback to the inbox alone (202) when
the backlog cannot be saved; `decision resolve` on a decision the panel resolved; a panel answer and a CLI write at
the same time; an answer in the owner's own words (free); what the state shows after answering (answered until the
orchestrator reads it). On the active fake product of tests/test_panel.py."""

import json
import re
import threading
import time
import unittest
from datetime import date
from unittest import mock

from test_panel import PanelCase

from foreman import band, cli, loop, notes, orchestrator, panel
from foreman.backlog import Backlog

BY = re.compile(r"^Owner, \d{4}-\d{2}-\d{2} \d{2}:\d{2} \(panel\)$")


class PanelDecideTest(PanelCase):
    def setUp(self):
        super().setUp()
        self.b = Backlog(self.project)
        with self.b.mutate() as data:
            self.b.resolve_decision(data, "Area J1", "a: yes")  # the fixture's own question: out of the way
            self.b.add_issue(data, "A1", "PF-5", "Issue 5", status="ready", criterion="it works")
            self.b.add_decision(data, "PF J1", "Which way?", "Context.", ["a", "b"], blocks=["PF-5"])
            self.b.add_decision(data, "PF J2", "Paint it?", "Context.", ["yes", "no"])
        (self.project.state / "agents.json").write_text("{}")  # a free production slot: wip_production speaks

    def wip(self):
        (n,) = [n for n in loop.pipeline(self.project) if n["kind"] == "wip_production"]
        return n["text"]

    def owner_msgs(self):
        return [m for m in notes.inbox(self.project) if m["from"] == "owner"]

    def resolved(self, did):
        return next(d for d in self.b.load()["decisions_resolved"] if d["id"] == did)

    def questions(self):
        orchestrator.mark(self.project, "S1")
        ask = [p for p in band.lines(self.project)["rows"][0] if p["role"] == "ask"]
        return ask[0]["text"] if ask else ""

    def test_answer_resolves_the_decision(self):
        """Criteria 1 and 2: 200 with what it frees; resolved with the option, today and `by`; one unread owner
        message with did, resolved and freed; after `inbox read`, nothing shows it as pending any more."""
        self.assertEqual(self.questions(), "2 questions for you")
        self.assertIn("waits for decision", self.wip())
        self.assertEqual([d["id"] for d in cli.launch_holds(self.project, "PF-5", None)[0]], ["PF J1"])
        code, d = self.req("/api/decide?p=fake&id=PF%20J1&op=a", "POST")
        self.assertEqual(code, 200, d)
        self.assertEqual(d, {"resolved": "PF J1", "freed": ["freed: PF-5"]})
        r = self.resolved("PF J1")
        self.assertEqual((r["answer"], r["resolved"]), ("a", date.today().isoformat()))
        self.assertRegex(r["by"], BY)
        self.assertNotIn("PF J1", [x["id"] for x in self.b.load()["decisions"]])
        (m,) = self.owner_msgs()
        self.assertEqual(
            (m["text"], m["decision"], m["did"], m["resolved"], m["freed"], m["read"]),
            ("a", "PF J1, Which way?", "PF J1", True, ["freed: PF-5"], False),
        )
        self.assertIn("decision «PF J1, Which way?» (resolved from the panel; freed: PF-5): a", notes.inbox_line(m))
        self.assertIn("1 decision answered (already resolved): apply it", notes.inbox_counts([m]))
        notes.inbox_read(self.project)
        state = self.req("/api/state?p=fake")[1]["project"]
        self.assertEqual([x["id"] for x in state["decisions"]], ["PF J2"])
        self.assertEqual(self.questions(), "1 question for you")  # PF J2 only
        self.assertEqual(self.b.counts(self.b.load())["ready"], 3)  # PF-1, PF-2 and PF-5
        self.assertNotIn("waits for decision", self.wip())
        self.assertEqual(cli.launch_holds(self.project, "PF-5", None), ([], "PF-5"))
        # the band's 0: PF J2 answered too
        self.assertEqual(self.req("/api/decide?p=fake&id=PF%20J2&op=no", "POST")[0], 200)
        notes.inbox_read(self.project)
        self.assertEqual(self.questions(), "")

    def test_twice_a_foreign_option_or_an_unknown_id_is_a_400(self):
        """Criterion 3: nothing more in the inbox, nothing changed in the backlog."""
        self.assertEqual(self.req("/api/decide?p=fake&id=PF%20J1&op=a", "POST")[0], 200)
        before = (len(notes.inbox(self.project)), self.b.data_path.read_bytes())
        for q, said in (
            ("id=PF%20J1&op=a", "already answered: "),
            ("id=PF%20J1&op=b", "already answered: "),
            ("id=PF%20J2&op=maybe", "is not one of its options"),
            ("id=PF%20J9&op=a", "no pending decision PF J9"),
            ("id=PF%20J2", "id and op are required"),
        ):
            code, d = self.req(f"/api/decide?p=fake&{q}", "POST")
            self.assertEqual(code, 400, q)
            self.assertIn(said, d["error"], q)
        code, d = self.req("/api/decide?p=fake&id=PF%20J1&op=b", "POST")
        self.assertRegex(d["error"], r"already answered: \d{4}-\d{2}-\d{2} \(by Owner, .* \(panel\)\): «a»")
        self.assertEqual((len(notes.inbox(self.project)), self.b.data_path.read_bytes()), before)

    def test_the_older_page_sends_the_card_title(self):
        """Criterion 3: d="ID, title" (a page loaded before the change) still answers, and resolves too."""
        code, d = self.req("/api/decide?p=fake&d=PF%20J1%2C%20Which%20way%3F&op=b", "POST")
        self.assertEqual((code, d["resolved"]), (200, "PF J1"), d)
        self.assertEqual(self.resolved("PF J1")["answer"], "b")
        # an empty id (the new page on a card without one) goes by d as well
        code, d = self.req("/api/decide?p=fake&id=&d=PF%20J2%2C%20Paint%20it%3F&op=yes", "POST")
        self.assertEqual((code, d["resolved"]), (200, "PF J2"), d)
        self.assertEqual([m["did"] for m in self.owner_msgs()], ["PF J1", "PF J2"])

    def test_cli_resolve_of_a_panel_answer_changes_nothing(self):
        """Criterion 4: exit 0, «already resolved … (panel)», backlog.json untouched; an unknown id is still 2."""
        self.assertEqual(self.req("/api/decide?p=fake&id=PF%20J1&op=a", "POST")[0], 200)
        before = self.b.data_path.read_bytes()
        r = self.cli("backlog", "decision", "resolve", "PF J1", "--answer", "x")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r"PF J1 already resolved \d{4}-\d{2}-\d{2} \(by Owner, .* \(panel\)\): «a»; nothing")
        self.assertEqual(self.b.data_path.read_bytes(), before)
        self.assertEqual(self.cli("backlog", "decision", "resolve", "PF J9", "--answer", "x").returncode, 2)
        # `backlog show` finds the resolved one, with its answer and who gave it
        r = self.cli("backlog", "show", "PF J1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r"resolved \d{4}-\d{2}-\d{2} \(by Owner, .* \(panel\)\): «a»")

    def test_backlog_not_saved_falls_back_to_the_inbox(self):
        """The backlog's lock held longer than the answer waits: 202 and the inbox message alone, as before (no
        did, no resolved); the decision stays pending for the orchestrator."""
        with mock.patch.object(panel, "DECIDE_LOCK_S", 0.2), self.b.locked():
            code, d = self.req("/api/decide?p=fake&id=PF%20J1&op=a", "POST")
        self.assertEqual(code, 202, d)
        self.assertIn("recorded; the orchestrator will resolve it", d["note"])
        (m,) = self.owner_msgs()
        self.assertEqual((m["text"], m["decision"], m["read"]), ("a", "PF J1, Which way?", False))
        self.assertNotIn("resolved", m)
        self.assertIn("PF J1", [x["id"] for x in self.b.load()["decisions"]])
        # a backlog that does not validate (another issue's hard error): the same
        raw = json.loads(self.b.data_path.read_text())
        raw["projects"][0]["issues"][0]["status"] = "nonsense"
        self.b.data_path.write_text(json.dumps(raw))
        code, d = self.req("/api/decide?p=fake&id=PF%20J2&op=yes", "POST")
        self.assertEqual(code, 202, d)
        self.assertEqual([m.get("resolved") for m in self.owner_msgs()], [None, None])

    def test_a_panel_answer_and_a_cli_edit_at_once(self):
        """Criterion 5: both wait on the same lock; both changes land and backlog.json stays valid."""
        out = {}

        def answer():
            out["panel"] = panel.act("/api/decide", {"p": ["fake"], "id": ["PF J1"], "op": ["a"]}, "")

        def edit():
            out["cli"] = self.cli("backlog", "edit", "PF-2", "--detail", "Edited at the same time.")

        with self.b.locked():  # both start while the lock is taken, then race for it
            threads = [threading.Thread(target=answer), threading.Thread(target=edit)]
            for t in threads:
                t.start()
            time.sleep(1.0)  # the CLI starts and waits on the lock too
        for t in threads:
            t.join(60)
        self.assertEqual(out["panel"], {"resolved": "PF J1", "freed": ["freed: PF-5"]})
        self.assertEqual(out["cli"].returncode, 0, out["cli"].stderr)
        data = self.b.load()
        self.assertEqual(self.b.issue(data, "PF-2")["detail"], "Edited at the same time.")
        self.assertEqual(self.resolved("PF J1")["answer"], "a")
        r = self.cli("check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_answered_shows_until_the_orchestrator_reads_it(self):
        """J1: a question answered in the panel stays in the state as answered (its option, no buttons: `answer`)
        while its message is unread, after the pending ones; once read, it is gone for good. It never counts."""
        self.assertEqual(self.req("/api/decide?p=fake&id=PF%20J1&op=a", "POST")[0], 200)
        state = self.req("/api/state?p=fake")[1]["project"]
        self.assertEqual([(x["id"], x["answer"]) for x in state["decisions"]], [("PF J2", None), ("PF J1", "a")])
        self.assertEqual(state["decisions"][1]["title"], "PF J1, Which way?")
        self.assertNotIn("free", state["decisions"][1])
        self.assertEqual(self.questions(), "1 question for you")
        notes.inbox_read(self.project)
        state = self.req("/api/state?p=fake")[1]["project"]
        self.assertEqual([(x["id"], x["answer"]) for x in state["decisions"]], [("PF J2", None)])
        # resolved by hand (no panel message): never shown as answered
        with self.b.mutate() as data:
            self.b.resolve_decision(data, "PF J2", "no", by="Owner, 10:00 (message)")
        self.assertEqual(self.req("/api/state?p=fake")[1]["project"]["decisions"], [])

    def test_an_answer_only_in_the_inbox_keeps_its_buttons_and_says_so(self):
        """The 202 fallback: still pending (answer None, the buttons), with `sent`, the text that reached the
        inbox; gone once the orchestrator resolves it."""
        with mock.patch.object(panel, "DECIDE_LOCK_S", 0.2), self.b.locked():
            self.assertEqual(self.req("/api/decide?p=fake&id=PF%20J1&op=b", "POST")[0], 202)
        (j1,) = [x for x in self.req("/api/state?p=fake")[1]["project"]["decisions"] if x["id"] == "PF J1"]
        self.assertEqual((j1["answer"], j1["sent"]), (None, "b"))
        self.assertEqual(self.questions(), "2 questions for you")
        notes.inbox_read(self.project)
        (j1,) = [x for x in self.req("/api/state?p=fake")[1]["project"]["decisions"] if x["id"] == "PF J1"]
        self.assertNotIn("sent", j1)

    def test_free_answer_resolves_marked_free(self):
        """J2: the «Other answer» box: the text in the body resolves the decision like a button, with `by`, and
        `free` in the backlog, the inbox message, the answer and the state; the notice says to interpret it."""
        text = "Neither: do a only on the phone, and ask me again for the desktop."
        code, d = self.req("/api/decide?p=fake&id=PF%20J1&free=1", "POST", body=f"  {text}\n".encode())
        self.assertEqual(code, 200, d)
        self.assertEqual(d, {"resolved": "PF J1", "freed": ["freed: PF-5"], "free": True})
        r = self.resolved("PF J1")
        self.assertEqual((r["answer"], r["free"]), (text, True))
        self.assertRegex(r["by"], BY)
        (m,) = self.owner_msgs()
        self.assertEqual((m["text"], m["did"], m["resolved"], m["free"]), (text, "PF J1", True, True))
        self.assertIn("(resolved from the panel; free text: interpret it; freed: PF-5)", notes.inbox_line(m))
        self.assertIn("1 in free text: read and interpret it", notes.inbox_counts([m]))
        (j1,) = [x for x in self.req("/api/state?p=fake")[1]["project"]["decisions"] if x["id"] == "PF J1"]
        self.assertEqual((j1["answer"], j1["free"]), (text, True))
        self.assertEqual(self.cli("check").returncode, 0)
        r = self.cli("backlog", "show", "PF J1")
        self.assertRegex(r.stdout, r"\(by Owner, .* \(panel\)\) \(free text\): «Neither")
        self.assertNotIn('"free": false', self.b.data_path.read_text())  # only set when true

    def test_free_answer_empty_too_long_or_already_answered_is_a_400(self):
        """Nothing written for an empty or too long text, or a decision already answered; 2,000 characters pass."""
        self.assertEqual(self.req("/api/decide?p=fake&id=PF%20J1&op=a", "POST")[0], 200)
        before = (len(notes.inbox(self.project)), self.b.data_path.read_bytes())
        long = "x" * (panel.FREE_ANSWER_MAX + 1)
        for q, body, said in (
            ("id=PF%20J2&free=1", b"", "empty answer"),
            ("id=PF%20J2&free=1", b" \n\t ", "empty answer"),
            ("id=PF%20J2&free=1", long.encode(), "answer too long: 2001 characters, at most 2000"),
            ("id=PF%20J1&free=1", b"something else", "already answered: "),
            ("id=PF%20J9&free=1", b"something", "no pending decision PF J9"),
            ("free=1", b"something", "id and op are required"),
        ):
            code, d = self.req(f"/api/decide?p=fake&{q}", "POST", body=body)
            self.assertEqual(code, 400, (q, d))
            self.assertIn(said, d["error"], q)
        self.assertEqual((len(notes.inbox(self.project)), self.b.data_path.read_bytes()), before)
        code, d = self.req("/api/decide?p=fake&id=PF%20J2&free=1", "POST", body=long[:-1].encode())
        self.assertEqual((code, d.get("free")), (200, True), d)

    def test_free_answer_not_saved_falls_back_to_the_inbox(self):
        """Same lock and fallback as a button: 202, the message alone with `free` and no `resolved`."""
        with mock.patch.object(panel, "DECIDE_LOCK_S", 0.2), self.b.locked():
            code, d = self.req("/api/decide?p=fake&id=PF%20J1&free=1", "POST", body=b"my own words")
        self.assertEqual(code, 202, d)
        (m,) = self.owner_msgs()
        self.assertEqual((m["text"], m["decision"], m["free"]), ("my own words", "PF J1, Which way?", True))
        self.assertNotIn("resolved", m)
        self.assertIn("decision «PF J1, Which way?» (free text: interpret it): my own words", notes.inbox_line(m))
        self.assertIn("PF J1", [x["id"] for x in self.b.load()["decisions"]])


if __name__ == "__main__":
    unittest.main()
