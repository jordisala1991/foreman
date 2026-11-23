"""A delivered change or bug task without a session goes back to its agent before its review (FN-428): one test per line of
the acceptance criteria."""

import json

import pytest
from helpers import Env, fm
from test_review_rules import commit

from foreman.core.review import session
from foreman.core.tasks import tasktypes
from foreman.loop import tick
from foreman.providers import registry
from foreman.ui.panel import views

pytestmark = pytest.mark.real_session_check

COMMAND = "```\n$ foreman show TY-1\nok\n```\n"
SCREENSHOT = "![login](a.png)\n"


def delivered(e: Env, text: str | None = None, kind: str = "") -> str:
    """A task delivered with a session file of this text (none for None); one tick of the loop has looked at it."""
    tid = registry.backlog(e.p).add("x", status="ready", criteria="y")
    if kind:
        registry.backlog(e.p).update(tid, type=kind)
    tick.tick(e.p)
    commit(e.task(tid)["worktree"], "app.py", "# change\n")
    if text is not None:
        f = e.home / "reports" / tid / "session.md"
        f.parent.mkdir(parents=True)
        f.write_text(text)
    registry.backlog(e.p).set_status(tid, "in review", report={"summary": "s", "confused": ""})
    tick.tick(e.p)
    return tid


def session_path(e: Env, tid: str) -> str:
    return str(e.home / "reports" / tid / "session.md")


class TestSessionReturn:
    def test_a_missing_session_goes_back_to_its_agent(self) -> None:
        e = Env()
        tid = delivered(e)
        assert e.task(tid)["status"] == "in progress"
        assert "hunter" not in e.task(tid)

    @pytest.mark.parametrize("text", ["", "I tried it and it works.\n", "```\n\n```\n"])
    def test_an_empty_session_goes_back(self, text: str) -> None:
        e = Env()
        tid = delivered(e, text)
        assert e.task(tid)["status"] == "in progress"
        assert "hunter" not in e.task(tid)

    @pytest.mark.parametrize("text", [COMMAND, SCREENSHOT])
    def test_a_session_with_a_command_or_a_screenshot_is_reviewed(self, text: str) -> None:
        e = Env()
        tid = delivered(e, text)
        assert e.task(tid)["status"] == "in review"
        assert e.task(tid)["hunter"]["tip"]

    def test_a_bug_without_a_session_goes_back(self) -> None:
        e = Env()
        tid = delivered(e, kind="bug")
        assert e.task(tid)["status"] == "in progress"

    @pytest.mark.parametrize("kind", ["mechanical", "definition"])
    def test_a_mechanical_or_definition_task_is_not_asked_for_one(self, kind: str) -> None:
        e = Env()
        tid = delivered(e, kind=kind)
        assert e.task(tid)["status"] == "in review"
        assert e.task(tid)["hunter"]["tip"]

    def test_the_reason_shows_in_events_and_the_panel(self) -> None:
        e = Env()
        tid = delivered(e)
        path = session_path(e, tid)
        rows = [r for r in json.loads(fm(e.home, "events", "--json", "--task", tid)) if r["kind"] == "session"]
        assert len(rows) == 1
        assert path in rows[0]["text"]
        assert any(path in q["answer"] for q in views.task(e.p, tid)["questions"])


class TestRule:
    def test_gap(self) -> None:
        assert session.gap(None) == "missing"
        assert session.gap("") == "empty"
        assert session.gap("```\n\n```") == "empty"
        assert session.gap("```\nx\n```") == ""
        assert session.gap("~~~sh\nx\n~~~") == ""
        assert session.gap("![a](a.png)") == ""

    def test_the_note_names_the_file(self) -> None:
        for why in ("missing", "empty"):
            assert "/r/session.md" in session.note("/r/session.md", why)

    def test_only_change_and_bug_want_a_session(self) -> None:
        assert [t for t in tasktypes.TYPES if tasktypes.wants_session({"type": t})] == ["change", "bug"]
        assert tasktypes.wants_session({})
