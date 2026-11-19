"""`foreman watch`: one line each time something starts needing the owner, decided from the backlog, ci.json and the
session listing (never the text of events.jsonl). Assertions are on keys and on ids and text being in the line."""

import contextlib
import json
from collections.abc import Callable

import pytest
from fakerepo import FakeRepo
from helpers import Env, fm

from foreman import util
from foreman.core.tasks import backlog, questions
from foreman.steps import watch

CHECK = {"head": "h1", "risk": "high", "tests": "pass", "review": "clean", "ci": "none", "conflict": False}
REVIEW = {"round": 1, "tip": "h1", "open": 0}


class Done(Exception):
    pass


def row(sid: str = "s1", **f: object) -> dict:
    return {"id": sid[:2], "sessionId": sid, "name": "n", "state": "blocked", "status": "idle", "pid": 1, **f}


def listing(e: Env, *rows: dict) -> None:
    (e.bin / "agents.json").write_text(json.dumps(list(rows)))


def task(e: Env, **f: object) -> str:
    tid = backlog.add(e.p, "a task", status="ready", criteria="c")
    backlog.update(e.p, tid, **f)
    return tid


def in_review(e: Env, **f: object) -> str:
    return task(e, status="in review", check=dict(CHECK), review=dict(REVIEW), **f)


def parent(e: Env, sid: str = "s1") -> str:
    return task(e, status="backlog", agent={"kind": "parent", "session": sid})


def blocked(e: Env, tid: str, reason: str) -> Callable[[], None]:
    def change() -> None:
        backlog.update(e.p, tid, blocked=reason)

    return change


def drive(e: Env, monkeypatch: pytest.MonkeyPatch, *changes: Callable[[], None], once: bool = False) -> None:
    """Runs watch with the listing readable; each poll first applies the next change; after the last it stops."""
    rest = list(changes)

    def pause() -> None:
        if not rest:
            raise Done
        rest.pop(0)()

    monkeypatch.setattr(watch, "pause", pause)
    with contextlib.suppress(Done):
        watch.run(e.p, once)


def printed(capsys: pytest.CaptureFixture[str]) -> list[str]:
    return capsys.readouterr().out.splitlines()


def test_an_open_question_is_a_need(fake_git: FakeRepo):
    e = Env(repo=fake_git)
    tid = task(e)
    qid = questions.ask(e.p, tid, "agent", "which   way?")
    found = watch.needs(e.p, {})
    assert list(found) == [f"q:{qid}"]
    assert tid in found[f"q:{qid}"] and qid in found[f"q:{qid}"] and "which way?" in found[f"q:{qid}"]


def test_a_task_waits_for_accept_only_when_after_check_says_owner(fake_git: FakeRepo):
    e = Env(repo=fake_git)
    tid = in_review(e)
    assert list(watch.needs(e.p, {})) == [f"accept:{tid}"]
    assert tid in watch.needs(e.p, {})[f"accept:{tid}"]
    backlog.update(e.p, tid, accepted={"by": "owner"})
    assert watch.needs(e.p, {}) == {}
    backlog.update(e.p, tid, accepted=None, check={**CHECK, "head": "h2"})  # a check for another tip than the verdict's
    assert watch.needs(e.p, {}) == {}
    backlog.update(e.p, tid, check={**CHECK, "review": "open 1"}, review={"round": 2, "tip": "h1", "open": 1})  # waiting after the rounds
    assert list(watch.needs(e.p, {})) == [f"accept:{tid}"]


def test_a_task_the_autonomy_merges_is_not_a_need(fake_git: FakeRepo):
    e = Env(repo=fake_git, autonomy="low")
    task(e, status="in review", check={**CHECK, "risk": "low"}, review=dict(REVIEW))
    assert watch.needs(e.p, {}) == {}


@pytest.mark.parametrize("reason", ["x", "could not launch its agent: boom"])
def test_a_blocked_task_is_a_need_with_its_reason(fake_git: FakeRepo, reason: str):
    e = Env(repo=fake_git)
    tid = task(e, blocked=reason)
    found = watch.needs(e.p, {})
    assert list(found) == [f"blocked:{tid}"]
    assert tid in found[f"blocked:{tid}"] and reason in found[f"blocked:{tid}"]
    backlog.update(e.p, tid, status="done")
    assert watch.needs(e.p, {}) == {}


def test_main_tests_failing_is_a_need(fake_git: FakeRepo):
    e = Env(repo=fake_git)
    (e.p.state).mkdir(parents=True, exist_ok=True)
    util.write_json(e.p.state / "ci.json", {"result": "fail", "since": "t1"})
    assert list(watch.needs(e.p, {})) == ["ci:t1"]
    util.write_json(e.p.state / "ci.json", {"result": "pass", "since": "t1"})
    assert watch.needs(e.p, {}) == {}


def test_a_parent_agent_idle_in_its_chat_is_a_need(fake_git: FakeRepo):
    e = Env(repo=fake_git)
    tid = parent(e)
    idle = {"s1": row()}
    assert list(watch.needs(e.p, idle)) == [f"parent:{tid}"]
    assert tid in watch.needs(e.p, idle)[f"parent:{tid}"]
    assert watch.needs(e.p, {"s1": row(state="working", status="busy")}) == {}
    assert watch.needs(e.p, {"s1": row(pid=None)}) == {}  # stopped
    qid = questions.ask(e.p, tid, "agent", "q?")
    assert list(watch.needs(e.p, idle)) == [f"q:{qid}"]


def test_what_needed_the_owner_at_start_is_not_printed(fake_git: FakeRepo, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    e = Env(repo=fake_git)
    task(e, blocked="x")
    questions.ask(e.p, task(e), "agent", "q?")
    parent(e)
    listing(e, row())
    drive(e, monkeypatch, lambda: None, lambda: None)
    assert printed(capsys) == []


def test_each_thing_is_printed_once_when_it_starts(fake_git: FakeRepo, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    e = Env(repo=fake_git)
    tid = task(e)
    listing(e, row("s9"))
    drive(e, monkeypatch, blocked(e, tid, "x"), lambda: None, lambda: None)
    assert [tid in line for line in printed(capsys)] == [True]
    drive(e, monkeypatch, blocked(e, tid, ""), blocked(e, tid, "x"))
    assert len(printed(capsys)) == 1


def test_progress_merges_and_unblocked_print_nothing(fake_git: FakeRepo, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    e = Env(repo=fake_git)
    tid = task(e, blocked="x")
    parent_id = parent(e)
    listing(e, row(state="working", status="busy"))
    drive(
        e,
        monkeypatch,
        lambda: backlog.update(e.p, tid, blocked="", status="in progress"),
        lambda: backlog.update(e.p, tid, status="done"),
        lambda: listing(e, row(state="working", status="busy", pid=2)),
        lambda: backlog.update(e.p, parent_id, notified=["TY-9"]),
    )
    assert printed(capsys) == []


def test_once_stops_after_the_first_line(fake_git: FakeRepo, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    e = Env(repo=fake_git)
    a, b = task(e), task(e)
    listing(e, row("s9"))

    def both() -> None:
        backlog.update(e.p, a, blocked="x")
        backlog.update(e.p, b, blocked="y")

    drive(e, monkeypatch, both, once=True)  # Done would be raised by a second pause
    assert len(printed(capsys)) == 1


def test_cli_watch_once_exits_after_its_first_line(fake_git: FakeRepo, monkeypatch: pytest.MonkeyPatch):
    e = Env(repo=fake_git)
    tid = task(e)
    listing(e, row("s9"))
    monkeypatch.setattr(watch, "pause", lambda: backlog.update(e.p, tid, blocked="x"))
    out = fm(e.home, "watch", "--once")
    assert len(out.splitlines()) == 1 and tid in out


def test_an_unreadable_listing_does_not_reprint_parents(fake_git: FakeRepo, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    e = Env(repo=fake_git)
    parent(e)
    listing(e, row())
    drive(e, monkeypatch, (e.bin / "agents.json").unlink, lambda: listing(e, row()))
    assert printed(capsys) == []


def test_events_text_does_not_change_what_is_printed(fake_git: FakeRepo):
    e = Env(repo=fake_git)
    tid = task(e)
    for text in ("asks the owner (question): x", "blocked: x", "launch failed: x", "ready for review", "checked: tests fail"):
        backlog.event(e.p, tid, text)
    assert watch.needs(e.p, {}) == {}
    backlog.update(e.p, tid, blocked="x")
    before = watch.needs(e.p, {})
    events = e.p.state / "events.jsonl"
    events.write_text(events.read_text().replace("blocked: x", "reworded").replace("asks the owner", "pregunta"))
    assert watch.needs(e.p, {}) == before


def test_watch_refuses_without_the_listing(fake_git: FakeRepo):
    e = Env(repo=fake_git)
    (e.bin / "agents.json").write_text("[]")
    with pytest.raises(SystemExit, match="needs the session listing"):
        fm(e.home, "watch")
