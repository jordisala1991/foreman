"""The backlog on a phone: the title of an issue gets its own line and may wrap, the code, project and
sub-issues go in grey under it; the «no priority» dash is gone; «Hecho» starts folded and «Por clasificar» comes
before it. app.js runs in node over a minimal DOM (tests/panel_dom.js); a layout rule is read from the CSS, since
node has no layout (the screenshots of the report show it)."""

import re
import unittest

import test_panel_stuck
from helpers import ROOT
from test_panel import PanelCase

ROW = """
const c = {id: 'AB-1', title: 'A very long title that on a phone used to be cut to ten letters', lane: 'ready',
  priority: 'none', p: 'fake', eid: 'E1', epic: 'E1', epic_t: 'Panel', agent: '', plan: [], subs: [],
  production: false};
const hi = {...c, id: 'AB-2', priority: 'high'};
return {none: storyRow(c, false, true), high: storyRow(hi, false, true),
  folded: ['done', 'triage', 'canceled', 'backlog', 'ready'].map(l => isFolded('all', l)), lanes: LANES};
"""


class BacklogRowsTest(PanelCase):
    run_page = test_panel_stuck.StuckTest.run_page

    def test_no_priority_has_no_dash(self):
        out = self.run_page(ROW)
        self.assertNotIn("<path", re.search(r'<svg class="prio.*?</svg>', out["none"]).group(0))
        self.assertIn("<rect", re.search(r'<svg class="prio.*?</svg>', out["high"]).group(0))

    def test_row_is_marked_for_the_phone_layout(self):
        out = self.run_page(ROW)
        self.assertIn('class="lrow story', out["none"])

    def test_done_is_folded_and_triage_comes_before_it(self):
        out = self.run_page(ROW)
        self.assertEqual(out["folded"], [True, True, True, False, False])
        lanes = out["lanes"]
        self.assertLess(lanes.index("triage"), lanes.index("done"))

    def test_css_puts_the_title_on_its_own_line_on_a_phone(self):
        css = (ROOT / "panel/app.css").read_text()
        m = re.search(r"@media\(max-width:699px\)\{\n\.lrow\.story\{[^}]*grid.*?\n\}\n", css, re.S)
        self.assertTrue(m, "no phone rule for .lrow.story")
        block = m.group(0)
        self.assertRegex(block, r"\.lrow\.story \.tt\{[^}]*white-space:normal")
        self.assertRegex(block, r"\.lrow\.story \.prio\{display:none")


if __name__ == "__main__":
    unittest.main()
