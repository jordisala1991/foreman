"""The optional fields of a question for the owner (docs/owner.md): each option's effect, cost and whether it can be
undone (`--effect`, `--cost`, `--undo` after their `--option`), what stays stopped while nobody answers and the day
to remind them (`--if-no-answer`, `--answer-by`; foreman never answers for the owner) and a page that explains it
(`--page`). `decision add` stores them only when given (a question of before is written and shown as before), checks
them with `foreman plain`, warns without blocking (an option without effect, two questions in one, a past date, a
missing or too large page, a page not written for the question); `show`, `decision list [--json]`, the decisions
page and `foreman check` know them."""

import json
import unittest
from datetime import date, timedelta

from helpers import BASE_TOML, ProjectCase

from foreman import backlog_check, backlog_text
from foreman.backlog import PAGE_MAX, Backlog

TITLE = "Should the weekly report include the export?"
TEXT = "The export runs every night. The report goes out on Mondays."
PAGE = "reviews/2026-11-05-AB-1-question/question.md"

# Texts of two real questions, copied as they were recorded: the first asks two questions in its text; the second
# quotes a question in its title, which is not asked.
TWO_QUESTIONS = {
    "title": "AP-12: cuándo limpiar la búsqueda y la caché al cambiar de cuenta",
    "text": "Al cambiar de cuenta, la cuenta nueva ve un instante las conversaciones y los no leídos de la anterior "
    "(caché de React Query en MMKV), y también hereda la búsqueda y la ubicación. ¿Cuándo se limpia? ¿Viste el fallo "
    "tras cerrar y reabrir la app?",
}
QUOTED_QUESTION = {
    "title": "Cuando me preguntas «¿qué ha pasado?», ¿desde cuándo quieres que cuente?",
    "text": "Te resumiré siempre en cuatro partes: lo terminado, lo que está en marcha, lo que te espera y lo que fue "
    "mal. Siempre puedes pedir otro tramo con tus palabras («desde el viernes»).",
}


def later(days):
    return (date.today() + timedelta(days=days)).isoformat()


class DecisionFieldsTest(ProjectCase):
    def setUp(self):
        super().setUp()
        self.b = Backlog(self.project)
        self.b.init()
        with self.b.mutate() as d:
            self.b.add_project(d, "E1", "First", "What E1 is for.")
            self.b.add_issue(d, "E1", "AB-1", "Export to spreadsheet", status="in progress")
        page = self.home / PAGE
        page.parent.mkdir(parents=True)
        page.write_text("# The export\n\n![before](before-390.png)\n")

    def add(self, *extra, title=TITLE, text=TEXT):
        return self.cli("backlog", "decision", "add", "--issue", "AB-1", "--title", title, "--text", text, *extra)

    def rich(self, *extra):
        return self.add(
            *("--option", "yes", "--effect", "the report gets a page with the export", "--cost", "one more minute"),
            *("--undo", "yes"),
            *("--option", "no", "--effect", "the report stays as it is", "--undo", "hard"),
            *("--recommend", "yes: it is what they ask for", "--recommend-option", "yes"),
            *("--if-no-answer", "the export waits; the rest goes on", "--answer-by", later(7), "--page", PAGE),
            *extra,
        )

    def warnings(self, r):
        return [x for x in r.stderr.splitlines() if "warning" in x]

    # ---------- stored, shown ----------

    def test_fields_are_stored_and_shown(self):
        r = self.rich()
        self.assertEqual((r.returncode, r.stdout.strip(), r.stderr), (0, "decision AB-1 J1", ""))
        d = self.b.load()["decisions"][0]
        self.assertEqual(
            d["details"],
            {
                "yes": {"effect": "the report gets a page with the export", "cost": "one more minute", "undo": "yes"},
                "no": {"effect": "the report stays as it is", "undo": "hard"},
            },
        )
        self.assertEqual(d["unanswered"], {"waits": "the export waits; the rest goes on", "by": later(7)})
        self.assertEqual(d["page"], PAGE)
        self.assertEqual(self.cli("check").returncode, 0)
        # show: after the options; the page's path relative to the home, as it is stored
        show = self.cli("backlog", "show", "AB-1 J1").stdout
        self.assertIn(
            "Options: yes · no What each option changes: yes: the report gets a page with the export (one more "
            "minute; can be undone) · no: the report stays as it is (can be undone, with work) Until there is an "
            f"answer (reminder on {later(7)}): the export waits; the rest goes on Page: {PAGE}",
            show,
        )
        self.assertNotIn("../", show)
        lst = self.cli("backlog", "decision", "list").stdout
        self.assertIn("  1. yes  (recommended)\n     the report gets a page with the export (one more minute;", lst)
        self.assertIn("  2. no\n     the report stays as it is (can be undone, with work)", lst)
        self.assertIn(f"  if no answer: the export waits; the rest goes on (reminder on {later(7)})", lst)
        self.assertIn(f"  page: {self.home / PAGE}", lst)
        js = json.loads(self.cli("backlog", "decision", "list", "--json").stdout)[0]
        self.assertEqual(js["details"], d["details"])
        self.assertEqual(js["unanswered"], d["unanswered"])
        self.assertEqual((js["page"], js["page_path"]), (PAGE, str(self.home / PAGE)))
        # resolving keeps them as history; check stays quiet about a resolved one's page
        self.assertEqual(self.cli("backlog", "decision", "resolve", "AB-1 J1", "--option", "1").returncode, 0)
        (self.home / PAGE).unlink()
        done = self.b.load()["decisions_resolved"][0]
        self.assertEqual((done["page"], done["details"]["no"]["undo"]), (PAGE, "hard"))
        self.assertNotIn("question.md is missing", self.cli("check").stdout)

    def test_spanish_labels(self):
        self.write_toml(BASE_TOML.replace('language = "en"', 'language = "es"'))
        self.assertEqual(self.rich().returncode, 0)
        page = self.cli("backlog", "show", "AB-1 J1").stdout
        self.assertIn("Qué cambia cada opción: yes: ", page)
        self.assertIn(
            "(one more minute; se puede deshacer) · no: the report stays as it is (se puede deshacer, con", page
        )
        self.assertIn(f"Mientras no contestes (recordatorio el {later(7)}): the export waits", page)
        self.assertIn(f"Página: {PAGE}", page)
        b = Backlog(self.project)
        self.assertEqual(b.s["undo_no"], "sin vuelta atrás")

    def test_a_decision_of_before_is_written_and_shown_as_before(self):
        r = self.add("--option", "yes", "--option", "no", "--recommend", "yes, it is cheap", "--date", "2026-11-01")
        self.assertEqual(r.returncode, 0, r.stderr)
        d = self.b.load()["decisions"][0]
        self.assertEqual(list(d), ["id", "title", "date", "text", "options", "recommendation"])
        line = backlog_text.render_decision(self.b, d)
        self.assertEqual(
            line,
            f"- **AB-1 J1, {TITLE}** (2026-11-01): {TEXT} Recommendation: yes, it is cheap Options: yes · no",
        )
        js = json.loads(self.cli("backlog", "decision", "list", "--json").stdout)[0]
        self.assertEqual(
            set(js),
            {
                "id",
                "title",
                "date",
                "age_days",
                "text",
                "options",
                "recommendation",
                "recommended",
                "holds",
                "issue",
                "plans",
            },
        )
        lst = self.cli("backlog", "decision", "list").stdout
        self.assertNotIn("if no answer", lst)
        self.assertIn("  2. no\n  recommendation: yes, it is cheap", lst)
        raw = json.loads((self.home / "backlog.json").read_text())["decisions"][0]
        self.assertEqual(list(raw), ["id", "title", "date", "text", "options", "recommendation"])

    # ---------- warnings: never blocking ----------

    def test_an_option_without_effect_warns(self):
        r = self.add("--option", "yes", "--effect", "they see it", "--option", "no", "--undo", "no")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(
            self.warnings(r),
            [
                "foreman backlog: warning: AB-1 J1: option 2 («no») has no --effect: say what the owner will see or "
                "do differently with it (--effect after its --option)"
            ],
        )
        self.assertEqual(
            self.b.load()["decisions"][0]["details"], {"yes": {"effect": "they see it"}, "no": {"undo": "no"}}
        )

    def test_two_questions_in_one_warn_and_a_quoted_one_does_not(self):
        self.assertEqual(Backlog.question_count(TWO_QUESTIONS["text"]), 2)
        self.assertEqual(Backlog.question_count(TWO_QUESTIONS["title"]), 0)
        self.assertEqual(Backlog.question_count(QUOTED_QUESTION["title"]), 1)
        self.assertEqual(Backlog.question_count(QUOTED_QUESTION["text"]), 0)
        self.assertEqual(Backlog.question_count('Is it «done?» or “ready?” or "gone?" now?'), 1)
        self.assertEqual(Backlog.question_count("Really?? Sure?!"), 2)
        r = self.add("--option", "a", "--effect", "x", "--as-is", **TWO_QUESTIONS)
        self.assertEqual(r.returncode, 0, r.stderr)
        w = self.warnings(r)
        self.assertEqual(len(w), 1, w)
        self.assertIn("the text asks 2 questions: split it, one decision per question", w[0])
        r = self.add("--option", "a", "--effect", "x", **QUOTED_QUESTION)
        self.assertEqual((r.returncode, self.warnings(r)), (0, []), r.stderr)

    def test_past_date_and_page_warn(self):
        r = self.add(
            *("--option", "a", "--effect", "x", "--if-no-answer", "the export waits"),
            *("--answer-by", "2020-01-01", "--page", "reviews/later/question.md"),
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        w = "\n".join(self.warnings(r))
        self.assertIn("--answer-by 2020-01-01 is in the past", w)
        self.assertIn("the page reviews/later/question.md is not there yet", w)
        self.assertIn("warning: decision AB-1 J1: page reviews/later/question.md is missing", self.cli("check").stdout)
        (self.home / PAGE).write_text("x" * (PAGE_MAX + 1))
        r = self.add("--option", "a", "--effect", "x", "--page", PAGE)
        self.assertIn("is larger than 400 KB: the panel will not show it", r.stderr)

    def page_warns(self, page, body):
        """The warnings of a question whose page is `page` with `body`."""
        f = self.home / page
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body)
        r = self.add("--option", "a", "--effect", "x", "--page", page)
        self.assertEqual(r.returncode, 0, r.stderr)
        return "\n".join(self.warnings(r))

    def test_a_page_not_written_for_the_question_warns(self):
        short = "# The export\n\n" + "A line.\n" * 50 + "![before](shots/before_390.png)\n"
        self.assertEqual(self.page_warns("reviews/q1/question.md", short), "")
        w = self.page_warns("reviews/q2/question.md", "A line.\n" * 61)
        self.assertIn("the page reviews/q2/question.md has 61 lines: a page for one question takes about 30", w)
        w = self.page_warns("reviews/q1/question.md", short)
        self.assertIn("the page reviews/q1/question.md is already the page of AB-1 J1: each question", w)
        for page in ("reviews/2026-11-05-ab-1-study.md", "reviews/q3/plan-AB-1.md"):
            w = self.page_warns(page, "# Study\n")
            self.assertIn(f"the page {page} is a report or a plan", w)
        with self.b.mutate() as d:
            self.b.issue(d, "AB-1")["subs"] = [{"id": "T1", "title": "x", "status": "done", "report": "notes/r.md"}]
        self.assertIn("the page notes/r.md is a report or a plan", self.page_warns("notes/r.md", "# R\n"))
        w = self.page_warns("reviews/q4/question.md", "The run_export job fails.\n")
        self.assertIn("the page reviews/q4/question.md names without explaining: «run_export»", w)
        self.assertEqual(len(self.b.load()["decisions"]), 7)

    # ---------- refused: exit 2, nothing written ----------

    def assert_refused(self, r, said):
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn(said, r.stderr)
        self.assertEqual(self.b.load()["decisions"], [])

    def test_bad_values_are_refused(self):
        self.assert_refused(self.add("--effect", "x", "--option", "a"), "--effect goes after the --option it describes")
        self.assert_refused(self.add("--option", "a", "--cost", "x", "--cost", "y"), "--cost given twice for option 1")
        self.assert_refused(self.add("--option", "a", "--undo", "maybe"), "invalid choice: 'maybe'")
        self.assert_refused(
            self.add("--option", "a", "--if-no-answer", "it waits", "--answer-by", "2026-13-01"), "is not a date"
        )
        self.assert_refused(self.add("--option", "a", "--answer-by", later(3)), "--answer-by needs --if-no-answer")
        for page in ("../x.md", "/tmp/x.md", "pages/decisions.md", "reviews/x.txt", "reviews/../pages/x.md"):
            self.assert_refused(self.add("--option", "a", "--page", page), "--page ")
        self.assert_refused(
            self.cli("backlog", "decision", "resolve", "AB-1 J1", "--option", "a", "--effect", "x"),
            "unrecognized arguments: --effect x",
        )

    def test_plain_checks_the_new_texts(self):
        r = self.add(
            "--option", "yes", "--effect", "T5 starts", "--cost", "fix_widget breaks", "--if-no-answer", "J2 waits"
        )
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("option effect: «T5»", r.stderr)
        self.assertIn("option cost: «fix_widget»", r.stderr)
        self.assertIn("if no answer: «J2»", r.stderr)
        r = self.add("--option", "a", "--option", "b", "--effect", "T5 starts")
        self.assertIn("option 2 effect: «T5»", r.stderr)

    # ---------- foreman check ----------

    def test_check_validates_the_fields(self):
        with self.b.mutate() as data:
            self.b.add_decision(data, None, TITLE, TEXT, ["a", "b"], issue="AB-1")
        data = self.b.load()
        d = data["decisions"][0]
        d["details"] = {"a": {"effect": "x", "undo": "maybe", "colour": "red"}, "c": {"effect": "y"}, "b": "z"}
        d["unanswered"] = {"by": "tomorrow", "option": "a"}
        d["page"] = "pages/decisions.md"
        d["extra"] = 1
        errs = backlog_check.validate(self.b, data, files=False)
        for e in (
            "decision AB-1 J1: details of 'a': undo 'maybe' is not one of yes, hard, no",
            "warning: decision AB-1 J1: details of 'a': unknown key colour (dropped at the next write)",
            "decision AB-1 J1: details for 'c', which is not one of its options",
            "decision AB-1 J1: details of 'b' is not an object",
            "decision AB-1 J1: unanswered has no waits (what stays stopped until the owner answers)",
            "decision AB-1 J1: unanswered by 'tomorrow' is not a date (YYYY-MM-DD)",
            "warning: decision AB-1 J1 unanswered: unknown keys option (dropped at the next write)",
            "warning: decision AB-1 J1: unknown keys extra (dropped at the next write)",
        ):
            self.assertIn(e, errs)
        self.assertTrue(any(e.startswith("decision AB-1 J1: page 'pages/decisions.md' is outside") for e in errs))
        d["details"], d["unanswered"], d["page"] = [], "x", 3
        errs = backlog_check.validate(self.b, data, files=False)
        for e in (
            "decision AB-1 J1: details is not an object",
            "decision AB-1 J1: unanswered is not an object",
            "decision AB-1 J1: page 3 is not a path",
        ):
            self.assertIn(e, errs)


if __name__ == "__main__":
    unittest.main()
