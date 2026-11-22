"""The run ledger (FN-393): every agent session the loop starts is a run of its task, with a kind, an attempt, when it
started and ended, and how it ended (done, failed, canceled). Expected values come from the task's criteria."""

import json
from typing import Any

import pytest
from fakerepo import FakeRepo
from helpers import Env, fm
from test_misled import run_as
from test_relaunch import KINDS, mechanic_, planning, responsible
from test_watch import listing, row, task

from foreman.core.oversight import diagnosis
from foreman.core.tasks import runs
from foreman.providers import registry
from foreman.providers.claude.rows import ClaudeRows
from foreman.services.agents import launch
from foreman.services.delivery import merge
from foreman.steps import mechanic


def of(e: Env, tid: str, kind: str) -> list[runs.Run]:
    return [r for r in runs.load(e.p) if r.task == tid and r.kind == kind]


def read(e: Env, *args: str) -> Any:
    return json.loads(fm(e.home, *args))


def test_start_then_end(env: Env):
    r = runs.start(env.p, "TY-1", "responsible", runs.Session("s1", "h1", "opus", "high"))
    (got,) = runs.load(env.p)
    assert got == r
    assert (got.task, got.kind, got.attempt, got.session, got.handle, got.model, got.effort) == ("TY-1", "responsible", 1, "s1", "h1", "opus", "high")
    assert got.active and got.started and got.ended is None and got.outcome is None
    runs.end(env.p, "TY-1", ("responsible",), "failed", "why")
    (got,) = runs.load(env.p)
    assert (got.outcome, got.failure_reason, got.active) == ("failed", "why", False)
    assert got.ended
    runs.end(env.p, "TY-1", ("responsible",), "done")  # ending twice keeps the first outcome
    assert runs.load(env.p) == [got]
    runs.start(env.p, "TY-2", "review", runs.Session("s2", "h2", "opus", "high"))
    runs.end(env.p, "TY-2", ("review",), "done", "ignored")
    assert (runs.load(env.p)[1].outcome, runs.load(env.p)[1].failure_reason) == ("done", "")


def test_a_second_start_of_the_kind_supersedes_the_first(env: Env):
    first = runs.start(env.p, "TY-1", "responsible", runs.Session("s1", "h1", "m", "e"))
    second = runs.start(env.p, "TY-1", "responsible", runs.Session("s1", "h1", "m", "e"))
    runs.start(env.p, "TY-1", "hunter", runs.Session("s2", "h2", "m", "e"))
    runs.start(env.p, "TY-2", "responsible", runs.Session("s3", "h3", "m", "e"))
    a, b, hunter, other = runs.load(env.p)
    assert first.id != second.id
    assert (a.id, a.attempt, a.outcome) == (first.id, 1, "canceled")
    assert (b.id, b.attempt, b.active) == (second.id, 2, True)
    assert (hunter.attempt, hunter.active, other.attempt, other.active) == (1, True, 1, True)


@pytest.mark.usefixtures("owner")
@pytest.mark.parametrize(
    ("helper", "kind"), [("planning", "planning"), ("responsible", "responsible"), ("mechanic", "mechanic"), ("parent", "parent"), ("chain parent", "chain")]
)
def test_each_launch_appends_a_run(helper: str, kind: str, fake_git: FakeRepo):
    e = Env(repo=fake_git)
    tid, role = KINDS[helper](e)
    launch.fill_sessions(e.p, registry.rows(e.p))
    (r,) = runs.load(e.p)
    assert (r.task, r.kind, r.attempt, r.session) == (tid, kind, 1, e.task(tid)[role]["session"])
    assert r.handle and r.started and r.active


def test_a_session_found_later_is_attached_to_its_run(env: Env):
    tid = registry.backlog(env.p).add("clear", status="ready", criteria="y")
    registry.backlog(env.p).update(tid, agent={"short": "aa", "kind": "responsible"}, reviewer={"short": "rr"})
    runs.start(env.p, tid, "responsible", runs.Session("", "aa", "m", "e"))
    runs.start(env.p, tid, "review", runs.Session("", "rr", "m", "e"))
    launch.fill_sessions(env.p, ClaudeRows({"aa-full": {"id": "aa"}, "rr-full": {"id": "rr"}}))
    assert [r.session for r in runs.load(env.p)] == ["aa-full", "rr-full"]


@pytest.mark.usefixtures("owner")
class TestOutcomes:
    @pytest.fixture(autouse=True)
    def set_up(self, fake_git: FakeRepo) -> None:
        self.e = Env(repo=fake_git)

    def test_ready_ends_the_planning_run_done(self):
        tid, _ = planning(self.e)
        run_as(self.e, tid, "ty-1-planning", "ready", "--no-plan", "trivial")
        assert [r.outcome for r in of(self.e, tid, "planning")] == ["done"]

    def test_done_ends_the_responsible_run_done_and_a_reject_starts_attempt_2_on_the_same_session(self):
        tid, _ = responsible(self.e)
        run_as(self.e, tid, "ty-1", "done", "--summary", "s")
        assert [r.outcome for r in of(self.e, tid, "responsible")] == ["done"]
        fm(self.e.home, "reject", tid, "fix the name")
        first, again = of(self.e, tid, "responsible")
        assert (again.attempt, again.active, again.session) == (2, True, first.session)

    def test_verdict_ends_the_review_run_done(self):
        tid, _ = responsible(self.e)
        launch.launch_reviewer(self.e.p, self.e.task(tid), "abc123", "hunter")
        launch.launch_reviewer(self.e.p, self.e.task(tid), "abc123")
        run_as(self.e, tid, "ty-1-review", "verdict", "--open", "0", "--file", "r")
        assert [r.outcome for r in of(self.e, tid, "review")] == ["done"]

    def test_diagnose_ends_the_mechanic_run_done(self):
        tid, _ = mechanic_(self.e)
        f = self.e.tmp / "diagnosis.md"
        f.write_text("\n".join(f"## {s}\nx" for s in diagnosis.SECTIONS))
        run_as(self.e, tid, "ty-1-mechanic", "diagnose", "--file", str(f))
        assert [r.outcome for r in of(self.e, tid, "mechanic")] == ["done"]

    def test_a_mechanic_without_a_diagnosis_fails_with_the_reason(self):
        tid, _ = mechanic_(self.e)
        mechanic.note_missing(self.e.p, tid, "ran longer than 30 minutes")
        assert [(r.outcome, r.failure_reason) for r in of(self.e, tid, "mechanic")] == [("failed", "ran longer than 30 minutes")]

    def test_a_cancel_ends_its_active_runs_canceled(self):
        tid, _ = responsible(self.e)
        merge.close(self.e.p, tid, "canceled", "no")
        assert [r.outcome for r in of(self.e, tid, "responsible")] == ["canceled"]


def test_old_state_reads_as_no_runs(fake_git: FakeRepo):
    env = Env(repo=fake_git)
    task(env, status="in progress", agent={"short": "s1", "session": "s1", "kind": "responsible", "model": "m", "effort": "e"})
    listing(env, row("s1"))  # its session is alive, but it started before the ledger
    assert not (env.p.state / "ledger.jsonl").exists()
    assert read(env, "runs", "--json") == []
    assert read(env, "runs", "--json", "--active") == []
    assert read(env, "agents", "--json") == []
