"""What an agent run cost (lib/foreman/costs.py): exact sums from a fixture transcript (a response written twice, two
models, a call with cache reads only), kept beside the backlog, replaced when the agent delivers again, and read by
`foreman agents --cost`."""

import json

from helpers import ProjectCase
from test_identity import TOML
from test_transcripts import rec

from foreman import costs, util


class CostsTest(ProjectCase):
    toml = TOML

    def setUp(self):
        super().setUp()
        d = self.tmp / "claude-projects" / "-p" / "S1" / "subagents"
        d.mkdir(parents=True)
        self.f = d / "agent-a1.jsonl"
        brief = {
            "type": "user",
            "message": {"role": "user", "content": "[foreman launch abcdefghij] zed · AB-1 T1 · x"},
        }
        usage = {
            "input_tokens": 1000,
            "cache_creation_input_tokens": 2000,
            "cache_creation": {"ephemeral_1h_input_tokens": 500},
            "cache_read_input_tokens": 10000,
        }
        self.lines = [
            json.dumps(brief, ensure_ascii=False),
            rec("m1", ts="2026-11-06T10:00:00Z", output_tokens=50, **usage),
            rec("m1", ts="2026-11-06T10:00:00Z", output_tokens=100, **usage),  # the same response again
            rec(
                "m2",
                ts="2026-11-06T10:30:00Z",
                model="claude-sonnet-5-5",
                input_tokens=0,
                cache_read_input_tokens=4000,
                cache_creation_input_tokens=0,
                output_tokens=0,
            ),
            rec("m3", ts="2026-11-06T10:31:00Z", model="<synthetic>", input_tokens=0),
        ]
        self.f.write_text("".join(ln + "\n" for ln in self.lines))
        d.joinpath("agent-a1.meta.json").write_text('{"agentType": "foreman:dev"}')
        util.write_json(
            self.project.state / "agents.json",
            {
                "a1": {
                    "issue": "AB-1",
                    "sub": "T1",
                    "type": "foreman:dev",
                    "name": "zed",
                    "transcript": str(self.f),
                    "start": "2026-11-06T10:00:00",
                }
            },
        )

    def test_exact_sums_and_prices(self):
        run = costs.build(str(self.f), "a1", "AB-1", "T1", "foreman:dev", "zed", "ok")
        o = run["models"]["claude-opus-5-5"]
        # opus, per MTok: 1000 x 4 + 1500 x 5 + 500 x 8 + 10000 x 0.2 + 100 x 20 = 19500 micro-dollars
        self.assertEqual((o["tokens"], o["usd"]), (13100, 0.0195))
        # sonnet: 4000 cache reads x 0.1
        self.assertEqual(run["models"]["claude-sonnet-5-5"]["usd"], 0.0004)
        self.assertEqual((run["tokens"], run["usd"], run["minutes"], run["type"]), (17100, 0.0199, 30.0, "dev"))

    def test_a_delivery_is_kept_and_replaced_by_the_next(self):
        self.cli("progress", "--done", "x", env={**self.env, "FOREMAN_WHO": "zed"})
        self.assertEqual(len(costs.runs(self.project)), 1)
        self.cli("progress", "--done", "y", env={**self.env, "FOREMAN_WHO": "zed"})
        (r,) = costs.runs(self.project)
        self.assertEqual((r["issue"], r["sub"], r["deliveries"]), ("AB-1", "T1", 2))

    def test_the_view(self):
        costs.store(self.project, [costs.build(str(self.f), "a1", "AB-1", "T1", "dev", "zed", "ok")])
        costs.store(self.project, [costs.build(str(self.f), "a2", "AB-1", "T1", "reviewer", "r", "ok")])
        res = json.loads(self.cli("agents", "--cost", "--by", "model", "--json").stdout)
        self.assertEqual([(r["key"], r["tokens"]) for r in res["rows"]], [("opus", 26200), ("sonnet", 8000)])
        res = json.loads(self.cli("agents", "--cost", "--json").stdout)
        (row,) = res["rows"]
        self.assertEqual(
            (row["key"], row["runs"], row["rework"], row["usd"], row["cheap_share"]), ("AB-1", 2, 0, 0.04, 23.4)
        )
        res = json.loads(self.cli("agents", "--cost", "--by", "week", "--since", "2026-11-07", "--json").stdout)
        self.assertEqual(res["rows"], [])
        self.assertIn("AB-1", self.cli("agents", "--cost").stdout)

    def test_the_pass_records_a_stopped_agent_and_backfill_the_old_ones(self):
        costs.sweep(self.project)  # never delivered, not working: stopped for lack of news
        self.assertEqual(costs.runs(self.project), [])  # still counts as live (waiting)
        agents = util.read_json(self.project.state / "agents.json", {})
        agents["a1"]["stopped"] = True
        util.write_json(self.project.state / "agents.json", agents)
        costs.sweep(self.project)
        self.assertEqual([(r["agent"], r["outcome"]) for r in costs.runs(self.project)], [("a1", "stopped")])
        self.assertEqual(costs.backfill(self.project, "2000-01-01"), 1)
        (r,) = costs.runs(self.project)
        self.assertEqual((r["issue"], r["sub"], r["type"]), ("AB-1", "T1", "dev"))
