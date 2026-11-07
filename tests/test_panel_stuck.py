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
