"""A project on GitHub as the owner sees it besides `foreman list`/`show`: the panel's list and task page agree with them, and
`foreman watch` prints the same lines as on the file."""

import json
from typing import Any

import pytest
from fakegh import FakeGh, board_env
from helpers import Env, fm

from foreman import proc
from foreman.providers import registry
from foreman.providers.runner import NO_ROWS
from foreman.steps import watch
from foreman.ui.panel import views

CHECK = {"risk": "high", "tests": "pass", "review": "clean", "ci": "", "conflict": False, "head": "h1", "main": "m1"}
SAME = ("title", "status", "priority", "model", "effort", "type", "parent", "blocked_by", "blocked", "acceptance", "detail")


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeGh:
    f = FakeGh()
    monkeypatch.setattr(proc, "run", f)
    return f


def test_the_panel_list_and_task_page_match_list_and_show(fake: FakeGh):
    e = board_env()
    fake.add(5, title="parent", fields={"Status": "Ready"})
    fields: dict[str, str | int] = {"Status": "Backlog", "Priority": "high", "Model": "opus", "Effort": "low", "Task type": "bug", "Blocked": "why"}
    fake.add(6, title="child", body="## Goal\ng\n## Acceptance criteria\n- c1\n- c2\n## Detail\nd", parent=5, blockers=[5], fields=fields)
    fake.add(7, title="to triage", fields={"Status": "Triage"})
    listed = [ln.split()[:2] for ln in fm(e.home, "list").splitlines()]
    assert listed == [["TY-5", "ready"], ["TY-6", "backlog"], ["TY-7", "triage"]]
    assert [[t["id"], t["status"]] for t in views.state(e.p)["tasks"]] == listed
    for tid in ("TY-5", "TY-6", "TY-7"):
        shown, page = json.loads(fm(e.home, "show", tid)), views.task(e.p, tid)["task"]
        assert {k: page.get(k) for k in SAME} == {k: shown.get(k) for k in SAME}
    page = views.task(e.p, "TY-6")["task"]
    assert (page["priority"], page["parent"], page["acceptance"]["criteria"]) == ("high", "TY-5", "c1\nc2")


def scene(e: Env) -> tuple[dict[str, str], str]:
    """One blocked task, one with a question, one waiting for the accept; the lines `watch` finds."""
    b = registry.backlog(e.p)
    one = b.add("one", status="ready", criteria="c")
    b.update(one, blocked="needs X")
    two = b.add("two", status="ready", criteria="c")
    q = b.ask(two, "an agent", "which one?")
    three = b.add("three", status="ready", criteria="c")
    review: dict[str, Any] = {"round": 1, "tip": "h1", "open": 0}
    b.update(three, status="in review", branch="b", check=CHECK, review=review)
    return watch.needs(e.p, NO_ROWS), q


def test_watch_prints_the_same_lines_on_github_as_on_the_file(fake: FakeGh):
    on_file, q = scene(Env())
    g = board_env()
    registry.setup_board(g.p)
    on_github, same_q = scene(g)
    assert same_q == q
    assert on_file == {
        f"q:{q}": f"TY-2 asks the owner ({q}): which one?",
        "accept:TY-3": "TY-3 waits for the owner's accept: three",
        "blocked:TY-1": "TY-1 blocked: needs X",
    }
    assert on_github == on_file
