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
from pathlib import Path
from typing import ClassVar
from urllib.parse import quote
from zoneinfo import ZoneInfo

from helpers import ROOT, ProjectCase, config_problems
from test_panel import PanelCase

from foreman import agents as agents_mod
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
        self.assertNotIn("link", a)  # the click asks for it: Remote Control holds it only while it is on
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

    def test_the_poll_does_not_read_a_whole_big_transcript_each_time(self):
        """A running agent's transcript grows all the time, so its cache never holds: the screen's poll (every 10 s,
        each open tab) must read its end only, not tens of MB of it."""
        f = Path(agents_mod.agents(self.project)["a1"]["transcript"])
        said = {"id": "m", "model": "claude-opus-4-7", "role": "assistant", "usage": {"input_tokens": 1},
                "content": [{"type": "text", "text": "x" * 3000}]}  # fmt: skip
        line = json.dumps({"type": "assistant", "message": said}) + "\n"
        with f.open("a") as w:
            w.write(line * 15000)  # 45 MB
        self.assertGreater(f.stat().st_size, 40_000_000)
        self.req("/api/agents?p=fake")
        with f.open("a") as w:
            w.write(line)  # the agent said one more thing: every cache of this file is stale
        t0 = time.perf_counter()
        code, d = self.req("/api/agents?p=fake")
        took = time.perf_counter() - t0
        self.assertEqual(code, 200, d)
        self.assertEqual(d["live"][0]["family"], "opus")
        self.assertLess(took, 0.5, f"the poll took {took:.2f} s with a 45 MB transcript")

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
        """What /api/metrics gives for the real week of 1-7 Nov (the mockups' figures), over `days` days."""
        ds = [f"2026-10-{d:02d}" for d in range(9, 32)] + [f"2026-11-0{d}" for d in range(1, 8)]
        ds = ds[-days:]
        cost = {d: {**zero_day(), "cheap_share": None} for d in ds}
        cost["2026-11-01"] = {"opus": 211.1, "sonnet": 8.1, "haiku": 0, "other": 0, "usd": 219.2, "cheap_share": 0.061}
        cost["2026-11-05"] = {"opus": 47.4, "sonnet": 0.7, "haiku": 0, "other": 0, "usd": 48.1, "cheap_share": 0.016}
        cost["2026-11-07"] = {"opus": 60.8, "sonnet": 13.0, "haiku": 0.5, "other": 0, "usd": 74.3, "cheap_share": 0.239}
        week = {  # day: integrated, measured, complete, partly, failed
            "01": (71, 0, 76, 0, 0),
            "02": (66, 1, 66, 3, 0),
            "03": (24, 0, 27, 1, 0),
            "04": (0, 0, 0, 0, 0),
            "05": (15, 2, 19, 0, 0),
            "06": (51, 1, 97, 2, 0),
            "07": (29, 3, 51, 2, 1),
        }
        keys = ("integrated", "measured", "ok", "partial", "failed")
        deliveries = {d: dict.fromkeys(keys, 0) for d in ds}
        for d, v in week.items():
            if f"2026-11-{d}" in deliveries:
                deliveries[f"2026-11-{d}"] = dict(zip(keys, v))
        reviews = {d: {"clean": 0, "back": 0} for d in ds}
        reviews["2026-11-06"] = {"clean": 5, "back": 12}
        reviews["2026-11-07"] = {"clean": 8, "back": 2}
        guard = {d: {"agent": 0, "orchestrator": 0, "other": 0} for d in ds}
        for d, v in {"02": (4, 2, 0), "03": (6, 2, 0), "05": (4, 1, 0), "06": (7, 4, 0), "07": (11, 3, 4)}.items():
            guard[f"2026-11-{d}"] = dict(zip(("agent", "orchestrator", "other"), v))
        return {
            "days": ds,
            "cost": cost,
            "total": 341.6,
            "top_issues": [
                {"issue": "AB-1", "short": "Guardián sin agujeros", "usd": 75.5, "runs": 12},
                {"issue": "AB-2", "short": "Código más simple", "usd": 48.3, "runs": 8},
            ],
            "by_type": [
                {"type": "dev", "usd": 595.0, "runs": 90},
                {"type": "reviewer", "usd": 43.5, "runs": 30},
                {"type": "weird", "usd": 5.0, "runs": 1},
            ],
            "deliveries": deliveries,
            "reviews": reviews,
            "rounds": {"1": 11, "2": 15, "3": 4},
            "guard": guard,
            "guard_rules": [
                {"rule": "state files", "band": "never", "n": 9},
                {"rule": "(^|\\s)release now", "band": "orchestrator", "n": 4},
            ],
            "since": {"cost": "2026-11-01", "deliveries": "2026-10-31", "reviews": "2026-11-06", "guard": "2026-11-02"},
            "quota": [
                {"kind": "five_hour", "percent_used": 30, "resets_at": "2030-01-15T22:00:00Z"},
                {"kind": "seven_day", "percent_used": 85.4, "resets_at": "2030-01-15T22:00:00Z"},
            ],
        }

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
        self.assertEqual([c["kind"] for c in g1["columns"]], ["data", "zero", "zero", "zero", "data", "zero", "data"])
        self.assertIn("dom 1 $219, lun 2 $0", g1["aria"])
        self.assertEqual(g1["tip"], "")

    def charts(self, **extra):
        return {c["name"]: c for c in self.how(**extra)["charts"]}

    def test_all_eight_charts_in_the_order_of_the_mockup(self):
        out = self.how()
        self.assertEqual([c["name"] for c in out["charts"]], ["g1", "g2", "g3", "g4", "g5", "g6", "g7", "g8"])
        self.assertEqual(
            [c["title"] for c in out["charts"]],
            [
                "Coste por día y modelo",
                "Ritmo de entregas",
                "Cómo acaban los agentes",
                "Revisiones devueltas",
                "Parte en modelos baratos",
                "Dónde se va el coste",
                "Bloqueos del guardián",
                "Cuota ahora",
            ],
        )

    def test_the_delivery_pace(self):
        g2 = self.charts()["g2"]
        self.assertEqual(g2["figure"], "256 cambios en main en 7 días · 37 al día · 7 medidas")
        self.assertEqual(g2["legend"], ["Cambios integrados", "2 Medidas cerradas"])
        # the measures closed that day sit on the bar as a pill
        self.assertEqual(
            [(c["day"][-2:], c["top"]) for c in g2["columns"] if c["top"]],
            [("02", "1"), ("05", "2"), ("06", "1"), ("07", "3")],
        )
        self.assertEqual(g2["columns"][0]["segs"], [["integrated", 71]])
        self.assertEqual(g2["columns"][3]["kind"], "zero")  # tue 4: nothing, but the data had begun
        self.assertIn("mar 3 24, mié 4 0", g2["aria"])

    def test_figures_on_narrow_bars_never_touch(self):
        answers = {"/api/metrics?p=alpha&days=30": self.metrics(30)}
        g = self.charts(click="30 días", answers=answers, width=300)
        for name in ("g2", "g4"):
            tops = [(c["day"][-2:], c["top"]) for c in g[name]["columns"] if c["top"]]
            self.assertEqual(tops[-1][0], "07", name)  # today's is always kept
            self.assertLess(len(tops), 4, name)  # the neighbours that would overlap it are left out
        self.assertEqual([c["top"] for c in g["g2"]["columns"] if c["top"]][-1], "3")

    def test_how_the_agents_end(self):
        g3 = self.charts()["g3"]
        self.assertEqual(g3["legend"], ["Completas", "A medias", "Fallidas"])
        self.assertEqual(
            g3["figure"], "97 % completas · 336 de 345, 1 fallidas"
        )  # complete 76+66+27+19+97+51, plus 6 partly and 1 failed
        sat = g3["columns"][-1]
        self.assertEqual(sat["segs"], [["ok", 51], ["partial", 2], ["failed", 1]])  # complete at the bottom
        self.assertEqual([c["top"] for c in g3["columns"] if c["top"]], ["54"])  # today's total only

    def test_reviews_sent_back(self):
        g4 = self.charts()["g4"]
        self.assertEqual(g4["figure"], "52 % 14 de 27 pidieron cambios, desde el vie 6")
        self.assertEqual(
            [(c["day"][-2:], c["top"]) for c in g4["columns"] if c["top"]],
            [("06", "12/17"), ("07", "2/10")],  # a phone: the short form, or they overlap
        )
        wide = self.charts(width=900)["g4"]  # room enough: in words
        self.assertEqual([c["top"] for c in wide["columns"] if c["top"]], ["12 de 17", "2 de 10"])
        self.assertEqual([c["kind"] for c in g4["columns"]], ["none"] * 5 + ["data", "data"])
        self.assertEqual(g4["columns"][5]["segs"], [["clean", 5], ["back", 12]])  # sent back on top, amber
        self.assertEqual(g4["empty"], "Aún no hay datos de esto")  # said over the five days without data
        self.assertEqual(
            self.charts(
                answers={
                    "/api/metrics?p=alpha&days=7": {
                        **self.metrics(7),
                        "reviews": {},
                        "since": {**self.metrics(7)["since"], "reviews": None},
                    }
                }
            )["g4"]["empty"],
            "Aún no hay datos de esto",
        )

    def test_the_share_on_cheaper_models(self):
        g5 = self.charts()["g5"]
        self.assertEqual(g5["figure"], "23.9 % hoy · el dom 1, 6.1 %")
        self.assertEqual(g5["dots"], ["2026-11-01", "2026-11-05", "2026-11-07"])
        self.assertEqual(g5["ylabels"][0], "0 %")
        self.assertEqual(g5["xlabels"][-1], "sáb 7")
        # a day without work (tue 4) cuts the line: two runs of days with data when thu 6 has some too
        m = self.metrics(7)
        m["cost"]["2026-11-06"] = {
            "opus": 132.7,
            "sonnet": 7.5,
            "haiku": 0,
            "other": 0,
            "usd": 140.2,
            "cheap_share": 0.076,
        }
        g5 = self.charts(answers={"/api/metrics?p=alpha&days=7": m})["g5"]
        self.assertEqual(len(g5["line"]), 2)  # sun 1 alone, and thu 5..sat 7: tue 4 cuts the line
        self.assertEqual(len(g5["line"][-1].split()), 3)

    def test_where_the_cost_goes(self):
        g6 = self.charts()["g6"]
        self.assertEqual(g6["figure"], "$75.5 la tarea más cara del periodo")
        self.assertEqual(g6["tasks"], ["Guardián sin agujeros · AB-1 $75.5", "Código más simple · AB-2 $48.3"])
        self.assertEqual([float(x.rstrip("%")) for x in g6["bars"][:2]], [100, 63.97350993377483])  # to the dearest
        # the kinds of agent in words; a kind foreman does not know shows as it is
        self.assertEqual(g6["types"], ["desarrollo $595.0", "revisores $43.5", "weird $5.0"])

    def test_the_guard_refusals(self):
        g7 = self.charts()["g7"]
        self.assertEqual(g7["figure"], "48 bloqueos desde el 2 nov · 18 hoy")
        self.assertEqual(g7["legend"], ["A agentes", "Al orquestador", "A otras sesiones"])
        self.assertEqual(g7["columns"][0]["kind"], "none")  # sun 1: before the guard's log starts
        self.assertEqual(g7["columns"][-1]["segs"], [["agent", 11], ["orchestrator", 3], ["other", 4]])
        self.assertEqual(g7["empty"], "")  # one day before the log: nothing written over it
        self.assertEqual(
            g7["rules"], ["Prohibido siempre · state files · 9", "Reservado al orquestador · release now · 4"]
        )
        self.assertIn("Datos desde el lun 2", self.how()["text"])

    def test_the_quota_now(self):
        g8 = self.charts()["g8"]
        self.assertEqual(g8["figure"], "85 % de la semana usado")
        five, week = g8["meters"]
        self.assertEqual((five["kind"], five["width"], five["fill"]), ("five_hour", "30%", "fill"))
        self.assertIn("se renueva el mar 15 a las 23:00", five["text"])
        self.assertEqual((week["width"], week["fill"]), ("85.4%", "fill warn"))  # from 80 %, amber
        none = self.charts(answers={"/api/metrics?p=alpha&days=7": {**self.metrics(7), "quota": []}})["g8"]
        self.assertEqual(none["figure"], "— Aún no hay una medida de la cuota")
        self.assertEqual(none["meters"], [])

    def test_a_series_without_data_says_so_and_the_rest_still_draw(self):
        m = self.metrics(7)
        blank = {
            "cost": {d: {**zero_day(), "cheap_share": None} for d in m["days"]},
            "total": 0.0,
            "top_issues": [],
            "by_type": [],
            "since": {**m["since"], "deliveries": None, "reviews": None, "guard": None},
            "reviews": {},
            "guard": {},
            "deliveries": {},
            "guard_rules": [],
            "rounds": {},
            "quota": [],
        }
        out = self.how(answers={"/api/metrics?p=alpha&days=7": {**m, **blank}})
        self.assertEqual(len(out["charts"]), 8)
        g = {c["name"]: c for c in out["charts"]}
        self.assertEqual(g["g2"]["figure"], "0 cambios en main en 7 días · 0 al día · 0 medidas")
        self.assertEqual(g["g5"]["figure"], "— Aún no hay datos de esto")  # no line, no dots
        self.assertEqual(g["g5"]["dots"], [])
        self.assertEqual(g["g6"]["figure"], "— Aún no hay datos de esto")
        self.assertEqual(g["g4"]["empty"], "Aún no hay datos de esto")
        self.assertEqual({c["kind"] for c in g["g7"]["columns"]}, {"none"})

    def test_it_is_read_again_and_the_connection_dropping_keeps_the_charts_dimmed(self):
        url = "/api/metrics?p=alpha&days=7"
        out = self.how(tick=1)
        self.assertEqual(out["fetched"].count(url), 2)  # read again at the tick
        self.assertEqual(out["offline"], "")
        self.assertFalse(out["dim"])
        out = self.how(tick=1, fail=[url])
        self.assertRegex(out["offline"], r"^Sin conexión · reintentando datos de las \d\d:\d\d$")
        self.assertTrue(out["dim"])
        self.assertEqual(len(out["charts"]), 8)  # the charts stay

    def test_a_first_failed_read_heals_at_the_next_periodic_read(self):
        url = "/api/metrics?p=alpha&days=7"
        out = self.how(down=[url], tick=1, heal=[url])
        self.assertNotIn("No se ha podido cargar", out["text"])
        self.assertEqual(len(out["charts"]), 8)
        self.assertEqual(out["offline"], "")

    def test_tapping_a_day_shows_its_detail(self):
        g1 = self.how(tap="2026-11-07")["charts"][0]
        self.assertEqual(g1["tip"], "sáb 7 · hoy Haiku $0.5 Sonnet $13.0 Opus $60.8 Total $74.3")
        g1 = self.how(tap="2026-11-03")["charts"][0]
        self.assertEqual(g1["tip"], "mar 3 sin trabajo")

    def test_the_delivery_average_divides_by_the_days_with_data(self):
        # 30 days, deliveries known since 31 Oct: 8 days (31 Oct .. 7 Nov), 256 integrated -> 32 a day, not 256/30
        g2 = self.charts(answers={"/api/metrics?p=alpha&days=30": self.metrics(30)}, click="30 días")["g2"]
        self.assertEqual(g2["figure"], "256 cambios en main desde el 31 oct · 32 al día · 7 medidas")

    def test_thirty_days(self):
        answers = {"/api/metrics?p=alpha&days=30": self.metrics(30)}
        out = self.how(click="30 días", answers=answers, width=300)
        g1 = out["charts"][0]
        self.assertIn("datos desde el 1 nov", out["text"])  # the 30 days start before the data
        self.assertTrue(g1["figure"].startswith("$342 desde el 1 nov · "), g1["figure"])
        kinds = [c["kind"] for c in g1["columns"]]
        self.assertEqual(kinds[:23], ["none"] * 23)  # before the data starts: no data, not a day without work
        self.assertEqual(kinds[23:], ["data", "zero", "zero", "zero", "data", "zero", "data"])
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
        s, a, w = out["agents"]  # the stuck one first, then as the server gave them
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

    def test_the_stuck_agent_comes_first(self):
        stuck = self.card(
            id="a9", task="Atascado", status="maybe stuck", since=int(time.time() - 10 * 60), session=False
        )
        out = self.agents([self.card(), self.card(id="a2", task="Otro"), stuck], [])
        self.assertEqual([a["task"] for a in out["agents"]], ["Atascado", "Agentes que ves desde la app", "Otro"])

    def test_a_slow_answer_of_the_old_choice_does_not_replace_the_new_ones(self):
        both = {"projects": [self.ALPHA, {**self.BETA, "next": True}], "language": "es"}
        everybody = {"projects": ["alpha", "beta"], "done": [], "next_pass": None,
                     "live": [self.card(), self.card(id="b1", p="beta", task="De beta")]}  # fmt: skip
        mine = {"projects": ["alpha"], "live": [self.card(task="Solo de alpha")], "done": [], "next_pass": None}
        out = self.mount({
            "hash": "#/agents", "store": {"next-all": "1"}, "pick": "alpha", "delay": {"/api/agents?p=*": 250},
            "answers": {"/api/projects": both, "/api/agents?p=*": everybody, "/api/agents?p=alpha": mine},
        })  # fmt: skip
        self.assertEqual([a["task"] for a in out["agents"]], ["Solo de alpha"])

    def test_see_conversation_opens_the_waiting_page_inside_the_click(self):
        wait = "/agent/wait?id=a1&p=alpha&name=Agentes%20que%20ves%20desde%20la%20app&t=k3y"
        out = self.agents([self.card()], [], click="Ver conversación")
        self.assertEqual(out["windows"], [{"href": wait, "closed": False}])  # the page asks and goes to the link
        self.assertFalse([f for f in out["fetched"] if "agent/watch" in f])  # this page does not ask itself

    def test_a_browser_that_blocks_the_tab_gets_the_link_on_the_card(self):
        post = "POST /api/agent/watch?id=a1&p=alpha"
        out = self.agents([self.card()], [], click="Ver conversación", blockPopups=True,
                          answers={post: {"link": "https://claude.ai/code/session_x"}})  # fmt: skip
        self.assertIn(post, out["fetched"])
        self.assertIn(
            "El navegador ha bloqueado la ventana: abre el enlace : https://claude.ai/code/session_x", out["text"]
        )
        out = self.agents([self.card()], [], click="Ver conversación", blockPopups=True)  # the server says no: 404
        self.assertIn("No se ha podido abrir la conversación: 404", out["text"])

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
        self.assertIn("Alpha no tiene wiki", out["text"])

    def test_no_product_with_the_new_panel_says_how_to_turn_it_on(self):
        none = {**self.PROJECTS, "projects": [{**p, "next": False} for p in self.PROJECTS["projects"]]}
        out = self.mount({"hash": "#/how", "answers": {"/api/projects": none}})
        self.assertIn("Ningún producto tiene el panel nuevo", out["text"])
        self.assertIn("next = true", out["text"])
        self.assertEqual(out["charts"], [])
