"""bin/check_size and bin/check: a file over its limit fails; exceptions may shrink, never grow."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SIZE = REPO / "bin" / "check_size"


def tree(files: dict[str, int], exceptions: str = "") -> Path:
    root = Path(tempfile.mkdtemp())
    (root / "pyproject.toml").write_text(f"[tool.fm-size]\nlimit = 10\ntests_limit = 20\n[tool.fm-size.exceptions]\n{exceptions}")
    for rel, n in files.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x = 1\n" * n)
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
            self.assertIn(f"{rel}: 11 lines, over its limit of 10", r.stderr)

    def test_tests_have_their_own_larger_limit(self):
        self.assertEqual(run(tree({"tests/t.py": 20})).returncode, 0)
        self.assertEqual(run(tree({"tests/t.py": 21})).returncode, 1)

    def test_an_exception_holds_its_size_but_not_more(self):
        self.assertEqual(run(tree({"fm/a.py": 15}, '"fm/a.py" = 15\n')).returncode, 0)
        r = run(tree({"fm/a.py": 16}, '"fm/a.py" = 15\n'))
        self.assertEqual(r.returncode, 1)
        self.assertIn("over its limit of 15", r.stderr)

    def test_a_stale_exception_fails(self):
        self.assertIn("remove it", run(tree({"fm/a.py": 9}, '"fm/a.py" = 15\n')).stderr)
        self.assertIn("does not exist", run(tree({}, '"fm/gone.py" = 15\n')).stderr)

    def test_the_repo_passes(self):
        r = subprocess.run([sys.executable, str(SIZE)], capture_output=True, text=True, check=False)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
