"""An issue as fm's task (core/tasks/issues.py, pure): the body's sections, the Status, the dates, the ids."""

import time
from typing import Any

import pytest

from foreman.core.tasks import backlog, board
from foreman.core.tasks.backlog import BacklogError
from foreman.core.tasks.ids import legacy_ids, number, task_id
from foreman.core.tasks.issues import Issue, Linked, body_of, body_parts, fields_of, joined, split, stub, stuck, to_task


@pytest.fixture(autouse=True)
def utc(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def issue(state: str = "OPEN", reason: str = "", **fields: str | int) -> Issue:
    closed = "2026-11-21T09:00:00Z" if state == "CLOSED" else ""
    return Issue(5, "t", "", state, reason, "2026-11-21T08:08:03Z", closed, None, (), dict(fields), True)


def test_body_parts():
    body = "<!-- foreman:legacy-id=FN-140 -->\n## Goal\nthe goal\n\n## Acceptance criteria\n- one\n- two\n\n## Detail\nsome\n## sub\nmore"
    assert body_parts(body) == ("the goal", "one\ntwo", "some\n## sub\nmore")
    assert body_parts("just text\nhere") == ("", "", "just text\nhere")


@pytest.mark.parametrize("name", board.STATUS)
def test_each_status(name: str):
    assert to_task(issue(Status=name), "FN", {})["status"] == name.lower()
    assert name.lower() in backlog.STATUSES


def test_no_status():
    assert to_task(issue(), "FN", {})["status"] == "triage"
    assert to_task(issue("CLOSED", "COMPLETED"), "FN", {})["status"] == "done"
    assert to_task(issue("CLOSED", "NOT_PLANNED"), "FN", {})["status"] == "canceled"
    assert to_task(issue("CLOSED", "DUPLICATE"), "FN", {})["status"] == "duplicate"


def test_an_unknown_status_is_refused():
    with pytest.raises(BacklogError, match="#5: Status 'Todo' is not one of fm's"):
        to_task(issue(Status="Todo"), "FN", {})


def test_the_dates_in_fms_format():
    t = to_task(issue("CLOSED", "COMPLETED"), "FN", {})
    assert (t["created"], t["closed"]) == ("2026-11-21 08:08:03", "2026-11-21")
    assert "closed" not in to_task(issue(), "FN", {})


def test_every_field():
    i = Issue(
        5, "t", "## Goal\ng\n## Acceptance criteria\n- c", "OPEN", "", "2026-11-21T08:08:03Z", "", 10, (Linked(11, "b", "OPEN", "", "", ""),),
        {"Status": "Ready", "Model": "opus", "Effort": "xhigh", "Priority": "high", "Task type": "bug", "Repo key": "app", "Source": "s:1",
         "Blocked": "why", "Lens": "lens", "Max steps": 3},
        True,
    )  # fmt: skip
    t = to_task(i, "FN", {"FN-140": 10})
    assert t == {
        "id": "FN-5", "title": "t", "status": "ready", "blocked": "why", "detail": "", "priority": "high", "created": "2026-11-21 08:08:03",
        "acceptance": {"goal": "g", "criteria": "c"}, "model": "opus", "effort": "xhigh", "type": "bug", "repo": "app", "source": "s:1",
        "parent": "FN-140", "blocked_by": ["FN-11"], "chain": {"lens": "lens", "max_steps": 3, "max_lines": 0},
    }  # fmt: skip


def test_a_closed_blocker_as_a_stub():
    s = stub(Linked(11, "b", "CLOSED", "NOT_PLANNED", "2026-11-01T00:00:00Z", "2026-11-02T00:00:00Z"), "FN", {})
    assert (s["id"], s["status"], s["closed"]) == ("FN-11", "canceled", "2026-11-02")


def test_ids():
    assert task_id("FN", 272, {"FN-140": 272}) == "FN-140"
    assert task_id("FN", 301, {"FN-140": 272}) == "FN-301"
    assert legacy_ids(None) == {}
    with pytest.raises(BacklogError, match="legacy-ids.json"):
        legacy_ids({"FN-140": "272"})
    assert (number("FN", "FN-140", {"FN-140": 272}), number("FN", "FN-301", {}), number("FN", "XY-1", {})) == (272, 301, None)


TASK: dict[str, Any] = {
    "id": "FN-5", "title": "t", "status": "in progress", "blocked": "why", "detail": "d\n## sub\nmore", "priority": "high",
    "acceptance": {"goal": "g", "criteria": "c1\n\n- c2", "verdict": "accepted"}, "model": "opus", "effort": "xhigh", "type": "bug",
    "repo": "app", "source": "s:1", "parent": "FN-1", "blocked_by": ["FN-2"], "chain": {"lens": "l", "max_steps": 3, "max_lines": 40},
    "agent": {"name": "a"}, "plan_file": "/p.md",
}  # fmt: skip


def test_body_of_reads_back():
    assert body_parts(body_of(TASK, "")) == ("g", "c1\n\n- c2", "d\n## sub\nmore")
    assert body_of({"detail": "d"}, "<!-- foreman:legacy-id=FN-140 -->\n## Detail\nold") == "<!-- foreman:legacy-id=FN-140 -->\n\n## Detail\nd"
    assert body_of({"detail": ""}, "") == ""


def test_fields_of_reads_back():
    assert fields_of(TASK) == {
        "Status": "In progress", "Priority": "high", "Blocked": "why", "Model": "opus", "Effort": "xhigh", "Task type": "bug", "Repo key": "app",
        "Source": "s:1", "Lens": "l", "Max steps": 3, "Max lines": 40,
    }  # fmt: skip
    i = Issue(5, "t", "", "OPEN", "", "2026-11-21T08:08:03Z", "", None, (), fields_of(TASK), True)
    assert fields_of(to_task(i, "FN", {})) == fields_of(TASK)
    assert fields_of({"status": "triage", "priority": "none", "blocked": ""}) == {"Status": "Triage"}


def test_split_and_joined():
    on, local = split(TASK)
    assert local == {"agent": {"name": "a"}, "plan_file": "/p.md", "acceptance": {"verdict": "accepted"}}
    assert on["acceptance"] == {"goal": "g", "criteria": "c1\n\n- c2"} and "agent" not in on
    assert joined(on, local) == TASK


def test_stuck():
    assert stuck(TASK, dict(TASK)) == []
    assert stuck(TASK, {**TASK, "priority": "none", "blocked_by": ["FN-3"], "detail": "x"}) == ["body", "Priority", "blocked_by"]
    assert stuck(TASK, {**TASK, "blocked_by": ["FN-2"], "detail": "d\n## sub\nmore\n"}) == []  # read back the same
