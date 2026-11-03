import json
import os
import subprocess
import time
import unittest
from datetime import datetime

from helpers import ProjectCase

from foreman import notes, util

TOML = """
[project]
name = "fake"
owner = "Owner"
language = "es"

[resources.widget]

[[recurring]]
name = "Loop"
every = "hourly"
sources = [{ heartbeat = "loop", max_h = 1 }]

[[recurring]]
name = "Health"
every = "daily"
sources = [{ label = "ran", glob = "reviews/*-health.md", max_h = 26 }]
"""


class NotesTest(ProjectCase):
    toml = TOML

    def test_note_entries_and_language(self):
        r = self.cli("note", "Result", "3.2 s per token (reviews/x.md)")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.cli("note", "-", "a one-liner")
        self.cli("note", "From stdin", inp="piped text\n")
        text = (self.home / "notes" / f"{datetime.now():%Y-%m-%d}.md").read_text()
        self.assertTrue(text.startswith(f"# Notas {datetime.now():%Y-%m-%d}\n"))
        self.assertRegex(text, r"\n## \d\d:\d\d Result\n3.2 s per token")
        self.assertRegex(text, r"\n- \d\d:\d\d a one-liner\n")
        self.assertIn("From stdin\npiped text\n", text)
        r = self.cli("note", "Only a title", inp="")
        self.assertEqual(r.returncode, 2)

    def test_note_lock_file_lives_in_the_state_not_among_the_notes(self):
        self.cli("note", "Result", "text")
        day = f"{datetime.now():%Y-%m-%d}.md"
        self.assertEqual(sorted(p.name for p in (self.home / "notes").iterdir()), [day])
        self.assertTrue((self.project.state / "locks-notes" / (day + ".lock")).exists())

    def test_progress_inbox_heartbeat(self):
        self.cli("progress", "first result: 12 ms", env={"AGENT": "zed"})
        line = json.loads((self.project.state / "progress.jsonl").read_text())
        self.assertEqual((line["agent"], line["text"]), ("zed", "first result: 12 ms"))
        self.cli("inbox", "add", "can you look at the login bug?")
        self.cli("inbox", "reply", "on it")
        r = self.cli("inbox", "new")
        self.assertIn("message: can you look at the login bug?", r.stdout)
        self.assertNotIn("on it", r.stdout)
        self.cli("inbox", "read")
        self.assertEqual(self.cli("inbox", "new").stdout, "")
        self.cli("recurring", "done", "loop")
        r = self.cli("recurring")
        self.assertIn("ok   Loop", r.stdout)
        self.assertIn("LATE Health", r.stdout)
        (self.home / "reviews").mkdir()
        (self.home / "reviews/2026-10-30-health.md").write_text("x")
        self.assertIn("ok   Health", self.cli("recurring").stdout)


AT_TOML = (
    TOML
    + """
[[recurring]]
name = "Watch"
every = "daily at 09:30"
sources = [
    { label = "ran", glob = "reviews/*-watch.md", at = "09:30" },
    { label = "read", heartbeat = "read-watch", at = "09:30" },
]

[[recurring]]
name = "Late night"
sources = [{ heartbeat = "night", at = "23:30" }]

[[recurring]]
name = "Lint"
sources = [{ log = "lint", file = "log.md", at = "13:00" }]
"""
)


class RecurringAtTest(ProjectCase):
    """A source with at = "HH:MM" is late once today's HH:MM has passed and it has nothing after it (and,
    before that time, while yesterday's has nothing after it). A fixed clock, local time."""

    toml = AT_TOML

    def setUp(self):
        super().setUp()
        (self.home / "reviews").mkdir()

    def state(self, when):
        return {
            t["name"]: (t["late"], [p["late"] for p in t["parts"]])
            for t in notes.recurring(self.project, when=datetime.fromisoformat(when))
        }

    def ran(self, when, beat="read-watch"):
        """The watch's report written and its heartbeat at `when`."""
        f = self.home / f"reviews/{when[:10]}-watch.md"
        f.write_text("x")
        ts = datetime.fromisoformat(when).timestamp()
        os.utime(f, (ts, ts))
        beats = notes.heartbeats(self.project)
        beats[beat] = when
        util.write_json(self.project.state / "heartbeats.json", beats)

    def test_before_and_after_the_time(self):
        self.ran("2026-10-30T09:40:00")  # yesterday's, done
        self.assertEqual(self.state("2026-10-31T09:29:00")["Watch"], (False, [False, False]))
        self.assertEqual(self.state("2026-10-31T09:31:00")["Watch"], (True, [True, True]))
        self.ran("2026-10-31T09:10:00")  # before 09:30: not today's
        self.assertEqual(self.state("2026-10-31T09:31:00")["Watch"], (True, [True, True]))
        self.ran("2026-10-31T09:45:00")
        self.assertEqual(self.state("2026-10-31T09:50:00")["Watch"], (False, [False, False]))
        self.assertEqual(self.state("2026-10-31T23:59:00")["Watch"], (False, [False, False]))
        self.assertEqual(self.state("2026-11-01T09:29:00")["Watch"], (False, [False, False]))
        self.assertEqual(self.state("2026-11-01T09:30:00")["Watch"], (True, [True, True]))

    def test_one_source_missing(self):
        self.ran("2026-10-31T09:45:00")
        beats = notes.heartbeats(self.project)
        beats["read-watch"] = "2026-10-31T08:00:00"
        util.write_json(self.project.state / "heartbeats.json", beats)
        self.assertEqual(self.state("2026-10-31T10:00:00")["Watch"], (True, [False, True]))

    def test_never_done_and_missed_day(self):
        self.assertTrue(self.state("2026-10-31T09:31:00")["Watch"][0])
        self.ran("2026-10-29T09:40:00")  # yesterday missed
        self.assertTrue(self.state("2026-10-31T09:00:00")["Watch"][0])  # late until it is done

    def test_across_midnight(self):
        beats = {"night": "2026-10-30T23:45:00"}
        util.write_json(self.project.state / "heartbeats.json", beats)
        self.assertFalse(self.state("2026-10-31T00:15:00")["Late night"][0])  # yesterday's 23:30 is done
        self.assertFalse(self.state("2026-10-31T23:29:00")["Late night"][0])
        self.assertTrue(self.state("2026-10-31T23:31:00")["Late night"][0])
        util.write_json(self.project.state / "heartbeats.json", {"night": "2026-10-30T23:10:00"})
        self.assertTrue(self.state("2026-10-31T00:15:00")["Late night"][0])  # nothing after yesterday's 23:30

    def test_log_entry_counts_for_its_day(self):
        (self.home / "log.md").write_text("# Log\n\n## [2026-10-30] lint of the pages\n")
        self.assertFalse(self.state("2026-10-31T12:59:00")["Lint"][0])
        self.assertTrue(self.state("2026-10-31T13:01:00")["Lint"][0])
        (self.home / "log.md").write_text("# Log\n\n## [2026-10-31] lint of the pages\n")
        self.assertFalse(self.state("2026-10-31T18:00:00")["Lint"][0])

    def test_cli_says_the_time(self):
        self.assertIn("ran never (daily at 09:30) LATE", self.cli("recurring").stdout)


class WaitsTest(ProjectCase):
    toml = TOML

    def test_wait_modes(self):
        # an orphan (not our child: a child of ours would stay a zombie until we reap it)
        pid = subprocess.run(
            ["bash", "-c", "sleep 0.5 >/dev/null 2>&1 & echo $!"], capture_output=True, text=True, check=False
        ).stdout.strip()
        r = self.cli("wait", "-t", "20", "-i", "0.1", "pid", pid)
        self.assertEqual(r.returncode, 0)
        f = self.tmp / "log"
        f.write_text("starting\n")
        r = self.cli("wait", "-t", "0.5", "-i", "0.1", "line", str(f), "^rc=")
        self.assertEqual(r.returncode, 124)
        f.write_text("starting\nrc=0\n")
        self.assertEqual(self.cli("wait", "-i", "0.1", "line", str(f), "^rc=").returncode, 0)
        self.assertEqual(self.cli("wait", "-i", "0.1", "file", str(f)).returncode, 0)
        self.assertEqual(self.cli("wait", "-i", "0.1", "lock", "widget", "zed").returncode, 0)
        self.assertEqual(self.cli("wait", "bogus", "x").returncode, 2)

    def test_wait_lock_holder(self):
        self.cli("lock", "widget", "acquire", "hold", "5", env={"FOREMAN_LOCK_WHO": "zed"})
        r = self.cli("wait", "-t", "0.6", "-i", "0.1", "lock", "widget", "zed")
        self.assertEqual(r.returncode, 124)
        self.cli("lock", "widget", "release", env={"FOREMAN_LOCK_WHO": "zed"})
        self.assertEqual(self.cli("wait", "-i", "0.1", "lock", "widget", "zed").returncode, 0)

    def test_time_limit(self):
        t0 = time.monotonic()
        r = self.cli("time-limit", "1", "bash", "-c", "sleep 30; echo never")
        self.assertEqual(r.returncode, 124)
        self.assertLess(time.monotonic() - t0, 10)
        self.assertEqual(self.cli("time-limit", "5", "bash", "-c", "exit 3").returncode, 3)

    def test_hung_shells_runs(self):
        r = self.cli("hung-shells")
        self.assertIn(r.returncode, (0, 1))


if __name__ == "__main__":
    unittest.main()
