"""lib/foreman/util.py: the helpers with no foreman dependencies, and lock.people."""

import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from foreman import lock, util


class UtilTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.tmp)])

    def test_json_and_atomic_write(self):
        f = self.tmp / "a" / "b.json"
        self.assertEqual(util.read_json(f, {"x": 1}), {"x": 1})  # missing
        util.write_json(f, {"name": "Zoë", "n": [1, 2]})
        self.assertEqual(util.read_json(f, None), {"name": "Zoë", "n": [1, 2]})
        self.assertIn("Zoë", f.read_text(encoding="utf-8"))  # not escaped
        f.write_text("{not json")
        self.assertEqual(util.read_json(f, []), [])
        # only when the content changes; the file keeps its permissions; no temporary file is left behind
        t = self.tmp / "page.md"
        self.assertTrue(util.write_text_atomic(t, "one\n"))
        self.assertEqual(t.stat().st_mode & 0o777, 0o644)
        t.chmod(0o600)
        before = t.stat().st_mtime_ns
        self.assertFalse(util.write_text_atomic(t, "one\n"))
        self.assertEqual(t.stat().st_mtime_ns, before)
        self.assertTrue(util.write_text_atomic(t, "two\n"))
        self.assertEqual((t.read_text(), t.stat().st_mode & 0o777), ("two\n", 0o600))
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ["a", "page.md"])
        self.assertEqual([p.name for p in f.parent.iterdir()], ["b.json"])

    def test_iso_epoch(self):
        self.assertEqual(util.iso_epoch("2026-10-31T07:18:00"), datetime(2026, 10, 31, 7, 18).timestamp())
        self.assertEqual(util.iso_epoch("2026-10-31T07:18:00+00:00"), 1793431080.0)
        for bad in (None, "", "yesterday", 12, "2026-13-01T00:00:00"):
            self.assertIsNone(util.iso_epoch(bad), bad)

    def test_fromiso_reads_what_311_reads_on_every_python(self):
        """Claude Code's `…Z` times and fractions of any length read the same on 3.9 as on 3.11+."""
        utc = datetime(2026, 10, 31, 7, 18, tzinfo=timezone.utc)
        self.assertEqual(util.fromiso("2026-10-31T07:18:00.000Z"), utc)
        self.assertEqual(util.fromiso("2026-10-31T07:18:00z"), utc)
        self.assertEqual(util.iso_epoch("2026-10-31T07:18:00Z"), 1793431080.0)
        self.assertEqual(util.fromiso("2026-10-31 07:18:00.1234567+00:00"), utc.replace(microsecond=123456))
        self.assertEqual(util.fromiso("2026-10-31T07:18:00.5"), datetime(2026, 10, 31, 7, 18, 0, 500000))
        self.assertEqual(util.fromiso(" 2026-10-31 "), datetime(2026, 10, 31))
        for bad in ("", "yesterday", "2026-13-01T00:00:00Z"):
            with self.assertRaises(ValueError, msg=bad):
                util.fromiso(bad)

    def test_parse_when(self):
        now = datetime(2020, 1, 15, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(util.parse_when("24h", now), now - timedelta(hours=24))
        self.assertEqual(util.parse_when("7d", now), now - timedelta(days=7))
        self.assertEqual(util.parse_when("30m", now), now - timedelta(minutes=30))
        self.assertEqual(util.parse_when("09:30", now), now.replace(hour=9, minute=30))
        self.assertEqual(util.parse_when("2020-01-15T10:00+00:00", now), now.replace(hour=10))
        self.assertIsNotNone(util.parse_when("2020-01-15", now).tzinfo)
        for bad in ("", "soon", "25:99:00"):
            with self.assertRaises(ValueError):
                util.parse_when(bad, now)

    def test_pid_alive(self):
        self.assertTrue(util.pid_alive(os.getpid()))
        self.assertTrue(util.pid_alive(str(os.getpid())))
        p = subprocess.Popen(["true"])
        p.wait()
        self.assertFalse(util.pid_alive(p.pid))
        self.assertFalse(util.pid_alive("x"))
        self.assertIsNone(util.pid_alive(""))
        self.assertIsNone(util.pid_alive(None))

    def test_now_follows_foreman_now(self):
        with mock.patch.dict(os.environ, {"FOREMAN_NOW": "2020-03-04T23:50:00"}):
            a = util.now()
            self.assertEqual(a.replace(second=0), datetime(2020, 3, 4, 23, 50))  # the clock starts there
            self.assertLess(util.now() - a, timedelta(seconds=30))
            self.assertGreaterEqual(util.now(), a)  # and keeps running
        with mock.patch.dict(os.environ, clear=False):
            os.environ.pop("FOREMAN_NOW", None)
            self.assertLess(abs(util.now() - datetime.now()), timedelta(seconds=30))

    def test_git(self):
        util.git(self.tmp, "init", "-q")
        self.assertEqual(Path(util.git(self.tmp, "rev-parse", "--show-toplevel")).resolve(), self.tmp.resolve())

        class Oops(Exception):
            pass

        with self.assertRaisesRegex(Oops, r"^git rev-parse --verify nope: "):
            util.git(self.tmp, "rev-parse", "--verify", "nope", error=Oops)
        self.assertEqual(util.git(self.tmp, "rev-parse", "--verify", "--quiet", "nope", check=False), "")
        self.assertEqual(util.git(self.tmp / "missing", "status", check=False), "")

    def test_git_runs_no_hook_nor_fsmonitor(self):
        marker = self.tmp / "fired.log"
        hooks = self.tmp / "hooks"
        hooks.mkdir()
        for name in ("post-checkout", "reference-transaction", "fsmonitor"):
            h = hooks / name
            h.write_text(f'#!/bin/sh\necho {name} >> "{marker}"\nexit 0\n')
            h.chmod(0o755)
        repo = self.tmp / "r"
        repo.mkdir()
        env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}

        def plain(*args):
            subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)

        plain("init", "-q", "-b", "main")
        plain("config", "core.hooksPath", str(hooks))  # in the repo's own config: GIT_QUIET wins over it
        plain("config", "core.fsmonitor", str(hooks / "fsmonitor"))
        (repo / "f").write_text("x\n")
        plain("add", "f")
        plain("-c", "core.hooksPath=/dev/null", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "c")
        marker.unlink(missing_ok=True)  # `add` already ran the fsmonitor
        with mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}):
            util.git(repo, "checkout", "-q", "-b", "other")
            util.git(repo, "status", "--porcelain")
        self.assertFalse(marker.exists())
        plain("checkout", "-q", "main")  # the control: plain git runs them
        plain("status", "--porcelain")
        self.assertEqual(
            sorted(set(marker.read_text().split())), ["fsmonitor", "post-checkout", "reference-transaction"]
        )

    def test_lock_people(self):
        sts = [
            {"holders": [{"who": "ana"}], "queue": [{"who": "bo"}], "next": None},
            {"holders": [], "queue": [], "next": "cy: panel (10:00)"},
            {"holders": [{"who": "dee"}], "queue": [{"who": "ana"}], "next": ""},
        ]
        self.assertEqual(lock.people(sts), ({"ana", "dee"}, {"bo", "cy", "ana"}))
        self.assertEqual(lock.people([]), (set(), set()))


if __name__ == "__main__":
    unittest.main()
