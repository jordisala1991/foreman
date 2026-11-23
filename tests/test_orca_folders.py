"""Where each agent runs under Orca (services/agents/worktree.py `agent_folder`): every agent but the responsible one gets
a detached worktree of its own in `state/agent-folders`, never the main checkout (FN-218 F3); under claude, nothing changes."""

from pathlib import Path

import pytest
from fakerepo import FakeRepo
from fakerunner import FakeRunner
from helpers import Env

from foreman import util
from foreman.core.tasks import backlog, runs
from foreman.loop import tick
from foreman.providers import registry
from foreman.providers.repo import FileDiff
from foreman.providers.runner import NO_ROWS, State
from foreman.services.agents import launch
from foreman.services.agents.launch import launch_parent
from foreman.services.agents.worktree import owner_of
from foreman.steps import folders, mechanic, parents


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
    launch_parent(e.p, task(e, registry.backlog(e.p).add(status="backlog", criteria="y", title="parent")))
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


def test_a_done_parents_folder_goes_on_the_next_tick(fake_git: FakeRepo, fake_runner: FakeRunner):
    e = Env(repo=fake_git)
    cwd = started(e, "orca", fake_runner, fake_git)
    assert cwd["ty-4"] in fake_git.worktrees()  # open: kept
    runs.end(e.p, "TY-4", ("parent",), "done")  # its turn is over: its session gone, recover leaves it
    tick.tick(e.p)
    assert cwd["ty-4"] in fake_git.worktrees()
    sub = registry.backlog(e.p).add("sub", status="triage", criteria="y", parent="TY-4")
    registry.backlog(e.p).set_status(sub, "canceled")
    parents.finish(e.p, "TY-4", "s")
    tick.tick(e.p)
    assert cwd["ty-4"] not in fake_git.worktrees()


def sweep(e: Env, fake_runner: FakeRunner) -> None:
    folders.run(e.p, fake_runner.rows())


def sid_of(fake_runner: FakeRunner, name: str) -> str:
    n = next(k for k, (agent, _, _) in enumerate(fake_runner.started, 1) if agent.name == name)
    return f"fk{n}-sid"


def kept(fake_git: FakeRepo, cwd: dict[str, Path], name: str) -> bool:
    return cwd[name] in fake_git.worktrees()


def test_the_loop_removes_the_folders_of_a_task_closed_long_ago(fake_git: FakeRepo, fake_runner: FakeRunner):
    e = Env(repo=fake_git)
    cwd = started(e, "orca", fake_runner, fake_git)
    registry.backlog(e.p).set_status("TY-1", "done", "s")  # closed with no stop_all: as a done parent or an edit
    tick.tick(e.p)
    assert not kept(fake_git, cwd, "ty-1-planning")
    assert kept(fake_git, cwd, "ty-3-mechanic")


def test_planning_folder_goes_once_ready(fake_git: FakeRepo, fake_runner: FakeRunner):
    e = Env(repo=fake_git)
    cwd = started(e, "orca", fake_runner, fake_git)
    assert task(e, "TY-1")["status"] == "refining"
    sweep(e, fake_runner)
    assert kept(fake_git, cwd, "ty-1-planning")
    registry.backlog(e.p).update("TY-1", status="ready")
    sweep(e, fake_runner)
    assert not kept(fake_git, cwd, "ty-1-planning") and cwd["ty-1-planning"] in fake_runner.removed


def test_a_busy_or_recently_active_session_keeps_the_folder(fake_git: FakeRepo, fake_runner: FakeRunner):
    e = Env(repo=fake_git)
    cwd = started(e, "orca", fake_runner, fake_git)
    registry.backlog(e.p).update("TY-1", status="ready")
    sid = sid_of(fake_runner, "ty-1-planning")
    fake_runner.states[sid] = State.WORKING
    sweep(e, fake_runner)
    assert kept(fake_git, cwd, "ty-1-planning")
    fake_runner.states[sid] = State.IDLE  # idle, its transcript just written: the owner may be talking to it
    transcript = e.tmp / "transcripts/proj" / f"{sid}.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("{}\n")
    sweep(e, fake_runner)
    assert kept(fake_git, cwd, "ty-1-planning")
    transcript.unlink()
    sweep(e, fake_runner)
    assert not kept(fake_git, cwd, "ty-1-planning")


def test_a_closed_task_goes_when_its_session_is_idle(fake_git: FakeRepo, fake_runner: FakeRunner):
    e = Env(repo=fake_git)
    cwd = started(e, "orca", fake_runner, fake_git)
    sid = sid_of(fake_runner, "ty-1-planning")
    fake_runner.states[sid] = State.IDLE
    transcript = e.tmp / "transcripts/proj" / f"{sid}.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("{}\n")
    registry.backlog(e.p).set_status("TY-1", "canceled", "s")
    fake_runner.states[sid] = State.WORKING
    sweep(e, fake_runner)
    assert kept(fake_git, cwd, "ty-1-planning")  # busy
    fake_runner.states[sid] = State.IDLE
    sweep(e, fake_runner)
    assert not kept(fake_git, cwd, "ty-1-planning")


def test_hunter_folder_goes_with_its_file(fake_git: FakeRepo, fake_runner: FakeRunner):
    e = Env(repo=fake_git)
    cwd = started(e, "orca", fake_runner, fake_git)
    sweep(e, fake_runner)
    assert kept(fake_git, cwd, "ty-2-hunt")
    Path(task(e, "TY-2")["hunter"]["file"]).write_text("findings")
    sweep(e, fake_runner)
    assert not kept(fake_git, cwd, "ty-2-hunt") and kept(fake_git, cwd, "ty-2-review")


def test_the_hunters_folder_does_not_wait_for_the_reviewers_session(fake_git: FakeRepo, fake_runner: FakeRunner):
    e = Env(repo=fake_git)
    cwd = started(e, "orca", fake_runner, fake_git)
    Path(task(e, "TY-2")["hunter"]["file"]).write_text("findings")
    fake_runner.states[sid_of(fake_runner, "ty-2-review")] = State.WORKING
    sweep(e, fake_runner)
    assert not kept(fake_git, cwd, "ty-2-hunt") and kept(fake_git, cwd, "ty-2-review")


def test_reviewer_folder_goes_with_its_verdict(fake_git: FakeRepo, fake_runner: FakeRunner):
    e = Env(repo=fake_git)
    cwd = started(e, "orca", fake_runner, fake_git)
    sweep(e, fake_runner)
    assert kept(fake_git, cwd, "ty-2-review")
    tip = task(e, "TY-2")["reviewer"]["tip"]
    registry.backlog(e.p).update("TY-2", review={"round": 1, "tip": tip, "open": 0, "file": "f", "at": util.now()})
    sweep(e, fake_runner)
    assert not kept(fake_git, cwd, "ty-2-review")


def test_an_older_rounds_folders_go(fake_git: FakeRepo, fake_runner: FakeRunner):
    e = Env(repo=fake_git)
    started(e, "orca", fake_runner, fake_git)
    old = [e.p.state / "agent-folders" / f"ty-2-{role}-aaaaaaaaa" for role in ("hunt", "review")]
    for folder in old:
        fake_git.add_detached(folder, fake_git.head("main"))
    sweep(e, fake_runner)
    assert not [f for f in old if f in fake_git.worktrees()]


def test_a_mechanic_folder_goes_with_its_diagnosis(fake_git: FakeRepo, fake_runner: FakeRunner):
    e = Env(repo=fake_git)
    cwd = started(e, "orca", fake_runner, fake_git)
    sweep(e, fake_runner)
    assert kept(fake_git, cwd, "ty-3-mechanic")
    registry.backlog(e.p).update("TY-3", diagnosis={"at": util.now(), "file": "f"})
    sweep(e, fake_runner)
    assert not kept(fake_git, cwd, "ty-3-mechanic")


@pytest.mark.parametrize("status", ["done", "canceled", "duplicate"])
@pytest.mark.parametrize("tid", ["TY-1", "TY-2"])
def test_every_close_removes_all_its_folders(fake_git: FakeRepo, fake_runner: FakeRunner, tid: str, status: str):
    e = Env(repo=fake_git)
    cwd = started(e, "orca", fake_runner, fake_git)
    mine = [f for name, f in cwd.items() if name.startswith(tid.lower() + "-")]
    registry.backlog(e.p).set_status(tid, status, "s")
    sweep(e, fake_runner)
    assert mine and not [f for f in mine if f in fake_git.worktrees()]
    assert kept(fake_git, cwd, "ty-3-mechanic")  # another task's stays


def test_the_responsible_worktree_is_untouched(fake_git: FakeRepo, fake_runner: FakeRunner):
    e = Env(repo=fake_git)
    cwd = started(e, "orca", fake_runner, fake_git)
    registry.backlog(e.p).set_status("TY-2", "done", "s")
    sweep(e, fake_runner)
    assert not kept(fake_git, cwd, "ty-2-hunt") and cwd["ty-2"] in fake_git.worktrees()


def test_a_failed_removal_is_one_line_and_tried_again(fake_git: FakeRepo, fake_runner: FakeRunner):
    e = Env(repo=fake_git)
    cwd = started(e, "orca", fake_runner, fake_git)
    registry.backlog(e.p).set_status("TY-1", "done", "s")
    registry.backlog(e.p).set_status("TY-2", "done", "s")
    fake_runner.fail_remove.add(cwd["ty-1-planning"])
    sweep(e, fake_runner)
    lines = util.read_jsonl(e.p.state / "errors.jsonl")
    assert [x["step"] for x in lines] == ["folders"] and cwd["ty-1-planning"].name in lines[0]["error"]
    assert kept(fake_git, cwd, "ty-1-planning") and not kept(fake_git, cwd, "ty-2-hunt") and not kept(fake_git, cwd, "ty-2-review")
    fake_runner.fail_remove.clear()
    sweep(e, fake_runner)
    assert not kept(fake_git, cwd, "ty-1-planning") and len(util.read_jsonl(e.p.state / "errors.jsonl")) == 1


def test_a_removal_that_leaves_the_folder_is_a_line_in_errors(fake_git: FakeRepo, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch):
    e = Env(repo=fake_git)
    cwd = started(e, "orca", fake_runner, fake_git)
    registry.backlog(e.p).set_status("TY-1", "done", "s")
    folder = cwd["ty-1-planning"]
    folder.mkdir(parents=True, exist_ok=True)

    def refuses(path: Path | str) -> None:  # git refuses and the folder stays
        return None

    monkeypatch.setattr(fake_git, "remove_worktree", refuses)
    sweep(e, fake_runner)
    [line] = util.read_jsonl(e.p.state / "errors.jsonl")
    assert line["step"] == "folders" and folder.name in line["error"]


def test_no_sessions_listed_removes_nothing(fake_git: FakeRepo, fake_runner: FakeRunner):
    e = Env(repo=fake_git)
    cwd = started(e, "orca", fake_runner, fake_git)
    registry.backlog(e.p).set_status("TY-1", "done", "s")
    folders.run(e.p, NO_ROWS)
    assert kept(fake_git, cwd, "ty-1-planning")


def test_under_claude_the_step_does_nothing(fake_git: FakeRepo, fake_runner: FakeRunner):
    e = Env(repo=fake_git)
    started(e, "claude", fake_runner, fake_git)
    registry.backlog(e.p).set_status("TY-1", "done", "s")
    sweep(e, fake_runner)
    assert not fake_runner.removed


def test_the_owner_of_a_folder_is_read_from_its_name():
    assert owner_of(Path("fn-326-planning-59a6d2eb4")) == ("fn-326", "planning")
    assert owner_of(Path("fn-140-5e0f64ae7")) == ("fn-140", "parent")
    assert owner_of(Path("fn-316-hunt-38e61805c")) == ("fn-316", "hunt")
    assert owner_of(Path("notes")) is None
