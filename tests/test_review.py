"""A delivered tip's verdict is one GitHub PR review with its findings on their lines, against the fake gh."""

import unittest

from helpers import Env, git
from test_github import GH

from fm import backlog, loop, pulls
from fm.inline import Review
from fm.providers import registry

DIFF = "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1,2 +1,4 @@\n a\n+b\n+c\n d\n"


def tip(g: GH, iid: str, text: str, counts: tuple[int, int, int] = (0, 0, 0)) -> None:
    """Delivers the task's current tip with a verdict (already sent back, so it is delivered) and ticks."""
    backlog.set_status(g.e.p, iid, "in review", report={"summary": "does x", "confused": ""})
    g.e.review(iid, *counts)
    (g.e.home / "reviews" / f"{iid}.md").write_text(text)
    backlog.update(g.e.p, iid, review={**g.e.issue(iid)["review"], "sent_back": True})
    loop.tick(g.e.p)
    loop.tick(g.e.p)


def commit(g: GH, iid: str) -> str:
    wt = g.e.repo.parent / g.e.issue(iid)["worktree"]
    with open(wt / "app.py", "a") as f:
        f.write("# more\n")
    git(wt, "commit", "-qam", "more")
    return git(wt, "rev-parse", "HEAD").strip()


class Review_(unittest.TestCase):
    def make(self) -> tuple[GH, str]:
        g = GH(test="true")
        self.addCleanup(g.close)
        g.set(diff=DIFF)
        return g, g.work()

    def pr(self, g: GH) -> dict:
        return g.state["prs"]["1"]

    def test_a_delivered_tip_posts_one_review_and_no_comment(self):
        g, iid = self.make()
        tip(g, iid, "Findings:\nF1 | M | app.py:3 | the what | the why\n", (0, 1, 0))
        loop.tick(g.e.p)
        pr = self.pr(g)
        self.assertEqual(len(pr["reviews"]), 1)
        r = pr["reviews"][0]
        self.assertEqual((r["event"], r["commit_id"]), ("COMMENT", g.e.issue(iid)["pr"] and git(g.e.repo, "rev-parse", g.e.issue(iid)["branch"]).strip()))
        self.assertEqual(pr["comments"], [])
        (c,) = r["comments"]
        self.assertEqual((c["path"], c["line"], c["side"]), ("app.py", 3, "RIGHT"))
        self.assertIn("the what", c["body"])
        self.assertIn("the why", c["body"])
        self.assertNotIn("the why", r["body"])
        self.assertLess(r["body"].index("<details"), r["body"].index("the what"))

    def test_findings_outside_the_diff_are_listed_in_the_body(self):
        g, iid = self.make()
        tip(g, iid, "Findings:\nF1 | H | app.py:99 | boom | w\nF2 | L | - | nit\n", (1, 0, 1))
        (r,) = self.pr(g)["reviews"]
        self.assertEqual(r["comments"], [])
        self.assertIn("boom", r["body"])
        self.assertLess(r["body"].index("boom"), r["body"].index("<details"))
        self.assertGreater(r["body"].index("nit"), r["body"].index("<details"))

    def test_a_finding_already_commented_is_not_posted_again(self):
        g, iid = self.make()
        tip(g, iid, "Findings:\nF1 | M | app.py:3 | the what | the why\n", (0, 1, 0))
        commit(g, iid)
        tip(g, iid, "Findings:\nF2 | M | app.py:4 | the what\n", (0, 1, 0))
        self.assertEqual(len(self.pr(g)["reviews"]), 2)
        self.assertEqual(len(self.pr(g)["review_comments"]), 1)
        commit(g, iid)
        tip(g, iid, "Findings:\nF2 | M | app.py:4 | another what\n", (0, 1, 0))
        self.assertEqual(len(self.pr(g)["review_comments"]), 2)

    def test_a_lost_record_does_not_post_the_review_twice(self):
        g, iid = self.make()
        tip(g, iid, "Findings:\nF1 | M | app.py:3 | w\n", (0, 1, 0))
        backlog.update(g.e.p, iid, pr={**g.e.issue(iid)["pr"], "commented": ""})
        loop.tick(g.e.p)
        loop.tick(g.e.p)
        self.assertEqual(len(self.pr(g)["reviews"]), 1)

    def test_a_failed_diff_read_posts_the_review_without_inline_comments(self):
        g, iid = self.make()
        g.set(fail_always=["pr", "diff"])
        tip(g, iid, "Findings:\nF1 | M | app.py:3 | w\n", (0, 1, 0))
        loop.tick(g.e.p)
        (r,) = self.pr(g)["reviews"]
        self.assertEqual(r["comments"], [])

    def test_rejected_inline_comments_still_post_the_review(self):
        g, iid = self.make()
        g.set(reject_inline=True)
        tip(g, iid, "Findings:\nF1 | M | app.py:3 | w\n", (0, 1, 0))
        loop.tick(g.e.p)
        (r,) = self.pr(g)["reviews"]
        self.assertEqual(r["comments"], [])

    def test_unlisted_findings_are_never_read_as_ready(self):
        g, iid = self.make()
        tip(g, iid, "no findings line here\n", (0, 1, 0))
        (r,) = self.pr(g)["reviews"]
        self.assertNotIn("Ready", r["body"])
        self.assertEqual(g.calls("pr", "diff"), [])

    def test_a_verdict_of_another_tip_adds_no_findings(self):
        g, iid = self.make()
        f = g.e.home / "x.md"
        f.write_text("Findings:\nF1 | M | app.py:3 | w\n")
        i = {"id": iid, "title": "t", "review": {"tip": "aaa", "file": str(f), "high": 0, "medium": 1, "low": 0}}
        c = {"head": "bbb", "risk": "low", "tests": "pass", "files": 1, "lines": 1}
        self.assertEqual(pulls.findings_of(i, "bbb"), [])
        self.assertEqual(len(pulls.findings_of(i, "aaa")), 1)
        self.assertIsNone(pulls.case_of(i, c).counts)

    def test_local_provider_reviews_nothing(self):
        e = Env()
        provider = registry.get(e.p)
        self.assertEqual(provider.name, "local")
        c = {"id": "FN-1", "head": "abc", "pr": None}
        self.assertEqual(provider.diff(c), "")
        self.assertEqual(provider.review(c, Review("k", "b", [])), 0)


if __name__ == "__main__":
    unittest.main()
