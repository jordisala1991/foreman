"""The parallel selftest (lib/foreman/selftest.py): test classes grouped for the workers, a worker's protocol kept
apart from what the tests print or read, each worker in its own $TMPDIR, and the merged summary with its exit code."""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar

import helpers

from foreman import selftest


class Probe(unittest.TestCase):
    """Loaded only by name (no test_ methods, so discovery runs none of these)."""

    def check_passes(self):
        self.assertTrue(True)

    def check_fails(self):
        self.assertEqual(1, 2)

    def check_noisy(self):
        print("a line on stdout that must not reach the protocol")
        print("and one on stderr", file=sys.stderr)
        subprocess.run(["cat"], timeout=10, check=False)  # reads stdin: /dev/null in a worker, so it ends at once
        input_line = sys.stdin.readline()
        self.assertEqual(input_line, "")

    def check_tmpdir(self):
        self.assertEqual(Path(tempfile.gettempdir()).name, "w0")
        self.assertEqual(os.environ["TMPDIR"], tempfile.gettempdir())


class Dies(unittest.TestCase):
    def check_dies(self):
        os._exit(3)


class Survives(unittest.TestCase):
    def check_passes(self):
        self.assertTrue(True)


class SelftestTest(unittest.TestCase):
    def test_classes_grouped_failed_imports_local(self):
        loader = unittest.TestLoader()
        suite = unittest.TestSuite(
            [
                loader.loadTestsFromNames(["test_selftest.Probe.check_passes", "test_selftest.Probe.check_fails"]),
                loader.loadTestsFromName("no_such_module_here"),
            ]
        )
        todo, local = selftest.classes(suite)
        self.assertEqual(
            todo, {"test_selftest.Probe": ["test_selftest.Probe.check_passes", "test_selftest.Probe.check_fails"]}
        )
        self.assertEqual(local.countTestCases(), 1)

    def test_worker_protocol(self):
        root = Path(tempfile.mkdtemp(prefix="foreman-test-")).resolve()
        self.addCleanup(shutil.rmtree, root, True)
        w = selftest.Worker(root, 0, False)
        self.addCleanup(w.stop)
        w.send(
            "test_selftest.Probe",
            [f"test_selftest.Probe.{m}" for m in ("check_passes", "check_fails", "check_noisy", "check_tmpdir")],
        )
        out = json.loads(w.proc.stdout.readline())
        self.assertEqual(out["run"], 4)
        self.assertEqual(out["text"], ".F..")
        self.assertEqual(len(out["failures"]), 1)
        self.assertIn("check_fails", out["failures"][0][0])
        self.assertIn("AssertionError: 1 != 2", out["failures"][0][1])
        self.assertEqual(out["errors"], [])
        # a second job on the same worker
        w.send("test_selftest.Probe", ["test_selftest.Probe.check_passes"])
        self.assertEqual(json.loads(w.proc.stdout.readline())["run"], 1)
        self.assertIn("must not reach the protocol", w.log_tail())

    def test_worker_that_dies(self):
        """A worker that ends in the middle of a class: an error for that class, and a new worker runs the rest."""
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromNames(["test_selftest.Dies.check_dies", "test_selftest.Survives.check_passes"])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = selftest.run_parallel(suite, False, 1, 0)
        out = buf.getvalue()
        self.assertEqual(rc, 1, out)
        self.assertIn("ERROR: test_selftest.Dies\n", out)
        self.assertIn("the worker process ended (exit 3) while running it", out)
        self.assertIn("selftest: FAIL: 1 tests, 0 failures, 1 errors", out)  # Survives ran on the new worker

    def report(self, results, problems=()):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = selftest.report(results, list(problems), 0)
        return rc, buf.getvalue()

    def result(self, **kw):
        return {
            "run": 1,
            "text": ".",
            "s": 0,
            "errors": [],
            "failures": [],
            "skipped": [],
            "expected": 0,
            "unexpected": [],
            **kw,
        }

    def test_report_merges_like_unittest(self):
        rc, out = self.report(
            [self.result(run=3, text="..."), self.result(run=2, text="s.", skipped=[["m.C.t", "no node"]])]
        )
        self.assertEqual(rc, 0)
        self.assertIn("Ran 5 tests in", out)
        self.assertIn("\nOK (skipped=1)\n", out)
        self.assertRegex(out, r"selftest: PASS: 5 tests, 0 failures, 0 errors, 1 skipped, static 0 problems, \d+ s")
        self.assertIn("  skipped m.C.t: no node", out)
        rc, out = self.report(
            [
                self.result(run=2, text="F.", failures=[["t (m.C.t)", "Traceback: boom\n"]]),
                self.result(text="E", errors=[["u (m.D.u)", "Traceback: bang\n"]]),
            ]
        )
        self.assertEqual(rc, 1)
        self.assertIn("=" * 70 + "\nERROR: u (m.D.u)\n" + "-" * 70 + "\nTraceback: bang\n", out)
        self.assertIn("FAIL: t (m.C.t)", out)
        self.assertLess(out.index("ERROR: u"), out.index("FAIL: t"))  # unittest's order: errors, then failures
        self.assertIn("\nFAILED (failures=1, errors=1)\n", out)
        self.assertIn("selftest: FAIL: 3 tests, 1 failures, 1 errors, 0 skipped", out)
        rc, out = self.report([self.result()], problems=["json x: bad"])  # a static problem alone fails the run
        self.assertEqual(rc, 1)
        self.assertIn("selftest: FAIL: 1 tests", out)


if __name__ == "__main__":
    unittest.main()


class Py39Test(unittest.TestCase):
    """The static check that keeps the code to Python 3.9, on files written here: each construct is caught on its own
    line, whatever Python runs the check (on 3.9 its own parser rejects those it cannot parse)."""

    NEWER: ClassVar[dict] = {
        "match": "match x:\n    case 1:\n        pass\n",
        "f-string own quote": "x = f'{'a'}'\n",
        "f-string own quote in the second of two parts": "x = f'{a}' f'{b['k']}'\n",
        "f-string own quote nested": "x = f'{a}{f'{b}'}'\n",
        "f-string backslash": "x = f\"{'\\\\n'.join(y)}\"\n",
        "f-string comment": "x = f'''{1  # one\n}'''\n",
        "starred subscript": "x = a[*b]\n",
        "walrus subscript": "x = a[y := 1]\n",
        "runtime annotation": "x: int | None = None\n",
        "argument annotation": "def f(a: int | None):\n    pass\n",
        "zip strict": "x = zip(a, b, strict=True)\n",
        "tomllib": "import tomllib\n",
        "from typing": "from typing import Self\n",
        "datetime.UTC": "import datetime\nx = datetime.UTC\n",
        "builtin": "x = anext(it)\n",
        "method": "self.enterContext(cm)\n",
        "dataclass slots": "from dataclasses import dataclass\n@dataclass(slots=True)\nclass A:\n    pass\n",
    }
    OLD = (
        "from __future__ import annotations\nx: int | None = None\n"
        "y = f\"{x!r} {d['k']} {s.split('#', 1)} {'—'}\" + f'{a[(z := 1)]}'\n"
        "def f(a: 'int | None') -> 'str | None':\n    pass\n"
        "from typing import Optional\nimport datetime\nz = datetime.timezone.utc\n"
    )

    def check(self, src):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "m.py"
            f.write_text(src)
            return selftest.py39_checks([f])

    def test_newer_constructs_are_caught(self):
        for what, src in self.NEWER.items():
            with self.subTest(what=what):
                got = self.check("import os\n" + src)
                self.assertTrue(got, f"{what} not caught")
                self.assertRegex(got[0], r"m\.py:[2-9]:")

    def test_what_39_has_passes(self):
        self.assertEqual(self.check(self.OLD), [])

    def test_each_concatenated_part_has_its_own_quote(self):
        """A field may hold the quote its own part does not use, whatever the other parts of the concatenation use
        (the formatter picks quotes part by part): not a 3.12 construct."""
        for src in (
            'x = f"{a}" f\'{b["k"]}\'\n',
            "x = f'{a[\"j\"]}' f\"{b['k']}\" \"plain\" f'{c}'\n",
            "x = (\n    f\"{a['j']}\"\n    f'{b[\"k\"]}'\n)\n",
            "x = f\"{a}{f'{b}'}{c['j']}\"\n",
        ):
            with self.subTest(src=src):
                self.assertEqual(self.check(src), [])

    def test_the_tree_runs_on_39(self):
        self.assertEqual(selftest.py39_checks(), [])

    def test_oldest_is_the_launchers_minimum(self):
        """py39_checks reads the launcher's min_python; its lists of newer names are written for 3.9."""
        self.assertEqual(selftest.OLDEST, (3, 9), "min_python moved: review NEWER_* in selftest.py and the docs")


class RunSlotTest(unittest.TestCase):
    """The workers of a run without -j: the machine's cores shared by the selftest runs holding a slot."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="foreman-slots-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        saved = os.environ.get(selftest.SLOTS_ENV)
        os.environ[selftest.SLOTS_ENV] = str(self.tmp / "slots")
        self.addCleanup(
            lambda: (
                os.environ.pop(selftest.SLOTS_ENV) if saved is None else os.environ.update({selftest.SLOTS_ENV: saved})
            )
        )

    def test_alone_takes_every_core(self):
        with selftest.RunSlot(cores=12) as s:
            self.assertEqual((s.live, s.jobs), (1, 12))
            self.assertEqual(len(list((self.tmp / "slots").glob("run-*"))), 1)
        self.assertEqual(list((self.tmp / "slots").glob("*")), [])  # freed at the end

    def test_live_runs_share_the_cores_with_a_minimum(self):
        with selftest.RunSlot(cores=12) as a:
            with selftest.RunSlot(cores=12) as b, selftest.RunSlot(cores=12) as c:
                self.assertEqual((a.live, b.live, c.live), (1, 2, 3))
                self.assertEqual((b.jobs, c.jobs), (6, 4))
                self.assertIn("12 cores, 3 selftest runs on this machine", c.why)
                with selftest.RunSlot(cores=4) as d:
                    self.assertEqual((d.live, d.jobs), (4, selftest.MIN_JOBS))
            with selftest.RunSlot(cores=12) as e:  # b and c ended: their cores are free again
                self.assertEqual(e.live, 2)

    def test_a_slot_nobody_holds_is_removed_and_not_counted(self):
        d = self.tmp / "slots"
        d.mkdir()
        (d / "run-99999-dead").write_text("")  # a run that died: its lock went with it
        with selftest.RunSlot(cores=8) as s:
            self.assertEqual((s.live, s.jobs), (1, 8))
        self.assertFalse((d / "run-99999-dead").exists())

    def test_a_run_in_another_process_counts(self):
        code = (
            f"import sys; sys.path.insert(0, {str(helpers.ROOT / 'lib')!r}); from foreman import selftest\n"
            "with selftest.RunSlot() as s:\n    print('in', flush=True); sys.stdin.read()\n"
        )
        p = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(p.stdout.readline(), "in\n")
            with selftest.RunSlot(cores=8) as s:
                self.assertEqual((s.live, s.jobs), (2, 4))
        finally:
            p.stdin.close()
            p.wait(30)
            p.stdout.close()

    def test_an_explicit_j_wins_and_says_so(self):
        r = subprocess.run(
            [
                sys.executable,
                str(helpers.ENTRY),
                "selftest",
                "--quality=off",
                "--mods=off",
                "-j",
                "3",
                "no_test_matches_x",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        self.assertIn("selftest: 3 workers (-j)\n", r.stdout)

    def test_the_share_follows_the_runs_alive(self):
        with selftest.RunSlot(cores=12) as a:
            self.assertEqual(a.share(), 12)
            with selftest.RunSlot(cores=12):
                a.counted = 0  # past its once-a-second limit
                self.assertEqual(a.share(), 6)
            a.counted = 0
            self.assertEqual(a.share(), 12)


class Another(unittest.TestCase):
    def check_passes(self):
        self.assertTrue(True)


class ShareTest(unittest.TestCase):
    """run_parallel with a share of the machine that changes while it runs."""

    def run_with(self, jobs, share):
        """Runs three one-test classes with share(events so far); the workers' sends and stops, in order, as (event,
        worker folder)."""
        from unittest import mock

        names = ["test_selftest.Survives.check_passes", "test_selftest.Probe.check_passes"]
        names.append("test_selftest.Another.check_passes")
        suite = unittest.TestLoader().loadTestsFromNames(names)
        events, send, stop = [], selftest.Worker.send, selftest.Worker.stop

        def sending(w, key, ids):
            events.append(("send", w.tmp.name))
            send(w, key, ids)

        def stopping(w):
            events.append(("stop", w.tmp.name))
            stop(w)

        out = io.StringIO()
        with mock.patch.object(selftest.Worker, "send", sending), mock.patch.object(selftest.Worker, "stop", stopping):
            with contextlib.redirect_stdout(out):
                rc = selftest.run_parallel(suite, False, jobs, 0.0, static=False, share=lambda: share(events))
        self.assertEqual(rc, 0, out.getvalue())
        self.assertIn("selftest: PASS: 3 tests", out.getvalue())
        return events

    def test_a_worker_over_the_share_stops_and_the_rest_still_runs(self):
        events = self.run_with(2, lambda events: 2 if len(events) < 2 else 1)  # 2 to start with, then 1
        sends = [i for i, e in enumerate(events) if e[0] == "send"]
        self.assertEqual(len(sends), 3)
        self.assertEqual(events[2][0], "stop")  # the first to end its class is over a share of 1: it stops
        self.assertNotEqual(events[2][1], events[sends[2]][1])  # the third class goes to the other worker

    def test_workers_are_added_when_the_share_grows(self):
        events = self.run_with(1, lambda events: 3)
        self.assertEqual(sorted({w for e, w in events if e == "send"}), ["w0", "w1"])  # one more, while there is work
