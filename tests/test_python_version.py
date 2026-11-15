import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import ROOT

from fm import config


def fake(d: Path, version: str, body: str) -> None:
    d.mkdir()
    p = d / "python3"
    p.write_text(f'#!/bin/sh\nif [ "$1" = "-c" ]; then echo {version}; else {body}; fi\n')
    p.chmod(0o755)


class PythonVersion(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.t = Path(tmp.name).resolve()
        (self.t / "bin").mkdir()
        shutil.copy(ROOT / "bin/fm", self.t / "bin/fm")
        (self.t / "pyproject.toml").write_text('requires-python = ">=3.99"\n')
        fake(self.t / "old", "3.9.6", "echo old")
        fake(self.t / "new", "3.99.1", 'echo "new: $@"')
        (self.t / "empty").mkdir()
        self.reg = self.t / "projects.json"

    def run_fm(self, path: str, *args: str, bin: Path | None = None, stdin: str = ""):
        env = {"PATH": path, "FM_REGISTRY": str(self.reg)}
        return subprocess.run([sys.executable, str(bin or self.t / "bin/fm"), *args], env=env, input=stdin, capture_output=True, text=True)

    def test_an_old_python3_first_defers_to_a_newer_one_on_the_path(self):
        r = self.run_fm(f"{self.t}/old:{self.t}/new", "list")
        self.assertEqual((r.returncode, r.stdout), (0, f"new: {self.t}/bin/fm list\n"))

    def test_without_a_new_enough_python_fm_exits_with_one_line(self):
        r = self.run_fm(f"{self.t}/old", "list")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")
        self.assertEqual(len(r.stderr.splitlines()), 1)
        for s in ("3.99", "3.9.6", f"{self.t}/old"):
            self.assertIn(s, r.stderr)

    def test_a_python3_that_times_out_is_named_in_the_line(self):
        fake(self.t / "hang", "3.99.1", "echo hang")
        (self.t / "hang/python3").write_text("#!/bin/sh\nexec /bin/sleep 30\n")
        r = self.run_fm(f"{self.t}/hang", "list")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(len(r.stderr.splitlines()), 1)
        self.assertIn("timed out", r.stderr)

    def test_the_hook_writes_its_note_before_its_own_timeout_when_pythons_hang(self):
        for n in ("a", "b"):
            fake(self.t / n, "3.99.1", "echo hang")
            (self.t / n / "python3").write_text("#!/bin/sh\nexec /bin/sleep 30\n")
        r = self.run_fm(f"{self.t}/a:{self.t}/b", "session-context", stdin='{"cwd": "/"}')
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
        self.assertIn("timed out", Path(f"{self.reg}.python").read_text())

    def test_no_python3_at_all_is_said(self):
        r = self.run_fm(f"{self.t}/empty", "list")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(len(r.stderr.splitlines()), 1)
        self.assertIn("3.99", r.stderr)
        self.assertIn("no python3", r.stderr)

    def test_the_hook_adds_nothing_and_leaves_the_line_for_health(self):
        r = self.run_fm(f"{self.t}/old", "session-context", stdin='{"cwd": "/"}')
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
        with mock.patch.dict(os.environ, {"FM_REGISTRY": str(self.reg)}):
            text = config.python_note_file().read_text()
        self.assertIn("3.99", text)
        self.assertIn("3.9.6", text)

    def test_a_new_enough_python_is_not_searched_for(self):
        marker = self.t / "ran"
        (self.t / "spy").mkdir()
        (self.t / "spy/python3").write_text(f"#!/bin/sh\ntouch {marker}\n")
        r = self.run_fm(f"{self.t}/spy", "--help", bin=ROOT / "bin/fm")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(marker.exists())
