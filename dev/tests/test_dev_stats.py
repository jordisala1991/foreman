"""`foreman-dev stats` on fake transcripts (dev/tests/stats_world.py): sums by product, session, agent, issue, type,
model and hour; the window; what foreman knows (orchestrator, agents' issues, the sessions its hooks recorded);
Claude Code's own figures of a closed session; --usage with a fake `claude`."""

import json
import os
import stat

import stats_world as W
from devtool import DevCase
from helpers import BASE_TOML

from foreman_dev import stats

TOML = (
    BASE_TOML
    + """
[repos.app]
path = "{tmp}/app"
remote = "none"
"""
)


class StatsTest(DevCase):
    toml = TOML

    def setUp(self):
        super().setUp()
        (self.tmp / "app").mkdir()
        self.bhome = self.tmp / "bhome"
        self.bhome.mkdir()
        (self.bhome / "foreman.toml").write_text(
            BASE_TOML.replace('name = "fake"', 'name = "other"').replace("{tmp}", str(self.tmp))
        )
        with open(self.tmp / "projects.toml", "a") as f:
            f.write(f'[[project]]\nname = "other"\nhome = "{self.bhome}"\nmode = "active"\n')
        (self.tmp / "elsewhere").mkdir()
        W.build(
            self.tmp / "claude-projects",
            self.tmp / "state",
            {"name": "fake", "home": self.home, "repo": self.tmp / "app", "prefix": "PF"},
            {"name": "other", "home": self.bhome, "repo": None, "prefix": "PF"},
            self.tmp / "elsewhere",
        )
        self.window = ["--since", "2020-01-15T00:00+00:00", "--until", "2020-01-16T00:00+00:00"]

    def stats(self, *args, env=None):
        r = self.dev("stats", *args, env=dict({"TZ": "UTC"}, **(env or {})))
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def js(self, *args, **k):
        return json.loads(self.stats(*args, "--json", **k))

    def rows(self, d, view):
        return {r["key"]: r for r in d["tables"][view]}

    def test_products_and_the_sessions_of_none(self):
        d = self.js("--all", *self.window)
        p = self.rows(d, "project")
        self.assertEqual(
            {k: r["cache_read"] for k, r in p.items()},
            {"fake": W.WORLD["A"], "other": W.WORLD["B"], stats.NO_PRODUCT: W.WORLD["none"]},
        )
        self.assertEqual(d["total"]["cache_read"], W.WORLD["A"] + W.WORLD["B"] + W.WORLD["none"])
        self.assertEqual(d["untested_claude_code_versions"], ["9.9.9"])  # the session of no product
        self.assertIn("not tested with", self.stats("--all", *self.window))

    def test_one_product_kinds_types_models(self):
        d = self.js(*self.window)
        self.assertEqual(d["products"], ["fake"])
        self.assertEqual(d["total"]["cache_read"], W.WORLD["A"])
        self.assertEqual(d["total"]["calls"], 3 + 2 + 1 + 1 + 2)  # the split response once, the day before out
        kind = self.rows(d, "kind")
        self.assertEqual((kind["main sessions"]["cache_read"], kind["agents"]["cache_read"]), (90000 + 17000, 24000))
        t = self.rows(d, "type")
        self.assertEqual(
            {k: r["cache_read"] for k, r in t.items()},
            {stats.MAIN: 107000, "foreman:dev": 11000, "foreman:planner": 9000, "general-purpose": 4000},
        )
        m = self.rows(d, "model")
        self.assertEqual(
            (m["claude-opus-5-5"]["cache_read"], m["claude-sonnet-5-5"]["cache_read"]),
            (90000 + 11000 + 9000, 4000 + 17000),
        )
        text = self.stats(*self.window)
        self.assertIn("sessions opened outside the product's folders", text)

    def test_sessions_orchestrator_and_claude_codes_figures(self):
        d = self.js("--by", "session", *self.window)
        s = self.rows(d, "session")
        o = s[W.S_ORCH]
        self.assertEqual(
            (o["role"], o["name"], o["agents"], o["cache_read"]), ("orchestrator", "orch-a", 3, W.WORLD["orch_session"])
        )
        self.assertEqual(
            (o["main"]["cache_read"], o["peak"], o["from"], o["to"], o["hours"]),
            (90000, 40501, "2020-01-15 08:00", "2020-01-15 10:30", 2.5),
        )
        self.assertEqual(o["growth_per_hour"], round((40501 - 23002) / 2.5))
        cc = o["claude_code"]
        self.assertEqual(
            (cc["cache_read"], cc["input"], cc["output"], cc["price_usd"], cc["closed"]),
            (164000, 909, 2050, 12.35, "2020-01-15 10:30"),
        )
        self.assertEqual(s[W.S_OTHER]["role"], "other session")
        self.assertIsNone(s[W.S_OTHER]["claude_code"])
        text = self.stats("--by", "session", *self.window)
        self.assertIn("12.35 $ equivalent", text)
        self.assertIn("not what a subscription pays", text)

    def test_one_whole_session_matches_claude_codes_own_count(self):
        d = self.js("--session", W.S_ORCH[:8])
        self.assertEqual(d["total"]["cache_read"], W.WORLD["orch_whole"])
        self.assertEqual(d["claude_code"][W.S_ORCH]["cache_read"], W.WORLD["orch_whole"])
        a = self.rows(d, "agent")
        self.assertEqual(a[W.S_ORCH]["type"], stats.MAIN)
        self.assertEqual(
            (a["a1"]["name"], a["a1"]["issue"], a["a1"]["sub"], a["a1"]["issue_from"]), ("dev1", "PF-1", "T1", "launch")
        )
        r = self.dev("stats", "--session", "nope")
        self.assertEqual(r.returncode, 0)
        self.assertIn("no model call", r.stdout)

    def test_issues_recorded_guessed_and_none(self):
        d = self.js("--by", "issue", *self.window)
        i = self.rows(d, "issue")
        self.assertEqual(
            {k: (r["cache_read"], r["agents"]) for k, r in i.items()},
            {stats.COORDINATION: (107000, 0), "PF-1": (11000, 1), stats.NO_ISSUE: (13000, 2)},
        )
        self.assertEqual(d["agents_without_launch_record"], {"agents": 2, "issue_guessed": 0, "no_issue": 2})
        # the planner's name is a sub-issue's agent in the backlog: its issue is guessed from it
        for args in (
            ["init", str(self.home), "--name", "fake", "--no-register"],
            ["backlog", "add", "-", "--id", "PF-2", "--title", "Plan", "--criterion", "done"],
            ["backlog", "sub", "PF-2", "T1", "--title", "the plan", "--agent", "plan2"],
        ):
            r = self.cli(*args)
            self.assertEqual(r.returncode, 0, r.stderr)
        d = self.js("--by", "issue", *self.window)
        self.assertEqual(self.rows(d, "issue")["PF-2"]["cache_read"], 9000)
        self.assertEqual(d["agents_without_launch_record"], {"agents": 2, "issue_guessed": 1, "no_issue": 1})
        text = self.stats("--by", "agent", *self.window)
        self.assertIn("PF-2 T1?", text)
        self.assertIn("1 matched an issue", text)

    def test_window_hours_and_top(self):
        d = self.js("--by", "hour", "--since", "2020-01-15T09:00+00:00", "--until", "2020-01-15T11:00+00:00")
        self.assertEqual(
            {k: r["calls"] for k, r in self.rows(d, "hour").items()}, {"2020-01-15 09": 2, "2020-01-15 10": 2}
        )
        d = self.js("--by", "agent", "--top", "1", *self.window)
        self.assertEqual(len(d["tables"]["agent"]), 1)
        self.assertIn("more rows", self.stats("--by", "agent", "--top", "1", *self.window))
        for bad in (["--since", "yesterday"], ["--top", "-1"]):
            self.assertEqual(self.dev("stats", *bad).returncode, 2)
        self.assertEqual(self.dev("stats", cwd=self.tmp / "elsewhere").returncode, 2)  # no product there

    def test_the_price_switch(self):
        saved = stats.SHOW_PRICE
        self.addCleanup(setattr, stats, "SHOW_PRICE", saved)
        stats.SHOW_PRICE = False
        cc = stats.claude_code(({"totalCostUSD": 3.0, "modelUsage": {"m": {"inputTokens": 1}}}, None))
        self.assertNotIn("price_usd", cc)
        self.assertEqual(stats.cc_lines([{"key": "x" * 8, "claude_code": cc}])[1].split("  ")[2][:5], "input")
        self.assertIsNone(stats.claude_code((None, None)))

    def test_usage_runs_claude_only_when_asked(self):
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        log = self.tmp / "claude-args"
        fake = bin_dir / "claude"
        fake.write_text(f"#!/bin/sh\necho \"$@\" > {log}\necho 'Current session: 12% used'\n")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}
        self.stats(*self.window, env=env)
        self.assertFalse(log.exists())
        text = self.stats("--usage", *self.window, env=env)
        self.assertIn("Current session: 12% used", text)
        self.assertEqual(log.read_text().split(), ["-p", "/usage", "--no-session-persistence"])
        d = self.js("--usage", *self.window, env=env)
        self.assertEqual(d["usage"], {"text": "Current session: 12% used", "error": None})
        fake.write_text("#!/bin/sh\necho broken >&2\nexit 3\n")
        d = self.js("--usage", *self.window, env=env)
        self.assertEqual(d["usage"]["error"], "claude -p /usage exited 3: broken")
        self.assertEqual(
            self.js("--usage", *self.window, env={"PATH": "/nonexistent"})["usage"]["error"],
            "claude is not on the PATH",
        )
