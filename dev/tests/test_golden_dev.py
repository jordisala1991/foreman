"""The developer tool's golden corpus: `foreman-dev stats|fill|timeline` on fixed fake worlds (dev/tests/stats_world.py,
tests/timeline_world.py), in UTC, with explicit windows, compared byte for byte with dev/tests/golden/ (the same
world and Normaliser as foreman's own corpus, tests/test_golden.py; `bin/foreman selftest --update-golden` rewrites
these files too)."""

import json
import re

import devtool
from test_golden import GoldenCase

GOLDEN = devtool.DEV / "tests/golden"


class DevGoldenCase(GoldenCase):
    def tool(self, *args, project=None, env=None):
        """`foreman-dev [--project P] ARGS…`, recorded in the transcript."""
        full = (["--project", project] if project else []) + list(args)
        r = devtool.run_tool(full, dict(self.env, **(env or {})), self.tmp)
        shown = " ".join(
            a if a and re.fullmatch(r"[\w./:=@+,-]+", a) else json.dumps(a, ensure_ascii=False) for a in full
        )
        prefix = " ".join(f"{k}={v}" for k, v in (env or {}).items())
        self.t.call((prefix + " " if prefix else "") + "foreman-dev " + shown, r)
        return r


class GoldenStats(DevGoldenCase):
    """`foreman-dev stats` and `fill` on fixed fake transcripts (stats_world.py)."""

    def test_stats(self):
        import stats_world

        (self.tmp / "elsewhere").mkdir()
        stats_world.build(
            self.tmp / "claude-projects",
            self.tmp / "state",
            {"name": "faro", "home": self.homes["faro"], "repo": self.tmp / "faro-app", "prefix": "FA"},
            {"name": "harbor", "home": self.homes["harbor"], "repo": self.tmp / "harbor-api", "prefix": "HB"},
            self.tmp / "elsewhere",
        )
        day = ["--since", "2020-01-15T00:00+00:00", "--until", "2020-01-16T00:00+00:00"]
        utc = {"TZ": "UTC"}
        self.t.section("one product, the summary")
        self.tool("stats", *day, project="faro", env=utc)
        self.tool("stats", *day, "--json", project="faro", env=utc)
        self.t.section("every product, by each grouping")
        for by in ("project", "session", "agent", "issue", "type", "model", "hour"):
            self.tool("stats", "--all", "--by", by, *day, env=utc)
        self.tool("stats", "--all", "--by", "session", *day, "--json", env=utc)
        self.t.section("one whole session, against Claude Code's own figures")
        self.tool("stats", "--session", stats_world.S_ORCH[:8], "--until", "2020-01-16T00:00+00:00", env=utc)
        self.t.section("what fills one session's context (fill), and an agent's")
        stats_world.build_fill(self.tmp / "claude-projects", self.homes["faro"], self.tmp / "state" / "faro")
        self.tool("fill", stats_world.S_FILL[:8], project="faro", env=utc)
        self.tool("fill", stats_world.S_FILL[:8], "--json", project="faro", env=utc)
        self.tool("fill", stats_world.S_OTHER[:8], project="faro", env=utc)
        self.tool("fill", stats_world.A_FILL, project="faro", env=utc)
        self.t.section("refused")
        self.tool("stats", "--since", "yesterday", project="faro", env=utc)
        self.tool("stats", env=utc)
        self.tool("fill", "nope", project="faro", env=utc)
        self.tool("fill", "a", project="faro", env=utc)
        self.check("stats.txt", GOLDEN)


class GoldenTimeline(DevGoldenCase):
    """`foreman-dev timeline` on a fixed day of one product (timeline_world.py)."""

    def test_timeline(self):
        import timeline_world

        timeline_world.build(
            self.tmp / "claude-projects", self.tmp / "state" / "faro", self.homes["faro"] / "notes", "FA", "faro"
        )
        day = ["--since", "2020-01-15", "--until", "2020-01-16"]
        utc = {"TZ": "UTC"}
        self.t.section("the whole day, as text and as JSON")
        self.tool("timeline", *day, project="faro", env=utc)
        self.tool("timeline", *day, "--json", "--limit", "5", project="faro", env=utc)
        self.t.section("filters")
        self.tool("timeline", *day, "--agent", "dev2", project="faro", env=utc)
        self.tool("timeline", *day, "--issue", "FA-1", "--kind", "ci,integrate,delivery", project="faro", env=utc)
        self.tool("timeline", *day, "--session", timeline_world.S[:8], "--kind", "spend", project="faro", env=utc)
        self.tool("timeline", "--since", "2020-01-14 23:00", "--until", "2020-01-15 09:05", project="faro", env=utc)
        self.t.section("refused")
        self.tool("timeline", "--kind", "nope", project="faro", env=utc)
        self.tool("timeline", "--limit", "-1", project="faro", env=utc)
        self.check("timeline.txt", GOLDEN)
