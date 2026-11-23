import contextlib
import io
import json
import os
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from helpers import Env

from foreman.core.oversight import guard
from foreman.core.project.config import Project
from foreman.core.tasks.backlog import Local
from foreman.providers import registry


def LOCAL(p: Project, tid: str) -> Local | None:  # the finder `foreman guard` passes
    return registry.backlog(p).local(tid)


class TestBash:
    def check(self, cmd: str, branch: str = "foreman/ty-1", own: str = "/w/ty-1") -> str | None:
        sc = guard.Scope(own, [Path(own)], branch, Path("/h/foreman.toml"), [], scratch=Path("/h/scratch"), task="TY-1")
        return guard.check_bash(cmd, sc)

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

    def test_a_redirection_is_not_a_path(self):
        for cmd in (
            "rm -f build 2>/dev/null",
            "rm -rf build >/dev/null 2>&1",
            "rm -rf /h/scratch/TY-1-r1-a-hunter/t &>/dev/null",
            "cd /Users/x && rm -f /tmp/x 2>/dev/null",
            "git worktree remove --force /tmp/x 2>/dev/null",
            "git branch -D foreman/ty-1 2>/dev/null",
            "rm '>' ../other",  # a quoted operator reads as a redirection, as a quoted ; reads as a separator: it now passes
        ):
            assert self.check(cmd) is None, cmd
        for cmd in (
            "rm -rf ../other 2>/dev/null",
            "rm -rf ../other >/dev/null 2>&1",
            "cd /Users/x && rm -rf t >/dev/null 2>&1",
            "rm -rf ../other &>/dev/null",
            "git branch -D foreman/ty-2 2>/dev/null",
            "git worktree remove /w/ty-2 2>/dev/null",
        ):
            assert self.check(cmd) == "delete", cmd

    def test_own_scratch_folder_absolute(self):
        assert self.check("rm -rf /h/scratch/TY-1-r1-abc123def-hunter/t") is None
        assert self.check("rm -rf /h/scratch/TY-1/x") is None

    def test_relative_after_cd(self):
        assert self.check("cd /h/scratch/TY-1-r1-a-hunter && rm -rf t") is None
        assert self.check("cd /tmp/x; rm -rf y") is None
        assert self.check("cd /Users/x; cd /w/ty-1; rm -rf build") is None
        assert self.check("cd /Users/x && rm -rf t") == "delete"
        assert self.check("cd /h/scratch/TY-1-r1-a-hunter; rm -rf ../TY-2-r1-a-hunter") == "delete"

    def test_variable_set_in_the_command(self):
        assert self.check("S=/tmp/x; rm -rf $S/y") is None
        assert self.check('S=/h/scratch/TY-1-r1-a-hunter; rm -rf "$S/t"') is None
        assert self.check("S=/tmp/x; git worktree remove --force $S") is None
        assert self.check("S=/tmp/x; cd $S; rm -rf t") is None
        assert self.check("export S=/tmp/x && rm -rf ${S}/y") is None

    def test_variable_or_cd_pointing_outside(self):
        assert self.check("S=/Users/x; rm -rf $S") == "delete"
        assert self.check("S=/Users/x; rm -rf ${S}/y") == "delete"
        assert self.check("S=/Users/x; cd $S; rm -rf t") == "delete"

    def test_a_value_of_several_words_is_unresolved(self):
        assert self.check("S='/tmp/x /Users/y'; rm -rf $S") == "delete"
        assert self.check("S='/tmp/x /Users/y'; git worktree remove $S") == "delete"
        assert self.check("S=/tmp/*; rm -rf $S") == "delete"

    def test_a_cd_that_may_not_carry_on_is_unresolved(self):
        for op in ("&", "|", "||", "|&"):
            assert self.check(f"cd /tmp/x {op} rm -rf foo", own="/Users/x") == "delete", op
            assert self.check(f"ls {op} cd /tmp/x; rm -rf foo", own="/Users/x") == "delete", op
            assert self.check(f"S=/tmp/x {op} rm -rf $S") == "delete", op
        assert self.check("ls | head; rm -rf build") is None

    def test_unresolved_variable_is_outside(self):
        for cmd in (
            "rm -rf $S/y",
            "rm -rf $HOME/x",
            "S=$(mktemp -d); rm -rf $S",
            "S=/tmp/x; S=$(mktemp -d -p /Users/x); rm -rf $S",
            "S=/tmp/x rm -rf $S",
            "cd $UNSET; rm -rf t",
        ):
            assert self.check(cmd) == "delete", cmd

    def test_other_tasks_scratch_and_the_root(self):
        for path in ("/h/scratch/TY-10-r1-a-hunter", "/h/scratch/TY-2-r1-a-hunter", "/h/scratch", "/h/scratch/x"):
            assert self.check(f"rm -rf {path}") == "delete", path

    def test_worktree_remove_only_of_scratch_or_temp(self):
        assert self.check("git worktree remove /tmp/x") is None
        assert self.check("git worktree remove /h/scratch/TY-1-r1-a-hunter") is None
        assert self.check("git worktree remove /w/ty-2") == "delete"
        assert self.check("git worktree remove /w/ty-1") == "delete"
        assert self.check("git -C /w/ty-1 worktree remove /tmp/x") == "delete"

    def test_config(self):
        assert self.check("sed -i '' s/manual/low/ /h/foreman.toml") == "config"
        assert self.check("echo x >> /h/foreman.toml") == "config"
        assert self.check("cat /h/foreman.toml") is None

    def test_owner_rules(self):
        extra = [{"name": "deploy", "pattern": r"^\s*make deploy"}]
        assert guard.check_bash("make deploy", guard.Scope("/w", [], "", Path("/h/foreman.toml"), extra)) == "deploy"

    def test_owner_rules_see_cd_and_export(self):
        extra = [{"name": "prod", "pattern": r"^cd /prod"}, {"name": "aws", "pattern": r"^export AWS"}]
        sc = guard.Scope("/w", [], "", Path("/h/foreman.toml"), extra)
        assert guard.check_bash("cd /prod && ls", sc) == "prod"
        assert guard.check_bash("export AWS_KEY=1", sc) == "aws"


class TestHook:
    @pytest.fixture(autouse=True)
    def set_up(self, env: Env):
        self.e = env
        self.tid = registry.backlog(self.e.p).add("t", status="ready", criteria="c")
        registry.backlog(self.e.p).update(self.tid, worktree=str(self.e.tmp / "wt"), branch="foreman/ty-1")
        os.environ.update(FOREMAN_HOME=str(self.e.home), FOREMAN_TASK=self.tid)
        yield
        os.environ.pop("FOREMAN_HOME")
        os.environ.pop("FOREMAN_TASK")

    def call(self, tool: str, inp: dict[str, Any]) -> str:
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

    def test_scratch_of_the_task(self):  # the hook gives the guard the project's scratch root and the task
        seen: list[guard.Scope] = []
        real = guard.check_bash

        def spy(cmd: str, sc: guard.Scope) -> str | None:
            seen.append(sc)
            return real(cmd, sc)

        with mock.patch.object(guard, "check_bash", spy):
            own = self.e.home / "scratch" / f"{self.tid}-r1-abc123def-hunter" / "x"
            assert self.call("Bash", {"command": f"rm -rf {own}"}) == ""
        assert (seen[0].scratch, seen[0].task) == ((self.e.home / "scratch").resolve(), self.tid)

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
