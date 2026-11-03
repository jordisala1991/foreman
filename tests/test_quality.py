"""The selftest's code quality checks (lib/foreman/quality.py) with fake tools on the PATH: no uv, no network, no real
ruff or pyright. A fake `uvx` keeps a fake cache (a file per fetched spec) and runs the fake tool; a fake `ruff` /
`pyright` on the PATH answers at the version the test gives it. Each tool says what to print from FAKE_* files."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import ENTRY, ROOT

from foreman import quality, selftest, toml_reader

PYPROJECT = """[dependency-groups]
dev = ["ruff==0.17.3", "pyright[nodejs]==1.1.414", "coverage==7.17.0"]

[tool.foreman.quality]
checks = {checks}
"""

# The one fake tool: `ruff` or `pyright` by its name (argv[0], or FAKE_AS when uvx runs it). It logs its arguments,
# says its version (FAKE_VERSION_<TOOL>), prints what FAKE_OUT_<check> holds and exits 1 when that is not empty;
# `ruff check --fix` and `ruff format` (without --check) write to FAKE_FIXES, as a fix would.
FAKE_TOOL = r"""
import json, os, sys
name = os.environ.get("FAKE_AS") or os.path.basename(sys.argv[0])
args = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps([name] + args) + "\n")
if args == ["--version"]:
    print(name, os.environ["FAKE_VERSION_" + name.upper()])
    sys.exit(0)
def answer(key, empty):
    out = open(os.environ[key]).read() if os.path.exists(os.environ.get(key, "")) else empty
    print(out)
    sys.exit(0 if out == empty else 1)
if name == "ruff" and args[0] == "check" and "--fix" in args or name == "ruff" and args[:1] == ["format"] \
        and "--check" not in args:
    if os.environ.get("FAKE_FIXES"):
        with open(os.environ["FAKE_FIXES"], "a") as f:
            f.write(args[0] + "\n")
    sys.exit(0)
if name == "ruff" and args[0] == "check":
    answer("FAKE_OUT_RUFF", "[]")
if name == "ruff" and args[0] == "format":
    answer("FAKE_OUT_FORMAT", "[]")
if name == "pyright":
    answer("FAKE_OUT_PYRIGHT", '{"generalDiagnostics": []}')
sys.exit(3)
"""

# A fake uvx: `--offline` works only for a spec already in its cache (FAKE_UV_CACHE/<spec>); without --offline it
# "downloads" (adds the spec to the cache) unless FAKE_UV_NET=0. Then it runs the fake tool at the spec's version.
FAKE_UVX = r"""
import json, os, subprocess, sys
args = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps(["uvx"] + args) + "\n")
offline = "--offline" in args
args = [a for a in args if a != "--offline"]
spec = args[args.index("--from") + 1]
tool = args[args.index("--from") + 2]
cache = os.path.join(os.environ["FAKE_UV_CACHE"], spec)
if not os.path.exists(cache):
    if offline:
        print("error: not found in the cache and the network is disabled", file=sys.stderr)
        sys.exit(1)
    if os.environ.get("FAKE_UV_NET") == "0":
        print("error: failed to fetch", file=sys.stderr)
        sys.exit(1)
    open(cache, "w").close()
version = spec.split("==")[1]
env = dict(os.environ, FAKE_AS=tool, **{"FAKE_VERSION_" + tool.upper(): version})
sys.exit(subprocess.run([sys.executable, os.environ["FAKE_TOOL"]] + args[args.index("--from") + 3:],
                        env=env).returncode)
"""


class QualityCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="foreman-test-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "repo"
        (self.root / "lib/pkg").mkdir(parents=True)
        (self.root / "lib/pkg/a.py").write_text("import os  # noqa: F401 (kept)\nx = 1  # pyright: ignore[x]\n")
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        (self.tmp / "cache").mkdir()
        self.tool = self.tmp / "faketool.py"
        self.tool.write_text(FAKE_TOOL)
        self.log = self.tmp / "log"
        self.env = {
            "PATH": f"{self.bin}{os.pathsep}/usr/bin{os.pathsep}/bin",
            "HOME": str(self.tmp),
            "FAKE_LOG": str(self.log),
            "FAKE_TOOL": str(self.tool),
            "FAKE_UV_CACHE": str(self.tmp / "cache"),
            "FAKE_FIXES": str(self.tmp / "fixes"),
            **{f"FAKE_OUT_{k}": str(self.tmp / f"out-{k.lower()}") for k in ("RUFF", "FORMAT", "PYRIGHT")},
        }
        self.checks(["ruff"])

    def checks(self, checks):
        (self.root / "pyproject.toml").write_text(PYPROJECT.format(checks=json.dumps(checks)))

    def script(self, name, body):
        f = self.bin / name
        f.write_text(f"#!{sys.executable}\n{body}")
        f.chmod(0o755)

    def with_uvx(self):
        self.script("uvx", FAKE_UVX)

    def on_path(self, tool, version):
        self.script(tool, FAKE_TOOL)
        self.env[f"FAKE_VERSION_{tool.upper()}"] = version

    def out(self, check, value):
        Path(self.env[f"FAKE_OUT_{check.upper()}"]).write_text(value if isinstance(value, str) else json.dumps(value))

    def calls(self):
        return [json.loads(x) for x in self.log.read_text().splitlines()] if self.log.exists() else []

    def run_quality(self, mode="auto"):
        said = []
        rep = quality.run(self.root, mode, self.env, say=said.append)
        return rep, said


class FindTest(QualityCase):
    def test_no_tool_auto_skips_require_fails(self):
        rep, _ = self.run_quality("auto")
        self.assertEqual(rep.problems, 0)
        self.assertEqual(rep.skipped, ["ruff"])
        self.assertEqual(len(rep.lines), 1)
        self.assertTrue(rep.lines[0].startswith("SKIP quality: ruff 0.17.3 not available"), rep.lines)
        self.assertIn("install uv (https://docs.astral.sh/uv/) or pip install --group dev", rep.lines[0])
        self.assertEqual(rep.word, "quality skipped: ruff")
        self.assertIn("ruff skipped", rep.line)
        rep, _ = self.run_quality("require")
        self.assertEqual(rep.problems, 1)
        self.assertTrue(rep.lines[0].startswith("FAIL quality: ruff 0.17.3 not available"), rep.lines)
        self.assertEqual(rep.word, "quality 1 problem")

    def test_off_runs_nothing(self):
        self.with_uvx()
        rep, _ = self.run_quality("off")
        self.assertEqual((rep.problems, rep.lines, rep.line, rep.word), (0, [], "", "quality off"))
        self.assertEqual(self.calls(), [])

    def test_uvx_cached_offline(self):
        self.with_uvx()
        (self.tmp / "cache/ruff==0.17.3").touch()
        rep, said = self.run_quality("require")
        self.assertEqual((rep.problems, rep.lines, said), (0, [], []))
        self.assertTrue(all("--offline" in c for c in self.calls() if c[0] == "uvx"), self.calls())
        self.assertEqual(
            rep.line, "quality: ruff 0 problems, format off, pyright off (ruff 0.17.3); 1 noqa, 1 pyright: ignore"
        )
        self.assertEqual(rep.word, "quality ok")

    def test_uvx_fetches_once_then_offline(self):
        self.with_uvx()
        rep, said = self.run_quality("require")
        self.assertEqual(said, ["quality: fetching ruff 0.17.3 (first run)"])
        self.assertEqual(rep.problems, 0, rep.lines)
        uvx = [c for c in self.calls() if c[0] == "uvx"]
        self.assertEqual(sum("--offline" not in c for c in uvx), 1, uvx)  # one download
        self.assertTrue((self.tmp / "cache/ruff==0.17.3").exists())
        self.log.unlink()
        rep, said = self.run_quality("require")  # the second time: from the cache, nothing said
        self.assertEqual(said, [])
        self.assertTrue(all("--offline" in c for c in self.calls() if c[0] == "uvx"))

    def test_uvx_without_network(self):
        self.with_uvx()
        self.env["FAKE_UV_NET"] = "0"
        rep, said = self.run_quality("auto")
        self.assertEqual(said, ["quality: fetching ruff 0.17.3 (first run)"])
        self.assertEqual(rep.skipped, ["ruff"])
        self.assertTrue(rep.lines[0].startswith("SKIP quality: ruff 0.17.3 not available"))
        rep, _ = self.run_quality("require")
        self.assertEqual(rep.problems, 1)

    def test_path_tool_at_the_pinned_version(self):
        self.on_path("ruff", "0.17.3")
        rep, said = self.run_quality("require")
        self.assertEqual((rep.problems, rep.lines, said), (0, [], []))
        self.assertIn(["ruff", "check", "--no-fix", "--output-format", "json", "."], self.calls())

    def test_path_tool_at_another_version_is_not_used(self):
        self.on_path("ruff", "0.16.0")
        rep, _ = self.run_quality("auto")
        self.assertEqual(rep.skipped, ["ruff"])
        self.assertEqual([c for c in self.calls() if c[1:] != ["--version"]], [])  # asked its version, nothing else
        rep, _ = self.run_quality("require")
        self.assertEqual(rep.problems, 1)

    def test_pyright_with_nodejs_wheel_only_without_node(self):
        self.with_uvx()
        self.checks(["pyright"])
        rep, _ = self.run_quality("require")
        self.assertEqual(rep.problems, 0, rep.lines)
        specs = {c[c.index("--from") + 1] for c in self.calls() if c[0] == "uvx"}
        self.assertEqual(specs, {"pyright[nodejs]==1.1.414"})
        self.script("node", "print('v24')\n")  # node on the PATH: plain pyright, which uses it
        self.log.unlink()
        rep, _ = self.run_quality("require")
        specs = {c[c.index("--from") + 1] for c in self.calls() if c[0] == "uvx"}
        self.assertEqual(specs, {"pyright==1.1.414"})
        self.assertIn("pyright 0 errors", rep.line)

    def test_settings_errors(self):
        (self.root / "pyproject.toml").write_text('[tool.foreman.quality]\nchecks = ["ruff", "mypy"]\n')
        rep, _ = self.run_quality("auto")
        self.assertEqual(rep.problems, 1)
        self.assertIn("unknown ['mypy']", rep.lines[0])
        (self.root / "pyproject.toml").write_text('[tool.foreman.quality]\nchecks = ["ruff"]\n')
        rep, _ = self.run_quality("auto")
        self.assertEqual(rep.lines, ["FAIL quality: no ruff==VERSION in pyproject.toml [dependency-groups] dev"])
        (self.root / "pyproject.toml").unlink()
        rep, _ = self.run_quality("auto")
        self.assertEqual(rep.lines, ["FAIL quality: no pyproject.toml"])

    def test_the_real_pins(self):
        """foreman's own pyproject.toml: every tool pinned, the checks known."""
        s = quality.settings(ROOT)
        self.assertIsNone(s.error)
        self.assertEqual(set(s.pins), {"ruff", "pyright", "coverage"})
        self.assertIn("ruff", s.checks)


class FailureTest(QualityCase):
    def setUp(self):
        super().setUp()
        self.on_path("ruff", "0.17.3")
        self.on_path("pyright", "1.1.414")
        self.script("node", "print('v24')\n")

    def finding(self, n, code="F401", safe=True):
        return {
            "code": code,
            "filename": str(self.root / "lib/pkg/a.py"),
            "location": {"row": n, "column": 5},
            "message": f"`os` imported\n  but unused {n}",
            "fix": {"applicability": "safe"} if safe else None,
        }

    def test_ruff_findings(self):
        self.out("ruff", [self.finding(12), self.finding(3, "E902", safe=False)])
        rep, _ = self.run_quality("auto")
        self.assertEqual(rep.problems, 2)
        self.assertEqual(
            rep.lines,
            [
                "FAIL ruff lib/pkg/a.py:3:5 E902 `os` imported but unused 3  (fix by hand; recheck: "
                "bin/foreman selftest --quality-only)",
                "FAIL ruff lib/pkg/a.py:12:5 F401 `os` imported but unused 12  (fix: bin/foreman selftest --fix)",
            ],
        )
        self.assertTrue(rep.line.startswith("quality: ruff 2 problems, format off, pyright off (ruff 0.17.3)"))
        self.assertEqual(rep.word, "quality 2 problems")

    def test_at_most_30_lines_per_tool(self):
        self.out("ruff", [self.finding(n) for n in range(1, 41)])
        rep, _ = self.run_quality("auto")
        self.assertEqual(rep.problems, 40)
        self.assertEqual(len(rep.lines), 31)
        self.assertTrue(rep.lines[-1].startswith("FAIL ruff: 10 more not shown ("), rep.lines[-1])

    def test_format_and_pyright(self):
        self.checks(["ruff", "format", "pyright"])
        self.out(
            "format",
            [
                {"code": "unformatted", "filename": str(self.root / f), "location": {"row": 1}}
                for f in ("lib/pkg/a.py", "lib/pkg/a.py", "tests/test_b.py")
            ],
        )
        self.out(
            "pyright",
            {
                "generalDiagnostics": [
                    {
                        "file": str(self.root / "lib/pkg/a.py"),
                        "severity": "error",
                        "rule": "reportArgumentType",
                        "message": 'Argument of type "None"\n  is not assignable',
                        "range": {"start": {"line": 108, "character": 4}},
                    },
                    {
                        "file": str(self.root / "lib/pkg/a.py"),
                        "severity": "warning",
                        "message": "only a warning",
                        "range": {"start": {"line": 1, "character": 0}},
                    },
                ]
            },
        )
        rep, _ = self.run_quality("require")
        self.assertEqual(
            rep.lines,
            [
                "FAIL ruff format: 2 files would change: lib/pkg/a.py, tests/test_b.py  "
                "(fix: bin/foreman selftest --fix)",
                'FAIL pyright lib/pkg/a.py:109:5 reportArgumentType: Argument of type "None" is not assignable  '
                "(fix by hand; recheck: bin/foreman selftest --quality-only)",
            ],
        )
        self.assertEqual(rep.problems, 3)
        self.assertEqual(
            rep.line,
            "quality: ruff 0 problems, format 2 files, pyright 1 error (ruff 0.17.3, "
            "pyright 1.1.414); 1 noqa, 1 pyright: ignore",
        )
        self.assertIn(["pyright", "--outputjson", "-p", str(self.root)], self.calls())

    def test_a_tool_that_breaks(self):
        self.out("ruff", "this is not json")
        rep, _ = self.run_quality("auto")
        self.assertEqual(rep.problems, 1)
        self.assertTrue(rep.lines[0].startswith("FAIL ruff: did not run (exit 1)"), rep.lines)


class FixTest(QualityCase):
    def test_fix_runs_ruff_and_says_what_changed(self):
        self.on_path("ruff", "0.17.3")
        # the fake fix: the log of the fixes lands in a .py file under the root, so the file changes
        self.env["FAKE_FIXES"] = str(self.root / "lib/pkg/a.py")
        said = []
        self.assertEqual(quality.fix(self.root, self.env, said.append), 0)
        self.assertIn(["ruff", "check", "--fix", "."], self.calls())
        self.assertFalse([c for c in self.calls() if c[1:2] == ["format"]])  # format is off: not run
        self.assertEqual(len(said), 1)
        self.assertRegex(said[0], r"^fix: ruff check --fix changed 1 file: lib/pkg/a\.py \(\d+\.\d s\)$")
        self.checks(["ruff", "format"])
        self.log.unlink()
        said.clear()
        self.env["FAKE_FIXES"] = str(self.tmp / "elsewhere")
        self.assertEqual(quality.fix(self.root, self.env, said.append), 0)
        self.assertIn(["ruff", "format", "."], self.calls())
        self.assertRegex(said[0], r"^fix: ruff check --fix and ruff format changed 0 files \(")

    def test_fix_without_ruff_fails(self):
        said = []
        self.assertEqual(quality.fix(self.root, self.env, said.append), 1)
        self.assertTrue(said[0].startswith("FAIL quality: ruff 0.17.3 not available"), said)


class AnnotationsRequiredTest(unittest.TestCase):
    """Annotations are required in every module of lib/foreman: the ANN rules are in the global ruff selection and no
    per-file-ignores entry switches them off (`typed modules N/M` is gone: there is no list left to count)."""

    def test_ann_rules_are_global_and_not_ignored(self):
        root = Path(__file__).resolve().parents[1]
        ruff = toml_reader.loads((root / "pyproject.toml").read_text())["tool"]["ruff"]["lint"]
        selected = set(ruff["select"]) | set(ruff["extend-select"])
        for rule in ("ANN001", "ANN201", "ANN202", "ANN204"):
            self.assertIn(rule, selected)
        for where, patterns in ruff.get("per-file-ignores", {}).items():
            if [p for p in patterns if p.startswith("ANN")]:
                self.assertTrue(
                    where.startswith(("tests/", "dev/tests/")), where
                )  # never lib/foreman nor dev/foreman_dev


class SelftestOptionsTest(QualityCase):
    """`foreman selftest --quality-only` through the CLI, on foreman's own tree, with the fake tools: the line of the
    quality checks, the summary and the exit code."""

    def selftest(self, *args):
        env = dict(self.env, PYTHONDONTWRITEBYTECODE="1")
        r = subprocess.run(
            [sys.executable, str(ENTRY), "selftest", "--mods=off", *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=300,
            check=False,
        )
        return r.returncode, r.stdout + r.stderr

    def test_quality_only(self):
        rc, out = self.selftest("--quality-only", "--quality=require")
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL quality: ruff 0.17.3 not available", out)
        self.assertRegex(
            out, r"selftest: FAIL: quality only, static 0 problems, quality \d+ problems?, mods off, \d+ s"
        )
        rc, out = self.selftest("--quality-only")  # auto: skipped, passes
        self.assertEqual(rc, 0, out)
        self.assertIn("SKIP quality: ruff 0.17.3 not available", out)
        self.assertIn("quality skipped: ruff", out)
        self.on_path("ruff", "0.17.3")
        self.out(
            "ruff",
            [
                {
                    "code": "F401",
                    "filename": str(ROOT / "lib/foreman/util.py"),
                    "location": {"row": 7, "column": 1},
                    "message": "`x` imported but unused",
                    "fix": {"applicability": "safe"},
                }
            ],
        )
        rc, out = self.selftest("--quality-only", "--quality=require")
        self.assertEqual(rc, 1, out)
        self.assertIn(
            "FAIL ruff lib/foreman/util.py:7:1 F401 `x` imported but unused  (fix: bin/foreman selftest --fix)\n", out
        )
        self.assertRegex(
            out,
            r"\nquality: ruff 1 problem, .*\(ruff 0\.17\.3\); \d+ noqa, \d+ pyright: ignore\n",
        )

    def test_python_passes_quality_off(self):
        """--python runs the selftest again with --quality=off and --mods=off unless told otherwise."""
        with mock.patch.object(selftest.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
            selftest.run(python=sys.executable)
            selftest.run(python=sys.executable, quality_only=True)
            selftest.run(python=sys.executable, quality="require", fix=True, mods="require")
        calls = [c.args[0] for c in run.call_args_list]
        self.assertEqual(calls[0][-2:], ["--quality=off", "--mods=off"])
        self.assertEqual(calls[1][-3:], ["--quality=off", "--mods=off", "--quality-only"])
        self.assertEqual(calls[2][-3:], ["--quality=require", "--mods=require", "--fix"])


if __name__ == "__main__":
    unittest.main()
