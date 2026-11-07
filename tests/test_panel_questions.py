"""Questions in the «Tú» list and their detail: the code leaves the title and goes small at the end of the row, the
recommended option says «Recomendada» (no bare ★), and a date is always the short «12 nov»."""

import unittest

import test_panel_stuck
from test_panel import PanelCase

ROWS = """
const mk = (title, extra = {}) => ({p: 'fake', title, id: 'Q', options: ['A: a', 'B: b'],
  recommended: 'B: b', text: 'Situation', unanswered: {by: '2026-11-12', waits: 'it waits'}, ...extra});
const strip = h => h.replace(/<[^>]*>/g, ' ').replace(/\\s+/g, ' ').trim();
const row = x => { lastData = {decisions: [x], ps: [], joint: false}; return decCardRich(x); };
const out = {};
for (const [k, title] of Object.entries({dec: 'DEC J5, ¿Arreglo en la app?', ap: 'AP-7 J2, ¿Mandamos?',
    colon: 'AB-1 J3: ¿Sí?',
    plain: '¿Sin código?', only: 'DEC J5,', lower: 'Dec j5, ¿Hola?', word: 'JSON J5 rules'})){
  const h = row(mk(title));
  out[k] = {title: (/<span>([^<]*)<\\/span>/.exec(h) || [])[1], code: (/class="dcode">([^<]*)</.exec(h) || [])[1]};
}
const h = row(mk('DEC J5, ¿Arreglo?'));
out.rec = strip(h); out.star = h.includes('★');
out.old = row(mk('DEC J5, Q', {details: undefined, recommendation: undefined}));
out.detail = decDetailRich(mk('DEC J5, Q'));
out.detail_head = dhead('k', esc(titleParts(mk('DEC J5, ¿Q?')).text) + codeTag(mk('DEC J5, ¿Q?')));
out.detail_raw = decDetailRich(mk('Q', {unanswered: {by: 'pronto'}}));
return out;
"""


class QuestionRowsTest(PanelCase):
    run_page = test_panel_stuck.StuckTest.run_page

    def test_code_leaves_the_title_and_goes_to_the_end_of_the_row(self):
        out = self.run_page(ROWS, tab="you")
        self.assertEqual(out["dec"], {"title": "¿Arreglo en la app?", "code": "DEC J5"})
        self.assertEqual(out["ap"], {"title": "¿Mandamos?", "code": "AP-7 J2"})
        self.assertEqual(out["colon"], {"title": "¿Sí?", "code": "AB-1 J3"})
        for k, title in (
            ("plain", "¿Sin código?"),
            ("only", "DEC J5,"),
            ("lower", "Dec j5, ¿Hola?"),
            ("word", "JSON J5 rules"),
        ):
            self.assertEqual(out[k], {"title": title}, k)
        head, _, tail = out["rec"].partition("¿Arreglo?")
        self.assertTrue(tail.strip().startswith("DEC J5"), "the code comes after the title")
        self.assertEqual(head.strip(), "")

    def test_recommended_is_written_out_and_the_row_is_one_design(self):
        out = self.run_page(ROWS, tab="you")
        self.assertIn("B: b Recomendada", out["rec"])
        self.assertFalse(out["star"])
        self.assertNotIn("(recomendada)", out["old"])
        self.assertIn('class="olines"', out["old"])

    def test_dates_are_short_in_the_row_and_in_the_detail(self):
        out = self.run_page(ROWS, tab="you")
        self.assertIn("Te lo recuerdo el 12 nov.", out["detail"].replace("<", " <"))
        self.assertNotIn("2026-11-12", out["detail"])
        self.assertIn("pronto", out["detail_raw"])
        self.assertIn("DEC J5", out["detail_head"])


if __name__ == "__main__":
    unittest.main()
