"""bin/check_size: a file over its limit fails, and a size/complexity rule cannot be silenced with noqa. No exceptions."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SIZE = REPO / "bin" / "check_size"


def tree(files: dict[str, str | int]) -> Path:
    root = Path(tempfile.mkdtemp())
    (root / "pyproject.toml").write_text("[tool.fm-size]\nlimit = 10\ntests_limit = 20\n")
    for rel, body in files.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x = 1\n" * body if isinstance(body, int) else body)
    for d in ("fm", "panel/src", "tests"):
        (root / d).mkdir(parents=True, exist_ok=True)
    return root


def run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SIZE), str(root)], capture_output=True, text=True, check=False)


class SizeCheck(unittest.TestCase):
    def test_under_the_limit_passes(self):
        self.assertEqual(run(tree({"fm/a.py": 10, "panel/src/b.tsx": 10, "tests/t.py": 20})).returncode, 0)

    def test_code_over_the_limit_fails(self):
        for rel in ("fm/a.py", "panel/src/b.ts", "panel/src/c.css"):
            r = run(tree({rel: 11}))
            self.assertEqual(r.returncode, 1, rel)
            self.assertIn(f"{rel}: 11 lines, over the limit of 10", r.stderr)

    def test_tests_have_their_own_larger_limit(self):
        self.assertEqual(run(tree({"tests/t.py": 20})).returncode, 0)
        self.assertEqual(run(tree({"tests/t.py": 21})).returncode, 1)

    def test_silencing_a_size_or_complexity_rule_fails(self):
        for code in ("C901", "PLR0912", "PLR0913", "PLR0915", "E501, C901"):
            r = run(tree({"fm/m.py": f"def f(a):  # noqa: {code}\n    return a\n"}))
            self.assertEqual(r.returncode, 1, code)
            self.assertIn("fm/m.py:1: a size/complexity rule is silenced", r.stderr)

    def test_other_noqa_are_not_this_checks_business(self):
        self.assertEqual(run(tree({"fm/m.py": "import os  # noqa: F401\n"})).returncode, 0)

    def test_the_repo_passes(self):
        r = subprocess.run([sys.executable, str(SIZE)], capture_output=True, text=True, check=False)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
