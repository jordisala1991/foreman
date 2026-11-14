"""A delivered tip's verdict is one GitHub PR review with its findings on their lines, against the fake gh."""

import unittest

from helpers import Env, git
from test_github import GH

from fm import backlog, loop, pulls, verdict
from fm.inline import Review, Thread
from fm.providers import Provider, Transient, registry

DIFF = "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1,2 +1,4 @@\n a\n+b\n+c\n d\n"


def tip(g: GH, iid: str, text: str, counts: tuple[int, int, int] = (0, 0, 0)) -> None:
    """Delivers the task's current tip with a verdict (already returned, so it is delivered) and ticks."""
    backlog.set_status(g.e.p, iid, "in review", report={"summary": "does x", "confused": ""})
    g.e.review(iid, sum(counts))
    (g.e.home / "reviews" / f"{iid}.md").write_text(text)
    backlog.update(g.e.p, iid, review={**g.e.task(iid)["review"], "returned": True})
    loop.tick(g.e.p)
    loop.tick(g.e.p)


def commit(g: GH, iid: str) -> str:
    wt = g.e.repo.parent / g.e.task(iid)["worktree"]
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
        self.assertEqual((r["event"], r["commit_id"]), ("COMMENT", g.e.task(iid)["pr"] and git(g.e.repo, "rev-parse", g.e.task(iid)["branch"]).strip()))
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
        backlog.update(g.e.p, iid, pr={**g.e.task(iid)["pr"], "commented": ""})
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

    def test_a_review_with_a_null_body_does_not_stop_the_review(self):
        g, iid = self.make()
        tip(g, iid, "Findings:\nF1 | M | app.py:3 | w\n", (0, 1, 0))
        prs = g.state["prs"]
        prs["1"]["reviews"].append({"body": None, "comments": []})
        g.set(prs=prs)
        commit(g, iid)
        tip(g, iid, "Findings:\nF1 | M | app.py:3 | w\n", (0, 1, 0))
        self.assertEqual(len(self.pr(g)["reviews"]), 3)

    def test_body_links_point_at_the_comment_or_the_reviewed_commit_and_line(self):
        g, iid = self.make()
        head = git(g.e.repo, "rev-parse", g.e.task(iid)["branch"]).strip()
        tip(g, iid, "Findings:\nF1 | H | app.py:99 | boom | w\nF2 | M | app.py:3 | mid | why: because\n", (1, 1, 0))
        (r,) = self.pr(g)["reviews"]
        self.assertIn(f"https://github.com/o/r/blob/{head}/app.py#L99", r["body"])
        self.assertIn("https://github.com/o/r/pull/1#discussion_r1", r["body"])
        self.assertNotIn("fm-link", r["body"])
        self.assertNotIn("why: because", r["comments"][0]["body"])
        self.assertIn("because", r["comments"][0]["body"])

    def test_a_failed_comment_link_edit_keeps_the_file_links(self):
        g, iid = self.make()
        g.set(fail_put=True)
        head = git(g.e.repo, "rev-parse", g.e.task(iid)["branch"]).strip()
        tip(g, iid, "Findings:\nF2 | M | app.py:3 | mid\n", (0, 1, 0))
        (r,) = self.pr(g)["reviews"]
        self.assertIn(f"/blob/{head}/app.py#L3", r["body"])

    def sha(self, g: GH, iid: str) -> str:
        return git(g.e.repo, "rev-parse", g.e.task(iid)["branch"]).strip()

    def resolves(self, g: GH) -> list[list[str]]:
        return [c for c in g.calls("api", "graphql") if any("resolveReviewThread" in x for x in c)]

    def fixed_once(self, g: GH, iid: str) -> None:
        tip(g, iid, "Findings:\nF1 | M | app.py:3 | what one\n", (0, 1, 0))
        commit(g, iid)
        tip(g, iid, "no findings\n", (0, 0, 0))

    def test_a_fixed_finding_resolves_its_thread(self):
        g, iid = self.make()
        self.fixed_once(g, iid)
        self.assertEqual(len(self.pr(g)["reviews"]), 2)
        self.assertTrue(self.pr(g)["threads"][0]["resolved"])

    def test_a_settled_thread_is_not_settled_again(self):
        g, iid = self.make()
        self.fixed_once(g, iid)
        backlog.update(g.e.p, iid, pr={**g.e.task(iid)["pr"], "commented": ""})
        loop.tick(g.e.p)
        loop.tick(g.e.p)
        commit(g, iid)
        tip(g, iid, "none\n", (0, 0, 0))
        self.assertEqual(len(self.resolves(g)), 1)

    def test_an_open_finding_keeps_its_thread(self):
        g, iid = self.make()
        tip(g, iid, "Findings:\nF1 | M | app.py:3 | what one\n", (0, 1, 0))
        commit(g, iid)
        tip(g, iid, "Findings:\nF2 | M | app.py:4 | what one\n", (0, 1, 0))
        self.assertFalse(self.pr(g)["threads"][0]["resolved"])
        self.assertEqual(self.resolves(g), [])
        self.assertEqual(len(self.pr(g)["review_comments"]), 1)
        self.assertIn(verdict.SINCE.format(fixed=0, new=0), self.pr(g)["reviews"][1]["body"])

    def test_a_refused_resolve_is_answered_with_the_sha_once(self):
        g, iid = self.make()
        g.set(reject_resolve=True)
        tip(g, iid, "Findings:\nF1 | M | app.py:3 | what one\n", (0, 1, 0))
        commit(g, iid)
        sha = self.sha(g, iid)
        tip(g, iid, "none\n", (0, 0, 0))
        t = self.pr(g)["threads"][0]
        self.assertFalse(t["resolved"])
        self.assertEqual(len(t["bodies"]), 2)
        self.assertIn(sha[:9], t["bodies"][1])
        commit(g, iid)
        tip(g, iid, "none\n", (0, 0, 0))
        self.assertEqual(len(self.pr(g)["threads"][0]["bodies"]), 2)

    def test_a_transient_settle_failure_stops_the_rest(self):
        g, iid = self.make()
        tried: list[str] = []

        class Down(Provider):
            def __init__(self) -> None:
                pass

            def settle(self, change, thread, sha):
                tried.append(thread.id)
                raise Transient("down")

        pulls._settle(g.e.p, Down(), g.e.task(iid), {"head": "h"}, [Thread("A", "k", False), Thread("B", "k", False)])
        self.assertEqual(tried, ["A"])

    def test_rejected_inline_comments_are_not_counted_new(self):
        g, iid = self.make()
        tip(g, iid, "Findings:\nF1 | M | app.py:3 | what one\n", (0, 1, 0))
        g.set(reject_inline=True)
        commit(g, iid)
        tip(g, iid, "Findings:\nF1 | M | app.py:3 | what one\nF2 | M | app.py:4 | what two\n", (0, 2, 0))
        body = self.pr(g)["reviews"][1]["body"]
        self.assertIn(verdict.SINCE.format(fixed=0, new=0), body)

    def test_the_second_review_counts_fixed_and_new(self):
        g, iid = self.make()
        tip(g, iid, "Findings:\nF1 | M | app.py:3 | what one\n", (0, 1, 0))
        commit(g, iid)
        tip(g, iid, "Findings:\nF2 | M | app.py:4 | what two\n", (0, 1, 0))
        first, second = self.pr(g)["reviews"]
        self.assertIn(verdict.SINCE.format(fixed=1, new=1), second["body"])
        self.assertNotIn("since last push", first["body"])

    def test_an_unlisted_verdict_settles_nothing(self):
        g, iid = self.make()
        tip(g, iid, "Findings:\nF1 | M | app.py:3 | what one\n", (0, 1, 0))
        commit(g, iid)
        tip(g, iid, "old format, no findings line\n", (0, 1, 0))
        self.assertEqual(self.resolves(g), [])
        self.assertEqual(len(self.pr(g)["reviews"]), 2)

    def test_a_failed_thread_read_still_posts_the_review_once(self):
        g, iid = self.make()
        g.set(fail_threads=True)
        tip(g, iid, "Findings:\nF1 | M | app.py:3 | w\n", (0, 1, 0))
        loop.tick(g.e.p)
        (r,) = self.pr(g)["reviews"]
        self.assertNotIn("since last push", r["body"])

    def test_threads_without_a_marker_are_ignored(self):
        g, iid = self.make()
        tip(g, iid, "Findings:\nF1 | M | app.py:3 | what one\n", (0, 1, 0))
        prs = g.state["prs"]
        prs["1"]["threads"].append({"id": "X", "resolved": False, "bodies": ["hi"]})
        g.set(prs=prs)
        commit(g, iid)
        tip(g, iid, "none\n", (0, 0, 0))
        x = next(t for t in self.pr(g)["threads"] if t["id"] == "X")
        self.assertFalse(x["resolved"])
        self.assertEqual(x["bodies"], ["hi"])

    def test_old_issue_comments_are_left_alone(self):
        g, iid = self.make()
        tip(g, iid, "Findings:\nF1 | M | app.py:3 | what one\n", (0, 1, 0))
        prs = g.state["prs"]
        prs["1"]["comments"].append("fm: merge risk low")
        g.set(prs=prs)
        commit(g, iid)
        tip(g, iid, "none\n", (0, 0, 0))
        self.assertEqual(self.pr(g)["comments"], ["fm: merge risk low"])
        self.assertEqual(g.calls("pr", "comment"), [])

    def test_local_provider_settles_nothing(self):
        e = Env()
        provider = registry.get(e.p)
        c = {"id": "FN-1", "head": "abc", "pr": None}
        self.assertEqual(provider.threads(c), [])
        self.assertIsNone(provider.settle(c, Thread("t", "k", False), "abc"))

    def test_local_provider_reviews_nothing(self):
        e = Env()
        provider = registry.get(e.p)
        self.assertEqual(provider.name, "local")
        c = {"id": "FN-1", "head": "abc", "pr": None}
        self.assertEqual(provider.diff(c), "")
        self.assertEqual(provider.review(c, Review("k", "b", [])), 0)


if __name__ == "__main__":
    unittest.main()
