"""The panel's /api/file (the images a document shows) and a decision's extra fields in the panel: what each option
changes, what waits until the answer and its page. /api/file serves a PNG, JPEG, GIF or WebP from the folders of
documents, with the panel's key, whose first bytes are its format's, up to FILE_MAX, never as another type nor as a
page; everything else is a 404. A decision recorded before carries the same keys as before.
"""

import json
import os
import shutil
import subprocess
import unittest

from helpers import ROOT
from test_panel import PanelCase

from foreman import panel
from foreman.backlog import Backlog

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 40
GIF = b"GIF89a" + b"\x00" * 40
WEBP = b"RIFF\x20\x00\x00\x00WEBPVP8 " + b"\x00" * 40
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


class PanelFileTest(PanelCase):
    def setUp(self):
        super().setUp()
        self.q = self.home / "reviews/2026-11-05-PF-1-J2-question"
        self.q.mkdir(parents=True)
        for name, data in (("a.png", PNG), ("b.jpg", JPEG), ("c.JPEG", JPEG), ("d.gif", GIF), ("e.webp", WEBP)):
            (self.q / name).write_bytes(data)

    def get(self, path, token=True, headers=None):
        """(status, headers, body) of /api/file for `path` in product «fake»."""
        import urllib.error
        import urllib.parse
        import urllib.request

        url = f"http://127.0.0.1:{self.port}/api/file?p=fake&path={urllib.parse.quote(str(path))}"
        if token:
            url += "&t=" + self.token
        r = urllib.request.Request(url, headers=headers or {})
        try:
            with urllib.request.urlopen(r, timeout=20) as resp:
                return resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()

    def test_good_images(self):
        rel = "reviews/2026-11-05-PF-1-J2-question"
        for name, ctype, data in (
            ("a.png", "image/png", PNG),
            ("b.jpg", "image/jpeg", JPEG),
            ("c.JPEG", "image/jpeg", JPEG),
            ("d.gif", "image/gif", GIF),
            ("e.webp", "image/webp", WEBP),
        ):
            code, h, body = self.get(f"{rel}/{name}")
            self.assertEqual((code, h["Content-Type"], body), (200, ctype, data), name)
            self.assertEqual(h["X-Content-Type-Options"], "nosniff")
            self.assertEqual(h["Content-Security-Policy"], "default-src 'none'; sandbox")
            self.assertEqual(h["Cache-Control"], "private, max-age=300")
            self.assertIsNone(h["Content-Encoding"])  # never gzipped
        # the same image again, with its ETag: 304
        code, h, _ = self.get(f"{rel}/a.png")
        self.assertEqual(self.get(f"{rel}/a.png", headers={"If-None-Match": h["ETag"]})[0], 304)
        # an absolute path under the folders of documents of a registered product
        self.assertEqual(self.get(self.q / "a.png")[0], 200)

    def test_refusals(self):
        rel = "reviews/2026-11-05-PF-1-J2-question"
        # without the key, or with another: 403
        self.assertEqual(self.get(f"{rel}/a.png", token=False)[0], 403)
        # outside the folders of documents: «..», an absolute path elsewhere, the home itself
        (self.home / "x.png").write_bytes(PNG)
        outside = self.tmp / "outside.png"
        outside.write_bytes(PNG)
        # a symbolic link under reviews/ that leads out
        os.symlink(outside, self.q / "link.png")
        # not an image the panel serves: SVG, a document, no extension
        (self.q / "f.svg").write_bytes(SVG)
        (self.q / "g").write_bytes(PNG)
        # a PNG name over other bytes (an SVG, an HTML page), and a JPEG named .png
        (self.q / "h.png").write_bytes(SVG)
        (self.q / "i.png").write_bytes(b"<html><script>alert(1)</script></html>")
        (self.q / "j.png").write_bytes(JPEG)
        # over 5 MB
        (self.q / "k.png").write_bytes(PNG + b"\x00" * panel.FILE_MAX)
        for bad in (
            f"{rel}/../../x.png",
            "../x.png",
            "x.png",
            str(outside),
            "/etc/passwd",
            f"{rel}/link.png",
            f"{rel}/f.svg",
            "reviews/2026-10-30-plan.md",
            f"{rel}/g",
            f"{rel}/h.png",
            f"{rel}/i.png",
            f"{rel}/j.png",
            f"{rel}/k.png",
            f"{rel}/missing.png",
            f"{rel}/a.png\0.md",
            "",
        ):
            code, _, body = self.get(bad)
            self.assertEqual(code, 404, bad)
            self.assertEqual(json.loads(body), {"error": "not found"}, bad)  # without saying why
        # another product's name, or none
        self.assertEqual(self.req(f"/api/file?p=nobody&path={rel}/a.png")[0], 404)
        self.assertEqual(self.req(f"/api/file?path={rel}/a.png")[0], 404)
        # /api/doc keeps its rule (home_file with .md)
        self.assertEqual(self.req("/api/doc?p=fake&path=reviews/2026-10-30-plan.md")[0], 200)
        self.assertEqual(self.req(f"/api/doc?p=fake&path={rel}/a.png")[0], 404)


class PanelDecisionFieldsTest(PanelCase):
    def test_decision_extras_in_the_state(self):
        """A decision with effects, what waits and a page carries them; one recorded before carries the same keys
        as before; the text goes whole (the detail shows it all, the card only its lead)."""
        page = "reviews/2026-11-05-PF-1-J2-question/question.md"
        (self.home / page).parent.mkdir(parents=True)
        (self.home / page).write_text("# Question\n\n![before](before.png)\n")
        long = "Long context. " * 60
        b = Backlog(self.project)
        with b.mutate() as data:
            b.add_decision(data, "Area J2", "Where does the notice go?", long, ["A: bottom", "B: menu"])
            d = data["decisions"][-1]
            d["details"] = {"A: bottom": {"effect": "one more order on screen", "undo": "yes"}}
            d["unanswered"] = {"waits": "the export stays as it is", "by": "2026-11-12"}
            d["page"] = page
            d["blocks"] = ["PF-1 T1", "PF-3", "PF-2"]  # PF-2 is closed below: it holds nothing any more
            lst, n, _ = Backlog.find(data, "PF-2")
            lst[n]["status"] = "canceled"
            data["decisions"][0]["blocks"] = ["PF-3"]  # Area J1, without details
        self.assertEqual(self.cli("check").returncode, 0)
        dec = {x["id"]: x for x in self.req("/api/state?p=fake")[1]["project"]["decisions"]}
        j2 = dec["Area J2"]
        self.assertEqual(j2["text"], long.strip())
        self.assertGreater(len(j2["text"]), 600)
        self.assertEqual(j2["details"], {"A: bottom": {"effect": "one more order on screen", "undo": "yes"}})
        self.assertEqual(j2["unanswered"], {"waits": "the export stays as it is", "by": "2026-11-12"})
        self.assertEqual(j2["page"], page)
        # the open work it holds, with its names (a sub-issue without a title, its issue's)
        self.assertEqual(j2["holds"], [{"ref": "PF-1 T1", "title": "Issue 1"}, {"ref": "PF-3", "title": "Issue 3"}])
        self.assertEqual(
            sorted(dec["Area J1"]),
            sorted(
                ["id", "title", "text", "lead", "recommendation", "recommended", "options", "date", "answer", "holds"]
            ),
        )
        # its page is a document the panel shows
        self.assertEqual(self.req(f"/api/doc?p=fake&path={page}")[0], 200)

    def test_page_shows_the_extras_only_when_there(self):
        js = (ROOT / "panel/app.js").read_text()
        for gone in ("hasDetails", "decisionActions", "decisionMore", "decWaits", "optLabel"):
            self.assertNotIn(gone, js)
        dec = js[js.index("  dec(key){") : js.index("  msg(key, obj){")]
        self.assertIn("decDetailRich(x)", dec)
        self.assertIn("mdFull(r.md.replace(", dec)
        you = js[js.index("function renderYou") : js.index("function renderGrants")]
        self.assertIn("card(decCardRich(x))", you)
        for lang in ("en", "es"):
            st = self.req(f"/api/strings?lang={lang}")[1]
            for k in ("dec_if_none", "dec_holds_n", "chip_undo_no", "dec_back"):
                self.assertIn(k, st)
            for k in ("dec_waits", "dec_see_page", "waits_answer", "recommended_mark"):
                self.assertNotIn(k, st)

    def rich(self, script):
        """The output of node running the panel's decision code with `script` (stubs for the rest of the page)."""
        node = shutil.which("node")
        if not node:
            self.skipTest("no node")
        js = (ROOT / "panel/app.js").read_text()
        part = "\n".join(
            js[js.index(a) : js.index(z)]
            for a, z in (
                ("const esc =", "const lsGet"),
                ("const decisionSent", "// ---- Trabajo"),
            )
        )
        strings = json.loads((ROOT / "panel/strings.en.json").read_text())
        stub = (
            f"const S = {json.dumps(strings)};\n"
            "function t(key, vars){ let s = S[key]; if(s == null) s = key;"
            " return String(s).replace(/\\{(\\w+)\\}/g, (m, k) => vars && vars[k] != null ? vars[k] : m); }\n"
            "const tn = (key, n, vars) => t(`${key}_${n===1 ? '1' : 'n'}`, {n, ...vars});\n"
            "const md = s => s, ptag = () => '', canWrite = () => true, decKey = x => `${x.p}|${x.title}`;\n"
            "const posted = [], sheet = [];\n"
            "const post = (path, p) => posted.push(path);\n"
            "const $ = id => id === 'csheet' ? null : {focus(){}, set onclick(f){ this.f = f; }};\n"
            "const document = {addEventListener(){}, body: {insertAdjacentHTML: (w, h) => sheet.push(h)}};\n"
            "let lastData = null;\n"
        )
        r = subprocess.run([node, "-e", stub + part + script], capture_output=True, text=True, check=False)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        return r.stdout

    def test_rich_question_layout(self):
        """A question with what each option changes: the card shows each option as one button with its name (★ the
        recommended one, ⊘ the one that cannot be undone); the detail tells the situation, what is at stake with the
        tasks it holds by name, one card per option in the order written with its own button, «Other answer» after
        them and the page folded; the «Asked» and «Status» rows are gone."""
        out = json.loads(
            self.rich("""
const x = {p: 'fake', id: 'AP-7 J1', title: 'AP-7 J1, How?', text: 'Situation.', lead: 'Situation.',
  options: ['A: app', 'B: mail', 'C: retry'], recommended: 'A: app', recommendation: 'almost as much as C',
  details: {'A: app': {effect: 'banner', undo: 'yes', cost: '2 days'}, 'B: mail': {effect: 'mail', undo: 'yes'},
            'C: retry': {effect: 'retries alone', undo: 'no', cost: 'bank annex'}},
  unanswered: {waits: 'nothing changes', by: '2026-11-12'}, page: 'reviews/q.md',
  holds: [{ref: 'AP-7', title: 'Warn the customer'}, {ref: 'AP-8 T2', title: 'Support panel'}]};
const easy = {...x, details: {'A: app': {effect: 'banner', undo: 'yes'}, 'B: mail': {effect: 'mail', undo: 'yes'},
                              'C: retry': {effect: 'retries', undo: 'yes'}}};
console.log(JSON.stringify({card: decCardRich(x), easy: decCardRich(easy), detail: decDetailRich(x)}));
""")
        )
        card, easy, detail = out["card"], out["easy"], out["detail"]
        # the card: the title, one situation line, each option one full-width button in the order written with its
        # letter and name, «Recommended» at the end of the recommended one, ⊘ before the one that cannot be undone
        # (which asks for the confirmation: data-undo="no"); no chips (they stay in the detail); a grey
        # line with what it holds and the reminder's day
        name = [card.index(f'<span class="olt">{o}</span>') for o in ("A: app", "B: mail", "C: retry")]
        self.assertEqual(name, sorted(name))
        self.assertEqual(card.count('<button class="sec oline"'), 3)
        self.assertEqual(card.count("★"), 0)
        self.assertEqual(card.count('class="orec"'), 1)
        self.assertLess(name[0], card.index('class="orec"'))
        self.assertLess(card.index('class="orec"'), name[1])
        self.assertEqual(card.count("⊘"), 1)
        self.assertLess(name[1], card.index("⊘"))
        self.assertLess(card.index("⊘"), name[2])
        self.assertEqual(card.count('data-undo="no"'), 1)
        self.assertEqual(easy.count('data-undo="yes"'), 3)
        self.assertNotIn("ochip", card)
        self.assertNotIn("2 days", card)
        self.assertNotIn("data-ch", card)
        self.assertIn('<div class="clead olead">Situation.</div>', card)
        self.assertIn('<div class="csub">holds 2 tasks · reminder on Nov 12</div>', card)
        self.assertNotIn("Why:", card)
        self.assertNotIn("Asked", detail)
        self.assertNotIn("waits for your answer", detail)
        self.assertLess(detail.index("Situation."), detail.index("If you do not answer: nothing changes"))
        self.assertIn("I will remind you on Nov 12.", detail)
        self.assertIn("Holds 2 tasks", detail)
        self.assertIn("Warn the customer", detail)
        self.assertIn("Support panel", detail)
        # the cards in the order written, each with its own button; the recommended one marked, its why inside
        self.assertLess(detail.index("A: app"), detail.index("B: mail"))
        self.assertLess(detail.index("B: mail"), detail.index("C: retry"))
        self.assertEqual([detail.count(f">Choose {k}</button>") for k in "ABC"], [1, 1, 1])
        self.assertEqual(detail.count('class="ocard rec"'), 1)
        self.assertEqual(detail.count("Why:"), 1)
        self.assertEqual(detail.count("almost as much as C"), 1)  # shown once
        self.assertEqual(detail.count("Situation."), 1)
        self.assertLess(detail.index("Choose C"), detail.index("Other answer"))
        self.assertLess(detail.index("Other answer"), detail.index("See more detail"))
        self.assertIn('<details class="sect dpage"', detail)

    def test_confirm_only_what_cannot_be_undone(self):
        """An option that can be undone (or with work) answers on one tap; one that cannot opens a sheet with what it
        changes and «Back» focused, and answers only with «Choose C»."""
        out = json.loads(
            self.rich("""
lastData = {decisions: [{p: 'fake', title: 'AP-7 J1, How?', options: ['A: app', 'C: retry'],
  details: {'A: app': {effect: 'banner', undo: 'yes'},
            'C: retry': {effect: 'retries alone', undo: 'no', cost: 'annex'}}}]};
const b = (op, undo) => ({dataset: {id: 'AP-7 J1', d: 'AP-7 J1, How?', op, p: 'fake', undo}});
decideOpt(b('A: app', 'yes')); decideOpt(b('B: mail', 'hard'));
const before = posted.length;
decideOpt(b('C: retry', 'no'));
const after = posted.length;
console.log(JSON.stringify({before, after, posted, sheet}));
""")
        )
        self.assertEqual(out["before"], 2)
        self.assertEqual(out["after"], 2)  # C waits for its confirmation
        self.assertIn("op=A%3A%20app", out["posted"][0])
        self.assertEqual(len(out["sheet"]), 1)
        sheet = out["sheet"][0]
        self.assertIn("C cannot be undone", sheet)
        self.assertIn("retries alone", sheet)
        self.assertIn("cost: annex", sheet)
        self.assertLess(sheet.index(">Back</button>"), sheet.index(">Choose C</button>"))
        self.assertIn('id="csback"', sheet)

    RICH = """
const x = {p: 'fake', id: 'AP-7 J1', title: 'AP-7 J1, How?', text: 'Situation.', lead: 'Situation.',
  options: ['A: app', 'B: mail', 'C: retry'], recommended: 'B: mail', recommendation: 'cheaper',
  details: {'A: app': {effect: 'banner', undo: 'yes'}, 'B: mail': {effect: 'mail', undo: 'yes'},
            'C: retry': {effect: 'retries alone', undo: 'no'}},
  unanswered: {waits: 'nothing changes', by: '2026-11-12'}, holds: [{ref: 'AP-7', title: 'Warn'}],
  answer: 'A: app'};
"""

    def test_answered_folds_and_change_brings_the_options_back(self):
        """Answered (until the orchestrator reads it): the card folds to «✓ You chose A: …» with «Change»; the detail
        keeps the situation, drops what is at stake, highlights the chosen card with «✓ Your choice», greys the
        others (the recommended one keeps only its label: no border, no why), has no option buttons and offers
        «Change answer». Changing brings the options back, each button marked as a change (data-ch), with «Keep my
        answer»; choosing one posts change=1 and closes the change."""
        out = json.loads(
            self.rich(
                self.RICH
                + """
const card = decCardRich(x), detail = decDetailRich(x), free = decCardRich({...x, answer: 'neither', free: true});
CHANGE_OPEN.add(freeKey(x));
const ccard = decCardRich(x), cdetail = decDetailRich(x);
answerOpt({dataset: {id: 'AP-7 J1', d: 'AP-7 J1, How?', op: 'B: mail', p: 'fake', ch: '1', fk: freeKey(x)}});
console.log(JSON.stringify({card, detail, ccard, cdetail, free, posted, open: CHANGE_OPEN.size}));
"""
            )
        )
        card, detail, ccard, cdetail = out["card"], out["detail"], out["ccard"], out["cdetail"]
        self.assertIn("You chose <b>A: app</b>", card)
        self.assertIn('onclick="changeToggle(this)">Change</button>', card)
        self.assertNotIn("oline", card)
        self.assertNotIn("Situation.", card)
        self.assertNotIn("holds", card)
        self.assertIn("You answered in your words: «neither»", out["free"])
        # the detail, answered
        self.assertIn("Situation.", detail)
        self.assertNotIn("If you do not answer", detail)
        self.assertNotIn("Holds 1 task", detail)
        self.assertEqual(detail.count('class="ocard chosen"'), 1)
        self.assertEqual(detail.count('class="ocard dim"'), 2)
        self.assertLess(detail.index('class="ocard chosen"'), detail.index("Your choice"))
        self.assertLess(detail.index("Your choice"), detail.index("B: mail"))
        self.assertEqual(detail.count(">Recommended</span>"), 1)
        self.assertNotIn("Why:", detail)
        self.assertNotIn("cheaper", detail)
        self.assertNotIn("Choose", detail)
        self.assertNotIn("Other answer", detail)
        self.assertIn(">Change answer</button>", detail)
        # changing: the options are back, as changes
        self.assertEqual(ccard.count('<button class="sec oline"'), 3)
        self.assertEqual(ccard.count('data-ch="1"'), 3)
        self.assertIn(">Keep my answer</button>", ccard)
        self.assertEqual([cdetail.count(f">Choose {k}</button>") for k in "ABC"], [1, 1, 1])
        self.assertEqual(cdetail.count('data-ch="1"'), 3)
        self.assertNotIn("ocard dim", cdetail)
        self.assertIn('class="ocard rec"', cdetail)
        self.assertIn("Your choice", cdetail)
        self.assertIn("Other answer", cdetail)
        self.assertIn(">Keep my answer</button>", cdetail)
        self.assertEqual(len(out["posted"]), 1)
        self.assertTrue(out["posted"][0].endswith("op=B%3A%20mail&change=1"))
        self.assertEqual(out["open"], 0)

    def test_question_without_details(self):
        """A question recorded without what each option changes (details missing) is told in the same layout, with
        an empty effect: neither its card nor its detail throws (F1 of the legacy review)."""
        out = json.loads(
            self.rich("""
const x = {p: 'fake', id: 'J1', title: 'J1, Ship?', text: 'Context.', options: ['a: yes', 'b: no'],
  recommended: 'b: no', recommendation: 'b', details: undefined};
const FREE = [];
console.log(JSON.stringify({card: decCardRich(x), detail: decDetailRich(x)}));
""")
        )
        self.assertEqual(out["card"].count('<button class="sec oline"'), 2)
        self.assertEqual(out["detail"].count('<div class="oeff"></div>'), 2)
        self.assertEqual(out["detail"].count('onclick="decideOpt(this)"'), 2)
        self.assertIn('data-undo=""', out["detail"])

    def test_document_images(self):
        """mdInline with a document's options: a relative image goes to /api/file joined to the document's folder;
        one from outside is never loaded (a link, or its text); without them (the wiki), as before."""
        node = shutil.which("node")
        if not node:
            self.skipTest("no node")
        js = (ROOT / "panel/app.js").read_text()
        part = "\n".join(
            js[js.index(a) : js.index(z)]
            for a, z in (
                ("const esc =", "const lsGet"),
                ("const api =", "// ---- Texts"),
                ("const DOC_OPTS", "// splits a table row"),
                ("const dirOf", "// a whole document"),
            )
        )
        script = (
            "const T = 'k', WIKI = {}, wikiLink = () => '', wikiTitle = n => n;\n"
            + part
            + """
const o = docOpts('fake', dirOf('reviews/q/question.md'));
console.log(JSON.stringify([
  mdInline('![before](before.png)', o),
  mdInline('![up](../shots/x.png)', o),
  mdInline('![far](../../../x.png)', o),
  mdInline('![ext](https://example.com/x.png)', o),
  mdInline('![data](data:image/png;base64,AAA)', o),
  mdInline('![svg](javascript:alert%281%29)', o),
  mdInline('![old](before.png)', DOC_OPTS),
  docOpts('fake', dirOf('/abs/reviews/q.md')).img('a.png', 'a'),
]));
"""
        )
        out = json.loads(subprocess.run([node, "-e", script], capture_output=True, text=True, check=True).stdout)
        src = "/api/file?p=fake&amp;path=reviews%2Fq%2Fbefore.png&amp;t=k"
        self.assertEqual(
            out[0],
            f'<a class="dimg" href="{src}" target="_blank" rel="noopener">'
            f'<img loading="lazy" src="{src}" alt="before"></a>',
        )
        self.assertIn("path=reviews%2Fshots%2Fx.png", out[1])
        self.assertEqual(out[2], "far")
        self.assertEqual(out[3], '<a href="https://example.com/x.png" target="_blank" rel="noopener">ext</a>')
        self.assertEqual((out[4], out[5]), ("data", "svg"))
        self.assertEqual(out[6], "!old")  # without docOpts: as before
        self.assertIn("path=%2Fabs%2Freviews%2Fa.png", out[7])


if __name__ == "__main__":
    unittest.main()
