"""What reaches the owner is checked by `foreman plain` (docs/owner.md): `backlog decision add` refuses a question
that names ids or internal names without explaining them (`--as-is` records it anyway), the recommendation carries
its label on the decisions page and in the panel, and every other text the owner reads gets a warning on stderr
that never blocks."""

import json
import unittest

from helpers import BASE_TOML, ROOT, ProjectCase

from foreman import panel_cards
from foreman.backlog import Backlog

BAD_TITLE = "Do I go ahead with T5?"
GOOD_TITLE = "Should the warning also reach the other products' orchestrators?"
GOOD_TEXT = "Today only this product's orchestrator sees it. With yes, every orchestrator gets it."


class OwnerTextsTest(ProjectCase):
    def setUp(self):
        super().setUp()
        self.b = Backlog(self.project)
        self.b.init()
        with self.b.mutate() as d:
            self.b.add_project(d, "E1", "First", "What E1 is for.")
            self.b.add_issue(d, "E1", "AB-1", "Export to spreadsheet", status="in progress")

    def decision(self, title, *extra, text=GOOD_TEXT, options=("yes", "no")):
        args = ["backlog", "decision", "add", "--issue", "AB-1", "--title", title, "--text", text]
        for o in options:
            args += ["--option", o, "--effect", "x"]
        return self.cli(*args, *extra)

    # ---------- decisions: refused, accepted, --as-is ----------

    def test_decision_with_a_bare_id_is_refused(self):
        r = self.decision(BAD_TITLE, "--recommend", "yes, because fix_widget is cheap")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("decision not recorded", r.stderr)
        self.assertIn("title: «T5»", r.stderr)
        self.assertIn("recommendation: «fix_widget»", r.stderr)
        self.assertIn("docs/owner.md", r.stderr)
        self.assertIn("--as-is", r.stderr)
        self.assertEqual(self.b.load()["decisions"], [])
        r = self.decision(GOOD_TITLE, options=("yes", "only on step 4"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("option 2: «step 4»", r.stderr)

    def test_explained_decision_is_recorded(self):
        r = self.cli(
            *("backlog", "decision", "add", "--issue", "AB-1", "--title", GOOD_TITLE, "--text", GOOD_TEXT),
            *("--option", "yes", "--effect", "they see it too", "--option", "no", "--effect", "only you see it"),
            *("--recommend", "yes: it costs one more step and they miss it today"),
        )
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "decision AB-1 J1"), r.stderr)
        self.assertEqual(r.stderr, "")
        d = self.b.load()["decisions"][0]
        self.assertNotIn("as_is", d)
        # the id in brackets is a handle, not a bare id
        r = self.decision("Do I add the export (AB-1) to the weekly report?")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_as_is_records_it_and_says_so(self):
        r = self.decision(BAD_TITLE, "--as-is")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "decision AB-1 J1"), r.stderr)
        d = self.b.load()["decisions"][0]
        self.assertIs(d["as_is"], True)
        self.assertIn('"as_is": true', (self.home / "backlog.json").read_text())
        r = self.cli("check")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("warning: decision AB-1 J1 was recorded with --as-is", r.stdout)
        r = self.cli("backlog", "show", "AB-1 J1", "--json")
        self.assertIs(json.loads(r.stdout)["as_is"], True)
        with self.b.mutate() as data:  # resolving keeps the mark; a decision without it never gets the key
            self.b.resolve_decision(data, "AB-1 J1", "yes")
            self.b.add_decision(data, None, GOOD_TITLE, GOOD_TEXT, ["yes"], issue="AB-1")
        data = self.b.load()
        self.assertIs(data["decisions_resolved"][0]["as_is"], True)
        self.assertNotIn("as_is", data["decisions"][0])

    # ---------- the recommendation's label ----------

    def test_recommendation_is_labelled(self):
        with self.b.mutate() as data:
            self.b.add_decision(
                data, None, GOOD_TITLE, GOOD_TEXT, ["yes", "no"], "yes, they miss it today", issue="AB-1"
            )
            self.b.add_decision(data, None, "Keep the old export?", "", ["yes", "no"], issue="AB-1")
        r = self.cli("backlog", "show", "AB-1 J1")
        self.assertIn(f"{GOOD_TEXT} Recommendation: yes, they miss it today Options: yes · no", r.stdout)
        page = self.cli("backlog", "show", "AB-1 J2").stdout
        self.assertIn("**AB-1 J2, Keep the old export?** (", page)
        self.assertNotIn("Recommendation: Options", page)
        cards = panel_cards.backlog_cards(self.project)["decisions"]
        self.assertEqual(cards[0]["text"], GOOD_TEXT)  # no longer glued to the text
        self.assertEqual(cards[0]["recommendation"], "yes, they miss it today")
        self.assertEqual(cards[1]["recommendation"], "")
        app = (ROOT / "panel/app.js").read_text()
        self.assertIn("x.recommendation && [t('recommendation'), esc(x.recommendation)]", app)
        for lang, label in (("en", "Recommendation"), ("es", "Recomendación")):
            strings = json.loads((ROOT / f"panel/strings.{lang}.json").read_text())
            self.assertEqual(strings["recommendation"], label)
            i18n = json.loads((ROOT / f"lib/foreman/i18n/{lang}.json").read_text())
            self.assertEqual(i18n["recommendation_label"], label)

    def test_recommendation_label_in_spanish(self):
        self.write_toml(BASE_TOML.replace('language = "en"', 'language = "es"'))
        b = Backlog(self.project)
        with b.mutate() as data:
            b.add_decision(data, None, GOOD_TITLE, "", ["sí", "no"], "sí", issue="AB-1")
        self.assertIn("Recomendación: sí Opciones: sí · no", self.cli("backlog", "show", "AB-1 J1").stdout)

    # ---------- warnings elsewhere: never blocking ----------

    def assert_warns(self, r, label, flagged):
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"foreman: warning: the {label} reaches the owner", r.stderr)
        self.assertIn(f"«{flagged}»", r.stderr)
        self.assertIn("nothing was blocked", r.stderr)

    def warning_paths(self):
        """(label, flagged, command) of every non-blocking path."""
        return [
            ("issue title", "main_ci", ("backlog", "add", "E1", "--title", "Show main_ci in the panel")),
            ("issue title", "T3", ("backlog", "edit", "AB-1", "--title", "Export, after T3")),
            ("result line", "fix_widget", ("backlog", "edit", "AB-1", "--result", "fix_widget is in")),
            ("result line", "J2", ("backlog", "status", "AB-1", "done", "--result", "as J2 said")),
            ("progress line", "step 4", ("progress", "stuck on step 4")),
            ("inbox reply", "AB-1", ("inbox", "reply", "AB-1 is merged")),
        ]

    def test_each_owner_text_warns_and_goes_through(self):
        for label, flagged, cmd in self.warning_paths():
            with self.subTest(cmd=cmd[:3]):
                self.assert_warns(self.cli(*cmd), label, flagged)
        data = self.b.load()
        i = self.b.issue(data, "AB-1")
        self.assertEqual((i["title"], i["status"], i["result"]), ("Export, after T3", "done", "as J2 said"))
        self.assertEqual(self.b.issue(data, "AB-2")["title"], "Show main_ci in the panel")
        progress = (self.project.state / "progress.jsonl").read_text()
        self.assertIn("stuck on step 4", progress)
        self.assertIn("AB-1 is merged", (self.project.state / "inbox.json").read_text())

    def test_backlog_integrated_result_warns(self):
        with self.b.mutate() as data:
            self.b.set_sub(data, "AB-1", "T1", title="the export", status="▶")
        r = self.cli("backlog", "integrated", "AB-1", "T1", "--commit", "abc1234", "--result", "see T1")
        self.assert_warns(r, "result line", "T1")

    def test_plain_texts_and_sub_issues_do_not_warn(self):
        for cmd in (
            ("backlog", "add", "E1", "--title", "Export to spreadsheet in 4 s"),
            ("backlog", "edit", "AB-1", "--result", "a 10 000-row report takes 4 s instead of 40"),
            ("backlog", "sub", "AB-1", "T1", "--title", "fix_widget in lock.py"),  # stays technical
            ("progress", "The export works; the cache is next"),
            ("inbox", "reply", "Two questions wait for you in the panel"),
        ):
            with self.subTest(cmd=cmd[:3]):
                r = self.cli(*cmd)
                self.assertEqual((r.returncode, r.stderr), (0, ""))


if __name__ == "__main__":
    unittest.main()
