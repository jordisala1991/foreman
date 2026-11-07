"""Switching screens in the new panel is one change, not several. The built bundle is mounted in node
(tests/next_dom.mjs, which samples the screen's header every 10 ms while the new screen's answers are on their way): to
each screen, with the answers late, the header goes from the old screen's to the new one's (at most two distinct
layouts, a placeholder of the same layout counting as the new one), is never swapped again after the new screen's
title is up, and no «Cargando…» card shows. A screen visited before shows at once; a tap on the open screen changes
nothing."""

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

    def everything(self):
        out = {}
        for hash_ in self.SCREENS:
            out.update(self.answers(hash_))
        return out

    def switch(self, target, delay, start="#/home"):
        return self.mount(
            {
                "hash": start,
                "answers": self.everything(),
                "now": 1_794_000_000_000,
                "steps": [{"hash": target, "delay": delay}],
            }
        )["layouts"][0]["seen"]

    def assert_one_change(self, seen, title, where):
        heads = [s["head"] for s in seen]
        self.assertLessEqual(len(set(heads)), 2, f"{where}: more than two layouts of the header: {heads}")
        new = [s for s in seen if s["title"] == title]
        self.assertTrue(new, f"{where}: the new screen never showed its title: {seen}")
        self.assertEqual(len({s["head"] for s in new}), 1, f"{where}: the header changed after the new title was up")
        self.assertFalse([s for s in seen if s["loading"]], f"{where}: a «Cargando…» card showed: {seen}")

    def test_each_screen_is_one_change_with_slow_answers(self):
        for target, title in TITLES.items():
            for delay in (100, 600):  # under and over the wait before a placeholder
                with self.subTest(screen=target, delay=delay):
                    self.assert_one_change(self.switch(target, delay), title, f"{target} after {delay} ms")

    def test_a_screen_switched_from_another_than_home_is_the_same(self):
        for start, target in (("#/agents", "#/needs"), ("#/system", "#/agents"), ("#/wiki", "#/how")):
            with self.subTest(start=start, screen=target):
                self.assert_one_change(self.switch(target, 600, start), TITLES[target], f"{start} to {target}")

    def test_a_tap_on_the_open_screen_changes_nothing(self):
        out = self.mount(
            {
                "hash": "#/more", "answers": self.everything(), "now": 1_794_000_000_000,
                "steps": [{"hash": "#/more", "delay": 300}, {"hash": "#/more", "delay": 300}],
            }
        )  # fmt: skip
        for step in out["layouts"]:
            self.assertEqual(len(step["seen"]), 1, f"a tap on the open screen changed it: {step['seen']}")
