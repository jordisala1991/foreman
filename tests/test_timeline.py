"""The product's history (lib/foreman/timeline.py, what `foreman catchup` reads) on a fixed day
(tests/timeline_world.py): the sources merged in time order, without reading a transcript for spend; and loop.jsonl,
the history of the loop passes. Printing it, with the spend, is the developer tool's
(dev/tests/test_dev_timeline.py)."""

import json
import os
from datetime import datetime
from unittest import mock

import timeline_world as W
from helpers import ProjectCase

from foreman import loop, notices, timeline, transcripts

SINCE, UNTIL = datetime(2020, 1, 15), datetime(2020, 1, 16)


class TimelineTest(ProjectCase):
    def setUp(self):
        super().setUp()
        W.build(self.tmp / "claude-projects", self.project.state, self.project.notes_dir, "PF", "fake")

    def collect(self, kinds=timeline.KINDS):
        with mock.patch.object(transcripts, "calls", side_effect=AssertionError("no spend in the collector")):
            return timeline.collect(self.project, SINCE, UNTIL, set(kinds))

    def test_every_source_merged_in_time_order(self):
        self.assertNotIn("spend", timeline.KINDS)
        w, evs = self.collect()
        times = [e["t"] for e in evs]
        self.assertEqual(times, sorted(times))
        self.assertTrue(all(SINCE <= t < UNTIL for t in times))  # the day before is out
        self.assertEqual(
            {e["kind"] for e in evs},
            {
                "session",
                "launch",
                "start",
                "progress",
                "block",
                "delivery",
                "stop",
                "ci",
                "integrate",
                "note",
                "loop",
                "inbox",
                "decision",
            },
        )
        dev1 = [(f"{e['t']:%H:%M}", e["kind"]) for e in evs if e["who"] == "dev1"]
        self.assertEqual(
            dev1[:5],
            [("09:04", "launch"), ("09:05", "start"), ("09:20", "progress"), ("09:44", "delivery"), ("09:45", "stop")],
        )
        self.assertTrue(all(e["ref"] for e in evs))  # where to dig, on every event
        self.assertEqual(w.orchestrators.get(W.S), "orch-a")  # the sessions of the orchestrators, for the spend
        self.assertEqual((w.mark or {}).get("session"), W.S)
        ci = [e for e in evs if e["kind"] == "ci"]
        self.assertEqual([(e["branch"], e["data"]["rc"]) for e in ci], [("pf1/t1", 0), ("pf1/t1", 0), ("pf2/t1", 1)])
        stop2 = next(e for e in evs if e["kind"] == "stop" and e["who"] == "dev2")
        self.assertIn("WITHOUT delivery", stop2["text"])
        _, only = self.collect({"ci", "block"})
        self.assertEqual({e["kind"] for e in only}, {"ci", "block"})

    def test_stats_and_timeline_are_no_commands(self):
        for cmd in ("stats", "timeline"):
            r = self.cli(cmd)
            self.assertEqual(r.returncode, 2, cmd)
            self.assertIn("invalid choice", r.stderr)
        self.assertNotRegex(self.cli("--help").stdout, r"(?m)^(stats|timeline) ")

    def test_a_broken_source_is_skipped(self):
        st = self.project.state
        with open(st / "progress.jsonl", "a") as f:
            f.write("not json\n")
        with open(st / "loop.jsonl", "a") as f:
            f.write("{broken\n")
        (st / "integrate" / "pf9-t1-ci.log").write_text(
            "== 2020-01-15 12:00:00 in /wt/pf9: make check\n== could not start: no such file\n"
            "== 2020-01-15 12:10:00 in /wt/pf9: make check\nstill running\n"
        )
        notes = self.project.notes_dir / "2020-01-15.md"
        notes.write_text(notes.read_text() + "\n## 25:99 not a time\n")
        _, evs = self.collect()
        ci = [e["text"] for e in evs if e["kind"] == "ci" and e["branch"] == "pf9-t1"]
        self.assertEqual(len(ci), 2)
        self.assertIn("could not start: no such file", ci[0])
        self.assertIn("no result in the log", ci[1])
        self.assertNotIn("not a time", " ".join(e["text"] for e in evs))
        (st / "progress.jsonl").unlink()
        (st / "loop.jsonl").unlink()
        _, evs = self.collect()
        self.assertFalse([e for e in evs if e["kind"] in ("progress", "delivery", "loop")])

    def test_a_measured_note_is_a_measure_event(self):
        notes = self.project.notes_dir / "2020-01-15.md"
        notes.write_text(notes.read_text() + "\n## 11:30 Measured fake:pf2/t1 (PF-2 T1)\nClosed as a measurement.\n")
        _, evs = self.collect({"measure"})
        self.assertEqual(
            [(f"{e['t']:%H:%M}", e["kind"], e["branch"], e["issue"]) for e in evs],
            [("11:30", "measure", "pf2/t1", "PF-2 T1")],
        )
        _, integrated = self.collect({"integrate"})
        self.assertNotIn("Measured", " ".join(e["text"] for e in integrated))

    def test_loop_pass_writes_its_line(self):
        f = loop.loop_log_file(self.project)
        f.unlink()
        _text, ns = loop.loop_pass(self.project, "sess-1")
        rows = [json.loads(x) for x in f.read_text().splitlines()]
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(set(r), {"time", "session", "skipped", "notices", "actionable", "duration_s"})
        self.assertEqual(r["session"], "sess-1")
        self.assertEqual(r["notices"], [n["kind"] for n in ns])
        self.assertEqual(r["actionable"], [n["kind"] for n in ns if n["actionable"]])
        self.assertEqual(r["skipped"], notices.idle(ns))

    def test_loop_log_keeps_thirty_days(self):
        f = loop.loop_log_file(self.project)
        f.write_text(json.dumps({"time": "2000-01-01T00:00:00", "skipped": True}) + "\n" + "x" * 64 + "\n")
        saved = loop.LOOP_LOG_TRIM_BYTES
        loop.LOOP_LOG_TRIM_BYTES = 10
        try:
            loop.log_pass(self.project, "s", [], 0.01)
        finally:
            loop.LOOP_LOG_TRIM_BYTES = saved
        rows = f.read_text().splitlines()
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0].startswith('{"time": "2000'))
        self.assertFalse(os.path.exists(str(f) + ".tmp"))
