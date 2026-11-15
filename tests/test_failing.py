"""Failing tests or CI return a delivered branch to its agent (FN-67): one test per row of DESIGN.md's table "What the
loop does with a task in review" that this adds. The owner only sees branches that pass."""

from helpers import Env
from test_github import GH
from test_review_rules import commit, head, launched, take_note

from fm import backlog, loop


def deliver(e: Env, name: str = "app.py") -> str:
    tid = backlog.add(e.p, "x", status="ready", criteria="y")
    loop.tick(e.p)
    commit(e.task(tid)["worktree"], name, "# change\n")
    backlog.set_status(e.p, tid, "in review", report={"summary": "s", "confused": ""})
    e.review(tid)
    return tid


def fix(e: Env, tid: str, open_findings: int = 0) -> None:
    """The agent takes the note, commits a fix and delivers it; its review gives the verdict."""
    take_note(e, tid)
    commit(e.task(tid)["worktree"], "app.py", "# fix\n")
    backlog.set_status(e.p, tid, "in review")
    e.review(tid, open_findings)


class TestFailing:
    def test_failing_tests_go_back_with_the_output(self):
        e = Env(test="echo BOOM-LINE; exit 1")
        tid = deliver(e)
        loop.tick(e.p)
        i = e.task(tid)
        assert (i["status"], i["failures"]) == ("in progress", 1)
        assert "tests fail [tests]" in i["check"]["reasons"]  # kept for the panel while it is back
        take_note(e, tid)
        assert "BOOM-LINE" in e.calls()[-1]["argv"][-1]

    def test_only_the_last_lines_of_the_output_go_back(self):
        e = Env(test="for n in $(seq 1 30); do echo L$n; done; exit 1")
        tid = deliver(e)
        loop.tick(e.p)
        take_note(e, tid)
        note = e.calls()[-1]["argv"][-1]
        assert "L30" in note
        assert "L1\n" not in note

    def test_the_fix_gets_a_review_of_only_that_fix(self):
        e = Env(test="echo BOOM-LINE; exit 1")
        tid = deliver(e)
        loop.tick(e.p)
        old = head(e, tid)
        take_note(e, tid)
        commit(e.task(tid)["worktree"], "app.py", "# fix\n")
        backlog.set_status(e.p, tid, "in review")
        e.hunt_then_review(tid)
        assert launched(e, tid)[-1].endswith("rule: failing tests or CI"), launched(e, tid)
        assert f"git diff {old} {head(e, tid)}" in e.calls()[-1]["argv"][-1]
        e.verdict(tid, 1)
        loop.tick(e.p)
        i = e.task(tid)
        assert (i["status"], i["failures"]) == ("in progress", 1)  # open findings go back: not a failure

    def test_the_third_failure_waits_for_the_owner(self):
        e = Env(test="echo BOOM-LINE; exit 1")
        tid = deliver(e)
        for n in (1, 2):
            loop.tick(e.p)
            assert (e.task(tid)["status"], e.task(tid)["failures"]) == ("in progress", n)
            fix(e, tid)
        before = len(e.calls())
        loop.tick(e.p)
        i = e.task(tid)
        assert (i["status"], i["failures"]) == ("in review", 2)
        assert "tests fail [tests]" in i["check"]["reasons"]
        assert len(e.calls()) == before

    def test_a_green_check_ends_the_row_without_a_test_command(self, request):
        """With a test command only the merge ends the row (FN-113, tests/test_merged_check.py); without one, the PR's
        CI stands in and a green check ends it."""
        g = GH(test="")
        request.addfinalizer(g.close)
        g.set(ci="fail")
        tid = g.work()
        g.deliver(tid)
        loop.tick(g.e.p)
        assert g.e.task(tid)["failure_streak"] == 1
        take_note(g.e, tid)
        commit(g.e.task(tid)["worktree"], "app.py", "# fix\n")
        g.set(ci="pass")
        backlog.set_status(g.e.p, tid, "in review")
        g.e.review(tid)
        loop.tick(g.e.p)
        i = g.e.task(tid)
        assert (i["status"], i["failure_streak"], i["failures"]) == ("in review", 0, 1)

    def test_failing_ci_goes_back_with_each_check(self, request):
        g = GH(test="true")
        request.addfinalizer(g.close)
        g.set(ci="fail", failed=[["lint", "https://ci/lint"], ["unit", "https://ci/unit"]])
        tid = g.work()
        g.deliver(tid)
        loop.tick(g.e.p)
        assert g.e.task(tid)["status"] == "in progress"
        take_note(g.e, tid)
        note = g.e.calls()[-1]["argv"][-1]
        for s in ("lint: https://ci/lint", "unit: https://ci/unit"):
            assert s in note
        assert "build" not in note
