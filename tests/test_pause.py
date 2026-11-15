"""A pause stops tasks from starting; what is in flight (reviews, checks, merges) goes on (FN-164)."""

from helpers import Env, fm
from test_review_rules import commit, deliver

from fm import loop
from fm.core.tasks import backlog


def pause(e: Env) -> None:
    e.p.state.mkdir(parents=True, exist_ok=True)
    (e.p.state / "paused").write_text("now")


def in_review(e: Env) -> str:
    tid = backlog.add(e.p, "x", status="ready", criteria="y")
    loop.tick(e.p)
    commit(e.task(tid)["worktree"], "app.py", "# change\n")
    backlog.set_status(e.p, tid, "in review", report={"summary": "s", "confused": ""})
    return tid


class TestPause:
    def test_a_task_in_review_gets_hunter_and_reviewer_while_paused(self):
        e = Env(slots=2, test="true")
        tid = in_review(e)
        pause(e)
        loop.tick(e.p)
        assert e.task(tid).get("hunter")
        e.hunter_done(tid)
        loop.tick(e.p)
        assert e.task(tid).get("reviewer")

    def test_paused_task_in_review_is_checked_and_merged_after_accept(self):
        e = Env(test="true")
        tid = deliver(e)
        pause(e)
        loop.tick(e.p)
        assert e.task(tid).get("check")
        fm(e.home, "accept", tid)
        loop.tick(e.p)
        assert e.task(tid)["status"] == "done"

    def test_paused_launches_no_task_agent(self):
        e = Env(slots=3, test="true")
        in_review(e)
        r = backlog.add(e.p, "r", status="ready", criteria="y")
        b = backlog.add(e.p, "b", status="backlog", criteria="y")
        pause(e)
        loop.tick(e.p)
        for t, s in ((r, "ready"), (b, "backlog")):
            assert e.task(t)["status"] == s
            assert "agent" not in e.task(t)

    def test_paused_launches_no_parent_agent(self, owner):
        e = Env(test="true")
        parent = backlog.add(e.p, "parent", status="backlog", criteria="c")
        fm(e.home, "add", "s", "--parent", parent, "--triage", "--criteria", "c")
        n = len(e.calls())
        pause(e)
        loop.tick(e.p)
        assert len(e.calls()) == n

    def test_in_flight_counts_work_still_running(self):
        e = Env(test="true")
        for s in ("in progress", "in review", "ready", "done"):
            tid = backlog.add(e.p, s, status="ready", criteria="y")
            backlog.set_status(e.p, tid, s, "test")
        assert backlog.in_flight(backlog.load(e.p)) == 2
        assert "2" in fm(e.home, "pause")
        assert "2" not in fm(e.home, "pause", "--off")
