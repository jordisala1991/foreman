"""A project on GitHub as the owner sees it besides `foreman list`/`show`: the panel's list and task page agree with them, and
`foreman watch` prints the same lines as on the file."""

import json
from typing import Any

import pytest
from fakegh import FakeGh, board_env, passed
from helpers import Env, fm

from foreman import proc, util
from foreman.core.tasks.backlog import BacklogError, NoSnapshot
from foreman.providers import registry
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
    passed(e)
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
    return watch.needs(e.p), q


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


CRITERIA = "## Acceptance criteria\n- c"


def test_panel_and_watch_make_no_github_call(fake: FakeGh):
    e = board_env()
    for n in (1, 2, 3):
        fake.add(n, title=f"t{n}", fields={"Status": "Backlog"})
    passed(e)
    n = len(fake.calls)
    for _ in range(2):
        views.summary(e.p)
        assert len(views.state(e.p)["tasks"]) == 3
        assert views.task(e.p, "TY-1")["task"]["title"] == "t1"
        views.system(e.p)
        watch.needs(e.p)
    assert len(fake.calls) == n


def test_a_go_and_a_local_write_show_at_once(fake: FakeGh):
    e = board_env()
    registry.setup_board(e.p)
    fake.add(1, title="t", body=CRITERIA, fields={"Status": "Triage"})
    passed(e)
    b = registry.backlog(e.p)
    b.set_status("TY-1", "backlog", "let in by the owner", blocked="")
    b.update("TY-1", accepted={"by": "owner", "head": "h", "at": "t"})
    n = len(fake.calls)
    assert [t["status"] for t in views.state(e.p)["tasks"]] == ["backlog"]
    assert views.task(e.p, "TY-1")["task"]["accepted"]["by"] == "owner"
    assert len(fake.calls) == n


def test_without_a_snapshot_they_say_so(fake: FakeGh):
    e = board_env()
    fake.add(1, fields={"Status": "Backlog"})
    state = views.state(e.p)
    assert (state["tasks"], state["notice"]) == ([], "no_snapshot")
    assert "broken" not in views.summary(e.p)
    with pytest.raises(NoSnapshot, match="the loop has not read the Board yet"):
        watch.run(e.p, True)
    assert fake.calls == []
    util.write_json(e.p.state / "backlog-cards.json", [1])
    with pytest.raises(BacklogError) as err:
        views.state(e.p)
    assert not isinstance(err.value, NoSnapshot)


def test_owner_commands_still_read_live(fake: FakeGh):
    e = board_env()
    fake.add(1, fields={"Status": "Backlog"})
    passed(e)
    fake.add(9, fields={"Status": "Backlog"})
    reads = fake.reads
    assert "TY-9" in fm(e.home, "list")
    assert fake.reads == reads + 1
    assert [t["id"] for t in views.state(e.p)["tasks"]] == ["TY-1"]
    passed(e)
    assert [t["id"] for t in views.state(e.p)["tasks"]] == ["TY-1", "TY-9"]


def test_a_project_without_backlog_is_unchanged():
    e = Env()
    tid = registry.backlog(e.p).add("t", criteria="c")
    assert [t["id"] for t in views.state(e.p)["tasks"]] == [tid]
    assert watch.needs(e.p) == {}
