"""Several repos: a task's check, merged check, PR, merge and close run in its own repo, with its settings; merges stay
one at a time across the project."""

import shutil
import tempfile
import threading
from pathlib import Path

import pytest
from fakerepo import FakeRepo
from helpers import git, template_repo
from test_worktree_repos import Setup

from foreman import util
from foreman.core.project.config import Project
from foreman.core.tasks import backlog
from foreman.loop import queue, tick
from foreman.providers import Change, ConfigError, registry
from foreman.providers.repo import FileDiff
from foreman.services.delivery import merge

TABLES = (
    '[repos.api]\npath = "$T/api"\n[repos.api.merge]\ntest = "echo api >> $T/ran"\n'
    '[repos.web]\npath = "$T/web"\nremote = "none"\n[repos.web.merge]\ntest = "echo web >> $T/ran"\n'
)


def setup(monkeypatch: pytest.MonkeyPatch, fake_git: FakeRepo, tables: str = TABLES) -> Setup:
    s = Setup(monkeypatch, fake_git, [], tables=tables)
    (s.e.tmp / "ran").write_text("")
    return s


def ran(s: Setup) -> str:
    return (s.e.tmp / "ran").read_text()


def deliver(s: Setup, key: str) -> str:
    """A task in repo `key`, launched, committed, delivered and reviewed; a last tick checks it."""
    tid = s.task(key)
    tick.tick(s.p)
    s.fakes[key].commit(s.e.task(tid)["branch"], FileDiff("x.py", 1, 0))
    backlog.set_status(s.p, tid, "in review", report={"summary": "s", "confused": ""})
    s.e.review(tid)
    tick.tick(s.p)
    return tid


def test_check_runs_the_tasks_repos_command(monkeypatch, fake_git):
    s = setup(monkeypatch, fake_git)
    web = deliver(s, "web")
    assert ran(s) == "web\n"
    assert s.e.task(web)["check"]["tests"] == "pass"
    deliver(s, "api")
    assert ran(s) == "web\napi\n"


def test_accepted_task_merges_into_its_repos_main(monkeypatch, fake_git):
    s = setup(monkeypatch, fake_git)
    tid = deliver(s, "web")
    s.fakes["web"].commit("main", FileDiff("m.py", 1, 0))
    s.fakes["api"].commit("main", FileDiff("a.py", 1, 0))
    s.fakes["api"].commit("main", FileDiff("b.py", 1, 0))
    web_main = s.fakes["web"].head("main")
    merge.accept(s.p, tid)
    tick.tick(s.p)
    i = s.e.task(tid)
    assert i["status"] == "done"
    assert i["check"]["merged_with"] == web_main
    assert ran(s) == "web\nweb\n"
    assert s.fakes["web"].messages()[-1] == f"Merge {tid}: t"
    assert not any(m.startswith("Merge") for m in s.fakes["api"].messages())


def test_remote_picks_the_provider(monkeypatch, fake_git):
    s = setup(monkeypatch, fake_git)
    for key in ("api", "web"):
        s.fakes[key].remotes = {"origin": "git@github.com:o/r.git"}
    assert registry.get(s.p.for_task({"repo": "api"})).name == "github"
    assert registry.get(s.p.for_task({"repo": "web"})).name == "local"
    s = setup(monkeypatch, fake_git, TABLES + 'provider = "github"\n')
    with pytest.raises(ConfigError, match='remote = "none"'):
        registry.get(s.p.for_task({"repo": "web"}))


def test_github_pushes_to_the_repos_remote():
    tmp = Path(tempfile.mkdtemp(prefix="fmtest-")).resolve()
    app, bare, home = tmp / "app", tmp / "up.git", tmp / "home"
    shutil.copytree(template_repo(), app, symlinks=True)
    git(tmp, "init", "-q", "--bare", str(bare))
    git(app, "remote", "add", "up", str(bare))
    git(app, "branch", "foreman/ty-1")
    home.mkdir()
    (home / "foreman.toml").write_text(
        f'[project]\nname = "toy"\nprefix = "TY"\nworktrees = "{tmp / "wts"}"\n'
        f'[repos.app]\npath = "{app}"\nremote = "up"\n[repos.app.merge]\nprovider = "github"\n'
    )
    gh = registry.get(Project.load(home).for_task({}))
    change = Change("TY-1", branch="foreman/ty-1", head=git(app, "rev-parse", "HEAD").strip())
    assert gh.pushed(change) is False
    gh.push(change)
    assert gh.pushed(change) is True
    assert "refs/heads/foreman/ty-1" in git(app, "ls-remote", "up")


def test_no_merged_check_starts_while_another_runs(monkeypatch, fake_git):
    s = setup(monkeypatch, fake_git)
    tids = [deliver(s, "web"), deliver(s, "api")]
    for key in ("web", "api"):
        s.fakes[key].commit("main", FileDiff("m.py", 1, 0))  # main moved: each merge needs a merged check
    for tid in tids:
        merge.accept(s.p, tid)
    before = ran(s)
    go = threading.Event()
    busy = threading.Thread(target=go.wait, daemon=True)
    busy.start()
    monkeypatch.setitem(util.BACKGROUND, queue.check_key(s.p), busy)
    tick.tick(s.p)
    assert [s.e.task(t)["status"] for t in tids] == ["in review", "in review"]
    assert ran(s) == before
    go.set()
    busy.join()
    tick.tick(s.p)
    assert [s.e.task(t)["status"] for t in tids] == ["done", "done"]


def test_cancel_removes_the_worktree_in_the_tasks_repo(monkeypatch, fake_git):
    s = setup(monkeypatch, fake_git)
    tid = s.task("web")
    tick.tick(s.p)
    assert s.fakes["web"].trees
    merge.close(s.p, tid, "canceled", "x")
    assert s.fakes["web"].trees == {}
