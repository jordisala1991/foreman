"""The selftest's check of the hooks module (lib/foreman/mods.py) with a fake `claude` (no real Claude Code runs), and
the static check of tests/mod/contract.ts (selftest.mod_contract)."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from foreman import mods, selftest, versions

# A fake `claude`: its version from FAKE_VERSION; `plugin validate` prints FAKE_VALIDATE (a file) and `plugin test`
# FAKE_TEST, each exiting with FAKE_*_RC.
FAKE_CLAUDE = """#!/bin/sh
case "$1 $2" in
  "--version "*) echo "$FAKE_VERSION (Claude Code)";;
  "plugin validate") cat "$FAKE_VALIDATE"; exit "${FAKE_VALIDATE_RC:-0}";;
  "plugin test") cat "$FAKE_TEST"; exit "${FAKE_TEST_RC:-0}";;
  *) exit 3;;
esac
"""


def validate_report(hooks=tuple(mods.MODULE_HOOKS), gating=(), success=True, warnings=()):
    notes = [f"{mods.MODULE} hooks: {', '.join(hooks)}"] if hooks else []
    return {
        "success": success,
        "manifest": {"file": "/x/.claude-plugin/plugin.json", "errors": [], "warnings": [], "notes": []},
        "contents": [
            {
                "file": "/x/hooks/hooks.json",
                "errors": [],
                "warnings": [{"path": "hooks", "message": w} for w in warnings],
                "notes": notes,
                "gatingHooks": [{"module": mods.MODULE, "hook": h, "hasCatch": c} for h, c in gating],
            }
        ],
    }


TEST_OK = "tests/mod/start.test.ts:\n(pass) a [1ms]\n(pass) b [1ms]\n\n 2 pass\n 0 fail\nRan 2 tests across 1 file.\n"
TEST_FAIL = "(pass) a [1ms]\n(fail) b: the status line [1ms]\n  AssertionError\n\n 1 pass\n 1 fail\n"


class ModsCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="foreman-mods-"))
        self.exe = self.tmp / "claude"
        self.exe.write_text(FAKE_CLAUDE)
        self.exe.chmod(0o755)
        self.env = {"PATH": "/usr/bin:/bin", "CLAUDE_CODE_EXECPATH": str(self.exe), "FAKE_VERSION": "2.1.314"}
        self.answer(validate_report(), TEST_OK)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def answer(self, validate, test, validate_rc=0, test_rc=0):
        (self.tmp / "validate.json").write_text(json.dumps(validate) if isinstance(validate, dict) else validate)
        (self.tmp / "test.txt").write_text(test)
        self.env.update(
            FAKE_VALIDATE=str(self.tmp / "validate.json"),
            FAKE_TEST=str(self.tmp / "test.txt"),
            FAKE_VALIDATE_RC=str(validate_rc),
            FAKE_TEST_RC=str(test_rc),
        )

    def run_mods(self, mode):
        return mods.run(self.tmp, mode, self.env)

    def test_ok(self):
        rep = self.run_mods("require")
        self.assertEqual((rep.problems, rep.lines, rep.word), (0, [], "mods ok"))
        self.assertEqual(
            rep.line,
            f"mods: validate hooks {', '.join(mods.MODULE_HOOKS)}, 0 gating; plugin test 2 tests pass "
            "(Claude Code 2.1.314)",
        )

    def test_off_runs_nothing(self):
        self.exe.unlink()
        rep = self.run_mods("off")
        self.assertEqual((rep.problems, rep.lines, rep.word), (0, [], "mods off"))

    def test_no_claude_or_too_old(self):
        for setup in ("missing", "old"):
            if setup == "missing":
                self.env["CLAUDE_CODE_EXECPATH"] = str(self.tmp / "nope")
                self.env["PATH"] = str(self.tmp / "empty")
            else:
                self.env["CLAUDE_CODE_EXECPATH"] = str(self.exe)
                self.env["FAKE_VERSION"] = "2.1.200"
            auto = self.run_mods("auto")
            self.assertEqual((auto.problems, auto.skipped, auto.word), (0, ["mods"], "mods skipped"), setup)
            self.assertTrue(auto.lines[0].startswith("SKIP mods: "), auto.lines)
            req = self.run_mods("require")
            self.assertEqual((req.problems, req.word), (1, "mods 1 problem"), setup)
            self.assertTrue(req.lines[0].startswith("FAIL mods: "), req.lines)
        self.assertIn(versions.CLAUDE_CODE_MIN, req.lines[0])
        self.assertIn("2.1.200", req.lines[0])

    def test_validate_findings(self):
        self.answer(
            validate_report(
                hooks=("session.start", "tool.call{tool=Bash}"),
                gating=[("tool.call{tool=Bash}", False), ("prompt.submit", True)],
                success=False,
                warnings=["hooks.Bogus: unknown hook event"],
            ),
            TEST_OK,
            validate_rc=1,
        )
        rep = self.run_mods("auto")
        self.assertEqual(rep.problems, 3, rep.lines)
        text = "\n".join(rep.lines)
        self.assertIn("hooks.Bogus: unknown hook event", text)
        self.assertIn("expected session.start", text)
        self.assertIn("gating hook without .catch: ./mod/foreman.ts tool.call{tool=Bash}", text)
        self.assertNotIn("without .catch: ./mod/foreman.ts prompt.submit", text)

    def test_validate_does_not_run(self):
        self.answer("not json", TEST_OK, validate_rc=2)
        rep = self.run_mods("auto")
        self.assertEqual(rep.problems, 1, rep.lines)
        self.assertIn("claude plugin validate did not run (exit 2)", rep.lines[0])

    def test_plugin_test_fails(self):
        self.answer(validate_report(), TEST_FAIL, test_rc=1)
        rep = self.run_mods("auto")
        self.assertEqual(rep.lines, ["FAIL mods: plugin test b: the status line [1ms]"])
        self.assertEqual(rep.word, "mods 1 problem")
        self.answer(validate_report(), "boom\n", test_rc=1)
        rep = self.run_mods("auto")
        self.assertEqual(rep.problems, 1)
        self.assertIn("claude plugin test (exit 1, 0 pass, 0 fail): boom", rep.lines[0])


class ContractTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="foreman-contract-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_repo_contract_reads(self):
        cases = selftest.mod_contract()
        self.assertTrue(any(c["python"] for c in cases))
        self.assertEqual(
            {c["op"] for c in cases},
            {"start", "measure", "tick", "renewed", "answer", "end", "status", "grant"},
        )

    def test_anything_else_is_refused(self):
        f = self.tmp / "contract.ts"
        case = dict.fromkeys(selftest.MOD_CONTRACT_KEYS)
        good = "// a comment\n// another\nexport default " + json.dumps([case]) + "\n"
        f.write_text(good)
        self.assertEqual(selftest.mod_contract(f), [case])
        for bad in (
            "export const cases = []\n",
            'export default [{"name": 1,}]\n',
            "export default []\n",
            "export default [" + json.dumps({"name": "x"}) + "]\n",
            good + "const x = 1\n",
        ):
            f.write_text(bad)
            with self.assertRaises(ValueError, msg=bad):
                selftest.mod_contract(f)


if __name__ == "__main__":
    unittest.main()
