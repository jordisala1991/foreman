"""Several repos: a task's check, merged check, PR, merge and close run in its own repo, with its settings; merges stay
one at a time across the project."""

import os
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
from foreman.core.review import verdict
from foreman.core.tasks import backlog
from foreman.loop import queue, tick
from foreman.providers import Change, ConfigError, registry
from foreman.providers.github.provider import GitHub
from foreman.providers.repo import FileDiff
from foreman.services.delivery import merge, pulls
from foreman.ui.panel import views


def logs(key: str) -> str:
    """A test command that logs its repo's key, except when health runs it on main (the tree is named main-KEY)."""
    return f"case $PWD in *main-*) ;; *) echo {key} >> $T/ran;; esac"


TABLES = (
    f'[repos.api]\npath = "$T/api"\n[repos.api.merge]\ntest = "{logs("api")}"\n'
    f'[repos.web]\npath = "$T/web"\nremote = "none"\n[repos.web.merge]\nstrategy = "merge"\ntest = "{logs("web")}"\n'
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
    registry.backlog(s.p).set_status(tid, "in review", report={"summary": "s", "confused": ""})
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
    assert len(s.fakes["web"].parents[s.fakes["web"].head("main")]) == 2  # web's strategy: a merge commit, not api's squash
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


def github_app() -> tuple[Path, GitHub]:
    """A real repo `app` whose only remote is `up` (a bare repo), and its github provider; branch foreman/ty-1 at HEAD."""
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
    assert isinstance(gh, GitHub)
    return app, gh


def test_github_pushes_to_the_repos_remote():
    app, gh = github_app()
    change = Change("TY-1", branch="foreman/ty-1", head=git(app, "rev-parse", "HEAD").strip())
    assert gh.pushed(change) is False
    gh.push(change)
    assert gh.pushed(change) is True
    assert "refs/heads/foreman/ty-1" in git(app, "ls-remote", "up")


def test_github_evidence_and_catch_up_use_the_repos_remote(monkeypatch):
    app, gh = github_app()
    bin_ = app.parent / "bin"
    bin_.mkdir()
    (bin_ / "gh").write_text("#!/bin/sh\necho o/r\n")  # `gh repo view`: the slug for the links
    (bin_ / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_}:{os.environ['PATH']}")
    for name in ("x.png", "y.png"):  # the second finds the branch on `up` and fetches it
        (app.parent / name).write_bytes(b"png")
        assert gh.evidence(Change("TY-1"), {name: app.parent / name})
    assert git(app, "ls-tree", "-r", "--name-only", "up/foreman-evidence").split() == ["TY-1/x.png", "TY-1/y.png"]
    other = app.parent / "other"
    git(app, "push", "-q", "up", "main")
    git(app.parent, "clone", "-q", "-b", "main", str(app.parent / "up.git"), str(other))
    git(other, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "moved")
    git(other, "push", "-q", "origin", "main")
    gh.catch_up()
    assert git(app, "rev-parse", "main") == git(other, "rev-parse", "HEAD")


def test_run_pulls_syncs_each_task_in_its_repo(monkeypatch, fake_git):
    s = setup(monkeypatch, fake_git)
    tid = s.task("web")
    tick.tick(s.p)
    seen: list[Path] = []
    monkeypatch.setattr(pulls, "running", lambda p: [s.e.task(tid)])
    monkeypatch.setattr(pulls, "sync", lambda p, i: seen.append(p.repo))
    tick.run_pulls(s.p)
    assert seen == [s.e.tmp / "web"]


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


def test_autonomy_is_the_repos(monkeypatch, fake_git):
    s = setup(monkeypatch, fake_git, TABLES.replace("[repos.api.merge]\n", '[repos.api.merge]\nautonomy = "low"\n'))
    api, web = deliver(s, "api"), deliver(s, "web")
    tick.tick(s.p)
    assert s.e.task(api)["status"] == "done"
    assert s.e.task(web)["status"] == "in review" and not s.e.task(web).get("accepted")
    merge.accept(s.p, web)
    tick.tick(s.p)
    assert s.e.task(web)["status"] == "done"


PUBLISHING = (
    '[repos.site]\npath = "$T/site"\nremote = "none"\npublish_on_merge = true\n[repos.site.merge]\nautonomy = "medium"\ntest = "true"\n'
    '[repos.api]\npath = "$T/api"\nremote = "none"\n[repos.api.merge]\nautonomy = "medium"\ntest = "true"\n'
)
WHY = "merging publishes this repo (site)"


def test_publishing_repo_waits_for_the_owner(monkeypatch, fake_git):
    s = setup(monkeypatch, fake_git, PUBLISHING)
    site, api = deliver(s, "site"), deliver(s, "api")
    tick.tick(s.p)
    assert s.e.task(api)["status"] == "done"
    i = s.e.task(site)
    assert (i["status"], i["check"]["risk"], i["check"]["reasons"][0]) == ("in review", "high", WHY)
    assert not i.get("accepted")
    merge.accept(s.p, site)
    tick.tick(s.p)
    assert s.e.task(site)["status"] == "done"


def test_the_reason_reaches_show_panel_and_pr(monkeypatch, fake_git):
    s = setup(monkeypatch, fake_git, PUBLISHING)
    site = deliver(s, "site")
    i = backlog.get(registry.backlog(s.p).load(), site)  # what `fm show` prints whole
    assert WHY in i["check"]["reasons"]
    shown = next(t for t in views.state(s.p)["tasks"] if t["id"] == site)
    assert WHY in shown["check"]["reasons"]
    assert f"Why high: {WHY}" in verdict.render(pulls.case_of(i, i["check"]), [], set())
