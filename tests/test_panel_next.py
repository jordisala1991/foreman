"""The new panel (/next/): behind the product's [panel] next switch and the panel's key, files only from the
allow-list of panel/next/, the old page (/) untouched, no request off the machine, the built files checked against
their source (panel/next/BUILD, without node), and the shell and the «How it goes» screen of the built bundle
mounted in node over a minimal DOM (tests/next_dom.mjs; skipped without node)."""

import gzip
import hashlib
import http.client
import json
import os
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
SRC = ROOT / "panel/next-src"
BUILT = {"index.html", "app.js", "app.css", "LICENSES.txt", "BUILD"}  # all that `npm run build` writes
SERVED = ("index.html", "app.js", "app.css")
# the URLs the bundled React carries: XML namespaces (never fetched) and the address its production errors name
BUNDLE_URLS = {
    "http://www.w3.org/1998/Math/MathML",
    "http://www.w3.org/1999/xlink",
    "http://www.w3.org/1999/xhtml",
    "http://www.w3.org/2000/svg",
    "http://www.w3.org/XML/1998/namespace",
    "https://react.dev/errors/",
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
        self.assertIn(f'<script type="module" src="/next/app.js?v={v}&t={quote(self.token)}">', html)
        self.assertIn(f'href="/next/app.css?v={v}&t={quote(self.token)}"', html)
        self.assertNotIn("{{", html)

    def test_next_without_slash_moves_to_the_index_with_its_query(self):
        code, headers, _ = self.raw(f"/next?t={self.token}")
        self.assertEqual(code, 301)
        self.assertEqual(headers["Location"], f"/next/?t={self.token}")

    def test_only_the_allow_list_is_served(self):
        for path in (
            "/next/../lib/foreman/panel.py",
            "/next/%2e%2e/lib/foreman/panel.py",
            "/next/BUILD",
            "/next/LICENSES.txt",
            "/next/index.html",
            "/next/vendor/preact.mjs",
            "/next/strings.es.json",
            "/next/nothing.js",
            "/next//etc/passwd",
        ):
            self.assertEqual(self.raw(f"{path}?t={self.token}")[0], 404, path)
        code, headers, body = self.raw(f"/next/app.js?t={self.token}")
        self.assertEqual(code, 200)
        self.assertEqual(headers["Content-Type"], "text/javascript; charset=utf-8")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(body, (NEXT / "app.js").read_bytes())
        code, headers, _ = self.raw(f"/next/app.css?t={self.token}&v={panel.next_version()}")
        self.assertEqual((code, headers["Content-Type"]), (200, "text/css; charset=utf-8"))
        self.assertIn("immutable", headers["Cache-Control"])
        self.assertEqual(set(panel.next_files()), {"app.js", "app.css"})

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
        self.assertEqual(d["since"]["cost"], "2026-11-07")
        self.assertEqual(self.req("/api/metrics?p=other&days=7")[0], 404)
        self.assertEqual(self.req("/api/metrics?p=fake&days=5")[0], 400)
        self.assertEqual(self.req("/api/metrics?p=fake&days=x")[0], 400)
        for k in ("deliveries", "reviews", "rounds", "guard", "guard_rules", "today", "top_issues", "by_type", "quota"):
            self.assertIn(k, d)
        self.assertEqual(d["quota"], [])

    def test_an_error_inside_metrics_is_not_a_400(self):
        from unittest import mock

        from foreman import metrics

        with mock.patch.object(metrics, "build", side_effect=ValueError("corrupt line")):
            code, _ = self.req("/api/metrics?p=fake&days=7")
        self.assertEqual(code, 500)


class NextFilesTest(PanelCase):
    def test_the_built_files_are_all_there_is(self):
        """panel/next/ holds only what the build writes: no copied libraries, no module served as written."""
        self.assertEqual({f.relative_to(NEXT).as_posix() for f in NEXT.rglob("*") if f.is_file()}, BUILT)

    def test_the_built_files_match_their_source(self):
        """BUILD (written by `npm run build`) holds the sha256 of every source file and of every file written; a
        source changed without a build, or a built file changed by hand, fails here, without node."""
        lines = [ln for ln in (NEXT / "BUILD").read_text().splitlines() if ln and not ln.startswith("#")]
        recorded = dict(reversed(ln.split(None, 1)) for ln in lines[1:])
        kind, source = lines[0].split()
        self.assertEqual(kind, "source")
        files = sorted(
            f.relative_to(SRC).as_posix()
            for f in SRC.rglob("*")
            if f.is_file() and not any(x.startswith(".") or x == "node_modules" for x in f.relative_to(SRC).parts)
        )
        listing = "".join(f"{hashlib.sha256((SRC / f).read_bytes()).hexdigest()}  {f}\n" for f in files)
        self.assertEqual(
            hashlib.sha256(listing.encode()).hexdigest(),
            source,
            "panel/next-src changed since the last build: run `npm run build` in panel/next-src",
        )
        self.assertEqual(set(recorded), BUILT - {"BUILD"})
        for name, sha in recorded.items():
            self.assertEqual(hashlib.sha256((NEXT / name).read_bytes()).hexdigest(), sha, f"panel/next/{name}")

    def test_the_wide_screen_hides_no_class_the_chart_draws(self):
        """The wide-screen rule that hides the phone's header and bar (`display: none`) must not match what the
        chart's SVG draws: it once hid the bar figures through the shared class `top`."""
        css = (NEXT / "app.css").read_text()
        wide = css[
            css.index("@media (min-width:900px)")
            if "@media (min-width:900px)" in css
            else css.index("@media (min-width: 900px)") :
        ]
        hidden = set()
        for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", wide):
            if re.search(r"display:\s*none", body):
                hidden |= set(re.findall(r"\.([\w-]+)", sel))
        drawn = set()
        for f in (SRC / "src/charts").glob("*.tsx"):
            for m in re.findall(r'className="([^"]+)"', f.read_text()):
                drawn |= set(m.split())
        self.assertEqual(hidden & drawn, set())

    def test_no_request_off_the_machine(self):
        for f in ("index.html", "app.css"):
            self.assertEqual(URL.findall((NEXT / f).read_text()), [], f"panel/next/{f} names a URL")
        self.assertLessEqual(set(URL.findall((NEXT / "app.js").read_text())), BUNDLE_URLS)
        for f in (SRC / "src").rglob("*"):
            if f.is_file():
                self.assertEqual(URL.findall(f.read_text()), [], f"{f.relative_to(ROOT)} names a URL")
        # no font, stylesheet or script from elsewhere, and no @import
        css = (NEXT / "app.css").read_text()
        self.assertNotIn("@import", css)
        self.assertNotIn("@font-face", css)

    def test_the_bundled_packages_carry_their_licences(self):
        text = (NEXT / "LICENSES.txt").read_text()
        for package in ("react", "react-dom", "@visx/scale", "@visx/shape", "d3-scale"):
            self.assertRegex(text, rf"(?m)^{re.escape(package)} \d+\.\d+\.\d+ \((MIT|ISC)\)$")

    def test_the_whole_new_panel_gzipped_is_under_150_kb(self):
        total = sum(len(gzip.compress((NEXT / f).read_bytes(), 9)) for f in SERVED)
        self.assertLessEqual(total, 150 * 1024, total)


def zero_day():
    return {"opus": 0, "sonnet": 0, "haiku": 0, "other": 0, "usd": 0}


class NextPageTest(ProjectCase):
    """The shell and the «How it goes» screen of the built bundle, mounted in node (tests/next_dom.mjs) with the
    panel's own answers replaced by the scenario's."""

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
            env={**os.environ, "TZ": "Europe/Madrid"},
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(out["errors"], [])
        return out

    ALPHA: ClassVar[dict] = {
        "name": "alpha",
        "title": "Alpha",
        "next": True,
        "decisions": 2,
        "unread": 1,
        "agents": 3,
        "orchestrator_health": {"verdict": "ok", "last_pass": "2026-11-07T12:00:00"},
    }
    PROJECTS: ClassVar[dict] = {
        "projects": [ALPHA, {"name": "beta", "title": "Beta", "next": False, "decisions": 9, "agents": 0}],
        "language": "es",
    }

    def metrics(self, days):
        ds = [f"2026-10-{d:02d}" for d in range(9, 32)] + [f"2026-11-0{d}" for d in range(1, 8)]
        ds = ds[-days:]
        cost = {d: zero_day() for d in ds}
        cost["2026-11-01"] = {"opus": 211.1, "sonnet": 8.1, "haiku": 0, "other": 0, "usd": 219.2}
        cost["2026-11-05"] = {"opus": 47.4, "sonnet": 0.7, "haiku": 0, "other": 0, "usd": 48.1}
        cost["2026-11-07"] = {"opus": 60.8, "sonnet": 13.0, "haiku": 0.5, "other": 0, "usd": 74.3}
        return {"days": ds, "cost": cost, "total": 341.6, "since": {"cost": "2026-11-01", "deliveries": None}}

    def how(self, **extra):
        answers = {"/api/projects": self.PROJECTS, "/api/metrics?p=alpha&days=7": self.metrics(7)}
        answers.update(extra.pop("answers", {}))
        return self.mount({"hash": "#/how", "answers": answers, **extra})

    def test_the_shell_as_in_the_mockups(self):
        out = self.how()
        self.assertEqual(out["tabs"], ["Inicio", "3 Te necesita", "3 Agentes", "Cómo va", "Más"])
        self.assertEqual(out["current"], ["Cómo va"])
        self.assertEqual(out["badges"], {"needs": "3", "agents": "3"})
        self.assertEqual(out["badgeColours"], {"needs": "badge red", "agents": "badge"})  # red, and grey
        # the sidebar: four sections, then the «More» group with its three entries, no icon on them
        self.assertEqual(
            out["side"],
            {"nav": ["Inicio", "Te necesita 3", "Agentes 3", "Cómo va", "Backlog", "Wiki", "Sistema"], "group": "Más"},
        )
        self.assertEqual(out["products"], ["Alpha"])  # beta has the new panel off
        # the status pill, by the picker on the phone and in the header on a wide screen
        self.assertEqual(out["pills"], [["pill running", "En marcha"]] * 2)
        # the theme follows the system: no switch
        self.assertEqual(out["buttons"], ["7 días", "30 días"])
        self.assertEqual(out["fetched"], ["/api/projects", "/api/metrics?p=alpha&days=7"])

    def test_the_status_pill_says_how_the_product_is(self):
        for fields, pill in (
            ({"paused": "paused at 12:00"}, ["pill paused", "En pausa"]),
            ({"orchestrator_health": {"verdict": "no_loop"}}, ["pill silent", "No responde"]),
            ({"orchestrator_health": {"verdict": "gone"}}, ["pill resting", "En reposo"]),
            ({"orchestrator_health": None}, ["pill resting", "En reposo"]),
        ):
            with self.subTest(pill=pill[1]):
                out = self.how(answers={"/api/projects": {"projects": [{**self.ALPHA, **fields}]}})
                self.assertEqual(out["pills"][0], pill)

    def test_the_cost_chart(self):
        g1 = self.how()["charts"][0]
        self.assertEqual(g1["title"], "Coste por día y modelo")
        # the period's total and the mean of the 3 days with work
        self.assertEqual(g1["figure"], "$342 en 7 días · $114 de media por día con trabajo")
        self.assertEqual(g1["legend"], ["Opus", "Sonnet", "Haiku"])  # «other» had no cost: not in the legend
        self.assertEqual(g1["ylabels"], ["$0", "$100", "$200"])
        self.assertEqual(g1["xlabels"], ["dom 1", "lun 2", "mar 3", "mié 4", "jue 5", "vie 6", "sáb 7"])
        self.assertEqual(g1["today"], "sáb 7")  # bold, as in the mockup
        cols = {c["day"]: c for c in g1["columns"]}
        self.assertEqual(cols["2026-11-07"]["segs"], [["opus", 60.8], ["sonnet", 13.0], ["haiku", 0.5]])
        # the highest day and today carry their figure; a day without work is a thin bar
        self.assertEqual(
            [(c["day"][-2:], c["top"]) for c in g1["columns"] if c["top"]], [("01", "$219"), ("07", "$74")]
        )
        self.assertEqual([c["kind"] for c in g1["columns"]], ["cost", "zero", "zero", "zero", "cost", "zero", "cost"])
        self.assertIn("dom 1 $219, lun 2 $0", g1["aria"])
        self.assertEqual(g1["tip"], "")

    def test_tapping_a_day_shows_its_detail(self):
        g1 = self.how(tap="2026-11-07")["charts"][0]
        self.assertEqual(g1["tip"], "sáb 7 · hoy Haiku $0.5 Sonnet $13.0 Opus $60.8 Total $74.3")
        g1 = self.how(tap="2026-11-03")["charts"][0]
        self.assertEqual(g1["tip"], "mar 3 sin trabajo")

    def test_thirty_days(self):
        answers = {"/api/metrics?p=alpha&days=30": self.metrics(30)}
        out = self.how(click="30 días", answers=answers, width=300)
        g1 = out["charts"][0]
        self.assertIn("datos desde el 1 nov", out["text"])  # the 30 days start before the data
        self.assertTrue(g1["figure"].startswith("$342 desde el 1 nov · "), g1["figure"])
        kinds = [c["kind"] for c in g1["columns"]]
        self.assertEqual(kinds[:23], ["none"] * 23)  # before the data starts: no data, not a day without work
        self.assertEqual(kinds[23:], ["cost", "zero", "zero", "zero", "cost", "zero", "cost"])
        # a narrow chart: one label in three, counted back from today, so today keeps its label
        self.assertEqual(g1["xlabels"][-1], "7")
        self.assertEqual(sum(1 for x in g1["xlabels"] if x), 10)
        wide = self.how(click="30 días", answers=answers, width=900)["charts"][0]
        self.assertTrue(all(wide["xlabels"]))  # a wide chart labels every day

    def test_changing_the_period_drops_the_old_chart_until_the_answer_comes(self):
        out = self.how(click="30 días", hang=["/api/metrics?p=alpha&days=30"])
        self.assertIn("Cargando", out["text"])
        self.assertEqual(out["charts"], [])

    def test_a_failed_answer_offers_to_retry(self):
        out = self.mount({"hash": "#/how", "answers": {"/api/projects": self.PROJECTS}})  # metrics: 404
        self.assertIn("No se ha podido cargar.", out["text"])
        self.assertIn("Reintentar", out["buttons"])

    def test_more_lists_backlog_wiki_and_system(self):
        out = self.mount({"hash": "#/more", "answers": {"/api/projects": self.PROJECTS}})
        self.assertIn("Backlog › Wiki › Sistema ›", out["text"])
        out = self.mount({"hash": "#/wiki", "answers": {"/api/projects": self.PROJECTS}})
        self.assertEqual(out["current"], ["Más"])  # a «More» screen lights the «More» tab
        self.assertIn("Alpha no tiene wiki", out["text"])

    def test_no_product_with_the_new_panel_says_how_to_turn_it_on(self):
        none = {**self.PROJECTS, "projects": [{**p, "next": False} for p in self.PROJECTS["projects"]]}
        out = self.mount({"hash": "#/how", "answers": {"/api/projects": none}})
        self.assertIn("Ningún producto tiene el panel nuevo", out["text"])
        self.assertIn("next = true", out["text"])
        self.assertEqual(out["charts"], [])
