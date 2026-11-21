import contextlib
import io
import json
import os
from pathlib import Path
from unittest import mock

import pytest

from foreman.core.oversight import guard
from foreman.core.project.config import Project
from foreman.core.tasks.backlog import Local
from foreman.providers import registry


def LOCAL(p: Project, tid: str) -> Local | None:  # the finder `foreman guard` passes
    return registry.backlog(p).local(tid)


class TestBash:
    def check(self, cmd: str, branch: str = "foreman/ty-1", own: str = "/w/ty-1") -> str | None:
        return guard.check_bash(cmd, guard.Scope(own, [Path(own)], branch, Path("/h/foreman.toml"), []))

    def test_publish_by_program_name(self):
        assert self.check("npm publish") == "publish"
        assert self.check("cd x && twine upload dist/*") == "publish"
        assert self.check("gh release create v1") == "publish"
        assert self.check("gh pr merge 3") == "publish"
        assert self.check("gh pr view 3") is None

    def test_gh_repo_and_project_delete_are_a_delete(self):
        assert self.check("gh repo delete X --yes") == "delete"
        assert self.check("gh project delete 7 --owner O") == "delete"
        assert self.check("cd x && gh repo delete X --yes") == "delete"

    def test_other_gh_writes_are_unchanged(self):
        for cmd in ("gh pr create", "gh release create v1", "gh pr merge 3", "gh repo create X", "gh release delete v1"):
            assert self.check(cmd) == "publish"
        for cmd in ("gh issue edit 3", "gh pr view 3", "gh repo view X"):
            assert self.check(cmd) is None

    def test_a_name_inside_a_string_is_not_an_action(self):
        assert self.check("echo 'never run git push origin main'") is None
        assert self.check('git commit -m "npm publish later; git push"') is None
        assert self.check("grep -n 'rm -rf /' notes.md") is None

    def test_push(self):
        assert self.check("git push origin main") == "push-main"
        assert self.check("git push origin HEAD:refs/heads/master") == "push-main"
        assert self.check("git push --force origin foreman/ty-1") == "push-main"
        assert self.check("git -C /w/ty-1 push origin main") == "push-main"
        assert self.check("git push origin foreman/ty-1") is None
        assert self.check("git push origin HEAD:foreman-evidence") == "push-evidence"
        assert self.check("git push") is None  # the current branch is the task's
        assert self.check("git push", branch="main") == "push-main"
        assert self.check("git push origin --delete other") == "delete"

    def test_push_with_plus(self):
        for cmd in (
            "git push origin +main",
            "git push origin +HEAD:main",
            "git push origin +refs/heads/main",
            "git push origin +foreman/ty-1",
            "git push origin +HEAD:foreman-evidence",
            "git push origin foreman/ty-1 +main",
            "git push -o -o origin +main",
        ):
            assert self.check(cmd) == "push-main", cmd
        for cmd in ("git push origin foreman/ty-1", "git push origin HEAD:foreman/ty-1", "git push -u origin foreman/ty-1", "git push"):
            assert self.check(cmd) is None, cmd

    def test_delete_what_is_not_yours(self):
        assert self.check("rm -rf build") is None
        assert self.check("rm -rf /w/ty-1/build") is None
        assert self.check("rm -f /tmp/x.txt") is None
        assert self.check("rm -rf ../other") == "delete"
        with mock.patch.dict(os.environ, HOME="/Users/owner"):  # the suite's HOME is a temp dir, which counts as yours
            assert self.check("rm -rf ~/Projects") == "delete"
        assert self.check("git branch -D foreman/ty-2") == "delete"
        assert self.check("git branch -D foreman/ty-1") is None
        assert self.check("git worktree remove /w/ty-2") == "delete"

    def test_config(self):
        assert self.check("sed -i '' s/manual/low/ /h/foreman.toml") == "config"
        assert self.check("echo x >> /h/foreman.toml") == "config"
        assert self.check("cat /h/foreman.toml") is None

    def test_owner_rules(self):
        extra = [{"name": "deploy", "pattern": r"^\s*make deploy"}]
        assert guard.check_bash("make deploy", guard.Scope("/w", [], "", Path("/h/foreman.toml"), extra)) == "deploy"


class TestHook:
    @pytest.fixture(autouse=True)
    def set_up(self, env):
        self.e = env
        self.tid = registry.backlog(self.e.p).add("t", status="ready", criteria="c")
        registry.backlog(self.e.p).update(self.tid, worktree=str(self.e.tmp / "wt"), branch="foreman/ty-1")
        os.environ.update(FOREMAN_HOME=str(self.e.home), FOREMAN_TASK=self.tid)
        yield
        os.environ.pop("FOREMAN_HOME")
        os.environ.pop("FOREMAN_TASK")

    def call(self, tool: str, inp: dict) -> str:
        return guard.hook(json.dumps({"tool_name": tool, "tool_input": inp, "cwd": str(self.e.tmp / "wt")}), LOCAL)

    def test_denies_with_a_reason_and_logs(self):
        out = json.loads(self.call("Bash", {"command": "git push origin main"}))
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "foreman ask --action push-main" in out["hookSpecificOutput"]["permissionDecisionReason"]
        assert (self.e.home / "state/guard.jsonl").exists()

    def test_edit_of_the_config(self):
        assert self.call("Edit", {"file_path": str(self.e.home / "foreman.toml")})
        assert self.call("Edit", {"file_path": str(self.e.tmp / "wt/app.py")}) == ""

    def test_a_grant_lets_it_pass_once(self):
        guard.grant(self.e.p, self.tid, "push-main")
        assert self.call("Bash", {"command": "git push origin main"}) == ""
        assert self.call("Bash", {"command": "git push origin main"})

    def test_one_grant_of_delete_lets_gh_delete_run(self):
        guard.grant(self.e.p, self.tid, "delete")
        assert self.call("Bash", {"command": "gh repo delete X --yes"}) == ""
        assert self.call("Bash", {"command": "gh repo delete X --yes"})
        guard.grant(self.e.p, self.tid, "delete")
        assert self.call("Bash", {"command": "gh project delete 7 --owner O"}) == ""

    def test_outside_an_agent_it_lets_everything_run(self):
        os.environ.pop("FOREMAN_HOME")
        assert self.call("Bash", {"command": "npm publish"}) == ""
        os.environ["FOREMAN_HOME"] = str(self.e.home)

    def test_a_broken_input_lets_it_run(self):
        with contextlib.redirect_stderr(io.StringIO()):  # the hook reports the broken input on stderr
            assert guard.hook("not json", LOCAL) == ""

    def test_push_main_covers_every_repos_main(self):
        t = self.e.tmp
        (t / "app").mkdir()
        (t / "api").mkdir()
        (self.e.home / "foreman.toml").write_text(
            f'[project]\nname = "toy"\nprefix = "TY"\nworktrees = "{t / "wt"}"\n'
            f'[repos.app]\npath = "{t / "app"}"\n[repos.api]\npath = "{t / "api"}"\nmain = "develop"\n'
        )
        registry.backlog(self.e.p).update(self.tid, repo="app")
        for branch in ("main", "develop"):
            out = json.loads(self.call("Bash", {"command": f"git push origin {branch}"}))
            assert "foreman ask --action push-main" in out["hookSpecificOutput"]["permissionDecisionReason"], branch
        assert self.call("Bash", {"command": "git push origin foreman/ty-1"}) == ""

    def test_a_one_repo_project_protects_only_its_own_main(self):
        assert self.call("Bash", {"command": "git push origin develop"}) == ""
        assert self.call("Bash", {"command": "git push origin main"})
