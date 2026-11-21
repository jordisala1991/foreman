"""The backlog on GitHub, writes (providers/github/backlog.py, apply.py, writes.py, files.py) against a fake gh: every
command's write lands on the Board and is read back, a write that did not stick names the field, a close sets its reason
with the Status, the rate limit, the calls per operation, the backlog lock, and the drags the pass finds."""

import fcntl
import time
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

import pytest
from fakegh import FakeGh, board_env
from helpers import Env, fm

from foreman import proc, util
from foreman.core.tasks.backlog import BacklogError, get
from foreman.providers import registry
from foreman.providers.backlog import Backlog
from foreman.providers.github.types import RateLimited

CRITERIA = "## Acceptance criteria\n- c"


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeGh:
    f = FakeGh()
    monkeypatch.setattr(proc, "run", f)
    return f


@pytest.fixture
def e(fake: FakeGh) -> Env:
    """A project on a Board that `foreman backlog setup` made fm's; the counters start after it."""
    env = board_env()
    registry.setup_board(env.p)
    fake.graphql = fake.mutations = fake.reads = 0
    return env


def state(e: Env, name: str, default: Any = None) -> Any:
    return util.read_json(e.p.state / name, default)


def errors(e: Env) -> list[str]:
    return [x["error"] for x in util.read_jsonl(e.p.state / "errors.jsonl")]


def tick(e: Env) -> None:
    """A tick's first load: the pass."""
    with registry.tick_scope(e.p):
        registry.backlog(e.p).load()


def test_add_files_every_field(fake: FakeGh, e: Env):
    fake.add(10, title="the parent", fields={"Status": "Ready"})
    fake.add(12, title="a blocker", fields={"Status": "Backlog"})
    b = registry.backlog(e.p)
    f: dict[str, Any] = {"goal": "g", "detail": "d", "priority": "high", "model": "opus", "effort": "high", "type": "bug", "source": "s:1"}
    tid = b.add("t", criteria="c1\nc2", parent="TY-10", blocked_by=["TY-12"], **f)
    assert tid == "TY-13"
    s = fake.issues[13]
    assert s.body == "## Goal\ng\n\n## Acceptance criteria\n- c1\n- c2\n\n## Detail\nd"
    assert s.fields == {"Status": "Backlog", "Priority": "high", "Model": "opus", "Effort": "high", "Task type": "bug", "Source": "s:1"}
    assert (s.carded, s.parent, s.blockers) == (True, 10, [12])
    t = get(b.load(), tid)
    shown = {k: v for k, v in f.items() if k != "goal"}
    assert {k: t[k] for k in (*shown, "status", "parent", "blocked_by")} == {**shown, "status": "backlog", "parent": "TY-10", "blocked_by": ["TY-12"]}
    assert t["acceptance"] == {"goal": "g", "criteria": "c1\nc2"}
    assert b.add("again", criteria="c", source="s:1") == "" and 14 not in fake.issues
    assert fake.issues[int(b.add("no criteria").removeprefix("TY-"))].fields == {"Status": "Triage"}


def test_a_filing_cut_short_leaves_an_error_line(fake: FakeGh, e: Env):
    fake.hook = lambda op: setattr(fake, "limited", op == "Card")
    with pytest.raises(RateLimited):
        registry.backlog(e.p).add("t", criteria="c")
    assert len(errors(e)) == 1 and errors(e)[0].startswith("#1 was filed but not finished: close it by hand (")


def test_go(fake: FakeGh, e: Env):
    fake.add(5, body=CRITERIA, fields={"Status": "Triage", "Blocked": "x"})
    fake.add(6, fields={"Status": "Triage"})
    fm(e.home, "go", "TY-5")
    assert fake.issues[5].fields == {"Status": "Backlog"}
    with pytest.raises(SystemExit, match="^foreman: TY-6: define its acceptance criteria with the owner first$"):
        fm(e.home, "go", "TY-6")
    assert fake.issues[6].fields == {"Status": "Triage"}


def test_edit_drops_the_plan(fake: FakeGh, e: Env):
    fake.add(5, body=CRITERIA, fields={"Status": "Ready"})
    util.write_json(e.p.state / "runs.json", {"TY-5": {"plan_file": "/p.md", "worktree": "w"}})
    fm(e.home, "edit", "TY-5", "--criteria", "x")
    assert fake.issues[5].fields == {"Status": "Backlog"} and fake.issues[5].body == "## Acceptance criteria\n- x"
    assert state(e, "runs.json") == {"TY-5": {"worktree": "w"}}


def test_ready_and_done(fake: FakeGh, e: Env, monkeypatch: pytest.MonkeyPatch):
    fake.add(5, body=CRITERIA + "\n\n## Detail\nold", fields={"Status": "Refining"})
    monkeypatch.setenv("FOREMAN_TASK", "TY-5")
    plan = e.tmp / "plan.md"
    plan.write_text("the plan")
    fm(e.home, "ready", "--plan", str(plan), "--detail", "d")
    assert fake.issues[5].fields == {"Status": "Ready"} and fake.issues[5].body.endswith("## Detail\nold\n\nd")
    assert state(e, "runs.json")["TY-5"]["plan_file"] == str(plan.resolve())
    fm(e.home, "done", "--summary", "s")
    assert fake.issues[5].fields == {"Status": "In review"}
    assert state(e, "runs.json")["TY-5"]["report"]["summary"] == "s"


def test_cancel(fake: FakeGh, e: Env):
    fake.add(5, fields={"Status": "Backlog"})
    fm(e.home, "cancel", "TY-5")
    s = fake.issues[5]
    assert (s.state, s.reason, s.fields["Status"]) == ("CLOSED", "NOT_PLANNED", "Canceled")
    assert [(t["id"], t["status"]) for t in state(e, "closed.json")] == [("TY-5", "canceled")]


def test_block_and_unblock(fake: FakeGh, e: Env, monkeypatch: pytest.MonkeyPatch):
    fake.add(5, fields={"Status": "In progress"})
    monkeypatch.setenv("FOREMAN_TASK", "TY-5")
    fm(e.home, "block", "why")
    assert fake.issues[5].fields == {"Status": "In progress", "Blocked": "why"}
    monkeypatch.delenv("FOREMAN_TASK")
    fm(e.home, "unblock", "TY-5")
    assert fake.issues[5].fields == {"Status": "In progress"}


def test_ask_and_answer_put_the_label_on_and_off(fake: FakeGh, e: Env, monkeypatch: pytest.MonkeyPatch):
    fake.add(5, fields={"Status": "In progress"})
    monkeypatch.setenv("FOREMAN_TASK", "TY-5")
    fm(e.home, "ask", "q1")
    fm(e.home, "ask", "q2")
    assert fake.issues[5].labels == ["needs-owner"]
    assert [q["id"] for q in state(e, "questions.json")["decisions"]] == ["TY-5 Q1", "TY-5 Q2"]
    monkeypatch.delenv("FOREMAN_TASK")
    fm(e.home, "answer", "TY-5 Q1", "a")
    assert fake.issues[5].labels == ["needs-owner"]  # Q2 is still open
    fm(e.home, "answer", "TY-5 Q2", "b")
    assert fake.issues[5].labels == []
    assert [q["id"] for q in state(e, "questions.json")["decisions_resolved"]] == ["TY-5 Q1", "TY-5 Q2"]


def test_sub_issue_and_blocker_writes(fake: FakeGh, e: Env):
    fake.add(5, body=CRITERIA, fields={"Status": "Backlog"})
    for n in (10, 12, 13):
        fake.add(n, fields={"Status": "Backlog"})
    fake.add(11, state="CLOSED", reason="COMPLETED", closed="2026-11-20T10:00:00Z", fields={"Status": "Done"})
    registry.backlog(e.p).update("TY-5", parent="TY-10")
    assert fake.issues[5].parent == 10
    fm(e.home, "edit", "TY-5", "--blocked-by", "TY-12")
    assert fake.issues[5].blockers == [12]
    fm(e.home, "edit", "TY-5", "--blocked-by", "TY-13")
    assert fake.issues[5].blockers == [13]
    registry.backlog(e.p).update("TY-5", blocked_by=["TY-13", "TY-11"])  # a closed blocker: looked up by number
    assert fake.issues[5].blockers == [13, 11]


def test_a_write_that_did_not_stick(fake: FakeGh, e: Env):
    fake.add(5, fields={"Status": "Backlog"})
    b = registry.backlog(e.p)
    fake.drop = {"Priority"}
    with pytest.raises(BacklogError, match="^TY-5: Priority did not stick: wrote 'high', read back None$"):
        b.update("TY-5", priority="high")
    fake.drop = {"Status"}
    with pytest.raises(BacklogError, match="^TY-5: Status did not stick: wrote 'Ready', read back 'Backlog'$"):
        b.set_status("TY-5", "ready")


@pytest.mark.parametrize(
    ("status", "reason", "column"), [("done", "COMPLETED", "Done"), ("canceled", "NOT_PLANNED", "Canceled"), ("duplicate", "DUPLICATE", "Duplicate")]
)
def test_close_reasons(fake: FakeGh, e: Env, status: str, reason: str, column: str):
    fake.add(5, fields={"Status": "In review"})
    b = registry.backlog(e.p)
    b.set_status("TY-5", status, result="r")
    assert fake.closes == [(5, reason, column)]  # the reason and the Status in one request
    [kept] = state(e, "closed.json")
    datetime.strptime(kept["created"], "%Y-%m-%d %H:%M:%S")
    assert (kept["id"], kept["status"], kept["closed"], "result" in kept) == ("TY-5", status, date.today().isoformat(), False)
    t = get(b.load(), "TY-5")
    assert (t["status"], t["result"]) == (status, "r")


def test_a_write_over_the_rate_limit_waits_for_the_reset(fake: FakeGh, e: Env):
    fake.add(5, fields={"Status": "Backlog"})
    with registry.tick_scope(e.p):
        b = registry.backlog(e.p)
        b.load()
        fake.limited, n = True, fake.graphql
        for _ in range(2):
            with pytest.raises(RateLimited, match="no call until"):
                b.set_status("TY-5", "done", commit="abc")
        assert fake.graphql == n + 1  # the first write's request, then none until the reset
        assert state(e, "runs.json") is None and state(e, "closed.json") is None
        util.write_json(e.p.state / "github-rate-limit.json", {"reset": int(time.time()) - 1})
        fake.limited = False
        b.set_status("TY-5", "ready")
    assert fake.issues[5].fields == {"Status": "Ready"}


def calls(fake: FakeGh, write: Callable[[], object]) -> int:
    n = fake.graphql
    write()
    return fake.graphql - n


def test_calls_per_operation(fake: FakeGh, e: Env):
    """Each port write beside DESIGN's table («Reads and writes per pass»): a command's write makes one call more, the
    read that finds the ids the table assumes known (the body edit's row already counts it); a tick's write, from the
    pass's read, makes the table's."""
    fake.add(5, body=CRITERIA, fields={"Status": "Backlog"})
    for n in (10, 12):
        fake.add(n, fields={"Status": "Backlog"})

    def b() -> Backlog:  # a fresh backlog per write, as each command makes one
        return registry.backlog(e.p)

    table = {
        "add": (4, lambda: b().add("t", criteria="c")),
        "add with links": (4, lambda: b().add("u", criteria="c", parent="TY-10", blocked_by=["TY-12"])),
        "set_status": (2, lambda: b().set_status("TY-5", "ready")),
        "blocked": (2, lambda: b().update("TY-5", blocked="x")),
        "ask": (2, lambda: b().ask("TY-5", "a", "q")),
        "answer": (2, lambda: b().answer("TY-5 Q1", "a")),
        "detail": (3, lambda: b().update("TY-5", detail="d")),
        "parent": (2, lambda: b().update("TY-5", parent="TY-10")),
        "blocked_by": (2, lambda: b().update("TY-5", blocked_by=["TY-12"])),
        "done": (2, lambda: b().set_status("TY-5", "done")),
    }
    seen = {op: calls(fake, write) for op, (_, write) in table.items()}
    assert seen == {op: want if op == "detail" else want + 1 for op, (want, _) in table.items()}  # the edit of a body counts its fresh read
    fake.add(6, fields={"Status": "Backlog"})
    with registry.tick_scope(e.p):
        assert calls(fake, lambda: b().load()) == 1  # the pass
        assert calls(fake, lambda: b().set_status("TY-6", "ready")) == 2
        assert calls(fake, lambda: b().update("TY-6", nudges=1)) == 0
    with registry.tick_scope(e.p):
        assert calls(fake, lambda: b().load()) == 1  # an idle pass


def test_writes_and_the_pass_hold_the_lock(fake: FakeGh, e: Env):
    fake.add(5, body=CRITERIA, fields={"Status": "Backlog"})
    seen: list[str] = []

    def held(op: str) -> None:
        with open(str(e.p.state / "backlog.lock"), "a") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                seen.append(op)
                return
            fcntl.flock(f, fcntl.LOCK_UN)
            seen.append(f"{op} unlocked")

    fake.hook = held
    with registry.tick_scope(e.p):
        b = registry.backlog(e.p)
        b.load()
        b.set_status("TY-5", "ready")
        with b.edit():
            held("edit")
        b.add("x", criteria="c")
    registry.backlog(e.p).update("TY-5", blocked="y")
    assert seen == ["Open", "Fields", "Issue", "edit", "Open", "File", "Card", "Fields", "Issue", "Open", "Fields", "Issue"]


def test_the_first_pass_detects_no_drag(fake: FakeGh, e: Env):
    fake.add(1, fields={"Status": "Backlog"})
    tick(e)
    assert state(e, "backlog-snapshot.json") == {"statuses": {"TY-1": "backlog"}}
    assert not (e.p.state / "errors.jsonl").exists() and fake.mutations == 0


def test_a_drag_triage_to_backlog_is_a_go(fake: FakeGh, e: Env):
    fake.add(1, body=CRITERIA, fields={"Status": "Triage", "Blocked": "x"})
    fake.add(2, fields={"Status": "Triage"})
    tick(e)
    assert fake.mutations == 0
    fake.issues[1].fields["Status"] = fake.issues[2].fields["Status"] = "Backlog"
    tick(e)
    assert fake.issues[1].fields == {"Status": "Backlog"}  # let in, its block cleared
    events = [x["text"] for x in util.read_jsonl(e.p.state / "events.jsonl") if x["task"] == "TY-1"]
    assert events == ["→ backlog: let in by the owner (dragged on the Board)"]
    assert fake.issues[2].fields == {"Status": "Triage"}
    assert errors(e) == ["TY-2: dragged triage → backlog on the Board but define its acceptance criteria with the owner first; moved back to triage"]
    assert state(e, "backlog-snapshot.json") == {"statuses": {"TY-1": "backlog", "TY-2": "triage"}}


def test_any_other_drag_is_an_error_and_moved_back(fake: FakeGh, e: Env):
    fake.add(3, fields={"Status": "Ready"})
    fake.add(4, fields={"Status": "Ready"})
    tick(e)
    fake.issues[3].fields["Status"], fake.issues[4].fields["Status"] = "In progress", "Done"
    tick(e)
    assert errors(e) == [
        "TY-3: dragged ready → in progress on the Board; moved back to ready",
        "TY-4: dragged ready → done on the Board; moved back to ready",
    ]
    assert [(fake.issues[n].fields["Status"], fake.issues[n].state) for n in (3, 4)] == [("Ready", "OPEN")] * 2
    registry.backlog(e.p).set_status("TY-3", "in progress")  # a move through the port is no drag
    tick(e)
    assert len(errors(e)) == 2 and fake.issues[3].fields["Status"] == "In progress"


def test_a_write_after_a_drag_keeps_the_drag_for_the_pass(fake: FakeGh, e: Env):
    fake.add(3, fields={"Status": "Ready"})
    tick(e)
    fake.issues[3].fields["Status"] = "In progress"
    registry.backlog(e.p).update("TY-3", blocked="why")
    tick(e)
    assert errors(e) == ["TY-3: dragged ready → in progress on the Board; moved back to ready"]
    assert fake.issues[3].fields == {"Status": "Ready", "Blocked": "why"}


def test_a_close_whose_read_back_fails_is_kept(fake: FakeGh, e: Env):
    fake.add(5, fields={"Status": "In review"})
    fake.hook = lambda op: setattr(fake, "limited", fake.limited or op == "Fields")
    b = registry.backlog(e.p)
    with pytest.raises(RateLimited):
        b.set_status("TY-5", "done", commit="abc")
    assert fake.issues[5].state == "CLOSED"
    assert [(t["id"], t["status"]) for t in state(e, "closed.json")] == [("TY-5", "done")]
    util.write_json(e.p.state / "github-rate-limit.json", {"reset": int(time.time()) - 1})
    fake.limited = False
    t = get(b.load(), "TY-5")
    assert (t["status"], t["commit"]) == ("done", "abc")


def test_an_answer_on_a_closed_task(fake: FakeGh, e: Env):
    fake.add(5, fields={"Status": "In progress"})
    b = registry.backlog(e.p)
    qid = b.ask("TY-5", "a", "q")
    b.set_status("TY-5", "canceled")
    n = fake.mutations
    b.answer(qid, "late")
    assert fake.mutations == n and [q["id"] for q in state(e, "questions.json")["decisions_resolved"]] == [qid]
