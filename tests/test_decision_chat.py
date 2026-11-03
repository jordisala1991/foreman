"""Going through the owner's pending decisions in the chat: `foreman backlog decision list [--json]` in
review order (those that hold open work first, each group oldest first) with what each holds, its issue and plans;
`decision resolve --option N|TEXT` (validated as the panel does) and `--answer … --free`, with `by` «Owner, … (chat)»
by default; the warning of a bare `--answer` that is not an option; and that the panel, the band and the counts agree
with a decision closed in the chat. On the active fake product of tests/test_panel.py."""

import json
import re
import unittest
from datetime import date, timedelta

from test_panel import PanelCase

from foreman import band, notes, orchestrator, panel
from foreman.backlog import Backlog

BY = re.compile(r"^Owner, \d{4}-\d{2}-\d{2} \d{2}:\d{2} \(chat\)$")
WARN = "not one of its options: use --option N, or --free for the owner's own words"


def ago(days):
    return (date.today() - timedelta(days=days)).isoformat()


class DecisionChatTest(PanelCase):
    def setUp(self):
        super().setUp()
        self.b = Backlog(self.project)
        with self.b.mutate() as data:
            self.b.resolve_decision(data, "Area J1", "a: yes")  # the fixture's own question: out of the way
            self.b.add_issue(data, "A1", "PF-5", "Issue 5: the parser", status="ready", criterion="it works")
            self.b.set_sub(data, "PF-5", "T1", title="part one")
            self.b.edit(data, "PF-5", plan=["reviews/2026-10-30-plan.md"])
            self.b.add_issue(data, "A1", "PF-6", "Issue 6", status="ready", criterion="it works")
            self.b.add_issue(data, "A1", "PF-7", "Issue 7", status="ready", criterion="it works")
            self.b.add_decision(
                data,
                "PF-5 J1",
                "Which way?",
                "Context one.",
                ["left", "right"],
                "Right, it is shorter.",
                when=ago(5),
                recommended="right",
                blocks=["PF-5 T1"],
            )
            self.b.add_decision(data, "PF J2", "Paint it?", "Context two.", ["yes", "no"], when=ago(10))
            self.b.add_decision(
                data, "PF J3", "How big?", "Context three.", ["small", "big"], when=ago(1), blocks=["PF-6", "PF-7"]
            )
            self.b.set_status(
                data, "PF-7", "canceled", result="dropped", verdict="discarded"
            )  # what J3 held there is closed: not listed in holds
        (self.project.state / "agents.json").write_text("{}")

    def ok(self, *args):
        r = self.cli("backlog", "decision", *args)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def resolved(self, did):
        return next(d for d in self.b.load()["decisions_resolved"] if d["id"] == did)

    def questions(self):
        orchestrator.mark(self.project, "S1")
        ask = [p for p in band.lines(self.project)["rows"][0] if p["role"] == "ask"]
        return ask[0]["text"] if ask else ""

    def test_list_in_review_order(self):
        """Criterion 1: the ones that hold work first, oldest first (J1, J3), then the rest (J2), with every field."""
        items = json.loads(self.ok("list", "--json").stdout)
        self.assertEqual([d["id"] for d in items], ["PF-5 J1", "PF J3", "PF J2"])
        j1, j3, j2 = items
        self.assertEqual(
            set(j1),
            {"id", "title", "date", "age_days", "text", "options", "recommendation", "recommended", "holds"}
            | {"issue", "plans"},
        )
        self.assertEqual((j1["age_days"], j3["age_days"], j2["age_days"]), (5, 1, 10))
        self.assertEqual((j1["holds"], j3["holds"], j2["holds"]), (["PF-5 T1"], ["PF-6"], []))
        self.assertEqual(
            j1["issue"], {"id": "PF-5", "title": "Issue 5: the parser", "short": "Issue 5", "status": "ready"}
        )
        self.assertEqual(
            (j1["plans"], j1["recommended"], j1["options"]),
            (["reviews/2026-10-30-plan.md"], "right", ["left", "right"]),
        )
        self.assertIsNone(j2["issue"])  # «PF» is no issue
        self.assertEqual(j2["plans"], [])
        # text: one block each, the options numbered, the recommended one marked
        out = self.ok("list").stdout
        self.assertTrue(out.startswith("3 pending decisions (Owner), in review order:"), out)
        self.assertLess(out.index("PF-5 J1 · Which way?"), out.index("PF J3"))
        self.assertLess(out.index("PF J3"), out.index("PF J2"))
        self.assertIn(f"PF-5 J1 · Which way?  ({ago(5)}, 5 days ago)\n  holds: PF-5 T1\n  issue: PF-5 · Issue 5", out)
        self.assertIn("  1. left\n  2. right  (recommended)\n  recommendation: Right, it is shorter.", out)
        # the order follows what is still open: once PF-6 closes, J3 holds nothing and goes after the older J2
        with self.b.mutate() as data:
            self.b.set_status(data, "PF-6", "canceled", result="dropped", verdict="discarded")
        self.assertEqual([d["id"] for d in json.loads(self.ok("list", "--json").stdout)], ["PF-5 J1", "PF J2", "PF J3"])
        # none: one line, exit 0; --json: []
        with self.b.mutate() as data:
            for did in ("PF-5 J1", "PF J2", "PF J3"):
                self.b.resolve_decision(data, did, "x")
        self.assertEqual(self.ok("list").stdout.strip(), "no pending decisions")
        self.assertEqual(json.loads(self.ok("list", "--json").stdout), [])

    def test_resolve_by_option_number(self):
        """Criteria 2 and 5: the exact option, `by` … (chat), freed; the panel, the band and the counts agree; again
        is «already resolved» with exit 0; the panel's button afterwards is a 400."""
        self.assertEqual(self.questions(), "3 questions for you")
        ready = self.b.counts(self.b.load())["ready"]
        r = self.ok("resolve", "PF-5 J1", "--option", "2")
        self.assertEqual(r.stdout.strip(), "freed: PF-5 T1")
        self.assertEqual(r.stderr, "")
        d = self.resolved("PF-5 J1")
        self.assertEqual((d["answer"], d["resolved"]), ("right", date.today().isoformat()))
        self.assertRegex(d["by"], BY)
        self.assertNotIn("free", d)
        state = self.req("/api/state?p=fake")[1]["project"]
        self.assertNotIn("PF-5 J1", [x["id"] for x in state["decisions"]])
        self.assertNotIn(
            "PF-5 J1", [x["id"] for x in panel.decisions_with_answers(self.b.load(), notes.inbox(self.project))]
        )
        self.assertEqual(
            [m for m in notes.inbox(self.project) if m["from"] == "owner"], []
        )  # no inbox message for what the chat closed (O5)
        self.assertEqual(self.questions(), "2 questions for you")
        self.assertEqual(self.b.counts(self.b.load())["ready"], ready + 1)  # PF-5 is launchable now
        before = self.b.data_path.read_bytes()
        r = self.ok("resolve", "PF-5 J1", "--option", "2")
        self.assertRegex(r.stdout, r"PF-5 J1 already resolved \d{4}-\d{2}-\d{2} \(by Owner, .* \(chat\)\): «right»")
        self.assertEqual(self.b.data_path.read_bytes(), before)
        code, e = self.req("/api/decide?p=fake&id=PF-5%20J1&op=left", "POST")
        self.assertEqual(code, 400)
        self.assertIn("already answered: ", e["error"])
        self.assertEqual(self.b.data_path.read_bytes(), before)

    def test_resolve_by_option_text(self):
        """Criterion 2: the exact text does the same; --by given is kept."""
        self.assertEqual(
            self.ok("resolve", "PF J3", "--option", "big", "--by", "Owner, 10:00 (message)").stdout.strip(),
            "freed: PF-6",
        )
        d = self.resolved("PF J3")
        self.assertEqual((d["answer"], d["by"]), ("big", "Owner, 10:00 (message)"))

    def test_bad_option_is_exit_2_and_changes_nothing(self):
        """Criterion 3."""
        before = self.b.data_path.read_bytes()
        for args, said in (
            (["--option", "7"], "'7' is not one of its options: --option 1 to 2"),
            (["--option", "0"], "is not one of its options"),
            (["--option", "no es una opción"], "is not one of its options"),
            (["--option", "1", "--answer", "x"], "--option goes alone"),
            (["--option", "1", "--option", "2"], "takes one --option"),
            (["--free"], "--free needs --answer"),
            (["--option", "1", "--free"], "--option goes alone"),
        ):
            r = self.cli("backlog", "decision", "resolve", "PF-5 J1", *args)
            self.assertEqual(r.returncode, 2, (args, r.stdout, r.stderr))
            self.assertIn(said, r.stderr, args)
        r = self.cli("backlog", "decision", "resolve", "PF J9", "--option", "1")
        self.assertEqual(r.returncode, 2)
        self.assertIn("no pending decision PF J9", r.stderr)
        for args in (
            ["add", "--title", "x", "--option", "a", "--free"],
            ["list", "--free"],
            ["resolve", "PF J2", "--json"],
        ):
            self.assertEqual(self.cli("backlog", "decision", *args).returncode, 2, args)
        self.assertEqual(self.b.data_path.read_bytes(), before)

    def test_free_answer(self):
        """Criterion 4: the owner's words, `free`, `by` … (chat); then the panel's box is a 400 too."""
        r = self.ok("resolve", "PF J2", "--answer", "  mis palabras ", "--free")
        self.assertEqual((r.stdout, r.stderr), ("", ""))
        d = self.resolved("PF J2")
        self.assertEqual((d["answer"], d["free"]), ("mis palabras", True))
        self.assertRegex(d["by"], BY)
        code, _ = self.req("/api/decide?p=fake&id=PF%20J2&free=1", "POST", body=b"other words")
        self.assertEqual(code, 400)
        r = self.ok("resolve", "PF J3", "--answer", "mine", "--free", "--by", "Owner, 11:00 (message)")
        self.assertEqual(self.resolved("PF J3")["by"], "Owner, 11:00 (message)")

    def test_bare_answer_as_before_with_a_warning(self):
        """Criterion 6: --answer alone resolves as given, no `by` by default; a warning when it is not an option."""
        r = self.ok("resolve", "PF J2", "--answer", "yes")
        self.assertEqual(r.stderr, "")
        self.assertNotIn("by", self.resolved("PF J2"))
        r = self.ok("resolve", "PF J3", "--answer", "medium, please", "--by", "Owner, 12:00 (message)")
        self.assertIn(f"PF J3: the answer is {WARN}", r.stderr)
        d = self.resolved("PF J3")
        self.assertEqual((d["answer"], d["by"]), ("medium, please", "Owner, 12:00 (message)"))
        self.assertNotIn("free", d)


if __name__ == "__main__":
    unittest.main()
