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
import time
from datetime import datetime
from typing import ClassVar
from urllib.parse import quote
from zoneinfo import ZoneInfo

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


class NextAgentsApiTest(PanelCase):
    """/api/agents: the agents screen's data: who is running, who finished today (costs.jsonl), the next pass."""

    def setUp(self):
        super().setUp()
        self.write_toml(self.project_toml() + "\n[panel]\nnext = true\n")
        panel.REG.__init__()

    def project_toml(self):
        return (self.home / "foreman.toml").read_text()

    def run_line(self, agent, issue, end, **extra):
        return {
            "agent": agent, "name": agent, "issue": issue, "sub": "T1", "type": "dev", "model": "claude-opus-4-7",
            "outcome": "ok", "start": end[:11] + "09:00", "end": end, "minutes": 8.0, "usd": 3.4, **extra,
        }  # fmt: skip

    def write_costs(self, *lines):
        from foreman.backlog import Backlog

        f = Backlog(self.project).data_path.parent / "costs.jsonl"
        f.write_text("".join(json.dumps(x) + "\n" for x in lines))

    def test_the_running_agent_comes_with_its_task_and_state(self):
        code, d = self.req("/api/agents?p=fake")
        self.assertEqual(code, 200, d)
        (a,) = d["live"]
        self.assertEqual(
            (a["p"], a["name"], a["issue"], a["sub"], a["type"]), ("fake", "ag1", "PF-1", None, "dev")
        )  # sub: the brief names none
        self.assertEqual(a["task"], "Issue 1")  # the issue's short name, not the brief
        self.assertEqual(a["status"], "holds a lock")
        self.assertEqual(a["msg"], "done for now")
        self.assertFalse(a["session"])  # a subagent: no conversation to open
        self.assertIsNone(a["link"])
        self.assertIsInstance(a["since"], int)
        self.assertIsInstance(a["sign"], int)  # its last sign of life: the screen says «no signs for 25 min»

    def test_finished_today_come_from_the_cost_file(self):
        today = datetime.now().strftime("%Y-%m-%d")
        self.write_costs(
            self.run_line("d1", "PF-1", f"{today} 10:30"),
            self.run_line("d2", "PF-2", f"{today} 11:45", outcome="partial", model="claude-sonnet-4-6", usd=1.25),
            self.run_line("old", "PF-1", "2020-01-01 10:00"),  # not today
            self.run_line("alien", "ZZ-9", f"{today} 12:00"),  # an issue of no backlog of this product
            self.run_line("a1", "PF-1", f"{today} 12:30"),  # running now: it is in `live`, not here
        )
        code, d = self.req("/api/agents?p=fake")
        self.assertEqual(code, 200, d)
        self.assertEqual([x["agent"] for x in d["done"]], ["d2", "d1"])  # the latest first
        d2 = d["done"][0]
        self.assertEqual(
            (d2["task"], d2["issue"], d2["sub"], d2["outcome"], d2["minutes"], d2["family"], d2["usd"], d2["end"]),
            ("Issue 2", "PF-2", "T1", "partial", 8.0, "sonnet", 1.25, f"{today} 11:45"),
        )

    def test_only_products_with_the_new_panel_and_all_of_them_with_a_star(self):
        self.assertEqual(self.req("/api/agents?p=other")[0], 404)
        self.assertEqual(self.req("/api/agents?p=nope")[0], 404)
        self.assertEqual(self.req("/api/agents")[0], 404)
        code, d = self.req("/api/agents?p=*")
        self.assertEqual(code, 200, d)
        self.assertEqual({a["p"] for a in d["live"]}, {"fake"})  # `other` has no [panel] next
        self.assertEqual(d["projects"], ["fake"])

    def test_next_pass_is_the_last_pass_plus_the_period(self):
        from foreman import orchestrator, util

        d = self.req("/api/agents?p=fake")[1]
        # this product has a [loop] prompt and a pass just now: due one period later
        last = util.iso_epoch(orchestrator.last_pass(self.project))
        self.assertEqual(d["next_pass"], int(last + orchestrator.every_min(self.project) * 60))
        self.assertGreater(d["next_pass"], last + 59 * 60)

    def test_an_error_inside_is_a_500_not_a_dropped_connection(self):
        from unittest import mock

        from foreman import agents_screen

        with mock.patch.object(agents_screen, "build", side_effect=ValueError("corrupt")):
            self.assertEqual(self.req("/api/agents?p=fake")[0], 500)


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

    # ---------- the agents screen ----------
    BETA: ClassVar[dict] = {**ALPHA, "name": "beta", "title": "Beta", "decisions": 0, "unread": 0, "agents": 1}

    def card(self, **extra):
        now = time.time()
        return {
            "id": "a1", "p": "alpha", "name": "ag1", "task": "Agentes que ves desde la app", "issue": "PF-7",
            "sub": "T1", "type": "foreman:dev", "family": "opus", "status": "working", "since": int(now - 25 * 60),
            "sign": int(now - 60), "msg": "El guardián ya trata como agente una sesión.", "session": True,
            "link": None, **extra,
        }  # fmt: skip

    def finished(self, **extra):
        return {
            "agent": "d1", "p": "alpha", "task": "Plan de «Agentes»", "issue": "PF-7", "sub": None,
            "type": "foreman:planner", "outcome": "ok", "minutes": 8.0, "family": "opus", "usd": 3.4,
            "end": "2026-11-07 10:30", **extra,
        }  # fmt: skip

    def agents(self, live, done, next_pass=None, **extra):
        data = {"projects": ["alpha"], "live": live, "done": done, "next_pass": next_pass}
        answers = {"/api/projects": self.PROJECTS, "/api/agents?p=alpha": data, **extra.pop("answers", {})}
        return self.mount({"hash": "#/agents", "answers": answers, **extra})

    def test_the_agents_cards_and_the_finished_list(self):
        stuck = self.card(
            id="a2", task="Contador de avisos", issue="AP-1", sub=None, type="foreman:mechanic", family="sonnet",
            status="maybe stuck", since=int(time.time() - 48 * 60), sign=int(time.time() - 25 * 60), session=False,
        )  # fmt: skip
        lock = self.card(id="a3", task="Revisión de la rama", issue=None, type="foreman:reviewer", family="sonnet",
                         status="waiting for a lock", session=False, msg=None, sub=None)  # fmt: skip
        partial = self.finished(agent="d2", task="Agentes", sub="T0", type="foreman:dev", outcome="partial")
        done = [self.finished(), {**partial, "minutes": 75, "family": "sonnet", "usd": 9.1}]
        out = self.agents([self.card(), lock, stuck], done)
        a, w, s = out["agents"]
        self.assertEqual((a["avatar"], a["task"], a["id"]), ("AD", "Agentes que ves desde la app", "PF-7"))
        self.assertEqual(a["meta"], "T1 · desarrollo Opus")
        self.assertEqual(a["state"], "Trabajando · lleva 25 min")
        self.assertEqual(a["stateTone"], "dot ok")
        self.assertEqual(a["msg"], "El guardián ya trata como agente una sesión.")
        self.assertTrue(a["watch"])  # a session agent: its conversation can be opened
        self.assertEqual(
            (w["state"], w["stateTone"], w["id"], w["watch"]),
            ("Esperando un turno de un recurso · lleva 25 min", "dot wait", "", False),
        )
        self.assertEqual(w["meta"], "revisión Sonnet")
        self.assertEqual(w["msg"], "")  # nothing to say: no empty line
        # stuck: red card, how long without signs instead of the last words, and no button for a subagent
        self.assertEqual((s["cls"], s["stateTone"], s["watch"]), ("agent stuck", "dot stuck", False))
        self.assertEqual(s["state"], "Puede estar atascado · lleva 48 min")
        self.assertEqual(s["msg"], "Sin señales desde hace 25 min")
        self.assertEqual(out["done"], [
            "Plan de «Agentes» · plan Completa ✓ 8 min Opus $3.40",
            "Agentes · T0 · desarrollo A medias 1 h 15 min Sonnet $9.10",
        ])  # fmt: skip
        self.assertIn("3 en marcha · 2 terminados hoy", out["text"])
        self.assertIn("Coste a precio de lista de la API", out["text"])
        self.assertEqual(out["current"], ["3 Agentes"])
        self.assertEqual(out["fetched"], ["/api/projects", "/api/agents?p=alpha"])

    def test_nobody_running_says_when_the_next_pass_is(self):
        later = time.time() + 3600
        out = self.agents([], [self.finished()], next_pass=int(later))
        hhmm = datetime.fromtimestamp(int(later), ZoneInfo("Europe/Madrid")).strftime("%H:%M")  # node runs in Madrid
        self.assertIn("Nadie trabajando ahora", out["nobody"])
        self.assertIn(f"El siguiente repaso, a las {hhmm}", out["nobody"])
        self.assertEqual(out["agents"], [])
        self.assertIn("Nadie en marcha · 1 terminado hoy", out["text"])
        due = self.agents([], [], next_pass=int(time.time() - 60))
        self.assertIn("El siguiente repaso toca ya", due["nobody"])
        self.assertIn("Aún no ha terminado nadie hoy", due["text"])

    def test_all_products_with_their_tags(self):
        both = {"projects": [self.ALPHA, self.BETA], "language": "es"}
        both["projects"][1] = {**self.BETA, "next": True}
        data = {
            "projects": ["alpha", "beta"],
            "live": [self.card(), self.card(id="b1", p="beta", task="Caché de prefijo", issue=None, session=False)],
            "done": [self.finished(p="beta")], "next_pass": None,
        }  # fmt: skip
        out = self.mount({
            "hash": "#/agents", "store": {"next-all": "1"},
            "answers": {"/api/projects": both, "/api/agents?p=*": data},
        })  # fmt: skip
        self.assertEqual(out["products"], ["Todos", "Alpha", "Beta"])
        self.assertEqual(out["fetched"], ["/api/projects", "/api/agents?p=*"])
        self.assertIn("2 en marcha en 2 productos · 1 terminado hoy", out["text"])
        self.assertEqual(out["pills"], [["pill running", "2 productos en marcha"]] * 2)
        self.assertEqual(
            [a["meta"] for a in out["agents"]], ["T1 · desarrollo Opus Alpha", "T1 · desarrollo Opus Beta"]
        )
        self.assertTrue(out["done"][0].endswith("$3.40 Beta"), out["done"])

    def test_all_is_only_for_the_agents_screen(self):
        both = {"projects": [self.ALPHA, {**self.BETA, "next": True}], "language": "es"}
        out = self.mount({"hash": "#/how", "store": {"next-all": "1"}, "answers": {"/api/projects": both}})
        self.assertEqual(out["products"], ["Alpha", "Beta"])  # the charts are of one product
        self.assertEqual(out["pills"][0], ["pill running", "En marcha"])
        self.assertEqual(out["fetched"][1], "/api/metrics?p=alpha&days=7")

    def test_see_conversation_opens_the_link_the_server_gives(self):
        post = "POST /api/agent/watch?id=a1&p=alpha"
        out = self.agents([self.card()], [], click="Ver conversación",
                          answers={post: {"link": "https://claude.ai/code/session_x"}})  # fmt: skip
        self.assertIn(post, out["fetched"])
        self.assertEqual(out["windows"], [{"href": "https://claude.ai/code/session_x", "closed": False}])
        # a browser that blocks the window: the link, on the card
        out = self.agents([self.card()], [], click="Ver conversación", blockPopups=True,
                          answers={post: {"link": "https://claude.ai/code/session_x"}})  # fmt: skip
        self.assertIn(
            "El navegador ha bloqueado la ventana: abre el enlace : https://claude.ai/code/session_x", out["text"]
        )
        # the server says no: its words, and the empty window closed
        out = self.agents([self.card()], [], click="Ver conversación")  # POST: 404
        self.assertIn("No se ha podido abrir la conversación: 404", out["text"])
        self.assertEqual(out["windows"], [{"href": "", "closed": True}])

    def test_agents_that_cannot_load_offer_to_retry(self):
        out = self.mount({"hash": "#/agents", "answers": {"/api/projects": self.PROJECTS}})
        self.assertIn("No se ha podido cargar.", out["text"])
        self.assertIn("Reintentar", out["buttons"])

    def test_the_agents_screen_waits_for_the_answer(self):
        out = self.mount({"hash": "#/agents", "answers": {"/api/projects": self.PROJECTS},
                          "hang": ["/api/agents?p=alpha"]})  # fmt: skip
        self.assertIn("Cargando", out["text"])

    def test_more_lists_backlog_wiki_and_system(self):
        out = self.mount({"hash": "#/more", "answers": {"/api/projects": self.PROJECTS}})
        self.assertIn("Backlog › Wiki › Sistema ›", out["text"])
        out = self.mount({"hash": "#/wiki", "answers": {"/api/projects": self.PROJECTS}})
        self.assertEqual(out["current"], ["Más"])  # a «More» screen lights the «More» tab
        self.assertIn("Abrir el panel de hoy", out["text"])  # not built yet: today's panel

    def test_no_product_with_the_new_panel_says_how_to_turn_it_on(self):
        none = {**self.PROJECTS, "projects": [{**p, "next": False} for p in self.PROJECTS["projects"]]}
        out = self.mount({"hash": "#/how", "answers": {"/api/projects": none}})
        self.assertIn("Ningún producto tiene el panel nuevo", out["text"])
        self.assertIn("next = true", out["text"])
        self.assertEqual(out["charts"], [])
