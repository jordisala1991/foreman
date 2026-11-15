import contextlib
import io
import json
import os
from pathlib import Path
from unittest import mock

import pytest

from fm import backlog, guard


class TestBash:
    def check(self, cmd: str, branch: str = "fm/ty-1", own: str = "/w/ty-1") -> str | None:
        return guard.check_bash(cmd, guard.Scope(own, [Path(own)], branch, Path("/h/fm.toml"), []))

    def test_publish_by_program_name(self):
        assert self.check("npm publish") == "publish"
        assert self.check("cd x && twine upload dist/*") == "publish"
        assert self.check("gh release create v1") == "publish"
        assert self.check("gh pr merge 3") == "publish"
        assert self.check("gh pr view 3") is None

    def test_a_name_inside_a_string_is_not_an_action(self):
        assert self.check("echo 'never run git push origin main'") is None
        assert self.check('git commit -m "npm publish later; git push"') is None
        assert self.check("grep -n 'rm -rf /' notes.md") is None

    def test_push(self):
        assert self.check("git push origin main") == "push-main"
        assert self.check("git push origin HEAD:refs/heads/master") == "push-main"
        assert self.check("git push --force origin fm/ty-1") == "push-main"
        assert self.check("git -C /w/ty-1 push origin main") == "push-main"
        assert self.check("git push origin fm/ty-1") is None
        assert self.check("git push origin HEAD:fm-evidence") == "push-evidence"
        assert self.check("git push") is None  # the current branch is the task's
        assert self.check("git push", branch="main") == "push-main"
        assert self.check("git push origin --delete other") == "delete"

    def test_push_with_plus(self):
        for cmd in (
            "git push origin +main",
            "git push origin +HEAD:main",
            "git push origin +refs/heads/main",
            "git push origin +fm/ty-1",
            "git push origin +HEAD:fm-evidence",
            "git push origin fm/ty-1 +main",
            "git push -o -o origin +main",
        ):
            assert self.check(cmd) == "push-main", cmd
        for cmd in ("git push origin fm/ty-1", "git push origin HEAD:fm/ty-1", "git push -u origin fm/ty-1", "git push"):
            assert self.check(cmd) is None, cmd

    def test_delete_what_is_not_yours(self):
        assert self.check("rm -rf build") is None
        assert self.check("rm -rf /w/ty-1/build") is None
        assert self.check("rm -f /tmp/x.txt") is None
        assert self.check("rm -rf ../other") == "delete"
        with mock.patch.dict(os.environ, HOME="/Users/owner"):  # the suite's HOME is a temp dir, which counts as yours
            assert self.check("rm -rf ~/Projects") == "delete"
        assert self.check("git branch -D fm/ty-2") == "delete"
        assert self.check("git branch -D fm/ty-1") is None
        assert self.check("git worktree remove /w/ty-2") == "delete"

    def test_config(self):
        assert self.check("sed -i '' s/manual/low/ /h/fm.toml") == "config"
        assert self.check("echo x >> /h/fm.toml") == "config"
        assert self.check("cat /h/fm.toml") is None

    def test_owner_rules(self):
        extra = [{"name": "deploy", "pattern": r"^\s*make deploy"}]
        assert guard.check_bash("make deploy", guard.Scope("/w", [], "", Path("/h/fm.toml"), extra)) == "deploy"


class TestHook:
    @pytest.fixture(autouse=True)
    def set_up(self, env):
        self.e = env
        self.tid = backlog.add(self.e.p, "t", status="ready", criteria="c")
        backlog.update(self.e.p, self.tid, worktree=str(self.e.tmp / "wt"), branch="fm/ty-1")
        os.environ.update(FM_HOME=str(self.e.home), FM_TASK=self.tid)
        yield
        os.environ.pop("FM_HOME")
        os.environ.pop("FM_TASK")

    def call(self, tool: str, inp: dict) -> str:
        return guard.hook(json.dumps({"tool_name": tool, "tool_input": inp, "cwd": str(self.e.tmp / "wt")}))

    def test_denies_with_a_reason_and_logs(self):
        out = json.loads(self.call("Bash", {"command": "git push origin main"}))
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "fm ask --action push-main" in out["hookSpecificOutput"]["permissionDecisionReason"]
        assert (self.e.home / "state/guard.jsonl").exists()

    def test_edit_of_the_config(self):
        assert self.call("Edit", {"file_path": str(self.e.home / "fm.toml")})
        assert self.call("Edit", {"file_path": str(self.e.tmp / "wt/app.py")}) == ""

    def test_a_grant_lets_it_pass_once(self):
        guard.grant(self.e.p, self.tid, "push-main")
        assert self.call("Bash", {"command": "git push origin main"}) == ""
        assert self.call("Bash", {"command": "git push origin main"})

    def test_outside_an_agent_it_lets_everything_run(self):
        os.environ.pop("FM_HOME")
        assert self.call("Bash", {"command": "npm publish"}) == ""
        os.environ["FM_HOME"] = str(self.e.home)

    def test_a_broken_input_lets_it_run(self):
        with contextlib.redirect_stderr(io.StringIO()):  # the hook reports the broken input on stderr
            assert guard.hook("not json") == ""
