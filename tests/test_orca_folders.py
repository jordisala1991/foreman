"""Where each agent runs under Orca (services/agents/worktree.py `agent_folder`): every agent but the responsible one gets
a detached worktree of its own in `state/agent-folders`, never the main checkout (FN-218 F3); under claude, nothing changes."""

from pathlib import Path

import pytest
from fakerepo import FakeRepo
from fakerunner import FakeRunner
from helpers import Env

from foreman.core.tasks import backlog
from foreman.providers import registry
from foreman.providers.repo import FileDiff
from foreman.services.agents import launch
from foreman.services.delivery import merge
from foreman.steps import mechanic, parents


def task(e: Env, tid: str) -> backlog.Task:
    return backlog.get(registry.backlog(e.p).load(), tid)


def started(e: Env, runner: str, fake_runner: FakeRunner, fake_git: FakeRepo) -> dict[str, Path]:
    """Launches each kind of agent; the folder each was started in, by name."""
    e.p.cfg["agents"]["runner"] = runner
    plan = registry.backlog(e.p).add(status="backlog", criteria="y", title="plan me")
    launch.launch(e.p, task(e, plan))
    work = registry.backlog(e.p).add(status="ready", criteria="y", title="do me")
    launch.launch(e.p, task(e, work))
    tip = fake_git.commit(task(e, work)["branch"], FileDiff("a.py", 1, 0))
    launch.launch_reviewer(e.p, task(e, work), tip, "hunter")
    launch.launch_reviewer(e.p, task(e, work), tip)
    mechanic.launch(e.p, task(e, registry.backlog(e.p).add(status="triage", criteria="y", title="diagnose me", source="health:x")))
    parents.launch(e.p, task(e, registry.backlog(e.p).add(status="backlog", criteria="y", title="parent")))
    return {agent.name: cwd for agent, _, cwd in fake_runner.started}


def test_under_orca_each_agent_but_the_responsible_gets_a_detached_folder(fake_git: FakeRepo, fake_runner: FakeRunner):
    e = Env(repo=fake_git)
    cwd = started(e, "orca", fake_runner, fake_git)
    wt = Path(task(e, "TY-2")["worktree"])
    assert cwd["ty-2"] == wt  # the responsible agent keeps its worktree
    main, tip = fake_git.head("main"), fake_git.head(task(e, "TY-2")["branch"])
    want = {"ty-1-planning": main, "ty-2-hunt": tip, "ty-2-review": tip, "ty-3-mechanic": main, "ty-4": main}
    for name, commit in want.items():
        folder = cwd[name]
        assert folder == e.p.state / "agent-folders" / f"{name}-{commit[:9]}"
        assert fake_git.trees[folder] == commit and fake_git.branch_at[folder] == ""
        assert folder not in (e.p.repo, wt)
    assert task(e, "TY-1")["agent"]["cwd"] == str(cwd["ty-1-planning"])


def test_under_claude_the_folders_are_as_before(fake_git: FakeRepo, fake_runner: FakeRunner):
    e = Env(repo=fake_git)
    cwd = started(e, "claude", fake_runner, fake_git)
    wt = Path(task(e, "TY-2")["worktree"])
    assert cwd == {"ty-1-planning": e.p.repo, "ty-2": wt, "ty-2-hunt": wt, "ty-2-review": wt, "ty-3-mechanic": e.p.repo, "ty-4": e.p.repo}
    assert not (e.p.state / "agent-folders").exists()


@pytest.mark.parametrize("tid", ["TY-1", "TY-2"])
def test_closing_a_task_removes_its_agent_folders(fake_git: FakeRepo, fake_runner: FakeRunner, tid: str):
    e = Env(repo=fake_git)
    cwd = started(e, "orca", fake_runner, fake_git)
    mine = [f for name, f in cwd.items() if name.startswith(tid.lower() + "-")]
    merge.stop_all(e.p, task(e, tid))
    assert mine and not [f for f in mine if f in fake_git.worktrees()]
    assert cwd["ty-3-mechanic"] in fake_git.worktrees()  # another task's stays
