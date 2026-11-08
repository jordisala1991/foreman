import contextlib
import io
import json
import os
import unittest
from pathlib import Path

from helpers import Env

from fm import backlog, guard


class Bash(unittest.TestCase):
    def check(self, cmd: str, branch: str = "fm/ty-1", own: str = "/w/ty-1") -> str | None:
        return guard.check_bash(cmd, guard.Scope(own, [Path(own)], branch, Path("/h/fm.toml"), []))

    def test_publish_by_program_name(self):
        self.assertEqual(self.check("npm publish"), "publish")
        self.assertEqual(self.check("cd x && twine upload dist/*"), "publish")
        self.assertEqual(self.check("gh release create v1"), "publish")
        self.assertEqual(self.check("gh pr merge 3"), "publish")
        self.assertIsNone(self.check("gh pr view 3"))

    def test_a_name_inside_a_string_is_not_an_action(self):
        self.assertIsNone(self.check("echo 'never run git push origin main'"))
        self.assertIsNone(self.check('git commit -m "npm publish later; git push"'))
        self.assertIsNone(self.check("grep -n 'rm -rf /' notes.md"))

    def test_push(self):
        self.assertEqual(self.check("git push origin main"), "push-main")
        self.assertEqual(self.check("git push origin HEAD:refs/heads/master"), "push-main")
        self.assertEqual(self.check("git push --force origin fm/ty-1"), "push-main")
        self.assertEqual(self.check("git -C /w/ty-1 push origin main"), "push-main")
        self.assertIsNone(self.check("git push origin fm/ty-1"))
        self.assertIsNone(self.check("git push"))  # the current branch is the task's
        self.assertEqual(self.check("git push", branch="main"), "push-main")
        self.assertEqual(self.check("git push origin --delete other"), "delete")

    def test_delete_what_is_not_yours(self):
        self.assertIsNone(self.check("rm -rf build"))
        self.assertIsNone(self.check("rm -rf /w/ty-1/build"))
        self.assertIsNone(self.check("rm -f /tmp/x.txt"))
        self.assertEqual(self.check("rm -rf ../other"), "delete")
        self.assertEqual(self.check("rm -rf ~/Projects"), "delete")
        self.assertEqual(self.check("git branch -D fm/ty-2"), "delete")
        self.assertIsNone(self.check("git branch -D fm/ty-1"))
        self.assertEqual(self.check("git worktree remove /w/ty-2"), "delete")

    def test_config(self):
        self.assertEqual(self.check("sed -i '' s/manual/low/ /h/fm.toml"), "config")
        self.assertEqual(self.check("echo x >> /h/fm.toml"), "config")
        self.assertIsNone(self.check("cat /h/fm.toml"))

    def test_owner_rules(self):
        extra = [{"name": "deploy", "pattern": r"^\s*make deploy"}]
        self.assertEqual(guard.check_bash("make deploy", guard.Scope("/w", [], "", Path("/h/fm.toml"), extra)), "deploy")


class Hook(unittest.TestCase):
    def setUp(self):
        self.e = Env()
        self.iid = backlog.add(self.e.p, "t", status="ready", criterion="c")
        backlog.update(self.e.p, self.iid, worktree=str(self.e.tmp / "wt"), branch="fm/ty-1")
        os.environ.update(FM_HOME=str(self.e.home), FM_ISSUE=self.iid)

    def tearDown(self):
        os.environ.pop("FM_HOME")
        os.environ.pop("FM_ISSUE")

    def call(self, tool: str, inp: dict) -> str:
        return guard.hook(json.dumps({"tool_name": tool, "tool_input": inp, "cwd": str(self.e.tmp / "wt")}))

    def test_denies_with_a_reason_and_logs(self):
        out = json.loads(self.call("Bash", {"command": "git push origin main"}))
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("fm ask --action push-main", out["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertTrue((self.e.home / "state/guard.jsonl").exists())

    def test_edit_of_the_config(self):
        self.assertTrue(self.call("Edit", {"file_path": str(self.e.home / "fm.toml")}))
        self.assertEqual(self.call("Edit", {"file_path": str(self.e.tmp / "wt/app.py")}), "")

    def test_a_grant_lets_it_pass_once(self):
        guard.grant(self.e.p, self.iid, "push-main")
        self.assertEqual(self.call("Bash", {"command": "git push origin main"}), "")
        self.assertTrue(self.call("Bash", {"command": "git push origin main"}))

    def test_outside_an_agent_it_lets_everything_run(self):
        os.environ.pop("FM_HOME")
        self.assertEqual(self.call("Bash", {"command": "npm publish"}), "")
        os.environ["FM_HOME"] = str(self.e.home)

    def test_a_broken_input_lets_it_run(self):
        with contextlib.redirect_stderr(io.StringIO()):  # the hook reports the broken input on stderr
            self.assertEqual(guard.hook("not json"), "")


if __name__ == "__main__":
    unittest.main()
