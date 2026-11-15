"""A delivered tip's verdict is one GitHub PR review with its findings on their lines, against the fake gh."""

import pytest
from helpers import Env, fm, git
from test_github import GH

from fm.core.review.inline import Review
from fm.core.tasks import backlog
from fm.loop import tick
from fm.providers import Change, registry
from fm.services.delivery import pulls

DIFF = "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1,2 +1,4 @@\n a\n+b\n+c\n d\n"


def tip(g: GH, iid: str, text: str, open: int = 0) -> None:
    """Delivers the task's current tip with a verdict (already sent back, so it is delivered) and ticks."""
    backlog.set_status(g.e.p, iid, "in review", report={"summary": "does x", "confused": ""})
    g.e.review(iid, open)
    (g.e.home / "reviews" / f"{iid}.md").write_text(text)
    backlog.update(g.e.p, iid, review={**g.e.task(iid)["review"], "returned": True})
    tick.tick(g.e.p)
    tick.tick(g.e.p)


def commit(g: GH, iid: str) -> str:
    wt = g.e.repo.parent / g.e.task(iid)["worktree"]
    with open(wt / "app.py", "a") as f:
        f.write("# more\n")
    git(wt, "commit", "-qam", "more")
    return git(wt, "rev-parse", "HEAD").strip()


class TestReview_:
    @pytest.fixture(autouse=True)
    def keep_request(self, request):
        self.request = request  # for the cleanups of helpers

    def make(self) -> tuple[GH, str]:
        g = GH(test="true")
        self.request.addfinalizer(g.close)
        g.set(diff=DIFF)
        return g, g.work()

    def pr(self, g: GH) -> dict:
        return g.state["prs"]["1"]

    def test_a_delivered_tip_posts_one_review_and_no_comment(self):
        g, iid = self.make()
        tip(g, iid, "Findings:\nF1 | M | app.py:3 | the what | the why\n", 1)
        tick.tick(g.e.p)
        pr = self.pr(g)
        assert len(pr["reviews"]) == 1
        r = pr["reviews"][0]
        assert (r["event"], r["commit_id"]) == ("COMMENT", g.e.task(iid)["pr"] and git(g.e.repo, "rev-parse", g.e.task(iid)["branch"]).strip())
        assert pr["comments"] == []
        (c,) = r["comments"]
        assert (c["path"], c["line"], c["side"]) == ("app.py", 3, "RIGHT")
        assert "the what" in c["body"]
        assert "the why" in c["body"]
        assert "the why" not in r["body"]
        assert r["body"].index("<details") < r["body"].index("the what")

    def test_findings_outside_the_diff_are_listed_in_the_body(self):
        g, iid = self.make()
        tip(g, iid, "Findings:\nF1 | H | app.py:99 | boom | w\nF2 | L | - | nit\n", 1)
        (r,) = self.pr(g)["reviews"]
        assert r["comments"] == []
        assert "boom" in r["body"]
        assert r["body"].index("boom") < r["body"].index("<details")
        assert r["body"].index("nit") > r["body"].index("<details")

    def test_a_finding_already_commented_is_not_posted_again(self):
        g, iid = self.make()
        tip(g, iid, "Findings:\nF1 | M | app.py:3 | the what | the why\n", 1)
        commit(g, iid)
        tip(g, iid, "Findings:\nF2 | M | app.py:4 | the what\n", 1)
        assert len(self.pr(g)["reviews"]) == 2
        assert len(self.pr(g)["review_comments"]) == 1
        fm(g.e.home, "reject", iid, "fix F2")  # after round 2 only an owner reject (or a conflict) gets a review
        commit(g, iid)
        tip(g, iid, "Findings:\nF2 | M | app.py:4 | another what\n", 1)
        assert len(self.pr(g)["review_comments"]) == 2

    def test_a_lost_record_does_not_post_the_review_twice(self):
        g, iid = self.make()
        tip(g, iid, "Findings:\nF1 | M | app.py:3 | w\n", 1)
        backlog.update(g.e.p, iid, pr={**g.e.task(iid)["pr"], "commented": ""})
        tick.tick(g.e.p)
        tick.tick(g.e.p)
        assert len(self.pr(g)["reviews"]) == 1

    def test_a_failed_diff_read_posts_the_review_without_inline_comments(self):
        g, iid = self.make()
        g.set(fail_always=["pr", "diff"])
        tip(g, iid, "Findings:\nF1 | M | app.py:3 | w\n", 1)
        tick.tick(g.e.p)
        (r,) = self.pr(g)["reviews"]
        assert r["comments"] == []

    def test_rejected_inline_comments_still_post_the_review(self):
        g, iid = self.make()
        g.set(reject_inline=True)
        tip(g, iid, "Findings:\nF1 | M | app.py:3 | w\n", 1)
        tick.tick(g.e.p)
        (r,) = self.pr(g)["reviews"]
        assert r["comments"] == []

    def test_unlisted_findings_are_never_read_as_ready(self):
        g, iid = self.make()
        tip(g, iid, "no findings line here\n", 1)
        (r,) = self.pr(g)["reviews"]
        assert "Ready" not in r["body"]
        assert g.calls("pr", "diff") == []

    def test_a_verdict_of_another_tip_adds_no_findings(self):
        g, iid = self.make()
        f = g.e.home / "x.md"
        f.write_text("Findings:\nF1 | M | app.py:3 | w\n")
        i = {"id": iid, "title": "t", "review": {"tip": "aaa", "file": str(f), "open": 1}}
        c = {"head": "bbb", "risk": "low", "tests": "pass", "files": 1, "lines": 1}
        assert pulls.findings_of(i, "bbb") == []
        assert len(pulls.findings_of(i, "aaa")) == 1
        assert pulls.case_of(i, c).open is None

    def test_a_review_with_a_null_body_does_not_stop_the_review(self):
        g, iid = self.make()
        tip(g, iid, "Findings:\nF1 | M | app.py:3 | w\n", 1)
        prs = g.state["prs"]
        prs["1"]["reviews"].append({"body": None, "comments": []})
        g.set(prs=prs)
        commit(g, iid)
        tip(g, iid, "Findings:\nF1 | M | app.py:3 | w\n", 1)
        assert len(self.pr(g)["reviews"]) == 3

    def test_body_links_point_at_the_comment_or_the_reviewed_commit_and_line(self):
        g, iid = self.make()
        head = git(g.e.repo, "rev-parse", g.e.task(iid)["branch"]).strip()
        tip(g, iid, "Findings:\nF1 | H | app.py:99 | boom | w\nF2 | M | app.py:3 | mid | why: because\n", 2)
        (r,) = self.pr(g)["reviews"]
        assert f"https://github.com/o/r/blob/{head}/app.py#L99" in r["body"]
        assert "https://github.com/o/r/pull/1#discussion_r1" in r["body"]
        assert "fm-link" not in r["body"]
        assert "why: because" not in r["comments"][0]["body"]
        assert "because" in r["comments"][0]["body"]

    def test_a_failed_comment_link_edit_keeps_the_file_links(self):
        g, iid = self.make()
        g.set(fail_put=True)
        head = git(g.e.repo, "rev-parse", g.e.task(iid)["branch"]).strip()
        tip(g, iid, "Findings:\nF2 | M | app.py:3 | mid\n", 1)
        (r,) = self.pr(g)["reviews"]
        assert f"/blob/{head}/app.py#L3" in r["body"]

    def test_local_provider_reviews_nothing(self):
        e = Env()
        provider = registry.get(e.p)
        assert provider.name == "local"
        c = Change(id="FN-1", head="abc")
        assert provider.diff(c) == ""
        assert provider.review(c, Review("k", "b", [])) == 0
