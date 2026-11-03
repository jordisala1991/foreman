"""The orchestrator's band (lib/foreman/band.py): each rule of its parts on a fake project, in-process, in English and
in Spanish; the issues' short names (backlog.short_title, `backlog edit --short`) and the count of questions shared
with the panel (notes.unanswered). The band as the module gets it and `hook mod status` prints it, in both languages
and with a branch ready to integrate, is in tests/golden/mod/band.txt."""

import json
import time
import unittest
from datetime import datetime

from helpers import ProjectCase

from foreman import band, hooks_mod, loop, notes, orchestrator, panel, renew, util
from foreman.backlog import SHORT_MAX, Backlog, short_title


def iso(epoch):
    return datetime.fromtimestamp(epoch).isoformat(timespec="seconds")


class BandTest(ProjectCase):
    NOW = datetime(2026, 3, 2, 10, 30).timestamp()

    def setUp(self):
        super().setUp()
        self.b = Backlog(self.project)
        self.b.init()
        with self.b.mutate() as d:
            self.b.add_project(d, "E1", "One", "intro")
            self.b.add_issue(
                d, "E1", "PF-1", "Keep the harbor lights on: a long title that goes on", status="ready", criterion="x"
            )
            self.b.add_issue(d, "E1", "PF-2", "Rebuild the jetty", status="ready", criterion="x")

    # ---------- helpers ----------

    def beat(self, epoch):
        util.write_json(self.project.state / "heartbeats.json", {"loop": iso(epoch)})

    def agents(self, n, minutes=10, issue="PF-2", sub=None):
        recs = {
            f"agent-{k}": {
                "name": f"ag{k}",
                "named_by": "launch",
                "start": iso(self.NOW - (minutes + k) * 60),
                **({"issue": issue} if issue else {}),
                **({"sub": sub} if sub else {}),
            }
            for k in range(n)
        }
        self.project.state.mkdir(parents=True, exist_ok=True)
        (self.project.state / "agents.json").write_text(json.dumps(recs))

    def mark(self):
        orchestrator.mark(self.project, "S1")

    def measure(self, tokens):
        hooks_mod.measure(
            {"cwd": str(self.home), "session": "S1", "context": {"tokens": tokens, "window": 1000000, "percent": 12}}
        )

    def decide(self, *titles):
        with self.b.mutate() as d:
            for t in titles:
                self.b.add_decision(d, None, t, "Context.", ["a: yes", "b: no"], issue="PF-1")

    def band(self, now=None):
        return band.lines(self.project, now=self.NOW if now is None else now)

    def row(self, n=0, now=None):
        return self.band(now)["rows"][n]

    def texts(self, n=0, now=None):
        return [p["text"] for p in self.row(n, now)]

    def find(self, start, n=0, now=None):
        return next((p for p in self.row(n, now) if p["text"].startswith(start)), None)

    def spanish(self):
        self.write_toml(self.read_toml().replace('language = "en"', 'language = "es"'))

    def read_toml(self):
        return (self.home / "foreman.toml").read_text()

    # ---------- the shape ----------

    def test_every_part_has_text_role_and_prio(self):
        self.mark()
        self.agents(band.MAX_AGENTS + 1)
        b = self.band()
        self.assertEqual(set(b), {"level", "rows"})
        self.assertEqual(len(b["rows"]), 2)
        for row in b["rows"]:
            for p in row:
                self.assertIsInstance(p["text"], str)
                self.assertIn(p["role"], ("dot", "ask", "bad", "warn", "plain", "dim", "more"))
                self.assertIsInstance(p["prio"], int)
                self.assertEqual(set(p) - {"text", "role", "prio"}, {"n"} if p["role"] == "more" else set())

    # ---------- the level and the dot ----------

    def test_level_bad_without_a_marked_session(self):
        b = self.band()
        self.assertEqual(b["level"], "bad")
        self.assertEqual(b["rows"][0][0], {"text": "●", "role": "dot", "prio": 0})
        self.assertEqual(self.find("no orchestrator")["role"], "bad")

    def test_level_ok_ask_bad(self):
        self.mark()
        self.assertEqual(self.band()["level"], "ok")
        self.decide("Ship it?")
        self.assertEqual(self.band()["level"], "ask")
        self.assertEqual(self.row()[0]["role"], "dot")  # the dot always first

    def test_level_bad_when_the_pass_is_late_even_with_questions(self):
        now = time.time()
        self.mark()
        rec = util.read_json(self.project.state / "orchestrator.json", {})
        rec["since"] = iso(now - 4 * 3600)
        util.write_json(self.project.state / "orchestrator.json", rec)
        self.beat(datetime.fromtimestamp(now - 3 * 3600).replace(second=0).timestamp())
        self.decide("Ship it?")
        b = self.band(now)
        self.assertEqual(b["level"], "bad")
        late = self.find("pass late since", now=now)
        self.assertEqual((late["role"], late["prio"]), ("bad", 0))
        self.assertEqual(late["text"], "pass late since " + datetime.fromtimestamp(now - 3 * 3600).strftime("%H:%M"))
        self.assertIsNone(self.find("next pass", now=now))
        self.assertLess(self.texts(now=now).index(late["text"]), self.texts(now=now).index("memory —"))

    def test_a_failure_is_one_row_with_the_red_dot(self):
        orig = band._lines
        band._lines = lambda *a: 1 / 0
        try:
            self.assertEqual(
                band.lines(self.project),
                {
                    "level": "bad",
                    "rows": [
                        [
                            {"text": "●", "role": "dot", "prio": 0},
                            {
                                "text": "foreman: the status could not be read (ZeroDivisionError: division by zero)",
                                "role": "bad",
                                "prio": 0,
                            },
                        ]
                    ],
                },
            )
            self.assertEqual(
                band.text(self.project),
                "needs attention · foreman: the status could not be read (ZeroDivisionError: division by zero)",
            )
        finally:
            band._lines = orig

    # ---------- the questions ----------

    def test_questions_first_after_the_dot_only_when_there_are_some(self):
        self.mark()
        self.assertFalse(any(p["role"] == "ask" for p in self.row()))
        self.decide("Ship it?")
        self.assertEqual(self.row()[1], {"text": "1 question for you", "role": "ask", "prio": 0})
        self.decide("Paint it blue?")
        self.assertEqual(self.row()[1]["text"], "2 questions for you")

    def test_a_question_answered_from_the_panel_counts_no_more(self):
        """Answered is resolved: the panel's answer resolves the decision, so it leaves the count at once and never
        comes back once the orchestrator reads the message; an unread message alone answers nothing."""
        self.mark()
        self.decide("Ship it?", "Paint it blue?")
        first = self.b.load()["decisions"][0]["id"]
        notes.inbox_add(self.project, "a: yes", sender="owner", decision="Ship it?")  # a message, nothing resolved
        self.assertEqual(self.row()[1]["text"], "2 questions for you")
        with self.b.mutate() as d:
            self.b.answer_decision(d, first, "a: yes", "Owner, 2026-11-02 10:00 (panel)")
        notes.inbox_add(self.project, "a: yes", sender="owner", decision=f"{first}, Ship it?", did=first, resolved=True)
        self.assertEqual(self.row()[1]["text"], "1 question for you")
        data = self.b.load()
        shown = panel.decisions_with_answers(data, notes.inbox(self.project))  # the panel, by the same rule
        self.assertEqual([(d["title"], d["answer"]) for d in shown], [("Paint it blue?", None)])
        notes.inbox_read(self.project)  # the orchestrator read it: still resolved, it does not count again
        self.assertEqual(self.row()[1]["text"], "1 question for you")

    # ---------- the memory ----------

    def test_memory_against_the_renewal_threshold(self):
        self.assertEqual(self.find("memory")["text"], "memory —")  # no marked session
        self.mark()
        self.assertEqual(self.find("memory"), {"text": "memory —", "role": "plain", "prio": band.PRIO_MEMORY})
        self.measure(120624)
        self.assertEqual(self.find("memory"), {"text": "memory 121K of 200K", "role": "plain", "prio": 2})
        self.measure(int(0.9 * 200000))
        self.assertEqual(self.find("memory")["role"], "warn")
        self.measure(int(0.9 * 200000) - 1)
        self.assertEqual(self.find("memory")["role"], "plain")
        self.write_toml(self.read_toml().replace("[loop]\n", "[loop]\ncontext_rotate = 0\n"))
        self.measure(120624)
        self.assertEqual(self.find("memory")["text"], "memory 121K")

    def test_memory_unknown_after_a_renewal_until_the_first_response(self):
        self.mark()
        hooks_mod.measure({"cwd": str(self.home), "session": "S1", "context": {"tokens": None, "window": 1000000}})
        self.assertEqual(self.find("memory")["text"], "memory —")

    def test_renewed_is_its_own_part_for_an_hour(self):
        self.mark()
        at = self.NOW - 600
        orig = renew.compacted_at
        renew.compacted_at = lambda *a: at
        try:
            texts = self.texts()
            renewed = self.find("renewed")
            self.assertEqual(renewed, {"text": "renewed 10:20", "role": "dim", "prio": band.PRIO_RENEWED})
            self.assertEqual(texts.index("renewed 10:20"), texts.index("memory —") + 1)
            self.assertIsNone(self.find("renewed", now=at + band.RENEWED_SHOWN_S + 1))
        finally:
            renew.compacted_at = orig

    # ---------- the branches ready to integrate ----------

    def test_ready_only_when_there_are_some(self):
        self.mark()
        self.assertIsNone(self.find("to integrate"))
        orig = loop.ready_branches
        loop.ready_branches = lambda *a, kind="code": ["api:a1/t1"] if kind == "code" else []
        try:
            self.assertEqual(self.find("to integrate"), {"text": "to integrate 1", "role": "plain", "prio": 3})
            self.assertIsNone(self.find("to close"))
        finally:
            loop.ready_branches = orig

    def test_measurements_to_close_are_counted_apart(self):
        self.mark()
        orig = loop.ready_branches
        loop.ready_branches = lambda *a, kind="code": {"code": ["api:a1/t1"], "measure": ["a:m1", "a:m2"]}[kind]
        try:
            texts = self.texts()
            self.assertEqual(self.find("to close"), {"text": "to close 2", "role": "plain", "prio": 3})
            self.assertEqual(texts.index("to close 2"), texts.index("to integrate 1") + 1)
            loop.ready_branches = lambda *a, kind="code": ["a:m1"] if kind == "measure" else []
            self.assertIsNone(self.find("to integrate"))
            self.spanish()
            self.assertIsNotNone(self.find("por cerrar 1"))
        finally:
            loop.ready_branches = orig

    # ---------- the pass ----------

    def test_next_pass_only(self):
        self.mark()
        self.assertEqual(self.find("no pass"), {"text": "no pass yet", "role": "plain", "prio": band.PRIO_NEXT_PASS})
        self.beat(datetime(2026, 3, 2, 10, 5).timestamp())
        self.assertEqual(self.find("next pass")["text"], "next pass 11:05")
        self.write_toml(self.read_toml().replace("[loop]\n", "[loop]\nevery_min = 15\n"))
        self.assertEqual(self.find("next pass")["text"], "next pass now")  # 10:20 is past

    def test_no_loop_prompt_no_pass_part(self):
        self.write_toml(self.read_toml().replace('prompt = "Fake orchestrator loop"', 'prompt = ""'))
        self.beat(datetime(2026, 3, 2, 10, 5).timestamp())
        self.assertFalse(any("pass" in t for t in self.texts()))

    # ---------- the jobs ----------

    def test_nothing_running(self):
        self.assertEqual(self.row(1), [{"text": "nothing running", "role": "dim", "prio": band.PRIO_JOBS}])

    def test_jobs_by_short_name_issue_and_minutes_oldest_first(self):
        self.agents(2, minutes=12, issue="PF-2", sub="T1")
        self.assertEqual(
            self.row(1),
            [
                {"text": "Rebuild the jetty (PF-2 T1) 13 min", "role": "dim", "prio": 1},
                {"text": "Rebuild the jetty (PF-2 T1) 12 min", "role": "dim", "prio": 2},
                {"text": "+{n} more", "role": "more", "prio": 0, "n": 0},
            ],
        )
        with self.b.mutate() as d:
            self.b.edit(d, "PF-2", short="Jetty")
        self.assertEqual(self.texts(1)[0], "Jetty (PF-2 T1) 13 min")
        self.agents(1, minutes=3, issue="PF-1")
        self.assertEqual(self.texts(1)[:-1], ["Keep the harbor lights on (PF-1) 3 min"])

    def test_a_job_without_issue_or_with_one_not_in_the_backlog_goes_by_the_agents_name(self):
        self.agents(1, minutes=5, issue=None)
        self.assertEqual(self.texts(1)[:-1], ["ag0 5 min"])
        self.agents(1, minutes=5, issue="PF-99")
        self.assertEqual(self.texts(1)[:-1], ["ag0 (PF-99) 5 min"])

    def test_at_most_three_jobs_then_how_many_more(self):
        self.agents(band.MAX_AGENTS + 2)
        row = self.row(1)
        self.assertEqual(len(row), band.MAX_AGENTS + 1)
        self.assertEqual(row[-1], {"text": "+{n} more", "role": "more", "prio": 0, "n": 2})
        self.assertTrue(band.text(self.project, now=self.NOW).split("\n")[1].endswith(" · +2 more"))

    def test_the_more_part_comes_with_every_job_list_and_zero_is_not_said(self):
        # the module counts in it the jobs it leaves out when the row does not fit
        self.agents(band.MAX_AGENTS)
        self.assertEqual(self.row(1)[-1], {"text": "+{n} more", "role": "more", "prio": 0, "n": 0})
        self.assertNotIn("more", band.text(self.project, now=self.NOW).split("\n")[1])

    # ---------- «foreman» ----------

    def test_foreman_last_and_first_to_go(self):
        self.mark()
        self.decide("Ship it?")
        row = self.row()
        self.assertEqual(row[-1], {"text": "foreman", "role": "dim", "prio": band.PRIO_FOREMAN})
        self.assertEqual(max(p["prio"] for p in row), band.PRIO_FOREMAN)

    def test_drop_order_is_the_owners_choice(self):
        # the owner's choice: «foreman», renewal time, next pass, branches to integrate, memory; questions and dot never
        self.assertEqual(
            [band.PRIO_FOREMAN, band.PRIO_RENEWED, band.PRIO_NEXT_PASS, band.PRIO_READY, band.PRIO_MEMORY],
            sorted([band.PRIO_FOREMAN, band.PRIO_RENEWED, band.PRIO_NEXT_PASS, band.PRIO_READY, band.PRIO_MEMORY])[
                ::-1
            ],
        )
        self.assertEqual(band.PRIO_KEEP, 0)

    # ---------- /foreman:status ----------

    def test_text_is_every_part_with_the_level_as_a_word(self):
        self.mark()
        self.measure(120624)
        self.beat(datetime(2026, 3, 2, 10, 5).timestamp())
        self.decide("Ship it?")
        self.agents(1, minutes=12)
        self.assertEqual(
            band.text(self.project, now=self.NOW),
            "something waits for you · 1 question for you · memory 121K of 200K · next pass 11:05 · foreman\n"
            "Rebuild the jetty (PF-2) 12 min",
        )
        self.assertEqual(hooks_mod.status({"cwd": str(self.home)})["text"].split(" · ")[0], "something waits for you")

    # ---------- Spanish ----------

    def test_spanish(self):
        self.spanish()
        self.assertEqual(
            band.text(self.project, now=self.NOW),
            "atención · ninguna sesión de orquestador marcada · memoria — · sin pasadas aún · foreman\nnada en marcha",
        )
        self.mark()
        self.measure(185000)
        self.beat(datetime(2026, 3, 2, 10, 5).timestamp())
        self.decide("Ship it?", "Paint it blue?")
        self.agents(band.MAX_AGENTS + 1, minutes=12, sub="T1")
        b = self.band()
        self.assertEqual(b["level"], "ask")
        self.assertEqual(
            [(p["text"], p["role"]) for p in b["rows"][0]],
            [
                ("●", "dot"),
                ("2 preguntas para ti", "ask"),
                ("memoria 185K de 200K", "warn"),
                ("próxima pasada 11:05", "plain"),
                ("foreman", "dim"),
            ],
        )
        self.assertEqual(
            band.text(self.project, now=self.NOW).split("\n")[1],
            "Rebuild the jetty (PF-2 T1) 15 min · Rebuild the jetty (PF-2 T1) 14 min · "
            "Rebuild the jetty (PF-2 T1) 13 min · +1 más",
        )
        self.assertEqual(band.text(self.project, now=self.NOW).split(" · ")[0], "te espera algo")
        self.write_toml(self.read_toml().replace("[loop]\n", "[loop]\nevery_min = 15\n"))
        self.assertEqual(self.find("próxima pasada")["text"], "próxima pasada ya")
        self.decide("Move the pier?")
        self.assertEqual(self.find("3 preguntas")["text"], "3 preguntas para ti")

    def test_spanish_one_question_and_bare_memory(self):
        self.spanish()
        self.write_toml(self.read_toml().replace("[loop]\n", "[loop]\ncontext_rotate = 0\n"))
        self.mark()
        self.measure(120624)
        self.decide("Ship it?")
        self.assertEqual(self.texts()[1:3], ["1 pregunta para ti", "memoria 121K"])
        self.assertEqual(band.text(self.project, now=self.NOW).split("\n")[0].split(" · ")[0], "te espera algo")


class ShortTitleTest(ProjectCase):
    def test_the_short_field_wins(self):
        self.assertEqual(short_title({"title": "Something long", "short": "Band"}), "Band")

    def test_the_title_up_to_the_first_cut(self):
        for title, want in (
            ("The band: easy to read", "The band"),
            ("The band (on the chat) for all", "The band"),
            ("The band, easy to read", "The band"),
            ("The band - easy to read", "The band"),
            ("`foreman status` in **plain** text", "foreman status in plain text"),
            ("re-run the half-done jobs", "re-run the half-done jobs"),
        ):
            self.assertEqual(short_title({"title": title}), want, title)

    def test_a_long_title_is_cut_by_words(self):
        t = short_title({"title": "The hourly review of the orchestrator that sleeps in the other workspace"})
        self.assertEqual(t, "The hourly review of the…")
        self.assertLessEqual(len(t), SHORT_MAX)
        self.assertEqual(short_title({"title": "x" * 40}), "x" * (SHORT_MAX - 1) + "…")

    def test_backlog_edit_short_sets_and_clears_it(self):
        b = Backlog(self.project)
        b.init()
        with b.mutate() as d:
            b.add_project(d, "E1", "One", "intro")
            b.add_issue(d, "E1", "PF-1", "The band over the chat", status="ready", criterion="x")
        r = self.cli("backlog", "edit", "PF-1", "--short", "Band")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(b.issue(b.load(), "PF-1")["short"], "Band")
        self.assertIn("short «Band»", self.cli("backlog", "show", "PF-1").stdout)
        self.assertEqual(self.cli("backlog", "edit", "PF-1", "--short", "").returncode, 0)
        self.assertNotIn("short", b.issue(b.load(), "PF-1"))


if __name__ == "__main__":
    unittest.main()
