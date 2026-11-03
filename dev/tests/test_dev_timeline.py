"""`foreman-dev timeline` on a fixed day (tests/timeline_world.py): the sources merged in time order with the spend,
the filters, a pointer on every line, the brief of an agent."""

import json
import re

import timeline_world as W
from devtool import DevCase

DAY = ["--since", "2020-01-15", "--until", "2020-01-16"]


class DevTimelineTest(DevCase):
    def setUp(self):
        super().setUp()
        W.build(self.tmp / "claude-projects", self.project.state, self.project.notes_dir, "PF", "fake")

    def tl(self, *args, rc=0):
        r = self.dev("timeline", *args, env={"TZ": "UTC"})
        self.assertEqual(r.returncode, rc, r.stderr)
        return r.stdout

    def js(self, *args):
        return json.loads(self.tl(*DAY, "--json", *args))

    def test_every_source_merged_in_time_order(self):
        d = self.js()
        times = [e["time"] for e in d["events"]]
        self.assertEqual(times, sorted(times))
        self.assertEqual(
            {e["kind"] for e in d["events"]},
            {
                "session",
                "launch",
                "start",
                "progress",
                "block",
                "delivery",
                "stop",
                "spend",
                "ci",
                "integrate",
                "note",
                "loop",
                "inbox",
                "decision",
            },
        )
        self.assertTrue(all(e["time"].startswith("2020-01-15") for e in d["events"]))  # the day before is out
        kinds = [(e["time"][11:16], e["kind"], e["who"]) for e in d["events"] if e["who"] in ("dev1", "dev2")]
        self.assertEqual(
            kinds[:6],
            [
                ("09:04", "launch", "dev1"),
                ("09:05", "start", "dev1"),
                ("09:20", "progress", "dev1"),
                ("09:40", "spend", "dev1"),
                ("09:44", "delivery", "dev1"),
                ("09:45", "stop", "dev1"),
            ],
        )
        spend = next(e for e in d["events"] if e["kind"] == "spend" and e["who"] == "dev1")
        self.assertEqual((spend["calls"], spend["cache_read"], spend["output"]), (2, 12000, 1000))
        orch = next(e for e in d["events"] if e["kind"] == "spend" and e["who"] == "orch-a")
        self.assertEqual((orch["calls"], orch["cache_read"], orch["session"]), (3, 91000, W.S))
        ci = [e for e in d["events"] if e["kind"] == "ci"]
        self.assertEqual([(e["branch"], e["rc"]) for e in ci], [("pf1/t1", 0), ("pf1/t1", 0), ("pf2/t1", 1)])
        self.assertIn("FAILED rc 1", ci[-1]["text"])
        self.assertIn("on main", ci[1]["text"])
        stop2 = next(e for e in d["events"] if e["kind"] == "stop" and e["who"] == "dev2")
        self.assertIn("WITHOUT delivery", stop2["text"])
        self.assertIn("4200 chars", stop2["text"])
        launch3 = next(e for e in d["events"] if e["kind"] == "launch" and e["who"] == "dev3")
        self.assertIn("no agent took it", launch3["text"])

    def test_a_pointer_on_every_line(self):
        out = self.tl(*DAY, "--limit", "0").splitlines()
        events = [ln for ln in out if re.match(r"\d\d:\d\d ", ln)]
        self.assertGreater(len(events), 20)
        for ln in events:
            self.assertRegex(ln, r" @ \S+$")
        legend = next(ln for ln in out if ln.startswith("paths: "))
        self.assertIn("$state = ", legend)
        self.assertIn("$S1 = ", legend)
        self.assertIn(f"{W.S}", legend)
        self.assertTrue(any(ln.endswith("@ $S1/subagents/agent-a1.jsonl") for ln in events))
        self.assertTrue(any(ln.endswith("@ $notes/2020-01-15.md:6") for ln in events))  # the Integrated heading
        self.assertTrue(any("3 skipped passes until 11:00" in ln and ln.endswith("loop.jsonl:4-6") for ln in events))
        self.assertTrue(any(" loop      orch-a: skipped; 0.4 s" in ln for ln in events))  # one alone stays

    def test_a_measured_note_is_a_measure_event(self):
        notes = self.project.notes_dir / "2020-01-15.md"
        notes.write_text(notes.read_text() + "\n## 11:30 Measured fake:pf2/t1 (PF-2 T1)\nClosed as a measurement.\n")
        d = self.js("--kind", "measure")
        self.assertEqual(
            [(e["time"][11:16], e["kind"], e["branch"], e["issue"]) for e in d["events"]],
            [("11:30", "measure", "pf2/t1", "PF-2 T1")],
        )
        self.assertNotIn("Measured", " ".join(e["text"] for e in self.js("--kind", "integrate")["events"]))

    def test_filters(self):
        agent = self.js("--agent", "dev1")
        self.assertTrue(agent["events"])
        self.assertTrue(all(e["who"] == "dev1" or e["branch"] == "pf1/t1" for e in agent["events"]))
        self.assertIn("integrate", {e["kind"] for e in agent["events"]})
        issue = self.js("--issue", "PF-2")
        self.assertTrue(issue["events"])
        self.assertTrue(all(e["issue"].startswith("PF-2") for e in issue["events"]))
        self.assertIn("progress", {e["kind"] for e in issue["events"]})  # a line without agent_id, by its name
        self.assertEqual({e["issue"] for e in self.js("--issue", "PF-1 T1")["events"]}, {"PF-1 T1"})
        kinds = self.js("--kind", "ci,block", "--kind", "loop")
        self.assertEqual({e["kind"] for e in kinds["events"]}, {"ci", "block", "loop"})
        sess = self.js("--session", W.S[:8])
        self.assertTrue(sess["events"])
        self.assertTrue(all((e["session"] or "").startswith(W.S[:8]) for e in sess["events"]))
        self.assertEqual(self.js("--session", "ffff")["events"], [])
        limited = self.tl(*DAY, "--limit", "3").splitlines()
        self.assertEqual(len([ln for ln in limited if re.match(r"\d\d:\d\d ", ln)]), 3)
        self.assertTrue(any(ln.startswith("… ") and "earlier lines" in ln for ln in limited))
        self.assertIn("unknown kind", self.dev("timeline", "--kind", "nope").stderr)
        self.tl("--kind", "nope", rc=2)
        self.tl("--since", "yesterday", rc=2)

    def test_agent_prints_its_brief(self):
        out = self.tl(*DAY, "--agent", "dev1")
        self.assertIn("brief of dev1 (a1) @ $S1/subagents/agent-a1.jsonl", out)
        self.assertIn("  | " + W.BRIEF1, out)
        self.assertEqual(self.js("--agent", "dev1")["briefs"][0]["brief"].splitlines()[-1], W.BRIEF1)
        r = self.dev("timeline", *DAY, "--agent", "nobody", env={"TZ": "UTC"})
        self.assertEqual(r.returncode, 0)
        self.assertIn("no agent 'nobody'", r.stderr)
