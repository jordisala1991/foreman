"""The Orca adapter (foreman/providers/orca/, `[agents] runner = "orca"`), against a fake `orca` (fakeorca.py) whose
answers have the shapes a real headless Orca gave: launch, send, state, stop, Orca down or restarted, repos and titles."""

import dataclasses
import json
from pathlib import Path

import pytest
from fakeorca import FakeOrca
from fakerepo import FakeRepo
from helpers import Env, fm

from foreman import util
from foreman.core.tasks import backlog, runs
from foreman.providers import registry
from foreman.providers.orca import settings as orca_settings
from foreman.providers.orca.sessions import Orca
from foreman.providers.runner import Agent, RunnerError, SessionRef, State, ref_of, settings
from foreman.services.agents import launch, relaunch, running
from foreman.services.agents.launch import launch_parent
from foreman.steps import mechanic, parents, recover, talk


class Setup:
    def __init__(self, e: Env, orca: FakeOrca) -> None:
        self.e, self.orca, self.p = e, orca, e.p
        self.wt = e.tmp / "wt" / "ty-1"
        self.wt.mkdir(parents=True)

    def start(self, cwd: Path | None = None, name: str = "ty-1", title: str = "TY-1 responsible · x") -> SessionRef:
        return Orca(self.p).start(Agent("TY-1", name, "sonnet", "low", title), "the brief", cwd or self.wt)

    def events(self) -> list[str]:
        return [ev["text"] for ev in util.read_jsonl(self.p.state / "events.jsonl")]


@pytest.fixture
def s(fake_git: FakeRepo, monkeypatch: pytest.MonkeyPatch) -> Setup:
    e = Env(repo=fake_git)
    e.p.cfg["agents"]["runner"] = "orca"
    orca = FakeOrca(e.tmp)
    monkeypatch.setenv("ORCA_BIN", str(orca.bin))
    return Setup(e, orca)


def test_the_registry_hands_out_orca(s: Setup):
    assert isinstance(registry.runner(s.p), Orca)


def test_launch_carries_the_guard_and_env_in_the_folder(s: Setup):
    s.start()
    carried = json.loads((s.wt / ".claude" / "settings.local.json").read_text())
    want = settings(s.p, "TY-1", "ty-1")
    assert carried["env"] == want["env"] and carried["enabledPlugins"] == want["enabledPlugins"]
    assert carried["hooks"]["PreToolUse"] == want["hooks"]["PreToolUse"]
    a = s.orca.call("orchestration worker-start")
    handle = s.orca.call("terminal create")  # the coordinator tab
    assert a[a.index("--from") + 1] == s.orca.load()["terminals"][0] and "--title" in handle
    assert a[a.index("--worktree") + 1] == f"path:{s.wt}" and a[a.index("--agent") + 1] == "claude" and "--keep-open" in a
    assert (a[a.index("--model") + 1], a[a.index("--effort") + 1]) == ("sonnet", "low")
    assert Path(a[a.index("--spec-file") + 1]).read_text() == "the brief"


def test_the_ref_holds_the_hooks_session_id_and_the_dispatch(s: Setup):
    ref = s.start()
    [(dispatch, w)] = s.orca.workers().items()
    assert ref.session == w["session"] and ref.short == w["session"][:8]
    assert (ref.dispatch, ref.coordinator) == (dispatch, s.orca.load()["terminals"][0])
    assert ref.pane == f"{w['chat']}:assignee"
    assert ref_of(ref.fields() | {"terminal": ref.terminal, "pane": ref.pane}) == ref


def test_a_worker_that_is_not_a_chat_is_stopped_and_refused(s: Setup):
    s.orca.terminal_mode()
    with pytest.raises(RunnerError, match="not a chat"):
        s.start()
    [dispatch] = s.orca.workers()
    assert s.orca.call("orchestration worker-stop")[-1] == dispatch
    assert s.orca.verbs().count("orchestration worker-start") == 1


def test_never_a_settings_file_in_a_main_checkout(s: Setup):
    with pytest.raises(RunnerError, match="main checkout"):
        s.start(cwd=s.p.repo)
    assert not (s.p.repo / ".claude").exists()


def test_a_send_to_a_live_chat_keeps_the_dispatch(s: Setup):
    ref = s.start()
    assert Orca(s.p).send(ref, "hello", s.wt) == ref
    a = s.orca.call("orchestration send")
    assert (a[a.index("--from") + 1], a[a.index("--to") + 1], a[a.index("--body") + 1]) == (ref.coordinator, f"dispatch:{ref.dispatch}", "hello")


def on_a_task(s: Setup) -> tuple[str, SessionRef]:
    ref = s.start()
    tid = registry.backlog(s.p).add(status="ready", criteria="y", title="x")
    with registry.backlog(s.p).edit() as d:
        it = backlog.get(d, tid)
        it["status"] = "in progress"
        it["agent"] = {"name": "ty-1", **ref.fields(), "kind": "responsible", "started": util.now(), "cwd": str(s.wt)}
        launch.record_session(it, ref, "responsible")
    return tid, ref


def test_a_send_after_a_restart_keeps_the_new_dispatch(s: Setup):
    tid, ref = on_a_task(s)
    s.orca.restart()
    assert running.send(s.p, backlog.get(registry.backlog(s.p).load(), tid), "again")
    i = backlog.get(registry.backlog(s.p).load(), tid)
    new = i["agent"]["dispatch"]
    assert new != ref.dispatch and s.orca.workers()[new]["live"]
    assert i["session_tabs"][ref.session][2] == new and i["agent"]["session"] == ref.session
    assert running.send(s.p, i, "and again")  # a live chat now: the dispatch stays
    assert backlog.get(registry.backlog(s.p).load(), tid)["agent"]["dispatch"] == new


def test_handle_is_the_orca_session_id(s: Setup):
    pane = "structured-agent-session-f8b0cecd-e4ef-4afa-879d-bc2554b9b0e2:63ffab2a-c77d-4a5b-a65c-6e5226a0e917"
    assert Orca(s.p).handle(SessionRef("a", "sid", "structworker_x", pane)) == "f8b0cecd-e4ef-4afa-879d-bc2554b9b0e2"
    assert Orca(s.p).handle(SessionRef("a", "sid")) == ""


def test_handle_of_a_live_ref_without_a_kept_pane_is_asked_of_orca(s: Setup):
    ref = s.start()
    assert ref.pane
    bare = dataclasses.replace(ref, pane=None)
    assert Orca(s.p).handle(bare) == Orca(s.p).handle(ref) != ""


@pytest.mark.parametrize(("word", "state"), [("working", State.WORKING), ("done", State.IDLE), ("blocked", State.WAITING), ("waiting", State.WAITING)])
def test_the_state_is_the_row_of_the_workers_pane(s: Setup, word: str, state: State):
    ref = s.start()
    other = s.e.tmp / "wt" / "ty-2"
    other.mkdir()
    s.start(cwd=other, name="ty-2")  # another chat, working: its row is not this one's
    s.orca.set_row(str(ref.dispatch), word)
    rows = Orca(s.p).rows()
    assert rows is not None and rows.state(ref) is state
    fresh = Orca(s.p).rows()
    assert fresh is not None and fresh.state(dataclasses.replace(ref, pane=None)) is state  # no kept pane: worker-show names it
    assert rows.busy(ref) is (state in (State.WORKING, State.WAITING))
    assert rows.state(SessionRef("x", "x")) is State.GONE


def test_a_restart_ends_the_chat_and_its_next_message_resumes_it(s: Setup):
    tid, ref = on_a_task(s)
    s.orca.restart()
    rows = Orca(s.p).rows()
    assert rows is not None and rows.state(ref) is State.ENDED and not rows.busy(ref)
    q = registry.backlog(s.p).ask(tid, "ty-1", "which?")
    registry.backlog(s.p).answer(q, "this one")
    talk.deliver_answers(s.p, rows)
    agent = backlog.get(registry.backlog(s.p).load(), tid)["agent"]
    assert s.orca.workers()[agent["dispatch"]]["live"] and f"answer {q} delivered to ty-1" in s.events()
    after = Orca(s.p).rows()
    assert after is not None and after.state(ref_of(agent)) is State.WORKING


def test_stop_releases_only_a_settled_worker(s: Setup):
    ref = s.start()
    Orca(s.p).stop(ref)
    assert s.orca.verbs()[-1] == "orchestration worker-stop"
    ref2 = s.start()
    s.orca.settle(str(ref2.dispatch))
    Orca(s.p).stop(ref2)
    assert s.orca.verbs()[-2:] == ["orchestration worker-stop", "orchestration worker-release"]


def test_orca_down_is_opened_before_a_launch(s: Setup):
    s.orca.down()
    assert s.start().dispatch
    verbs = s.orca.verbs()
    assert verbs.index("open") < verbs.index("orchestration worker-start")


def test_orca_that_stays_down_postpones_the_launch(s: Setup):
    s.orca.down(opens=False)
    tid = registry.backlog(s.p).add(status="backlog", criteria="y", title="x")
    launch.launch(s.p, backlog.get(registry.backlog(s.p).load(), tid))
    i = backlog.get(registry.backlog(s.p).load(), tid)
    assert i["status"] == "backlog" and not i.get("blocked") and not i.get("agent")
    assert [t for t in s.events() if t.startswith("launch postponed")] == ["launch postponed: Orca is not running and could not be opened"]


def test_rows_never_open_orca(s: Setup):
    s.orca.down()
    assert Orca(s.p).rows() is None
    assert "open" not in s.orca.verbs()


def test_the_repos_of_foreman_toml_are_added_and_shown(fake_git: FakeRepo, monkeypatch: pytest.MonkeyPatch):
    e = Env(repo=fake_git)
    (e.tmp / "api").mkdir()
    (e.tmp / "web").mkdir()
    toml = (e.home / "foreman.toml").read_text().replace(f'repo = "{e.repo}"\n', "")
    (e.home / "foreman.toml").write_text(toml + f'[repos.api]\npath = "{e.tmp / "api"}"\n[repos.web]\npath = "{e.tmp / "web"}"\n')
    p = type(e.p).load(e.home)
    orca = FakeOrca(e.tmp)
    monkeypatch.setenv("ORCA_BIN", str(orca.bin))
    orca.set(repos=[{"path": str(e.tmp / "web"), "externalWorktreeVisibility": "show"}])
    wt = e.tmp / "wt" / "ty-1"
    wt.mkdir(parents=True)
    Orca(p).start(Agent("TY-1", "ty-1", "sonnet", "low", "t"), "b", wt)
    assert [c for c in orca.calls() if c[0] == "repo" and c[1] == "add"] == [["repo", "add", "--path", str(e.tmp / "api")]]
    assert {r["path"]: r["externalWorktreeVisibility"] for r in orca.load()["repos"]} == {str(e.tmp / "web"): "show", str(e.tmp / "api"): "show"}


def test_titles(s: Setup):
    s.start(title="TY-1 responsible · x")
    a = s.orca.call("orchestration worker-start")
    assert a[a.index("--task-title") + 1] == "TY-1 responsible · x"
    assert s.orca.call("worktree set") == ["worktree", "set", "--worktree", f"path:{s.wt}", "--display-name", "TY-1 responsible · x"]
    s.start()  # a second start in the same folder: the worktree keeps its name
    assert s.orca.verbs().count("worktree set") == 1


def test_a_title_orca_refuses_leaves_no_chat_behind(s: Setup):
    s.orca.fail("worktree set")
    with pytest.raises(RunnerError, match="worktree set"):
        s.start()
    assert not [d for d, w in s.orca.workers().items() if w["live"]]


def waiting_agent(s: Setup) -> tuple[str, SessionRef]:
    tid, ref = on_a_task(s)
    s.orca.set_row(str(ref.dispatch), "blocked")
    return tid, ref


def watch(s: Setup) -> None:
    rows = Orca(s.p).rows()
    assert rows is not None
    talk.watch_waiting(s.p, rows)


def test_a_waiting_agent_gets_one_event_until_it_stops_waiting(s: Setup):
    tid, ref = waiting_agent(s)
    watch(s)
    watch(s)
    said = "ty-1 waits for an answer in its Orca chat"
    assert s.events().count(said) == 1
    s.orca.set_row(str(ref.dispatch), "done")
    watch(s)
    assert backlog.get(registry.backlog(s.p).load(), tid)["agent"]["waiting_seen"] is None
    s.orca.set_row(str(ref.dispatch), "waiting")
    watch(s)
    assert s.events().count(said) == 2


def test_the_claude_runner_writes_no_waiting_event(s: Setup):
    waiting_agent(s)
    rows = Orca(s.p).rows()
    s.p.cfg["agents"]["runner"] = "claude"
    assert rows is not None
    talk.watch_waiting(s.p, rows)
    assert not [t for t in s.events() if "waits for an answer" in t]


def test_a_review_waits_for_orca_too(s: Setup):
    s.orca.down(opens=False)
    tid = registry.backlog(s.p).add(status="ready", criteria="y", title="x")
    registry.backlog(s.p).update(tid, worktree=str(s.wt), branch="foreman/ty-1")
    launch.launch_reviewer(s.p, backlog.get(registry.backlog(s.p).load(), tid), s.e.fake_repo().head("main"), "hunter")
    i = backlog.get(registry.backlog(s.p).load(), tid)
    assert not i.get("hunter") and not i.get("blocked")
    assert [t for t in s.events() if t.startswith("launch postponed")] == ["launch postponed: Orca is not running and could not be opened"]


def test_a_mechanic_and_a_parent_wait_for_orca_too(s: Setup):
    s.orca.down(opens=False)
    triage = registry.backlog(s.p).add(status="triage", criteria="y", title="x", source="health:x")
    mechanic.launch(s.p, backlog.get(registry.backlog(s.p).load(), triage))
    parent = registry.backlog(s.p).add(status="backlog", criteria="y", title="p")
    launch_parent(s.p, backlog.get(registry.backlog(s.p).load(), parent))
    d = registry.backlog(s.p).load()
    assert not backlog.get(d, triage).get("diagnosis") and not backlog.get(d, triage).get("mechanic")
    assert not backlog.get(d, parent).get("blocked") and not backlog.get(d, parent).get("agent")
    assert len([t for t in s.events() if t.startswith("launch postponed")]) == 2


def relaunched(s: Setup, settled: bool) -> tuple[SessionRef, SessionRef]:
    ref = s.start()
    s.orca.age(str(ref.dispatch), "/old/bin")
    if settled:
        s.orca.settle(str(ref.dispatch))
    return ref, Orca(s.p).relaunch(ref, Agent("TY-1", "ty-1", "sonnet", "low", "t"), "carry on", s.wt)


def test_a_relaunch_runs_the_chat_on_todays_path(s: Setup):
    ref, new = relaunched(s, settled=False)
    assert s.orca.chat_path(str(new.dispatch)) == settings(s.p, "TY-1", "ty-1")["env"]["PATH"]
    assert new.dispatch != ref.dispatch and new.session == ref.session
    assert s.orca.workers()[str(ref.dispatch)]["live"] is False
    a = s.orca.call("orchestration send")
    assert (a[a.index("--to") + 1], a[a.index("--body") + 1]) == (f"dispatch:{ref.dispatch}", "carry on")


def test_a_relaunch_of_a_settled_chat_restarts_it_too(s: Setup):
    ref, new = relaunched(s, settled=True)
    assert s.orca.chat_path(str(new.dispatch)) == settings(s.p, "TY-1", "ty-1")["env"]["PATH"]
    assert new.dispatch != ref.dispatch and new.session == ref.session


def test_a_relaunch_that_orca_does_not_resume_is_an_error(s: Setup):
    ref = s.start()
    s.orca.set(resumes=False)
    with pytest.raises(RunnerError) as refused:
        Orca(s.p).relaunch(ref, Agent("TY-1", "ty-1", "sonnet", "low", "t"), "carry on", s.wt)
    assert "did not resume" in str(refused.value)


def test_relaunch_names_orca_when_its_listing_is_unreadable(s: Setup):
    tid, _ = on_a_task(s)
    s.orca.down(opens=False)
    with pytest.raises(RunnerError) as refused:
        relaunch.relaunch(s.p, tid)
    assert "orca" in str(refused.value) and "claude" not in str(refused.value)


def test_watch_names_orca_when_its_listing_is_unreadable(s: Setup):
    toml = s.e.home / "foreman.toml"
    toml.write_text(toml.read_text().replace("[agents]\n", '[agents]\nrunner = "orca"\n'))  # the CLI reloads the toml
    s.orca.down(opens=False)
    with pytest.raises(SystemExit) as refused:
        fm(s.e.home, "watch")
    assert "orca" in str(refused.value) and "claude" not in str(refused.value)


def test_remove_folder_asks_orca_to_rm_it(s: Setup):
    s.start()
    sid_file = orca_settings.session_file(s.p, s.wt)
    assert sid_file.exists()
    Orca(s.p).remove_folder(s.wt)
    assert s.orca.call("worktree rm") == ["worktree", "rm", "--worktree", f"path:{s.wt}", "--force"]
    assert not sid_file.exists()


def test_remove_folder_unknown_to_orca_is_fine(s: Setup):
    s.orca.set(rm={f"path:{s.wt}": "selector_not_found"})
    Orca(s.p).remove_folder(s.wt)


def test_remove_folder_other_error_raises(s: Setup):
    s.orca.set(rm={f"path:{s.wt}": "worktree_busy"})
    with pytest.raises(RunnerError, match="worktree_busy"):
        Orca(s.p).remove_folder(s.wt)


def parent_launched(s: Setup) -> str:
    P = registry.backlog(s.p).add("parent", status="backlog", criteria="c")
    registry.backlog(s.p).add("s", parent=P, criteria="c", status="triage")
    parents.run(s.p, registry.rows(s.p))
    return P


def test_an_orca_restart_closes_the_parents_run_as_runner_unavailable(s: Setup):
    P = parent_launched(s)
    recover.run(s.p, registry.rows(s.p))  # the epoch seen before the restart
    s.orca.restart()
    recover.run(s.p, registry.rows(s.p))
    parents.run(s.p, registry.rows(s.p))
    first, second = (r for r in runs.load(s.p) if r.task == P)
    assert (first.outcome, first.failure_reason) == ("failed", "runner_unavailable")
    assert (second.attempt, second.active) == (2, True)
    assert s.orca.verbs().count("orchestration worker-start") == 2
