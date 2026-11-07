"""Three panel bugs that left the owner stuck, each with its test. The page's app.js runs whole in node over
a minimal DOM (tests/panel_dom.js) against a real panel server; a layout rule is read from the CSS, since node has no
layout (the screenshots of the report show it)."""

import json
import re
import shutil
import subprocess
import unittest

from helpers import ROOT
from test_panel import PanelCase

BACK = """
const x = {p: 'fake', title: 'Q', id: 'Q1', options: ['A: a', 'C: c'],
  details: {'C: c': {undo: 'no', effect: 'e', cost: 'c'}}};
lastData = {decisions: [x]};
const b = {dataset: {undo: 'no', p: 'fake', d: 'Q', op: 'C: c'}};
const open = () => { decideOpt(b); return !!dom.registry.csheet; };
const out = {opened: open()};
dom.registry.csback.click(); out.after_back = !!dom.registry.csheet;
open(); press('Escape'); out.after_escape = !!dom.registry.csheet;
open(); dom.registry.csheet.click(); out.after_outside = !!dom.registry.csheet;
return out;
"""
WIKI = """
await settle(1500);
return {html: $('wtree').innerHTML, count: $('wcount').textContent};
"""


class StuckTest(PanelCase):
    def run_page(self, scenario, product="fake", tab="now"):
        node = shutil.which("node")
        if not node:
            self.skipTest("no node")
        f = self.tmp / "scenario.js"
        f.write_text(scenario)
        base = f"http://127.0.0.1:{self.port}"
        args = [
            node,
            str(ROOT / "tests/panel_dom.js"),
            str(ROOT / "panel/app.js"),
            base,
            self.token,
            product,
            tab,
            str(f),
        ]
        r = subprocess.run(args, capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout.strip().splitlines()[-1])

    def test_back_closes_the_confirmation_of_an_option_without_undo(self):
        """«Back», a tap outside and Escape close the sheet of «C cannot be undone» (a second closeSheet, the
        backlog filter's, used to replace it)."""
        out = self.run_page(BACK)
        self.assertEqual(out, {"opened": True, "after_back": False, "after_escape": False, "after_outside": False})
        js = (ROOT / "panel/app.js").read_text()
        names = re.findall(r"^(?:async )?function (\w+)\(", js, re.M)
        self.assertEqual(
            [n for n in set(names) if names.count(n) > 1], [], "a function declared twice replaces the first"
        )

    def test_reopening_on_the_wiki_tab_shows_the_pages(self):
        """The panel left on the Wiki tab: once the data arrives it lists the pages, not the key «wiki_failed»."""
        (self.ohome / "wiki/models").mkdir()
        (self.ohome / "wiki/models/m-one.md").write_text(
            "---\ntype: model\nstatus: activo\n---\n# Model One\n\nText.\n"
        )
        out = self.run_page(WIKI, product="other", tab="wiki")
        self.assertNotIn("wiki_failed", out["html"])
        self.assertIn("Model One", out["html"])
        self.assertEqual(out["count"], "1 páginas")

    def test_a_wiki_page_deleted_while_the_list_is_cached_does_not_break_the_tab(self):
        """The file list is kept 5 s: a page deleted inside that window (or a list left by another test of the same
        product name) made /api/wiki raise and close the connection, which the page showed as «fetch failed»."""
        (self.ohome / "wiki/models").mkdir()
        keep, gone = self.ohome / "wiki/models/m-one.md", self.ohome / "wiki/models/m-gone.md"
        for f in (keep, gone):
            f.write_text("# " + f.stem + "\n")
        self.assertEqual(len(self.req("/api/wiki?p=other")[1]["pages"]), 2)
        gone.unlink()
        code, body = self.req("/api/wiki?p=other")
        self.assertEqual(code, 200)
        self.assertEqual([p["n"] for p in body["pages"]], ["m-one"])
        self.assertEqual(self.req("/api/wiki/page?p=other&n=m-gone")[0], 404)
        self.assertEqual(self.req("/api/wiki/search?p=other&q=gone")[1]["results"], [])

    def test_picker_rows_leave_the_product_name_room_on_a_phone(self):
        """In the product picker the line about the orchestrator goes under the name and may wrap, on a narrow
        screen; on the one line it pushed the name out of the row."""
        css = (ROOT / "panel/app.css").read_text()
        js = (ROOT / "panel/app.js").read_text()
        self.assertIn("cls: v===P ? 'prow on' : 'prow'", js)
        narrow = css[css.index("@media(max-width:699px){\n.lrow.prow") :]
        rule = narrow[: narrow.index("}\n}") + 1]
        self.assertIn(".lrow.prow .meta{", rule)
        self.assertIn("white-space:normal", rule)


if __name__ == "__main__":
    unittest.main()


STALE = """
const ts = Math.round(Date.now() / 1000);
const text = '# Informe\\n\\n| a | b |\\n|---|---|\\n| 1 | 2 |\\n\\n1. uno\\n2. dos\\n';
const out = {};
await settle(800);
for(const [k, age] of [['fresh', 600], ['old', 143 * 3600 + 19 * 60]]){
  const d = JSON.parse(JSON.stringify(lastData));
  d.ps = [Object.assign(d.ps[0], {report: {ts: ts - age, text}})];
  renderNow(d);
  out[k] = $('report').innerHTML;
}
out.durs = [dur(90), dur(3 * 3600 + 5 * 60), dur(30 * 3600), dur(6 * 86400 + 100)];
out.ago_old = ago(143.3);
return out;
"""


class ReportTest(PanelCase):
    run_page = StuckTest.run_page

    def test_report_is_formatted_and_old_one_warns(self):
        """The orchestrator's report is a full document (heading, table, numbered list); past 6 h its age is amber
        with «puede estar desfasado»; durations past 2 days read in days, shorter ones as before."""
        js = (ROOT / "panel/app.js").read_text()
        self.assertNotIn("md(p.report.text)", js)
        out = self.run_page(STALE)
        self.assertEqual(out["durs"], ["1 min", "3 h 5 min", "30 h 0 min", "6 días"])
        self.assertEqual(out["ago_old"], "hace 6 días")
        for k in ("fresh", "old"):
            self.assertIn("<h1", out[k])
            self.assertIn("<table", out[k])
            self.assertIn("<ol", out[k])
        self.assertIn("hace 10 min", out["fresh"])
        self.assertIn("data-turn=", out["fresh"])  # turns amber once 6 h pass
        self.assertNotIn('class="warn stale-age"', out["fresh"])
        self.assertIn('class="warn stale-age"', out["old"])
        self.assertIn("hace 6 días", out["old"])
        self.assertIn("puede estar desfasado", out["old"])
        self.assertNotIn("hace 143 h", out["old"])
