"""bin/check_size and bin/check: a file over its limit fails; exceptions may shrink, never grow."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SIZE = REPO / "bin" / "check_size"


def tree(files: dict[str, int], exceptions: str = "", functions: str = "") -> Path:
    root = Path(tempfile.mkdtemp())
    (root / "pyproject.toml").write_text(
        f"[tool.fm-size]\nlimit = 10\ntests_limit = 20\n[tool.fm-size.exceptions]\n{exceptions}\n[tool.fm-size.functions]\n{functions}"
    )
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


FN = "def f(a, b):  # noqa: C901\n    if a:\n        return 1\n    return b\n"  # 3 statements, 1 decision, 2 arguments


def with_fn(body: str, recorded: str) -> subprocess.CompletedProcess[str]:
    root = tree({}, functions=recorded)
    (root / "fm" / "m.py").write_text(body)
    return run(root)


class FunctionRatchet(unittest.TestCase):
    def test_a_function_at_its_recorded_size_passes(self):
        self.assertEqual(with_fn(FN, '"fm/m.py:f" = [3, 1, 2]\n').returncode, 0)

    def test_more_branches_statements_or_arguments_fail(self):
        grown = {
            "branches": FN.replace("    return b", "    if b:\n        return 2\n    return b"),
            "statements": FN.replace("    return b", "    b = 1\n    return b"),
            "arguments": FN.replace("(a, b)", "(a, b, c)"),
        }
        for what, body in grown.items():
            r = with_fn(body, '"fm/m.py:f" = [3, 1, 2]\n')
            self.assertEqual(r.returncode, 1, what)
            self.assertIn("grew over", r.stderr, what)

    def test_a_noqa_function_without_an_entry_fails(self):
        self.assertIn("no entry", with_fn(FN, "").stderr)

    def test_shrinking_or_a_stale_entry_asks_to_lower_it(self):
        self.assertIn("shrank", with_fn(FN, '"fm/m.py:f" = [9, 9, 9]\n').stderr)
        self.assertIn("no longer has", with_fn("def f():\n    return 1\n", '"fm/m.py:f" = [1, 0, 0]\n').stderr)


if __name__ == "__main__":
    unittest.main()
