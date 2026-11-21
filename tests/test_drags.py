"""A card dragged on the Board (core/tasks/drags.py, pure): which cards moved, Go's checks, and the verdict on each drag."""

from foreman.core.tasks.drags import Drag, found, go_checks, verdict

WITH = {"id": "TY-1", "acceptance": {"criteria": "c"}}
WITHOUT = {"id": "TY-2"}


def test_found():
    assert found({"a": "triage", "b": "ready"}, {"a": "backlog", "b": "ready", "c": "done"}) == [Drag("a", "triage", "backlog")]


def test_go_checks():
    assert go_checks(WITH, [WITH], ["main"]) == ""
    assert go_checks(WITHOUT, [WITHOUT], ["main"]) == "TY-2: define its acceptance criteria with the owner first"
    assert go_checks(WITH, [WITH], ["a", "b"]) == "TY-1: choose its repo first (foreman edit TY-1 --repo a|b)"


def test_verdict():
    assert verdict(Drag("TY-1", "triage", "backlog"), WITH, [WITH], []) == ""
    assert verdict(Drag("TY-2", "triage", "backlog"), WITHOUT, [WITHOUT], []) == (
        "TY-2: dragged triage → backlog on the Board but define its acceptance criteria with the owner first; moved back to triage"
    )
    assert verdict(Drag("TY-1", "backlog", "ready"), WITH, [WITH], []) == "TY-1: dragged backlog → ready on the Board; moved back to backlog"
