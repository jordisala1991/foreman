"""The new panel must not redraw what did not change. The built bundle is mounted in node
(tests/next_dom.mjs, which counts React's commits through the devtools hook), the timers run while the answers stay the
same: an idle screen commits nothing. And when an answer does change, the screen keeps its content while the new one
is on its way: «Cargando…» is for the first answer only.
"""

import copy
from typing import ClassVar

import test_panel_next as base  # the module, not its classes: unittest would run their tests here too
import test_panel_next_more as more
from helpers import ProjectCase

NOW = 1_794_000_000_000  # a fixed «now» (ms): a tick never crosses a minute


class NextFlickerTest(ProjectCase):
    mount = base.NextPageTest.mount
    ALPHA = base.NextPageTest.ALPHA
    PROJECTS = base.NextPageTest.PROJECTS
    card = base.NextPageTest.card
    finished = base.NextPageTest.finished
    metrics = base.NextPageTest.metrics

    HOME: ClassVar[dict] = {
        "/api/state?p=alpha": base.state(decisions=[base.QUESTION], grants=[base.GRANT], messages=[base.MESSAGE]),
        "/api/backlog?p=alpha&closed=recent": {"issues": [{"id": "PF-197", "title": "Agentes", "short": "Agentes"}]},
        "/api/metrics?p=alpha&days=7": base.today_metrics(),
    }

    def answers(self, hash_):
        agents = {
            "projects": ["alpha"],
            "live": [self.card()],
            "done": [self.finished()],
            "next_pass": None,
        }
        by_screen = {
            "#/home": self.HOME,
            "#/needs": self.HOME,
            "#/agents": {"/api/agents?p=alpha": agents},
            "#/how": {"/api/metrics?p=alpha&days=7": self.metrics(7)},
            "#/backlog": {"/api/backlog?p=alpha&closed=recent": more.BACKLOG, "/api/state?p=alpha": more.STATE},
            "#/wiki": {"/api/wiki?p=alpha": {"pages": more.PAGES}, "/api/wiki/page?p=alpha&n=splash": more.PAGE},
            "#/system": {"/api/state?p=alpha": more.STATE},
        }
        projects = {
            **self.PROJECTS,
            "projects": [{**self.ALPHA, "wiki": True, "permissions": 1}, self.PROJECTS["projects"][1]],
            "mac": more.SystemTest.MAC,
            "quota": [{"kind": "five_hour", "percent_used": 41, "resets_at": None}],
            "errors": [],
        }
        return {"/api/projects": projects, **copy.deepcopy(by_screen[hash_])}

    SCREENS = ("#/home", "#/needs", "#/agents", "#/how", "#/backlog", "#/wiki", "#/system")

    def test_an_idle_screen_commits_nothing_when_the_answers_did_not_change(self):
        for hash_ in self.SCREENS:
            with self.subTest(screen=hash_):
                out = self.mount({"hash": hash_, "answers": self.answers(hash_), "now": NOW, "tick": 6})
                self.assertGreater(len(out["text"]), 80, out["text"])  # it did draw
                self.assertEqual(out["commitsInTicks"], 0, "an idle screen redrew with the same answers")

    def test_a_changed_answer_does_redraw(self):
        # the idle test above is only worth something if a real change still commits
        answers = self.answers("#/agents")
        later = copy.deepcopy(answers["/api/agents?p=alpha"])
        later["done"].append(self.finished(agent="d9", task="Otra tarea"))
        out = self.mount(
            {
                "hash": "#/agents",
                "answers": answers,
                "now": NOW,
                "tick": 1,
                "later": {"answers": {"/api/agents?p=alpha": later}},
            }
        )
        self.assertGreaterEqual(out["commitsInTicks"], 1)
        self.assertIn("2 terminados hoy", out["text"])

    def test_a_refetch_never_shows_loading_over_existing_data(self):
        changed = {
            "#/agents": {
                "/api/agents?p=alpha": {"projects": ["alpha"], "live": [], "done": [self.finished()], "next_pass": None}
            },
            "#/how": {"/api/metrics?p=alpha&days=7": {**self.metrics(7), "total": 999.0}},
            "#/home": {"/api/state?p=alpha": base.state(decisions=[], grants=[], messages=[])},
            "#/needs": {"/api/state?p=alpha": base.state(decisions=[], grants=[], messages=[])},
            "#/backlog": {"/api/state?p=alpha": {"project": {**more.STATE["project"], "notes": []}}},
            "#/system": {"/api/state?p=alpha": {"project": {**more.STATE["project"], "notes": []}}},
        }
        for hash_, later in changed.items():
            with self.subTest(screen=hash_):
                out = self.mount(
                    {
                        "hash": hash_, "answers": self.answers(hash_), "now": NOW, "tick": 1,
                        "later": {"answers": later, "delay": 250},
                    }
                )  # fmt: skip
                self.assertNotIn("Cargando", out["duringTick"], "the refetch blanked the screen")
                self.assertGreater(len(out["duringTick"]), 80)
                self.assertNotIn("Cargando", out["text"])

    def test_a_screen_opened_again_shows_what_it_had_at_once(self):
        answers = {**self.answers("#/home"), **self.answers("#/agents")}
        out = self.mount(
            {
                "hash": "#/home", "answers": answers, "now": NOW,
                "steps": [{"hash": "#/agents"}, {"hash": "#/home", "delay": 250}],
            }
        )  # fmt: skip
        self.assertIn("Agentes", out["duringNav"][0])
        self.assertNotIn("Cargando", out["duringNav"][1], "the screen went blank on the way back")
        self.assertGreater(len(out["duringNav"][1]), 80)

    def test_the_machines_load_changing_redraws_only_the_screen_that_shows_it(self):
        # /api/projects carries the machine's load, which differs at every read: only «System» draws it
        for hash_, expected in (("#/home", 0), ("#/agents", 0), ("#/system", 1)):
            with self.subTest(screen=hash_):
                answers = self.answers(hash_)
                busy = {**answers["/api/projects"], "mac": {**more.SystemTest.MAC, "load": 7.7}}
                out = self.mount(
                    {
                        "hash": hash_, "answers": answers, "now": NOW, "tick": 1,
                        "later": {"answers": {"/api/projects": busy}},
                    }
                )  # fmt: skip
                self.assertEqual(min(out["commitsInTicks"], 1), expected)
