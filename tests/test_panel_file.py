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
        self.assertEqual(self.cli("check").returncode, 0)
        dec = {x["id"]: x for x in self.req("/api/state?p=fake")[1]["project"]["decisions"]}
        j2 = dec["Area J2"]
        self.assertEqual(j2["text"], long.strip())
        self.assertGreater(len(j2["text"]), 600)
        self.assertEqual(j2["details"], {"A: bottom": {"effect": "one more order on screen", "undo": "yes"}})
        self.assertEqual(j2["unanswered"], {"waits": "the export stays as it is", "by": "2026-11-12"})
        self.assertEqual(j2["page"], page)
        self.assertEqual(
            sorted(dec["Area J1"]),
            sorted(["id", "title", "text", "lead", "recommendation", "recommended", "options", "date", "answer"]),
        )
        # its page is a document the panel shows
        self.assertEqual(self.req(f"/api/doc?p=fake&path={page}")[0], 200)

    def test_page_shows_the_extras_only_when_there(self):
        js = (ROOT / "panel/app.js").read_text()
        more = js[js.index("const decisionMore") : js.index("// the «other answer» boxes")]
        self.assertIn("x.answer || !(hasDetails(x) || (x.unanswered && x.unanswered.waits) || x.page) ? ''", more)
        dec = js[js.index("  dec(key){") : js.index("  msg(key, obj){")]
        self.assertIn("(hasDetails(x) ? sect(t('dec_effects')", dec)
        self.assertIn('(x.page ? `<div id="mpage"', dec)
        self.assertNotIn("dec_page", dec)
        # options and recommendation once: in the top table only without details, below only with them
        self.assertIn("...(hasDetails(x) ? [] : [[t('options')", dec)
        self.assertIn('<div class="drec">', dec)
        self.assertIn("mdFull(r.md, x.p, r.path)", dec)
        for lang in ("en", "es"):
            st = self.req(f"/api/strings?lang={lang}")[1]
            for k in ("dec_effects", "undo_yes", "undo_hard", "undo_no", "dec_waits_by", "dec_see_page"):
                self.assertIn(k, st)

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
