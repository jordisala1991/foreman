"""`foreman watch`: one line each time something starts needing the owner, decided from the backlog, ci.json and the
session listing (never the text of events.jsonl). Assertions are on keys and on ids and text being in the line."""

import contextlib
import json
from collections.abc import Callable
from typing import Any

import pytest
from fakerepo import FakeRepo
from helpers import Env, fm

from foreman import util
from foreman.core.tasks import backlog, questions
from foreman.providers import registry
from foreman.providers.claude.rows import ClaudeRows
from foreman.providers.runner import NO_ROWS
from foreman.steps import watch

CHECK = {"risk": "high", "tests": "pass", "review": "clean", "ci": "", "conflict": False}


class Done(Exception):
    pass


def row(sid: str = "s1", **f: object) -> dict[str, Any]:
    return {"id": sid[:2], "sessionId": sid, "name": "n", "state": "blocked", "status": "idle", "pid": 1, **f}


def listing(e: Env, *rows: dict[str, Any]) -> None:
    (e.bin / "agents.json").write_text(json.dumps(list(rows)))


def task(e: Env, **f: object) -> str:
    tid = backlog.add(e.p, "a task", status="ready", criteria="c")
    backlog.update(e.p, tid, **f)
    return tid


def in_review(e: Env, check: dict[str, Any] | None = None, **f: object) -> str:
    """In review, its check and verdict for the branch's head and main as they are now."""
    assert e.fake
    head, main = e.fake.commit("b"), e.fake.find("main")
    c = {**CHECK, "head": head, "main": main, **(check or {})}
    return task(e, status="in review", branch="b", check=c, review={"round": 1, "tip": head, "open": 0}, **f)


def parent(e: Env, sid: str = "s1") -> str:
    return task(e, status="backlog", agent={"kind": "parent", "session": sid})


def blocked(e: Env, tid: str, reason: str) -> Callable[[], None]:
    def change() -> None:
        backlog.update(e.p, tid, blocked=reason)

    return change


def drive(e: Env, monkeypatch: pytest.MonkeyPatch, *changes: Callable[[], object], once: bool = False) -> None:
    """Runs watch with the listing readable; each poll first applies the next change; after the last it stops."""
    rest = list(changes)

    def pause() -> None:
        if not rest:
            raise Done
        rest.pop(0)()

    monkeypatch.setattr(watch, "pause", pause)
    with contextlib.suppress(Done):
        watch.run(e.p, once, registry.runner(e.p).rows() or NO_ROWS)


def printed(capsys: pytest.CaptureFixture[str]) -> list[str]:
    return capsys.readouterr().out.splitlines()


def test_an_open_question_is_a_need(fake_git: FakeRepo):
    e = Env(repo=fake_git)
    tid = task(e)
    qid = questions.ask(e.p, tid, "agent", "which   way?")
    found = watch.needs(e.p, NO_ROWS)
    assert list(found) == [f"q:{qid}"]
    assert tid in found[f"q:{qid}"] and qid in found[f"q:{qid}"] and "which way?" in found[f"q:{qid}"]


def test_a_task_waits_for_accept_only_when_after_check_says_owner(fake_git: FakeRepo):
    e = Env(repo=fake_git)
    tid = in_review(e)
    assert list(watch.needs(e.p, NO_ROWS)) == [f"accept:{tid}"]
    assert tid in watch.needs(e.p, NO_ROWS)[f"accept:{tid}"]
    backlog.update(e.p, tid, accepted={"by": "owner"})
    assert watch.needs(e.p, NO_ROWS) == {}
    backlog.update(e.p, tid, accepted=None, check={**CHECK, "head": "other"})  # a check for another tip than the verdict's
    assert watch.needs(e.p, NO_ROWS) == {}
    head = fake_git.find("b")
    c = {**CHECK, "head": head, "main": fake_git.find("main"), "review": "open 1"}
    backlog.update(e.p, tid, check=c, review={"round": 2, "tip": head, "open": 1})  # waiting after the rounds
    assert list(watch.needs(e.p, NO_ROWS)) == [f"accept:{tid}"]


def test_accept_follows_the_stored_ci_result(fake_git: FakeRepo):
    e = Env(repo=fake_git, autonomy="low")
    tid = in_review(e, check={"risk": "low", "ci": "pass"})
    assert watch.needs(e.p, NO_ROWS) == {}  # the loop merges it
    c = {**CHECK, "risk": "low", "head": fake_git.find("b"), "main": fake_git.find("main"), "ci": "fail"}
    backlog.update(e.p, tid, check=c, failure_streak=2)  # failures went back to its agent twice: the owner decides
    assert list(watch.needs(e.p, NO_ROWS)) == [f"accept:{tid}"]


def test_a_pr_without_checks_waits_for_the_owner_but_a_local_branch_is_merged(fake_git: FakeRepo):
    e = Env(repo=fake_git, autonomy="low")
    tid = in_review(e, check={"risk": "low", "ci": "none"}, pr={"number": 1})  # the provider has CI and the PR has no checks: the loop leaves it to the owner
    assert list(watch.needs(e.p, NO_ROWS)) == [f"accept:{tid}"]
    backlog.update(e.p, tid, check={**CHECK, "risk": "low", "head": fake_git.find("b"), "main": fake_git.find("main")})  # local: no CI, the autonomy merges it
    assert watch.needs(e.p, NO_ROWS) == {}


def test_a_task_the_autonomy_merges_is_not_a_need(fake_git: FakeRepo):
    e = Env(repo=fake_git, autonomy="low")
    in_review(e, check={"risk": "low"})
    assert watch.needs(e.p, NO_ROWS) == {}


@pytest.mark.parametrize("reason", ["x", "could not launch its agent: boom"])
def test_a_blocked_task_is_a_need_with_its_reason(fake_git: FakeRepo, reason: str):
    e = Env(repo=fake_git)
    tid = task(e, blocked=reason)
    found = watch.needs(e.p, NO_ROWS)
    assert list(found) == [f"blocked:{tid}"]
    assert tid in found[f"blocked:{tid}"] and reason in found[f"blocked:{tid}"]
    backlog.update(e.p, tid, status="done")
    assert watch.needs(e.p, NO_ROWS) == {}


def test_main_tests_failing_is_a_need(fake_git: FakeRepo):
    e = Env(repo=fake_git)
    (e.p.state).mkdir(parents=True, exist_ok=True)
    util.write_json(e.p.state / "ci.json", {"result": "fail", "since": "t1"})
    assert list(watch.needs(e.p, NO_ROWS)) == ["ci:t1"]
    util.write_json(e.p.state / "ci.json", {"result": "pass", "since": "t1"})
    assert watch.needs(e.p, NO_ROWS) == {}


def test_a_parent_agent_idle_in_its_chat_is_a_need(fake_git: FakeRepo):
    e = Env(repo=fake_git)
    tid = parent(e)
    idle = ClaudeRows({"s1": row()})
    assert list(watch.needs(e.p, idle)) == [f"parent:{tid}"]
    assert tid in watch.needs(e.p, idle)[f"parent:{tid}"]
    assert watch.needs(e.p, ClaudeRows({"s1": row(state="working", status="busy")})) == {}
    assert watch.needs(e.p, ClaudeRows({"s1": row(pid=None)})) == {}  # stopped
    qid = questions.ask(e.p, tid, "agent", "q?")
    assert list(watch.needs(e.p, idle)) == [f"q:{qid}"]


def test_questions_blocked_and_parents_at_start_are_not_printed(fake_git: FakeRepo, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    e = Env(repo=fake_git)
    task(e, blocked="x")
    questions.ask(e.p, task(e), "agent", "q?")
    parent(e)
    listing(e, row())
    drive(e, monkeypatch, lambda: None, lambda: None)
    assert printed(capsys) == []


def redo(e: Env, tid: str, fake_git: FakeRepo, **check: object) -> Callable[[], None]:
    """The check done again because main moved."""

    def change() -> None:
        fake_git.commit("main")
        backlog.update(e.p, tid, check={**CHECK, **check, "head": fake_git.find("b"), "main": fake_git.find("main")})

    return change


def test_an_accept_waiting_at_start_is_printed_once(fake_git: FakeRepo, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    e = Env(repo=fake_git)
    tid = in_review(e)
    listing(e)
    drive(e, monkeypatch, lambda: None, lambda: None)
    out = printed(capsys)
    assert len(out) == 1 and tid in out[0] and "waits for the owner's accept" in out[0]


def test_a_redone_check_does_not_print_the_accept_again(fake_git: FakeRepo, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    e = Env(repo=fake_git)
    tid = in_review(e)
    listing(e)
    drive(e, monkeypatch, redo(e, tid, fake_git), lambda: None, redo(e, tid, fake_git), lambda: None)  # watch started after the first check
    assert len(printed(capsys)) == 1


def test_an_accept_checked_after_the_start_is_printed_once_despite_redos(
    fake_git: FakeRepo, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    e = Env(repo=fake_git)
    head = fake_git.commit("b")
    tid = task(e, status="in review", branch="b", review={"round": 1, "tip": head, "open": 0})  # reviewed, not checked yet
    listing(e)
    first = {**CHECK, "head": head, "main": fake_git.find("main")}
    drive(e, monkeypatch, lambda: backlog.update(e.p, tid, check=first), redo(e, tid, fake_git), redo(e, tid, fake_git), lambda: None)
    assert [tid in line for line in printed(capsys)] == [True]


def test_fn_216_replayed_prints_its_line(fake_git: FakeRepo, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    e = Env(repo=fake_git)
    tid = in_review(e, check={"tests": "pass", "ci": "pass"}, pr={"number": 264, "draft": False})  # checked 09:03:30, watch starts 09:05
    backlog.update(e.p, tid, review={"round": 2, "tip": fake_git.find("b"), "open": 0})
    listing(e)
    drive(e, monkeypatch, redo(e, tid, fake_git, ci="pass"), redo(e, tid, fake_git, ci="pass"), lambda: None)  # 09:23 and 09:34: main moved
    out = printed(capsys)
    assert len(out) == 1 and tid in out[0]


def test_once_waits_for_a_new_need_after_the_start_accept(fake_git: FakeRepo, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    e = Env(repo=fake_git)
    tid = in_review(e)
    other = task(e)
    listing(e)
    drive(e, monkeypatch, blocked(e, other, "x"), once=True)  # a second pause would raise Done
    out = printed(capsys)
    assert len(out) == 2 and tid in out[0] and other in out[1]


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
    listing(e)  # no session at all: a readable listing
    monkeypatch.setattr(watch, "pause", lambda: backlog.update(e.p, tid, blocked="x"))
    out = fm(e.home, "watch", "--once")
    assert len(out.splitlines()) == 1 and tid in out


def test_an_unreadable_listing_does_not_reprint_parents(fake_git: FakeRepo, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    e = Env(repo=fake_git)
    parent(e)
    listing(e, row())
    drive(e, monkeypatch, lambda: (e.bin / "agents.json").write_text("not json"), lambda: listing(e, row()))
    assert printed(capsys) == []


def test_a_poll_that_cannot_read_the_backlog_does_not_end_watch(fake_git: FakeRepo, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    e = Env(repo=fake_git)
    tid = task(e)
    good = e.p.backlog_file.read_text()
    listing(e)

    def corrupt() -> None:
        e.p.backlog_file.write_text('{"schema_version": 1}')

    def restore() -> None:
        e.p.backlog_file.write_text(good)
        backlog.update(e.p, tid, blocked="x")

    drive(e, monkeypatch, corrupt, restore)
    assert [tid in line for line in printed(capsys)] == [True]


def test_events_text_does_not_change_what_is_printed(fake_git: FakeRepo):
    e = Env(repo=fake_git)
    tid = task(e)
    for text in ("asks the owner (question): x", "blocked: x", "launch failed: x", "ready for review", "checked: tests fail"):
        backlog.event(e.p, tid, text)
    assert watch.needs(e.p, NO_ROWS) == {}
    backlog.update(e.p, tid, blocked="x")
    before = watch.needs(e.p, NO_ROWS)
    events = e.p.state / "events.jsonl"
    events.write_text(events.read_text().replace("blocked: x", "reworded").replace("asks the owner", "pregunta"))
    assert watch.needs(e.p, NO_ROWS) == before


def test_watch_refuses_without_the_listing(fake_git: FakeRepo):
    e = Env(repo=fake_git)
    (e.bin / "agents.json").write_text("not json")
    with pytest.raises(SystemExit, match="needs the session listing"):
        fm(e.home, "watch")
