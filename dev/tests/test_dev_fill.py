"""`foreman-dev fill SESSION` on fake transcripts (dev/tests/stats_world.py build_fill): growth split by
piece and weighed by re-reads, the largest tool outputs, foreman's notices by class, the start, and the «to spend
less» lines only when their rule holds; an agent's transcript; refusals."""

import json

import stats_world as W
from devtool import DevCase
from helpers import BASE_TOML, ROOT

from foreman_dev import fill


class FillTest(DevCase):
    toml = BASE_TOML

    def setUp(self):
        super().setUp()
        (self.tmp / "elsewhere").mkdir()
        W.build(
            self.tmp / "claude-projects",
            self.tmp / "state",
            {"name": "fake", "home": self.home, "repo": self.tmp / "app", "prefix": "PF"},
            {"name": "other", "home": self.tmp / "bhome", "repo": None, "prefix": "PF"},
            self.tmp / "elsewhere",
        )
        W.build_fill(self.tmp / "claude-projects", self.home, self.tmp / "state" / "fake")

    def run_fill(self, *args, rc=0):
        r = self.dev("fill", *args, env={"TZ": "UTC"})
        self.assertEqual(r.returncode, rc, r.stderr + r.stdout)
        return r

    def js(self, *args):
        return json.loads(self.run_fill(*args, "--json").stdout)

    def rows(self, d):
        return {r["key"]: r for r in d["rows"]}

    def test_growth_by_piece_and_reread(self):
        d = self.js(W.S_FILL[:8])
        self.assertEqual(
            (d["kind"], d["calls"], d["ctx_first"], d["ctx_last"], d["drops"]), ("session", 8, 20000, 150000, 1)
        )
        r = self.rows(d)
        top = [k for k, _, lvl in fill.ROWS if lvl == 0]
        self.assertAlmostEqual(
            sum(r[k]["growth"] for k in top), 25000 + 8400 + 12000 + 40000 + 3000 + 101600, delta=len(top)
        )  # each row rounded
        # own output: every response before a growing interval (the one before the compaction is not split)
        self.assertEqual(r["own"]["growth"], 1000 + 200 + 500 + 300 + 100 + 200)
        self.assertEqual(r["out_thinking"]["growth"], 400)  # Claude Code's own thinking count
        self.assertGreater(r["out_agent"]["growth"], r["out_foreman"]["growth"])  # a 2,000-char brief, a 600 note
        self.assertGreater(r["out_foreman"]["growth"], 0)
        # the sed output is alone in its interval: exact, and re-read by the 6 calls after it entered
        self.assertEqual((r["bash_read"]["growth"], r["bash_read"]["reread"]), (24000, 24000 * 6))
        self.assertEqual((r["bash_integrate"]["growth"], r["bash_integrate"]["reread"]), (101400, 0))
        self.assertGreater(r["bash_agents"]["growth"], 0)
        self.assertEqual(
            r["bash"]["growth"],
            sum(r[k]["growth"] for k in ("bash_read", "bash_agents", "bash_integrate", "bash_other")),
        )
        # a ToolSearch result has no text: alone in its interval, it takes the whole of it
        self.assertAlmostEqual(r["tools_other"]["growth"], 2900 + 8200 * 9 / self.hook_chars(), delta=1)
        self.assertGreater(r["read"]["growth"], 0)  # an image counts as IMAGE_CHARS
        for k in ("finals", "agent_result", "attachments", "peer_skill", "prompts", "hooks_foreman", "hooks_other"):
            self.assertGreater(r[k]["growth"], 0, k)
        self.assertEqual(r["unattributed"]["growth"], 0)

    def hook_chars(self):
        """The characters of what entered between c1 and c2 (the loop tool's result and the hook texts)."""
        pass_text = (
            "Loop pass 09:10 (heartbeat written, 0.0 s). Act on each line (those marked (info) only "
            "inform):\n- WIP (pipeline not full): unblocked ready 0/2 (refining 0): nothing to plan."
        )
        return len("scheduled") + 11 * len(W.WIP) + len(pass_text) + len("lint: nothing to fix")

    def test_largest_outputs_notices_finals_and_start(self):
        d = self.js(W.S_FILL)
        big = d["largest"][0]
        self.assertEqual(
            (big["time"], big["chars"], big["tokens"], big["tool"]),
            ("09:00", len(W.BIG), 24000, "Bash sed -n 1,3000p big.py"),
        )
        self.assertEqual(big["text"], " ".join(W.BIG.split())[:80])
        self.assertIn("Read shot.png", [x["tool"] for x in d["largest"]])
        h = {x["class"]: x for x in d["hooks"]}
        self.assertEqual(
            {k: (x["count"], x["foreman"]) for k, x in h.items()},
            {
                "wip_production": (11, True),
                "wip_ready": (1, True),
                "loop_pass": (1, True),
                "other: lint: nothing to fix": (1, False),
            },
        )
        self.assertEqual(d["finals"], {"count": 3, "median_chars": 2000})
        self.assertEqual(d["loop"], {"at": "2020-01-10 09:10", "context": 45000})
        self.assertEqual(d["growth_per_hour"], 130000)
        rules = {x["rule"]: x["figure"] for x in d["spend_less"]}
        self.assertEqual(
            rules, {"finals_median": 2000, "growth_per_hour": 130000, "start": 45000, "repeated_notice": 11}
        )
        text = self.run_fill(W.S_FILL[:8]).stdout
        for s in (
            "to spend less:",
            "notice wip_production came 11 times",
            "median 2 000 characters over 3",
            "45.0K before the first loop pass (09:10)",
            "a compaction: not split",
            "largest tool outputs",
        ):
            self.assertIn(s, text)

    def test_quiet_session_and_an_agent(self):
        d = self.js(W.S_OTHER[:8])
        self.assertEqual(d["spend_less"], [])
        self.assertIsNone(d["loop"])
        r = self.rows(d)
        self.assertEqual((r["own"]["growth"], r["unattributed"]["growth"]), (40, 260))  # nothing entered: not split
        text = self.run_fill(W.S_OTHER[:8]).stdout
        self.assertIn("to spend less: nothing over its threshold", text)
        self.assertIn("no loop pass in this session", text)
        for arg in (
            W.A_FILL[:7],
            "agent-" + W.A_FILL,
            str(self.tmp / "claude-projects" / "-fill" / W.S_FILL / "subagents" / f"agent-{W.A_FILL}.jsonl"),
        ):
            d = self.js(arg)
            self.assertEqual(
                (d["kind"], d["session"], d["who"], d["calls"], d["growth"]), ("agent", W.A_FILL, "dev1", 2, 5400), arg
            )
            self.assertEqual(self.rows(d)["bash_read"]["growth"], 5000)

    def test_refused(self):
        self.assertIn("no session or agent transcript", self.run_fill("nope", rc=2).stderr)
        self.assertIn("matches 3 transcripts", self.run_fill("a", rc=2).stderr)  # agents a1, a2, a3
        r = self.dev("fill", W.S_FILL, "--by", "agent")
        self.assertEqual(r.returncode, 2)
        self.assertIn("unrecognized arguments: --by", r.stderr)

    def test_the_loops_openings_are_still_in_its_code(self):
        code = "".join((ROOT / "lib/foreman" / f).read_text() for f in ("loop.py", "notices.py"))
        for kind, opening in fill.LOOP_OPENINGS:
            self.assertIn(opening, code, kind)
            if kind != fill.LOOP_HEAD:
                self.assertIn(f'"{kind}"', code, kind)

    def test_commands_and_notices(self):
        self.assertEqual(
            fill.command_kind("cd /x && FOREMAN_WHO=a bin/foreman --project p integrate p b"), ("foreman", "integrate")
        )
        self.assertEqual(fill.command_kind("sed -n 1,9p a | head"), ("sed", None))
        self.assertEqual(fill.foreman_subcommands('F="foreman --project p backlog sub X"\n$F T1'), ["backlog"])
        self.assertEqual(fill.foreman_subcommands('a(){ foreman note "$1"; }'), ["note"])
        ops = fill.openings()
        self.assertEqual(
            fill.notices_in(W.WIP + " " + W.WIP, ops),
            [("wip_production", len(W.WIP) + 1), ("wip_production", len(W.WIP))],
        )
        self.assertEqual(fill.notices_in("unrelated text", ops), [])
        es = "Esta sesión corre foreman 0.25.0 (/x); la instalada es 0.26.0 (/y)."
        self.assertEqual([k for k, _ in fill.notices_in(es, ops)], ["version_behind"])  # the catalogue, in Spanish
        self.assertEqual(fill.median([3, 1, 2]), 2)
        self.assertEqual(fill.median([1, 2, 3, 10]), 2.5)
