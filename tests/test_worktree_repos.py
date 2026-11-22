"""Several repos: a task's worktree, branch and post_create are in its own repo, and its planning agent starts there."""

import json
from pathlib import Path

import pytest
from fakerepo import FakeRepo
from helpers import Env

from foreman.core.project.config import Project
from foreman.loop.launch import launch_free
from foreman.providers import registry
from foreman.providers.repo import FileDiff
from foreman.services.agents import worktree


class Setup:  # tables: foreman.toml [repos.*] tables, $T standing for the temp folder
    def __init__(self, monkeypatch: pytest.MonkeyPatch, fake_git: FakeRepo, post_create: list[str], tables: str = "") -> None:
        self.e = Env(repo=fake_git)
        t = self.e.tmp
        tables = tables.replace("$T", str(t)) or (
            f'[repos.api]\npath = "{t / "api"}"\n[repos.web]\npath = "{t / "web"}"\nworktrees = "{t / "webwts"}"\npost_create = {json.dumps(post_create)}\n'
        )
        (self.e.home / "foreman.toml").write_text(
            f'[project]\nname = "toy"\nprefix = "TY"\nworktrees = "{t / "wts"}"\n[agents]\nslots = 2\nclaude = "{self.e.bin / "claude"}"\n{tables}'
        )
        self.p = self.e.p = Project.load(self.e.home)
        self.fakes: dict[str, FakeRepo] = {}
        for key in self.p.cfg["repos"]:
            (t / key).mkdir()
            self.fakes[key] = FakeRepo()

        def repo(p: Project) -> FakeRepo:
            return self.fakes[p.repo.name]

        monkeypatch.setattr(registry, "repo", repo)

    def task(self, repo: str, status: str = "ready") -> str:
        tid = registry.backlog(self.p).add("t", status=status, criteria="c")
        if repo:
            registry.backlog(self.p).update(tid, repo=repo)
        return tid

    def blocked(self, tid: str) -> str:
        return self.e.task(tid).get("blocked", "")


def test_the_worktree_is_made_in_the_tasks_repo(monkeypatch: pytest.MonkeyPatch, fake_git: FakeRepo):
    s = Setup(monkeypatch, fake_git, [])
    t = s.e.tmp
    s.task("web")
    s.task("api")
    launch_free(s.p)
    assert list(s.fakes["web"].trees) == [t / "webwts" / "ty-1"]
    assert s.fakes["web"].find("foreman/ty-1")
    assert list(s.fakes["api"].trees) == [t / "wts" / "ty-2"]
    assert s.fakes["api"].find("foreman/ty-1") == ""
    assert [Path(c["cwd"]).resolve() for c in s.e.calls()] == [t / "webwts" / "ty-1", t / "wts" / "ty-2"]


def test_the_planning_agent_starts_in_the_tasks_repo(monkeypatch: pytest.MonkeyPatch, fake_git: FakeRepo):
    s = Setup(monkeypatch, fake_git, [])
    s.task("web", status="backlog")
    launch_free(s.p)
    assert Path(s.e.calls()[0]["cwd"]).resolve() == s.e.tmp / "web"


def test_post_create_runs_once_in_the_new_worktree(monkeypatch: pytest.MonkeyPatch, fake_git: FakeRepo):
    s = Setup(monkeypatch, fake_git, ["echo one >> ran.txt", "echo {main} > main.txt"])
    tid = s.task("web")
    launch_free(s.p)
    wt = s.e.tmp / "webwts" / "ty-1"
    assert (wt / "ran.txt").read_text() == "one\n"
    assert (wt / "main.txt").read_text() == f"{s.e.tmp / 'web'}\n"
    i = s.e.task(tid)
    worktree.worktree(s.p.for_task(i), i)
    assert (wt / "ran.txt").read_text() == "one\n"


def test_a_failing_post_create_step_blocks_the_task(monkeypatch: pytest.MonkeyPatch, fake_git: FakeRepo):
    s = Setup(monkeypatch, fake_git, ["echo ok > a.txt", "echo boom >&2; exit 3", "echo no > never.txt"])
    tid = s.task("web")
    launch_free(s.p)
    assert s.blocked(tid) == "post_create step failed (echo boom >&2; exit 3): boom"
    assert s.fakes["web"].trees == {}
    assert not (s.e.tmp / "webwts" / "ty-1").exists()
    assert s.fakes["web"].find("foreman/ty-1") == ""
    assert s.e.calls() == []
    assert s.e.task(tid)["status"] == "ready"


def test_the_branch_it_did_not_make_is_kept(monkeypatch: pytest.MonkeyPatch, fake_git: FakeRepo):
    s = Setup(monkeypatch, fake_git, ["exit 1"])
    made = s.fakes["web"].commit("foreman/ty-1", FileDiff("x", 1, 0))
    tid = s.task("web")
    launch_free(s.p)
    assert s.blocked(tid).startswith("post_create step failed (exit 1)")
    assert s.fakes["web"].find("foreman/ty-1") == made


def test_the_output_is_cut_to_its_last_300_chars(monkeypatch: pytest.MonkeyPatch, fake_git: FakeRepo):
    out = "".join(str(n % 10) for n in range(400))
    s = Setup(monkeypatch, fake_git, [f"echo {out}; exit 1"])
    tid = s.task("web")
    launch_free(s.p)
    assert s.blocked(tid) == f"post_create step failed (echo {out}; exit 1): {out[-300:]}"


def test_a_slow_step_fails_like_a_failed_one(monkeypatch: pytest.MonkeyPatch, fake_git: FakeRepo):
    monkeypatch.setattr(worktree, "POST_CREATE_TIMEOUT", 0.2)
    s = Setup(monkeypatch, fake_git, ["sleep 5"])
    tid = s.task("web")
    launch_free(s.p)
    assert s.blocked(tid).startswith("post_create step failed (sleep 5): ")
    assert "timed out" in s.blocked(tid)
    assert s.fakes["web"].trees == {}


def test_one_repos_table_gives_every_task_its_repo(monkeypatch: pytest.MonkeyPatch, fake_git: FakeRepo):
    s = Setup(monkeypatch, fake_git, [], tables='[repos.main]\npath = "$T/main"\nworktrees = "$T/mainwts"\npost_create = ["touch built"]\n')
    s.task("")
    launch_free(s.p)
    assert list(s.fakes["main"].trees) == [s.e.tmp / "mainwts" / "ty-1"]
    assert (s.e.tmp / "mainwts" / "ty-1" / "built").exists()


def test_a_worktree_it_could_not_remove_is_named_in_the_block(monkeypatch: pytest.MonkeyPatch, fake_git: FakeRepo):
    s = Setup(monkeypatch, fake_git, ["exit 1"])

    def refuse(path: Path) -> None:  # git refuses, e.g. a locked worktree
        return None

    monkeypatch.setattr(s.fakes["web"], "remove_worktree", refuse)
    tid = s.task("web")
    launch_free(s.p)
    wt = s.e.tmp / "webwts" / "ty-1"
    assert s.blocked(tid) == f"post_create step failed (exit 1): ; its worktree {wt} could not be removed: remove it before unblocking"
