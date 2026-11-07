"""The You tab's permission requests: an orphan (of an orchestrator no longer running) has only «Discard», each
request says when it expires with its day, and one whose notice did not arrive says so. app.js runs in
node over a minimal DOM (tests/panel_dom.js)."""

import re

import test_panel_stuck
from test_panel import PanelCase

ROWS = """
const strip = h => h.replace(/<[^>]*>/g, ' ').replace(/\\s+/g, ' ').trim();
const iso = n => { const d = new Date(Date.now() + n * 86400000); return d.toLocaleDateString('sv') + 'T02:31:00'; };
const base = {p: 'fake', group: 'deploy', command: 'make deploy', summary: 'push', minutes: 30,
  at: iso(0), expires: iso(1)};
const render = list => { renderGrants({grants: list}); return $('grantreq').innerHTML; };
const live = render([{...base, pending: true, notified: 'no:no-surface'}]);
const ok = render([{...base, pending: true, notified: 'sent:terminal'}]);
const orphan = render([{...base, pending: false, orphan: 'process', expires: iso(0)}]);
return {live: strip(live), ok: strip(ok), orphan: strip(orphan), live_btns: live.match(/<button/g).length,
  orphan_btns: orphan.match(/<button/g).length, orphan_approve: /grantApprove/.test(orphan),
  orphan_discard: /grantDeny/.test(orphan)};
"""


class GrantRowsTest(PanelCase):
    run_page = test_panel_stuck.StuckTest.run_page

    def test_rows(self):
        out = self.run_page(ROWS, tab="you")
        self.assertIn("caduca mañana a las 02:31", out["live"])
        self.assertIn("lo pidió hoy a las 02:31", out["live"])
        self.assertIn("no se pudo avisar", out["live"])
        self.assertNotIn("no se pudo avisar", out["ok"])
        self.assertEqual(out["live_btns"], 2)  # yes and no
        self.assertIn("de un orquestador que ya no está en marcha", out["orphan"])
        self.assertEqual((out["orphan_btns"], out["orphan_approve"], out["orphan_discard"]), (1, False, True))
        self.assertIn("Descartar", out["orphan"])
        self.assertIsNone(re.search(r"\{[a-z]+\}", out["orphan"]))
