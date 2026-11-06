"""`foreman-dev guard-bench`: the line counter on a known source and on the guard's four files, the nearest-rank p90,
a timed run by the real hook path on a fake product (its verdicts, nothing left in the product's state) and its
refusals."""

import json
import textwrap

from devtool import DevCase
from helpers import BASE_TOML, ROOT

from foreman_dev import guardbench

PUSH = "git " + "push"


class CountTest(DevCase):
    def test_kinds_of_line(self):
        src = textwrap.dedent('''\
            """Module doc.

            Second paragraph."""

            # a comment
            import os  # trailing


            def f():
                """One line."""
                x = """not a
                docstring"""
                return x
            ''')
        got = guardbench.count(src)
        self.assertEqual(got, {"lines": 13, "code": 5, "docstrings": 4, "comments": 1, "blank": 4})

    def test_the_four_files_add_up(self):
        got = guardbench.lines(ROOT)
        self.assertEqual(set(got), {*guardbench.FILES, "total"})
        for f in guardbench.FILES:
            self.assertEqual(got[f]["lines"], len((ROOT / f).read_text().splitlines()), f)
        self.assertEqual(got["total"]["lines"], sum(got[f]["lines"] for f in guardbench.FILES))

    def test_nearest_rank(self):
        xs = [float(i) for i in range(1, 11)]
        self.assertEqual(guardbench.nearest_rank(xs, 0.9), 9.0)
        self.assertEqual(guardbench.nearest_rank([5.0], 0.9), 5.0)
        self.assertEqual(guardbench.nearest_rank([3.0, 1.0, 2.0], 0.5), 2.0)


class BenchTest(DevCase):
    toml = BASE_TOML + f'\n[deny]\npatterns = ["{PUSH} origin"]\n'

    def test_a_run_by_the_hook(self):
        r = self.dev("guard-bench", "--rounds", "2", "--cwd", str(self.home), "--case", "floor", "--case", "push",
                     "--case", "write", "--json")  # fmt: skip
        self.assertEqual(r.returncode, 0, r.stderr)
        got = json.loads(r.stdout)
        self.assertEqual((got["product"], got["rounds"], got["checkouts"]), ("fake", 2, [str(ROOT)]))
        verdicts = {c["case"]: c["runs"][0]["verdict"] for c in got["cases"]}
        self.assertEqual(verdicts, {"floor": "allowed", "push": "refused", "write": "allowed"})
        for c in got["cases"]:
            run = c["runs"][0]
            self.assertTrue(0 < run["median"] <= run["p90"], c)
        self.assertFalse((self.project.state / "guard.jsonl").exists())  # the refusal went to the bench's own state
        state = self.tmp / "state"
        logged = [p for p in state.rglob("*") if p.is_file() and "guard-bench" in p.read_text(errors="replace")]
        self.assertEqual(logged, [])

    def test_text_and_against(self):
        r = self.dev(
            "guard-bench", "--rounds", "1", "--cwd", str(self.home), "--case", "simple", "--against", str(ROOT)
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("against: " + str(ROOT), r.stdout)
        self.assertRegex(r.stdout, r"simple\s+[\d.]+\s+[\d.]+\s+[\d.]+\s+[\d.]+\s+[+-][\d.]+%\s+allowed")

    def test_refusals(self):
        for args, why in (
            (["--against", str(self.tmp)], "no checkout of foreman"),
            (["--cwd", str(self.tmp / "nobody")], "no active product owns"),
            (["--case", "nosuch"], "no case named nosuch"),
            (["--rounds", "0"], "at least 1"),
        ):
            with self.subTest(args=args):
                r = self.dev("guard-bench", "--cwd", str(self.home), *args)
                self.assertEqual(r.returncode, 2, r.stdout)
                self.assertIn(why, r.stderr)
        self.write_toml(self.toml.replace("[paths]\n", f'[paths]\nstate = "{self.tmp}/own-state"\n', 1))
        r = self.dev("guard-bench", "--cwd", str(self.home))
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("which the bench cannot redirect", r.stderr)
