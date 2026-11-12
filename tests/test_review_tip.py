import os
import unittest

from helpers import Env, fm, git

from fm import backlog, loop


def head(e: Env, iid: str) -> str:
    return git(e.repo.parent / e.issue(iid)["worktree"], "rev-parse", "HEAD").strip()


def commit(e: Env, iid: str) -> str:
    wt = e.repo.parent / e.issue(iid)["worktree"]
    with open(wt / "app.py", "a") as f:
        f.write("# more\n")
    git(wt, "commit", "-qam", "more")
    return head(e, iid)


def start(e: Env) -> tuple[str, str]:
    iid = backlog.add(e.p, "x", status="ready", criterion="y")
    loop.tick(e.p)
    commit(e, iid)
    backlog.set_status(e.p, iid, "in review", report={"summary": "s", "confused": ""})
    e.hunt_then_review(iid)
    return iid, head(e, iid)


def verdict(e: Env, iid: str, tip: str) -> None:
    os.environ["FM_ISSUE"] = iid
    try:
        fm(e.home, "verdict", "--tip", tip, "--open", "0", "--file", "r.md")
    finally:
        os.environ.pop("FM_ISSUE")


class Tip(unittest.TestCase):
    def test_a_verdict_for_an_old_tip_is_recorded_for_it_and_the_new_tip_is_reviewed(self):
        e = Env(test="true")
        iid, a = start(e)
        b = commit(e, iid)
        verdict(e, iid, a)
        i = e.issue(iid)
        self.assertEqual(i["reviews"][-1]["tip"], a)
        self.assertNotEqual(i["review"]["tip"], b)  # not the head: it decides nothing
        e.hunt_then_review(iid)
        self.assertNotIn("check", e.issue(iid))
        self.assertIn(f"Tip: {b}", e.calls()[-1]["argv"][-1])

    def test_a_verdict_for_an_old_tip_before_the_loop_saw_the_new_commit(self):
        e = Env(test="true")
        iid, a = start(e)
        verdict(e, iid, a)
        self.assertEqual(e.issue(iid)["review"]["tip"], a)
        b = commit(e, iid)
        e.hunt_then_review(iid)
        self.assertNotIn("check", e.issue(iid))
        last = e.calls()[-1]["argv"][-1]
        self.assertIn("Round: 2", last)
        self.assertIn(f"Tip: {b}", last)

    def test_a_verdict_for_the_current_tip_decides(self):
        e = Env(test="true")
        iid, a = start(e)
        verdict(e, iid, a)
        r = e.issue(iid)["review"]
        self.assertEqual((r["tip"], r["round"]), (a, 1))
        loop.tick(e.p)
        self.assertEqual(e.issue(iid)["check"]["head"], a)

    def test_two_reviewers_each_verdict_lands_on_its_own_tip(self):
        for order in ("ba", "ab"):
            e = Env(test="true")
            iid, a = start(e)
            b = commit(e, iid)
            e.hunt_then_review(iid)
            files = [r["file"] for r in e.issue(iid)["reviewers"]]
            self.assertEqual(len(set(files)), 2)
            tips = {"a": a, "b": b}
            for k in order:
                verdict(e, iid, tips[k])
            i = e.issue(iid)
            self.assertEqual([r["tip"] for r in i["reviews"]], [tips[k] for k in order])
            self.assertEqual(i["review"]["tip"], b)
            loop.tick(e.p)
            if order == "ba":
                self.assertEqual(e.issue(iid)["check"]["head"], b)

    def test_ab_order_waits_for_b(self):
        e = Env(test="true")
        iid, a = start(e)
        b = commit(e, iid)
        e.hunt_then_review(iid)
        verdict(e, iid, a)
        loop.tick(e.p)
        self.assertNotIn("check", e.issue(iid))
        verdict(e, iid, b)
        loop.tick(e.p)
        self.assertEqual(e.issue(iid)["check"]["head"], b)

    def test_an_unknown_tip_is_refused(self):
        e = Env(test="true")
        iid, _ = start(e)
        with self.assertRaises(SystemExit):
            verdict(e, iid, "deadbeef")
        i = e.issue(iid)
        self.assertNotIn("reviews", i)
        self.assertIsNone(i.get("review"))

    def test_an_abbreviated_tip_is_stored_in_full(self):
        e = Env(test="true")
        iid, a = start(e)
        verdict(e, iid, a[:9])
        self.assertEqual(e.issue(iid)["review"]["tip"], a)

    def test_with_one_slot_the_new_tip_is_reviewed_once_the_old_verdict_lands(self):
        e = Env(test="true", slots=1)
        iid, a = start(e)
        b = commit(e, iid)
        n = len(e.calls())
        loop.tick(e.p)
        self.assertEqual(len(e.calls()), n)
        verdict(e, iid, a)
        self.assertEqual(e.issue(iid)["review"]["tip"], a)
        e.hunt_then_review(iid)
        last = e.calls()[-1]["argv"][-1]
        self.assertIn("Round: 2", last)
        self.assertIn(f"Tip: {b}", last)
        self.assertNotIn("check", e.issue(iid))


class Passes(unittest.TestCase):
    def test_the_reviewer_is_told_the_file_the_hunter_wrote_even_if_a_verdict_landed_in_between(self):
        e = Env(test="true")
        iid, a = start(e)
        b = commit(e, iid)
        loop.tick(e.p)  # hunter of b, round 1
        hunter_file = e.issue(iid)["hunter"]["file"]
        verdict(e, iid, a)  # reviews grows: the round number moves
        e.hunter_done(iid)
        loop.tick(e.p)
        self.assertIn(f"Tip: {b}", e.calls()[-1]["argv"][-1])
        self.assertIn(hunter_file, e.calls()[-1]["argv"][-1])
        self.assertTrue(os.path.exists(hunter_file))

    def test_the_round_two_hunter_checks_the_fixes_only(self):
        e = Env(test="true")
        iid, a = start(e)
        verdict(e, iid, a)
        commit(e, iid)
        loop.tick(e.p)
        text = e.calls()[-1]["argv"][-1]
        self.assertIn("[fm hunter", text)
        self.assertIn("Round 2 looks at the fixes", text)
        self.assertIn(e.issue(iid)["reviews"][-1]["file"], text)
