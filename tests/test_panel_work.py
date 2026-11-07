"""«Trabajo»: two lines per agent (what it does in words, then its state and last words), no empty branch groups,
repos with nothing open in one line, and the progress of the last hour next to the agents."""

import unittest

import test_panel_stuck
from test_panel import PanelCase

SCENARIO = """
const strip = h => h.replace(/<[^>]*>/g, ' ').replace(/\\s+/g, ' ').trim();
const mk = (x) => ({id: 'a1', p: 'fake', name: 'dev7', type: 'dev', status: 'working', activity_ts: 100,
  end: null, ...x});
BL = [{id: 'AP-7', p: 'fake', title: 'Avisar al cliente', subs: [{id: 'T2', title: 'Parte dos'}]}];
const out = {};
out.sub = strip(agentWhat(mk({issue: 'AP-7', sub: 'T2'})).text);
out.issue = agentWhat(mk({issue: 'AP-7'})).text;
out.none = agentWhat(mk({issue: null, type: 'foreman:dev', task: 'You are revqb, review agent of'})).text;
out.rev = agentWhat(mk({issue: null, type: 'reviewer'})).text;
out.odd = agentWhat(mk({issue: null, type: 'zeta'})).text;
out.state = strip(agentState(mk({status: 'maybe stuck',
  last: '**Prueba** de 5.000 envíos: 0 perdidos\\nmás'})));
out.fin = strip(agentState(mk({status: 'finished', end: 'x', last: ''})));
lastData = {joint: false};
const d = {agents: [mk({issue: null, type: 'planner', task: 'You are'})], notes: [], progress: [], repos: [
  {p: 'fake', key: 'app', branches: [], today: [], prs: [], github: 'o/app'},
  {p: 'fake', key: 'api', branches: [], today: [], prs: null},
  {p: 'fake', key: 'web', github: 'o/web', today: [],
   prs: [{number: 3, title: 'T', branch: 'b', checks: {}}],
   branches: [{branch: 'x', col: 'working', subject: 's', committed_ts: 1}]}]};
renderWork(d);
out.agents = strip($('agents').innerHTML);
out.branches = $('branches').innerHTML;
out.prs = $('prs').innerHTML;
out.worklast = $('worklast').innerHTML;
return out;
"""


class WorkViewTest(PanelCase):
    run_page = test_panel_stuck.StuckTest.run_page

    def test_agent_lines_and_repos(self):
        out = self.run_page(SCENARIO, tab="work")
        self.assertEqual(out["sub"], "Parte dos")
        self.assertEqual(out["issue"], "Avisar al cliente")
        self.assertEqual(out["none"], "Agente de producción")
        self.assertEqual(out["rev"], "Agente de revisión")
        self.assertEqual(out["odd"], "Agente «zeta»")
        self.assertIn("posible atasco · sin actividad hace", out["state"])
        self.assertTrue(out["state"].endswith("Prueba de 5.000 envíos: 0 perdidos"), out["state"])
        self.assertEqual(out["fin"], "acabado")
        self.assertIn("Agente de planes", out["agents"])
        self.assertNotIn("You are", out["agents"])
        self.assertNotIn("Ninguna", out["branches"])
        self.assertEqual(out["branches"].count('class="grp'), 1)  # only «En curso»
        self.assertIn(
            "app, api: nada abierto", out["branches"].replace("</span>", "").replace('<span class="ptag">fake', "")
        )
        self.assertNotIn("Ningún PR", out["prs"])
        self.assertEqual(out["prs"].count("<h2"), 1)  # only the repo with a PR
        self.assertIn("Nada en la última hora", out["worklast"])


if __name__ == "__main__":
    unittest.main()
