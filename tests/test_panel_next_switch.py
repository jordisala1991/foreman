"""Switching screens in the new panel is one change, not several. The built bundle is mounted in node
(tests/next_dom.mjs, which samples the screen's header every 10 ms while the new screen's answers are on their way): to
each screen, with the answers late, the header goes from the old screen's to the new one's (at most two distinct
layouts, a placeholder of the same layout counting as the new one), is never swapped again after the new screen's
title is up, and no «Cargando…» card shows. A screen visited before shows at once; a tap on the open screen changes
nothing."""

import copy
from typing import ClassVar

import test_panel_next_flicker as flicker  # the module, not its class: unittest would run its tests here too
from helpers import ProjectCase

TITLES = {
    "#/needs": "Te necesita",
    "#/agents": "Agentes",
    "#/how": "Cómo va",
    "#/backlog": "Backlog",
    "#/wiki": "Wiki",
    "#/system": "Sistema",
    "#/more": "Más",
}


class NextSwitchTest(ProjectCase):
    mount = flicker.NextFlickerTest.mount
    answers = flicker.NextFlickerTest.answers
    ALPHA = flicker.NextFlickerTest.ALPHA
    PROJECTS = flicker.NextFlickerTest.PROJECTS
    HOME = flicker.NextFlickerTest.HOME
    SCREENS = flicker.NextFlickerTest.SCREENS
    card = flicker.NextFlickerTest.card
    finished = flicker.NextFlickerTest.finished
    metrics = flicker.NextFlickerTest.metrics
    BETA = flicker.base.NextPageTest.BETA

    def everything(self):
        out = {}
        for hash_ in self.SCREENS:
            out.update(self.answers(hash_))
        # the one metrics answer serves Home (its figures of today) and «How it goes» (its days)
        key = "/api/metrics?p=alpha&days=7"
        out[key] = {**out[key], "today": flicker.base.today_metrics()["today"]}
        return out

    PHONE: ClassVar[dict] = {"width": 390, "visible": []}
    WIDE: ClassVar[dict] = {"width": 1440, "visible": ["(min-width: 900px)"]}

    def switch(self, target, delay, start="#/home", layout=None):
        return self.mount(
            {
                "hash": start,
                "answers": self.everything(),
                "now": 1_794_000_000_000,
                "steps": [{"hash": target, "delay": delay}],
                **(layout or self.PHONE),
            }
        )["layouts"][0]["seen"]

    def assert_one_change(self, seen, title, where):
        # the old screen stays until the new one's data is there, then the new one draws once: the states seen (title,
        # block, number of cards and agents: the header and the body) are the old one and the new one, never a block
        states = [(s["title"], s["skeleton"], s["cards"], s["agents"]) for s in seen]
        self.assertLessEqual(len(set(states)), 2, f"{where}: more than two states of the screen: {states}")
        self.assertFalse([s for s in seen if s["skeleton"]], f"{where}: a placeholder block showed: {seen}")
        self.assertLessEqual(len({s["head"] for s in seen}), 2, f"{where}: more than two layouts of the header")
        new = [s for s in seen if s["title"] == title]
        self.assertTrue(new, f"{where}: the new screen never showed its title: {seen}")
        self.assertEqual(
            len({(s["head"], s["cards"], s["agents"]) for s in new}),
            1,
            f"{where}: the new screen changed after it was up",
        )
        self.assertFalse([s for s in seen if s["loading"]], f"{where}: a «Cargando…» card showed: {seen}")

    def test_each_screen_is_one_change_with_slow_answers(self):
        for layout in (self.PHONE, self.WIDE):
            for target, title in TITLES.items():
                for delay in (100, 600):  # a quick answer and a slow one
                    with self.subTest(screen=target, delay=delay, width=layout["width"]):
                        self.assert_one_change(
                            self.switch(target, delay, layout=layout), title, f"{target} after {delay} ms"
                        )

    def test_a_screen_switched_from_another_than_home_is_the_same(self):
        for start, target in (("#/agents", "#/needs"), ("#/system", "#/agents"), ("#/wiki", "#/how")):
            with self.subTest(start=start, screen=target):
                self.assert_one_change(self.switch(target, 600, start), TITLES[target], f"{start} to {target}")

    def test_a_revisit_waits_for_current_data_instead_of_painting_the_old_and_swapping(self):
        answers = self.everything()
        two = copy.deepcopy(answers["/api/agents?p=alpha"])
        two["live"].append(self.card(id="a2", task="Otro agente"))
        for layout in (self.PHONE, self.WIDE):
            with self.subTest(width=layout["width"]):
                out = self.mount(
                    {
                        "hash": "#/agents", "answers": answers, **layout,  # no fixed «now»: the cache must age
                        "steps": [
                            {"hash": "#/needs", "delay": 1100},  # longer than the second the cache counts as current
                            {"hash": "#/agents", "delay": 400, "answers": {"/api/agents?p=alpha": two}},
                        ],
                    }
                )  # fmt: skip
                seen = out["layouts"][1]["seen"]
                self.assertNotIn(
                    1, [s["agents"] for s in seen if s["title"] == "Agentes"], f"the old cards were painted: {seen}"
                )
                self.assertEqual(seen[-1]["agents"], 2)
                self.assert_one_change(seen, "Agentes", "the revisit")

    def test_a_tap_on_the_open_screen_changes_nothing(self):
        out = self.mount(
            {
                "hash": "#/more", "answers": self.everything(), "now": 1_794_000_000_000,
                "steps": [{"hash": "#/more", "delay": 300}, {"hash": "#/more", "delay": 300}],
            }
        )  # fmt: skip
        for step in out["layouts"]:
            self.assertEqual(len(step["seen"]), 1, f"a tap on the open screen changed it: {step['seen']}")

    def test_the_last_pick_of_the_product_wins_whatever_order_the_reads_come_back(self):
        both = {"projects": [self.ALPHA, {**self.BETA, "next": True}], "language": "es"}
        everybody = {
            "projects": ["alpha", "beta"],
            "done": [],
            "next_pass": None,
            "live": [self.card(id="b1", p="beta", task="De beta")],
        }
        slow = {
            "projects": ["beta"],
            "live": [self.card(id="b2", p="beta", task="Solo de beta")],
            "done": [],
            "next_pass": None,
        }
        mine = {"projects": ["alpha"], "live": [self.card(task="Solo de alpha")], "done": [], "next_pass": None}
        out = self.mount(
            {
                "hash": "#/agents", "store": {"next-product": "*"}, "picks": ["beta", "alpha"], "wait": 900,
                "delay": {"/api/agents?p=beta": 400},
                "answers": {
                    "/api/projects": both,
                    "/api/agents?p=*": everybody,
                    "/api/agents?p=beta": slow,
                    "/api/agents?p=alpha": mine,
                },
            }
        )  # fmt: skip
        self.assertEqual(out["picker"], "alpha")
        self.assertEqual([a["task"] for a in out["agents"]], ["Solo de alpha"])
