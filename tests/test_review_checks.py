import os
import unittest
from pathlib import Path

from helpers import Env, fm, git
from test_review_tip import head, start

from fm import backlog, loop


def checks(e: Env, iid: str) -> Path:
    return e.home / "reviews" / f"{iid}-checks"


def verdict(e: Env, iid: str, tip: str, too_much: int = 0) -> str:
    os.environ["FM_ISSUE"] = iid
    try:
        return fm(e.home, "verdict", "--tip", tip, "--too-much", str(too_much), "--file", "r.md")
    finally:
        os.environ.pop("FM_ISSUE")


class ReviewChecks(unittest.TestCase):
    def test_the_verdict_counts_the_checks_that_fail(self):
        e = Env(test="true")
        iid, tip = start(e)
        (checks(e, iid) / "C1.sh").write_text("exit 0\n")
        (checks(e, iid) / "F1.sh").write_text("exit 1\n")
        output = verdict(e, iid, tip)
        review = e.issue(iid)["review"]
        self.assertEqual((review["open"], review["failing"]), (1, ["F1.sh"]))
        self.assertIn("fails: F1.sh", output)

    def test_too_much_adds_to_what_is_open(self):
        e = Env(test="true")
        iid, tip = start(e)
        (checks(e, iid) / "F1.sh").write_text("exit 1\n")
        verdict(e, iid, tip, 2)
        self.assertEqual(e.issue(iid)["review"]["open"], 3)

    def test_a_fix_that_makes_the_check_pass_closes_the_finding(self):
        e = Env(test="true")
        iid, first = start(e)
        (checks(e, iid) / "F1.sh").write_text("test -f fixed.txt\n")  # relative: it runs in the task's worktree
        verdict(e, iid, first)
        self.assertEqual(e.issue(iid)["review"]["open"], 1)
        loop.tick(e.p)
        self.assertIn("F1.sh", e.calls()[-1]["argv"][-1])  # the agent is told which check to make pass

        wt = Path(e.issue(iid)["worktree"])
        (wt / "fixed.txt").write_text("fixed\n")
        git(wt, "add", "fixed.txt")
        git(wt, "commit", "-qm", "fix")
        backlog.set_status(e.p, iid, "in review", report={"summary": "s", "confused": ""})
        loop.tick(e.p)
        self.assertIn("Round: 2", e.calls()[-1]["argv"][-1])
        verdict(e, iid, head(e, iid))
        review = e.issue(iid)["review"]
        self.assertEqual((review["open"], review["failing"]), (0, []))
