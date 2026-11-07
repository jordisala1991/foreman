"""«All products» as the panel's home: it opens there when there are several products and the owner has
not chosen one, and its Now tab has one line per product saying whether it needs him. app.js runs whole in node over a
minimal DOM (tests/panel_dom.js) against a real panel server."""

import unittest

import test_panel_stuck
from test_panel import PanelCase

NEEDS = """
const f = o => ({mode: 'active', questions: 0, grants: 0, orch: {name: 'o'}, health: null, paused: '', late: 0, ...o});
const r = o => { const n = productNeeds(f(o)); return [n.level, n.reasons.map(x => x.replace(/<[^>]*>/g, ''))]; };
return {
  quiet: r({}),
  questions: r({questions: 2}),
  grants: r({grants: 1}),
  gone: r({health: {verdict: 'gone'}, questions: 1}),
  missing: r({orch: null}),
  inactive_missing: r({orch: null, mode: 'inactive'}),
  late: r({late: 1}),
  paused: r({paused: 'paused by me'}),
  broken: r({broken: true, error: 'bad toml'}),
};
"""
HOME = """
await settle(1500);
return {P, rows: $('products').innerHTML, picked: store_get('product')};
"""
QUIET = """
OVER = {...OVER, projects: OVER.projects.map(o => ({...o, decisions: 0}))};
renderPicker();
return {html: $('plist').innerHTML};
"""
COUNT = """
const bad = {name: 'bad', title: 'bad', broken: true, mode: 'active', late: [], error: 'x'};
OVER = {...OVER, projects: [...OVER.projects, bad]};
renderPicker();
return {html: $('plist').innerHTML};
"""

HEADLINE = """
const base = {name: 'a', title: 'A', mode: 'active', orchestrator: {name: 'o', session: 's'},
  orchestrator_health: null, paused: '', warnings: [], writable: true, recurring: []};
const strip = h => h.replace(/<[^>]*>/g, ' ').replace(/\\s+/g, ' ').trim();
const run = (ps, joint) => {
  const d = {ps, joint: !!joint, agents: [], decisions: [], grants: [], recurring: [], locks: [], repos: [],
    cards: [], notes: []};
  lastData = d; renderNow(d);
  return {head: strip($('headline').innerHTML), ico: ($('headline').innerHTML.match(/background:([^"]*)"/) || [])[1],
    notices: strip($('notices').innerHTML)}; };
const down = {...base, orchestrator_health: {verdict: 'gone'}};
return {
  fine: run([base]),
  none: run([{...base, orchestrator: null}]),
  paused: run([{...base, paused: 'paused by me'}]),
  down: run([down]),
  inactive: run([{...base, orchestrator: null, mode: 'inactive'}]),
  joint_one: run([down, {...base, name: 'b', title: 'B'}], true),
  joint_two: run([down, {...base, name: 'b', title: 'B', paused: 'p'}], true),
  joint_fine: run([base, {...base, name: 'b', title: 'B'}], true),
};
"""


class HomeTest(PanelCase):
    run_page = test_panel_stuck.StuckTest.run_page

    def test_opens_on_all_products_when_several_and_no_choice(self):
        out = self.run_page("return {P};", product="")
        self.assertEqual(out["P"], "*")

    def test_keeps_the_owners_last_choice(self):
        self.assertEqual(self.run_page("return {P};", product="other")["P"], "other")
        self.assertEqual(self.run_page("return {P};", product="*")["P"], "*")

    def test_needs_by_product(self):
        out = self.run_page(NEEDS, product="*")
        self.assertEqual(out["quiet"][0], "ok")
        self.assertEqual(out["questions"], ["acc", ["2 preguntas"]])
        self.assertEqual(out["grants"][0], "acc")
        self.assertEqual(out["gone"][0], "bad")
        self.assertEqual(len(out["gone"][1]), 2)
        self.assertEqual(out["missing"], ["warn", ["sin orquestador"]])
        self.assertEqual(out["inactive_missing"][0], "muted")
        self.assertEqual(out["late"], ["warn", ["1 atrasada"]])
        self.assertEqual(out["paused"][0], "warn")
        self.assertEqual(out["broken"][0], "bad")

    def test_all_products_now_has_one_line_per_product(self):
        out = self.run_page(HOME.replace("store_get('product')", "''"), product="*")
        rows = out["rows"]
        self.assertEqual(rows.count('class="lrow'), 2, rows)
        self.assertIn("pick('fake')", rows)
        self.assertIn("pick('other')", rows)
        self.assertIn("1 pregunta", rows)
        self.assertIn("sin orquestador", rows)  # the active product has none: not green

    def test_one_product_has_no_such_list(self):
        out = self.run_page(HOME.replace("store_get('product')", "''"), product="other")
        self.assertEqual(out["rows"], "")

    def test_picker_count_says_the_broken_one(self):
        html = self.run_page(COUNT, product="*")["html"]
        self.assertIn("2 productos · 1 no carga", html)

    def test_picker_dot_of_a_product_without_orchestrator_is_not_green(self):
        html = self.run_page(QUIET, product="*")["html"]
        self.assertNotIn("var(--ok)", html)
        self.assertIn("var(--warn)", html)

    def test_headline_tells_when_the_product_is_stopped(self):
        out = self.run_page(HEADLINE, product="*")
        self.assertIn("Todo en marcha", out["fine"]["head"])
        self.assertIn("Todo en marcha", out["joint_fine"]["head"])
        self.assertIn("Parado: el orquestador no responde", out["down"]["head"])
        self.assertEqual(out["down"]["ico"], "var(--bad)")
        self.assertIn("Parado: está en pausa", out["paused"]["head"])
        self.assertIn("Parado: no ha empezado", out["none"]["head"])
        self.assertIn("Parado: no ha empezado", out["inactive"]["head"])
        for k in ("down", "paused", "none", "inactive"):
            self.assertNotIn("Todo en marcha", out[k]["head"])
            self.assertNotIn("con avisos", out[k]["head"])

    def test_headline_of_all_products_counts_the_stopped_ones(self):
        out = self.run_page(HEADLINE, product="*")
        self.assertIn("Parado: el orquestador no responde", out["joint_one"]["head"])
        self.assertIn("A", out["joint_one"]["head"])
        self.assertIn("2 productos parados", out["joint_two"]["head"])

    def test_a_down_orchestrator_is_in_what_needs_you(self):
        out = self.run_page(HEADLINE, product="*")
        self.assertIn("Orquestador no vivo", out["down"]["notices"])
        self.assertIn("Te necesita", out["down"]["notices"])
        self.assertIn("Sin orquestador", out["none"]["notices"])
        self.assertNotIn("Sin orquestador", out["inactive"]["notices"])
        self.assertEqual(out["fine"]["notices"], "")


if __name__ == "__main__":
    unittest.main()
