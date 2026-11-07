"""The new panel (/next/): behind the product's [panel] next switch and the panel's key, files only from the
allow-list of panel/next/, the old page (/) untouched, no request off the machine, the copied libraries checked
against their MANIFEST, and the shell and the «How it goes» screen mounted in node over a minimal DOM
(tests/next_dom.mjs; skipped without node)."""

import gzip
import hashlib
import http.client
import json
import re
import shutil
import subprocess
from typing import ClassVar
from urllib.parse import quote

from helpers import ROOT, ProjectCase, config_problems
from test_panel import PanelCase

from foreman import panel
from foreman.panel_cache import STATIC, page_version

NEXT = ROOT / "panel/next"
VENDOR = NEXT / "vendor"
# the URLs the copied libraries carry: XML namespaces (never fetched) and their licence banners (comments)
VENDOR_URLS = {
    "http://www.w3.org/1998/Math/MathML",
    "http://www.w3.org/1999/xhtml",
    "http://www.w3.org/2000/svg",
    "https://www.chartjs.org",
    "https://github.com/kurkle/color#readme",
}
URL = re.compile(r"""https?://[^\s"'`)<>]+""")


def costs_line(issue, end, usd_by_model):
    models = {m: {"tokens": 1000, "usd": u} for m, u in usd_by_model.items()}
    return {"agent": f"{issue}-{end}", "issue": issue, "end": end, "usd": sum(usd_by_model.values()), "models": models}


class NextConfigTest(PanelCase):
    def test_next_is_off_by_default_and_must_be_a_bool(self):
        self.assertIs(self.other.section("panel")["next"], False)
        self.assertFalse([e for e in config_problems({"panel": {"next": True}}) if "next" in e])
        errs = config_problems({"panel": {"next": "yes"}})
        self.assertIn("[panel] next must be true or false", errs)


class NextServeTest(PanelCase):
    def setUp(self):
        super().setUp()
        self.write_toml(self.project_toml() + "\n[panel]\nnext = true\n")
        panel.REG.__init__()

    def project_toml(self):
        return (self.home / "foreman.toml").read_text()

    def raw(self, path):
        """(status, headers, body) of a GET sent as written: no client normalises `..` away."""
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        c.request("GET", path)
        r = c.getresponse()
        out = r.status, dict(r.getheaders()), r.read()
        c.close()
        return out

    def test_the_index_needs_the_key(self):
        self.assertEqual(self.raw("/next/?t=bad")[0], 403)
        self.assertEqual(self.raw("/next/app.js?t=bad")[0], 403)
        code, _, body = self.raw(f"/next/?t={self.token}")
        self.assertEqual(code, 200)
        html = body.decode()
        v = panel.next_version()
        # every file the page loads carries the key and the version, the import map too
        self.assertIn(f'"preact": "/next/vendor/preact.mjs?v={v}&t={quote(self.token)}"', html)
        self.assertIn(f"/next/app.css?v={v}&t={quote(self.token)}", html)
        self.assertNotIn("{{", html)

    def test_next_without_slash_moves_to_the_index_with_its_query(self):
        code, headers, _ = self.raw(f"/next?t={self.token}")
        self.assertEqual(code, 301)
        self.assertEqual(headers["Location"], f"/next/?t={self.token}")

    def test_only_the_allow_list_is_served(self):
        for path in (
            "/next/../lib/foreman/panel.py",
            "/next/%2e%2e/lib/foreman/panel.py",
            "/next/vendor/MANIFEST",
            "/next/vendor/LICENSES/preact.txt",
            "/next/index.html",
            "/next/nothing.js",
            "/next//etc/passwd",
        ):
            self.assertEqual(self.raw(f"{path}?t={self.token}")[0], 404, path)
        code, headers, body = self.raw(f"/next/vendor/preact.mjs?t={self.token}")
        self.assertEqual(code, 200)
        self.assertEqual(headers["Content-Type"], "text/javascript; charset=utf-8")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(body, (VENDOR / "preact.mjs").read_bytes())
        code, headers, _ = self.raw(f"/next/strings.es.json?t={self.token}&v={panel.next_version()}")
        self.assertEqual((code, headers["Content-Type"]), (200, "application/json; charset=utf-8"))
        self.assertIn("immutable", headers["Cache-Control"])

    def test_the_old_page_is_the_same_byte_for_byte(self):
        _, _, body = self.raw(f"/?t={self.token}")
        old = (
            (STATIC / "index.html")
            .read_text()
            .replace("{{T}}", quote(self.token))
            .replace("{{V}}", str(page_version()))
            .replace("{{LANG}}", "es")
        )
        self.assertEqual(body.decode(), old)
        # and its version does not move with the new panel's files
        old_files = ["index.html", "app.js", "app.css"]
        self.assertEqual(page_version(), int(max((STATIC / n).stat().st_mtime for n in old_files)))

    def test_projects_say_which_have_the_new_panel(self):
        code, d = self.req("/api/projects")
        self.assertEqual(code, 200)
        self.assertEqual({p["name"]: p.get("next") for p in d["projects"]}, {"fake": True, "other": False})

    def test_metrics_only_for_products_with_the_new_panel(self):
        from foreman.backlog import Backlog

        f = Backlog(self.project).data_path.parent / "costs.jsonl"
        f.write_text(json.dumps(costs_line("PF-1", "2026-11-07 10:00", {"claude-opus-4-7": 2.0})) + "\n")
        code, d = self.req("/api/metrics?p=fake&days=30")
        self.assertEqual(code, 200, d)
        self.assertEqual(len(d["days"]), 30)
        self.assertEqual(d["since"], "2026-11-07")
        self.assertEqual(self.req("/api/metrics?p=other&days=7")[0], 404)
        self.assertEqual(self.req("/api/metrics?p=fake&days=5")[0], 400)


class NextFilesTest(PanelCase):
    def own_files(self):
        return [f for f in NEXT.rglob("*") if f.is_file() and VENDOR not in f.parents]

    def test_no_request_off_the_machine(self):
        for f in self.own_files():
            self.assertEqual(URL.findall(f.read_text()), [], f"{f.relative_to(ROOT)} names a URL")
        for f in VENDOR.glob("*.*js"):
            self.assertLessEqual(set(URL.findall(f.read_text())), VENDOR_URLS, f.name)
        # no font, stylesheet or script from elsewhere, and no @import
        css = (NEXT / "app.css").read_text()
        self.assertNotIn("@import", css)
        self.assertNotIn("@font-face", css)

    def test_vendor_matches_its_manifest_and_carries_licences(self):
        rows = [ln.split() for ln in (VENDOR / "MANIFEST").read_text().splitlines() if ln and not ln.startswith("#")]
        listed = {r[0] for r in rows}
        self.assertEqual(listed, {f.name for f in VENDOR.glob("*.*js")})
        for name, _package, _version, sha, licence in rows:
            self.assertEqual(hashlib.sha256((VENDOR / name).read_bytes()).hexdigest(), sha, name)
            self.assertTrue((VENDOR / "LICENSES" / licence).is_file(), licence)

    def test_the_whole_new_panel_gzipped_is_under_150_kb(self):
        served = [f for f in NEXT.rglob("*") if f.suffix in panel.NEXT_TYPES or f.name == "index.html"]
        total = sum(len(gzip.compress(f.read_bytes(), 9)) for f in served)
        self.assertLessEqual(total, 150 * 1024, total)


class NextPageTest(ProjectCase):
    """The shell and the «How it goes» screen, mounted in node (tests/next_dom.mjs) with the panel's own answers
    replaced by the scenario's."""

    def mount(self, scenario):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        f = self.tmp / "scenario.json"
        f.write_text(json.dumps(scenario))
        r = subprocess.run(
            [node, str(ROOT / "tests/next_dom.mjs"), str(NEXT), str(f)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout.strip().splitlines()[-1])

    PROJECTS: ClassVar[dict] = {
        "projects": [
            {"name": "alpha", "title": "Alpha", "next": True, "decisions": 2, "unread": 1, "agents": 3},
            {"name": "beta", "title": "Beta", "next": False, "decisions": 9, "unread": 0, "agents": 0},
        ],
        "language": "es",
    }

    def metrics(self, days):
        ds = [f"2026-10-{d:02d}" for d in range(9, 32)] + [f"2026-11-0{d}" for d in range(1, 8)]
        ds = ds[-days:]
        cost = {d: {"opus": 0, "sonnet": 0, "haiku": 0, "other": 0, "usd": 0} for d in ds}
        cost["2026-11-05"] = {"opus": 47.4, "sonnet": 0.7, "haiku": 0, "other": 0, "usd": 48.1}
        cost["2026-11-07"] = {"opus": 60.8, "sonnet": 13.0, "haiku": 0.5, "other": 0, "usd": 74.3}
        return {"days": ds, "cost": cost, "total": 122.4, "since": "2026-11-01"}

    def test_shell_and_cost_chart(self):
        answers = {"/api/projects": self.PROJECTS, "/api/metrics?p=alpha&days=7": self.metrics(7)}
        out = self.mount({"hash": "#/how", "answers": answers})
        text = out["text"]
        self.assertEqual(out["tabs"], ["Inicio", "Te necesita 3", "Agentes 3", "Cómo va", "Más"])
        self.assertEqual(out["badges"], {"needs": "3", "agents": "3"})
        self.assertEqual(out["products"], ["Alpha"])  # beta has the new panel off
        self.assertIn("Coste por día y modelo", text)
        self.assertIn("$122", text)  # the period's total
        self.assertIn("$61", text)  # the mean of the 2 days with work
        self.assertNotIn("datos desde", text)  # the 7 days start on the first day with data
        chart = out["charts"][-1]
        self.assertEqual(chart["type"], "bar")
        self.assertEqual(len(chart["labels"]), 7)
        sets = {s["label"]: s["data"] for s in chart["datasets"]}
        self.assertEqual(sets["Opus"][-1], 60.8)
        self.assertEqual(sets["Sonnet"][-1], 13.0)
        self.assertEqual(sets["Haiku"][-1], 0.5)
        self.assertNotIn("Otros", sets)  # a family with no cost in the period has no series
        self.assertTrue(chart["stacked"])
        self.assertEqual(out["fetched"], ["/api/projects", "/api/metrics?p=alpha&days=7"])

    def test_thirty_days(self):
        out = self.mount(
            {
                "hash": "#/how",
                "click": "30 días",
                "answers": {
                    "/api/projects": self.PROJECTS,
                    "/api/metrics?p=alpha&days=7": self.metrics(7),
                    "/api/metrics?p=alpha&days=30": self.metrics(30),
                },
            }
        )
        self.assertEqual(len(out["charts"][-1]["labels"]), 30)
        self.assertIn("datos desde el 1 nov", out["text"])  # the 30 days start before it
        stubs = next(d["data"] for d in out["charts"][-1]["datasets"] if d["label"] == "")
        self.assertEqual(stubs[:23], [None] * 23)  # before the data starts: no bar, it is not a day without work
        self.assertEqual(stubs[23:], [0, 0, 0, 0, None, 0, None])  # from 1 nov: a thin bar on days without cost

    def test_today_stays_on_the_axis_of_a_narrow_chart(self):
        answers = {"/api/projects": self.PROJECTS, "/api/metrics?p=alpha&days=30": self.metrics(30)}
        out = self.mount(
            {
                "hash": "#/how",
                "click": "30 días",
                "answers": {**answers, "/api/metrics?p=alpha&days=7": self.metrics(7)},
            }
        )
        ticks = out["ticks"][-1]
        self.assertEqual(ticks["narrow"][-1], "hoy")
        self.assertEqual(sum(1 for x in ticks["narrow"] if x), 10)  # one label in three, counted back from today
        self.assertTrue(all(ticks["wide"]))  # a wide chart shows every day

    def test_changing_the_period_drops_the_old_chart_until_the_answer_comes(self):
        out = self.mount(
            {
                "hash": "#/how",
                "click": "30 días",
                "hang": ["/api/metrics?p=alpha&days=30"],
                "answers": {"/api/projects": self.PROJECTS, "/api/metrics?p=alpha&days=7": self.metrics(7)},
            }
        )
        self.assertIn("Cargando", out["text"])
        self.assertNotIn("Coste por día", out["text"])

    def test_a_missing_chart_library_says_so(self):
        answers = {"/api/projects": self.PROJECTS, "/api/metrics?p=alpha&days=7": self.metrics(7)}
        out = self.mount({"hash": "#/how", "noChart": True, "answers": answers})
        self.assertIn("No se pudo cargar la gráfica", out["text"])
        self.assertIn("$122", out["text"])  # the figures are still there

    def test_no_product_with_the_new_panel_says_how_to_turn_it_on(self):
        none = {**self.PROJECTS, "projects": [{**p, "next": False} for p in self.PROJECTS["projects"]]}
        out = self.mount({"hash": "#/how", "answers": {"/api/projects": none}})
        self.assertIn("Ningún producto tiene el panel nuevo", out["text"])
        self.assertIn("next = true", out["text"])
        self.assertEqual(out["charts"], [])
