"""Health across several repos: main's tests, orphan worktrees, a repo that is gone, and kept-out files, per repo (real git)."""

import itertools
import shutil
from pathlib import Path
from unittest import mock

from helpers import Env, git, template_repo

from foreman import util
from foreman.core.project.config import Project
from foreman.core.tasks import backlog
from foreman.loop import tick
from foreman.steps.health import checks, health, repos


def several(tests: dict[str, str], umbrella: bool = False) -> Env:
    """An Env whose project has the repos api and web (copies of the template repo), each with its test command."""
    e = Env()
    for key in tests:
        shutil.copytree(template_repo(), e.tmp / key, symlinks=True)
    tables = "".join(f'[repos.{k}]\npath = "{e.tmp / k}"\n[repos.{k}.merge]\ntest = "{t}"\n' for k, t in tests.items())
    shared = f'umbrella = "{e.tmp}"\n' if umbrella else ""
    text = (e.home / "foreman.toml").read_text().replace(f'repo = "{e.repo}"\n', shared)
    (e.home / "foreman.toml").write_text(text + tables)
    e.p = Project.load(e.home)
    return e


def commit(repo: Path, name: str) -> None:
    (repo / name).write_text("x")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", name)


def triage(e: Env) -> list[dict]:
    return [i for i in backlog.tasks(backlog.load(e.p)) if i["status"] == "triage"]


def test_main_tests_file_one_finding_per_failing_repo():
    e = several({"api": "false", "web": "true"})
    health.run(e.p, {})
    health.run(e.p, {})
    (t,) = triage(e)
    assert t["repo"] == "api"
    assert "api" in t["title"]


def test_a_failing_episode_is_per_repo():
    e = several({"api": "test ! -e broken", "web": "test ! -e broken"})
    stamps = (f"2026-01-01 00:00:{n:02d}" for n in itertools.count(1))
    with mock.patch.object(util, "now", lambda: next(stamps)):
        commit(e.tmp / "api", "broken")
        health.run(e.p, {})
        assert len(triage(e)) == 1
        commit(e.tmp / "api", "other")
        health.run(e.p, {})
        assert len(triage(e)) == 1
        commit(e.tmp / "web", "broken")
        health.run(e.p, {})
    assert {t["repo"] for t in triage(e)} == {"api", "web"}
    assert len(triage(e)) == 2


def test_an_orphan_worktree_of_any_repo_is_found():
    e = several({"api": "", "web": ""})
    tid = backlog.add(e.p, "x", status="ready", criteria="y")
    backlog.update(e.p, tid, repo="api")
    tick.tick(e.p)
    assert checks.orphan_worktrees(e.p) == []  # the task's own worktree is live
    git(e.tmp / "web", "worktree", "add", "-q", "-b", "g", str(e.home / "worktrees/ghost"), "main")
    (f,) = checks.orphan_worktrees(e.p)
    assert "ghost" in f["title"]
    assert "web" in f["title"]
    assert f["repo"] == "web"


def test_a_repo_that_is_gone_or_not_a_checkout_is_one_finding():
    e = several({"api": "", "web": "false"})
    shutil.rmtree(e.tmp / "web")
    for _ in range(2):
        found = [f for f in health.findings(e.p, {}) if f.get("repo") == "web"]
        assert len(found) == 1
        assert "web" in found[0]["title"]
        assert not [f for f in health.findings(e.p, {}) if f.get("repo") == "api"]
        shutil.rmtree(e.tmp / "web", ignore_errors=True)
        (e.tmp / "web").mkdir()


def test_stray_files_in_a_repo_are_one_finding_naming_repo_and_path():
    e = several({"api": "", "web": ""}, umbrella=True)
    api, web = e.tmp / "api", e.tmp / "web"
    (api / ".claude").mkdir()
    (api / ".claude/settings.json").write_text("{}")
    (api / "CLAUDE.md").write_text("tracked")
    git(api, "add", "CLAUDE.md")
    git(api, "commit", "-qm", "claude")
    (web / "CLAUDE.local.md").write_text("mine")
    (web / ".git/info/exclude").write_text("CLAUDE.local.md\n")
    (e.tmp / "CLAUDE.md").write_text("umbrella")
    (e.tmp / ".claude").mkdir()
    (e.tmp / ".claude/x").write_text("x")
    found = repos.stray_files(e.p)
    assert [f["repo"] for f in found] == ["api", "web"]
    assert ".claude" in found[0]["title"]
    assert "CLAUDE.md" not in found[0]["title"]
    assert "CLAUDE.local.md" in found[1]["title"]
    assert "Path: .claude" in found[0]["evidence"]


def test_a_one_repo_project_gets_no_new_finding():
    e = Env()
    (e.repo / ".claude").mkdir()
    (e.repo / ".claude/x").write_text("x")
    (e.repo / "CLAUDE.md").write_text("x")
    assert health.findings(e.p, {}) == []
    shutil.rmtree(e.repo)
    assert health.findings(e.p, {}) == []
