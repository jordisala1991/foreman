import os

import pytest
from helpers import Env, fm, git

from fm import backlog, loop


def head(e: Env, tid: str) -> str:
    return git(e.repo.parent / e.task(tid)["worktree"], "rev-parse", "HEAD").strip()


def commit(e: Env, tid: str) -> str:
    wt = e.repo.parent / e.task(tid)["worktree"]
    with open(wt / "app.py", "a") as f:
        f.write("# more\n")
    git(wt, "commit", "-qam", "more")
    return head(e, tid)


def start(e: Env) -> tuple[str, str]:
    tid = backlog.add(e.p, "x", status="ready", criteria="y")
    loop.tick(e.p)
    commit(e, tid)
    backlog.set_status(e.p, tid, "in review", report={"summary": "s", "confused": ""})
    e.hunt_then_review(tid)
    return tid, head(e, tid)


def verdict(e: Env, tid: str, tip: str) -> None:
    os.environ["FM_TASK"] = tid
    try:
        fm(e.home, "verdict", "--tip", tip, "--open", "0", "--file", "r.md")
    finally:
        os.environ.pop("FM_TASK")


class TestTip:
    def test_a_verdict_for_an_old_tip_is_recorded_for_it_and_the_new_tip_is_reviewed(self):
        e = Env(test="true")
        tid, a = start(e)
        b = commit(e, tid)
        verdict(e, tid, a)
        i = e.task(tid)
        assert i["reviews"][-1]["tip"] == a
        assert i["review"]["tip"] != b  # not the head: it decides nothing
        e.hunt_then_review(tid)
        assert "check" not in e.task(tid)
        assert f"Tip: {b}" in e.calls()[-1]["argv"][-1]

    def test_a_verdict_for_an_old_tip_before_the_loop_saw_the_new_commit(self):
        e = Env(test="true")
        tid, a = start(e)
        verdict(e, tid, a)
        assert e.task(tid)["review"]["tip"] == a
        b = commit(e, tid)
        e.hunt_then_review(tid)
        assert "check" not in e.task(tid)
        last = e.calls()[-1]["argv"][-1]
        assert "Round: 2" in last
        assert f"Tip: {b}" in last

    def test_a_verdict_for_the_current_tip_decides(self):
        e = Env(test="true")
        tid, a = start(e)
        verdict(e, tid, a)
        r = e.task(tid)["review"]
        assert (r["tip"], r["round"]) == (a, 1)
        loop.tick(e.p)
        assert e.task(tid)["check"]["head"] == a

    def test_two_reviewers_each_verdict_lands_on_its_own_tip(self):
        for order in ("ba", "ab"):
            e = Env(test="true")
            tid, a = start(e)
            b = commit(e, tid)
            e.hunt_then_review(tid)
            files = [r["file"] for r in e.task(tid)["reviewers"]]
            assert len(set(files)) == 2
            tips = {"a": a, "b": b}
            for k in order:
                verdict(e, tid, tips[k])
            i = e.task(tid)
            assert [r["tip"] for r in i["reviews"]] == [tips[k] for k in order]
            assert i["review"]["tip"] == b
            loop.tick(e.p)
            if order == "ba":
                assert e.task(tid)["check"]["head"] == b

    def test_ab_order_waits_for_b(self):
        e = Env(test="true")
        tid, a = start(e)
        b = commit(e, tid)
        e.hunt_then_review(tid)
        verdict(e, tid, a)
        loop.tick(e.p)
        assert "check" not in e.task(tid)
        verdict(e, tid, b)
        loop.tick(e.p)
        assert e.task(tid)["check"]["head"] == b

    def test_an_unknown_tip_is_refused(self):
        e = Env(test="true")
        tid, _ = start(e)
        with pytest.raises(SystemExit):
            verdict(e, tid, "deadbeef")
        i = e.task(tid)
        assert "reviews" not in i
        assert i.get("review") is None

    def test_an_abbreviated_tip_is_stored_in_full(self):
        e = Env(test="true")
        tid, a = start(e)
        verdict(e, tid, a[:9])
        assert e.task(tid)["review"]["tip"] == a

    def test_with_one_slot_the_new_tip_is_reviewed_once_the_old_verdict_lands(self):
        e = Env(test="true", slots=1)
        tid, a = start(e)
        b = commit(e, tid)
        n = len(e.calls())
        loop.tick(e.p)
        assert len(e.calls()) == n
        verdict(e, tid, a)
        assert e.task(tid)["review"]["tip"] == a
        e.hunt_then_review(tid)
        last = e.calls()[-1]["argv"][-1]
        assert "Round: 2" in last
        assert f"Tip: {b}" in last
        assert "check" not in e.task(tid)


class TestPasses:
    def test_the_reviewer_is_told_the_file_the_hunter_wrote_even_if_a_verdict_landed_in_between(self):
        e = Env(test="true")
        tid, a = start(e)
        b = commit(e, tid)
        loop.tick(e.p)  # hunter of b, round 1
        hunter_file = e.task(tid)["hunter"]["file"]
        verdict(e, tid, a)  # reviews grows: the round number moves
        e.hunter_done(tid)
        loop.tick(e.p)
        assert f"Tip: {b}" in e.calls()[-1]["argv"][-1]
        assert hunter_file in e.calls()[-1]["argv"][-1]
        assert os.path.exists(hunter_file)

    def test_the_round_two_hunter_checks_the_fixes_only(self):
        e = Env(test="true")
        tid, a = start(e)
        verdict(e, tid, a)
        commit(e, tid)
        loop.tick(e.p)
        text = e.calls()[-1]["argv"][-1]
        assert "[fm hunter" in text
        assert "Round 2 looks at the fixes" in text
        assert e.task(tid)["reviews"][-1]["file"] in text


class TestFreshReview:
    def test_fm_review_starts_both_passes_again_on_the_same_tip_as_round_one(self):
        e = Env(test="true")
        tid, a = start(e)
        verdict(e, tid, a)
        fm(e.home, "review", tid)
        i = e.task(tid)
        assert len(i["reviews"]) == 1  # history stays
        loop.tick(e.p)
        text = e.calls()[-1]["argv"][-1]
        assert "[fm hunter" in text
        assert "before it is merged, round 1" in text
        assert "looks at the fixes" not in text
        e.hunt_then_review(tid)
        assert "checks the fixes" not in e.calls()[-1]["argv"][-1]
        verdict(e, tid, a)
        assert (e.task(tid)["review"]["round"], len(e.task(tid)["reviews"])) == (1, 2)

    def test_fm_review_refuses_a_task_not_in_review(self):
        e = Env(test="true")
        tid = backlog.add(e.p, "x", status="ready", criteria="y")
        with pytest.raises(SystemExit):
            fm(e.home, "review", tid)

    def test_fm_review_clears_a_block_so_the_review_starts(self):
        e = Env(test="true")
        tid, a = start(e)
        verdict(e, tid, a)
        backlog.update(e.p, tid, blocked="its hunter ended without its findings file")
        fm(e.home, "review", tid)
        loop.tick(e.p)
        assert "[fm hunter" in e.calls()[-1]["argv"][-1]

    def test_fm_review_refuses_an_accepted_task_and_one_with_a_review_running(self):
        e = Env(test="true")
        tid, a = start(e)  # its reviewer has launched, no verdict yet
        with pytest.raises(SystemExit):
            fm(e.home, "review", tid)
        verdict(e, tid, a)
        backlog.update(e.p, tid, accepted={"by": "owner", "head": a, "at": ""})
        with pytest.raises(SystemExit):
            fm(e.home, "review", tid)
        assert "accepted" in e.task(tid)

    def test_fm_review_refuses_a_running_review_even_when_the_task_is_blocked(self):
        e = Env(test="true")
        tid, _ = start(e)  # its reviewer has launched, no verdict yet
        backlog.update(e.p, tid, blocked="something")
        with pytest.raises(SystemExit):
            fm(e.home, "review", tid)
