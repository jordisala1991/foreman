"""The backlog on GitHub, reads (providers/github/backlog.py, issues.py, api.py) against a fake gh: list and show print every
field, the ids, the closed tasks, one read per pass, the caps, the rate limit's stop; a project without `[backlog]` keeps
its file."""

import json
import time
from datetime import date, timedelta

import pytest
from fakegh import FakeGh, board_env
from helpers import Env, fm

from foreman import proc, util
from foreman.core.tasks.backlog import BacklogError
from foreman.providers import registry
from foreman.providers.backlogfile import BacklogFile
from foreman.providers.github.types import RateLimited
from foreman.steps import metrics
from foreman.ui.panel import views


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeGh:
    f = FakeGh()
    monkeypatch.setattr(proc, "run", f)
    return f


def test_list_and_show_print_each_field(fake: FakeGh):
    e = board_env()
    fake.add(10, title="the parent", fields={"Status": "Ready"})
    fake.add(11, title="done blocker", state="CLOSED", reason="COMPLETED", closed="2026-11-20T10:00:00Z", fields={"Status": "Done"})
    fake.add(12, title="open blocker", fields={"Status": "Backlog"})
    fields: dict[str, str | int] = {"Status": "Ready", "Priority": "high", "Model": "opus", "Effort": "xhigh", "Task type": "bug", "Blocked": "waiting on X"}
    fake.add(20, title="the task", body="## Goal\ng\n## Acceptance criteria\n- c1\n- c2\n## Detail\nd", parent=10, blockers=[11, 12], fields=fields)
    line = next(ln for ln in fm(e.home, "list").splitlines() if ln.startswith("TY-20 "))
    assert line == (
        f"{'TY-20':8} {'ready':12} {'bug':10} {'':14}   the task  [priority: high]  [model: opus, effort: xhigh]  [parent: TY-10]"
        "  [blocked: waiting on X]  [waits for TY-12]"
    )
    shown = json.loads(fm(e.home, "show", "TY-20"))
    assert {k: shown[k] for k in ("title", "status", "priority", "model", "effort", "type", "parent", "blocked_by", "blocked", "waiting_for")} == {
        "title": "the task", "status": "ready", "priority": "high", "model": "opus", "effort": "xhigh", "type": "bug", "parent": "TY-10",
        "blocked_by": ["TY-11", "TY-12"], "blocked": "waiting on X", "waiting_for": ["TY-12"],
    }  # fmt: skip
    assert shown["acceptance"] == {"goal": "g", "criteria": "c1\nc2"} and shown["detail"] == "d"


def test_ids(fake: FakeGh):
    e = board_env()
    util.write_json(e.home / "legacy-ids.json", {"TY-140": 272})
    fake.add(272, title="migrated", fields={"Status": "Backlog"})
    fake.add(301, title="new", fields={"Status": "Triage"})
    fake.prs |= {100, 302}
    fake.add(305, title="closed by hand", state="CLOSED", reason="COMPLETED", closed="2026-11-20T10:00:00Z")
    fake.add(306, title="no card", carded=False)
    old = BacklogFile(e.p)
    old.add("an old closed one", criteria="c")
    old.update("TY-1", id="TY-100", status="done", closed="2026-01-01")
    assert [ln.split()[0] for ln in fm(e.home, "list").splitlines()] == ["TY-140", "TY-301"]
    assert json.loads(fm(e.home, "show", "TY-140"))["title"] == "migrated"
    assert views.task(e.p, "TY-140")["task"]["title"] == "migrated"
    assert json.loads(fm(e.home, "show", "TY-100"))["title"] == "an old closed one"
    assert json.loads(fm(e.home, "show", "TY-305"))["status"] == "done"
    for tid, why in [
        ("TY-302", "no task TY-302: #302 is a pull request"),
        ("TY-272", "no task TY-272: #272 is TY-140"),
        ("TY-306", "no task TY-306: #306 is not on the Board"),
        ("TY-5000", "no task TY-5000: there is no #5000"),
        ("FN-1", "no task FN-1"),
    ]:
        with pytest.raises(SystemExit, match=f"^foreman: {why}$"):
            fm(e.home, "show", tid)


def test_closed_tasks_from_the_old_file_and_closed_json(fake: FakeGh):
    e = board_env()
    fake.add(301, title="open", fields={"Status": "Backlog"})
    old = BacklogFile(e.p)
    for title in ("done today", "done long ago"):
        old.set_status(old.add(title, criteria="c"), "done")
    old.update("TY-2", closed=(date.today() - timedelta(days=30)).isoformat())
    task = {
        "id": "TY-290",
        "title": "closed by fm",
        "status": "done",
        "detail": "",
        "priority": "none",
        "created": util.now(),
        "closed": date.today().isoformat(),
    }
    util.write_json(e.p.state / "closed.json", [task])
    assert [t["id"] for t in views.state(e.p)["tasks"]] == ["TY-301", "TY-1", "TY-290"]
    assert {r["id"] for r in metrics.per_task(e.p) if r["status"] == "done"} == {"TY-1", "TY-2", "TY-290"}


def test_a_pass_reads_once(fake: FakeGh):
    e = board_env()
    fake.add(301, fields={"Status": "Backlog"})
    with registry.tick_scope(e.p):
        for _ in range(3):
            registry.backlog(e.p).load()
    assert fake.reads == 1


def test_over_the_caps(fake: FakeGh):
    e = board_env()
    b = registry.backlog(e.p)
    fake.add(301, fields={"Status": "Backlog"})
    fake.total = 51
    with pytest.raises(BacklogError, match="o/r has 51 open issues: fm reads at most 50"):
        b.load()
    fake.total, fake.next_page = None, True
    with pytest.raises(BacklogError, match="o/r has 1 open issues: fm reads at most 50"):
        b.load()
    fake.next_page = False
    fake.add(302, fields={"Status": "Backlog"}, blocked_total=21)
    with pytest.raises(BacklogError, match="#302 has 21 blockers: fm reads at most 20"):
        b.load()
    fake.issues[302].blocked_total, fake.issues[302].labels = None, 11
    with pytest.raises(BacklogError, match="#302 has 11 labels: fm reads at most 10"):
        b.load()
    fake.issues[302].labels, fake.issues[302].subs = 0, 31
    with pytest.raises(BacklogError, match="#302 has 31 sub-issues: fm reads at most 30"):
        b.load()


def test_rate_limit_stops_calls_until_the_reset(fake: FakeGh):
    e = board_env()
    fake.add(301, fields={"Status": "Backlog"})
    fake.limited = True
    b = registry.backlog(e.p)
    with pytest.raises(RateLimited, match="no call until"):
        b.load()
    assert [c[:2] for c in fake.calls] == [["api", "graphql"], ["api", "rate_limit"]]
    for _ in range(2):
        with pytest.raises(RateLimited):
            b.load()
    assert len(fake.calls) == 2
    with pytest.raises(SystemExit, match="foreman: GitHub's rate limit is spent: no call until"):
        fm(e.home, "list")
    assert len(fake.calls) == 2
    util.write_json(e.p.state / "github-rate-limit.json", {"reset": int(time.time()) - 1})
    fake.limited = False
    assert [t["id"] for t in b.load()["tasks"]] == ["TY-301"]


def test_without_backlog_the_file():
    e = Env()
    assert isinstance(registry.backlog(e.p), BacklogFile)
    tid = registry.backlog(e.p).add("t", criteria="c")
    assert registry.backlog(e.p).find(tid)["title"] == "t"


def test_writes_are_refused(fake: FakeGh):
    e = board_env()
    with pytest.raises(BacklogError, match="read-only"):
        registry.backlog(e.p).add("t")
    with pytest.raises(SystemExit, match="foreman: the backlog on GitHub is read-only"):
        fm(e.home, "add", "t", "--criteria", "c")
