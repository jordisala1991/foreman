"""`selftest --coverage` (lib/foreman/cover.py): finding coverage, SKIP or FAIL when it is missing, reading the
report. The real run (the suite under coverage) is the objective check of the change, not a test of the suite."""

import io
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from foreman import cover

REPORT = """Name                  Stmts   Miss  Cover
-----------------------------------------
lib/foreman/a.py         10      5    50%
lib/foreman/b.py         90      5    94%
-----------------------------------------
TOTAL                   100     10    90%
"""
PYPROJECT = '[dependency-groups]\ndev = ["coverage==7.17.0"]\n'


class CoverTest(unittest.TestCase):
    def project(self, text=PYPROJECT):
        d = tempfile.mkdtemp(prefix="cover-test-")
        self.addCleanup(shutil.rmtree, d, True)
        (Path(d) / "pyproject.toml").write_text(text)
        return d

    def run_cover(self, root, mode):
        lines = []
        with mock.patch.object(cover, "find", return_value=None), redirect_stdout(io.StringIO()):
            rc = cover.run(root, mode, say=lines.append)
        return rc, lines

    def test_total_and_table(self):
        self.assertEqual(cover.total_of(REPORT), "90%")
        self.assertIsNone(cover.total_of("nothing"))
        self.assertEqual(len(cover.table(REPORT)), 6)

    def test_missing_coverage_is_a_skip_in_auto(self):
        rc, lines = self.run_cover(self.project(), "auto")
        self.assertEqual(rc, 0)
        self.assertTrue(lines[0].startswith("SKIP coverage: coverage 7.17.0 not available"), lines)

    def test_missing_coverage_fails_in_require(self):
        rc, lines = self.run_cover(self.project(), "require")
        self.assertEqual(rc, 1)
        self.assertTrue(lines[0].startswith("FAIL coverage: coverage 7.17.0 not available"), lines)

    def test_no_pin_fails(self):
        rc, lines = self.run_cover(self.project('[dependency-groups]\ndev = ["ruff==0.17.3"]\n'), "auto")
        self.assertEqual(rc, 1)
        self.assertIn("no coverage==VERSION", lines[0])

    def test_find_says_fetching_once_and_uses_offline_afterwards(self):
        said, calls = [], []

        def fake_probe(cmd, version, env, timeout=0):
            calls.append(cmd)
            return "--offline" not in cmd or len(calls) > 2  # offline works only after the fetch (2nd call)

        with (
            mock.patch.object(cover.shutil, "which", return_value="/x/uv"),
            mock.patch.object(cover, "probe", side_effect=fake_probe),
        ):
            cmd = cover.find("7.17.0", {"PATH": "/x"}, said.append)
        self.assertEqual(said, ["coverage: fetching coverage 7.17.0 (first run)"])
        self.assertIn("--offline", cmd)


if __name__ == "__main__":
    unittest.main()
