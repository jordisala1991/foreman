"""The report as the task's description: pure text rules, the GitHub PR body, and the panel's description."""

import unittest

from helpers import Env, git
from test_github import GH

from fm import agents, backlog, describe, loop, pulls


class Text(unittest.TestCase):
    def test_merge_body_replaces_the_block_and_keeps_outside_text(self):
        body = "note\n" + describe.wrap("old") + "\ntail"
        out = describe.merge_body(body, "new")
        self.assertTrue(out.startswith("note\n") and out.endswith("\ntail"))
        self.assertIn("new", out)
        self.assertNotIn("old", out)

    def test_merge_body_without_markers_appends(self):
        out = describe.merge_body("human text", "new")
        self.assertTrue(out.startswith("human text"))
        self.assertIn(describe.wrap("new"), out)
        self.assertEqual(describe.merge_body("  \n", "new"), describe.wrap("new"))

    def test_render_folds_review_rounds(self):
        out = describe.render("Body\n\n## Review rounds\nF1 fixed", "FOOT")
        self.assertLess(out.index("Body"), out.index("FOOT"))
        self.assertLess(out.index("FOOT"), out.index("<details>"))
        self.assertLess(out.index("<details>"), out.index("F1 fixed"))
        self.assertNotIn("<details>", describe.render("Body", "FOOT"))

    def test_footer_and_review_line(self):
        f = describe.footer("0123456789abc", describe.review_line({"round": 1, "high": 0, "medium": 0}), "t")
        self.assertIn("commit 012345678 ", f + " ")
        self.assertNotIn("9a", f)
        self.assertIn("review round 1, open H 0 M 0", f)
        self.assertEqual(describe.review_line(None), "no review")

    def test_render_description_falls_back_to_the_claim(self):
        g = GH()
        try:
            iid = backlog.add(g.e.p, "T", claim="The claim", criterion="c")
            for rep in ({}, {"file": ""}, {"file": str(g.e.tmp / "nope.md")}):
                i = {**g.e.issue(iid), "report": rep}
                self.assertIn("The claim", pulls.description(g.e.p, i, "abcdef0123", "now"))
            blank = g.e.tmp / "blank.md"
            blank.write_text("  \n")
            i = {**g.e.issue(iid), "report": {"file": str(blank)}}
            self.assertIn("The claim", pulls.description(g.e.p, i, "abcdef0123", "now"))
        finally:
            g.close()


class OnGitHub(unittest.TestCase):
    def setUp(self):
        self.g = GH(test="true")
        self.addCleanup(self.g.close)

    def deliver(self, text="Did X"):
        g = self.g
        iid = g.work()
        path = g.e.home / "reports" / f"{iid}.md"
        path.parent.mkdir(exist_ok=True)
        path.write_text(text)
        backlog.set_status(g.e.p, iid, "in review", report={"summary": "s", "confused": "", "file": str(path)})
        return iid

    def pr(self):
        return self.g.state["prs"]["1"]

    def run_ticks(self, n=2):
        for _ in range(n):
            loop.tick(self.g.e.p)

    def test_github_pr_body_is_the_report(self):
        iid = self.deliver()
        self.g.e.review(iid)
        self.run_ticks()
        body = self.pr()["body"]
        self.assertIn("Did X", body.split(describe.OPEN)[1].split(describe.CLOSE)[0])

    def test_github_human_text_outside_the_block_survives(self):
        iid = self.deliver()
        self.g.e.review(iid)
        self.run_ticks()
        prs = self.g.state["prs"]
        prs["1"]["body"] = "my note\n" + describe.wrap("stale block") + "\nmy tail"
        self.g.set(prs=prs)
        i = self.g.e.issue(iid)
        backlog.update(self.g.e.p, iid, pr={**i["pr"], "described": ""})
        self.run_ticks()
        body = self.pr()["body"]
        self.assertTrue(body.startswith("my note\n") and body.endswith("\nmy tail"))
        self.assertIn("Did X", body)
        self.assertNotIn("stale block", body)

    def test_github_footer_has_the_commit(self):
        iid = self.deliver()
        self.g.e.review(iid)
        self.run_ticks()
        head = git(self.g.e.repo, "rev-parse", self.g.e.issue(iid)["branch"]).strip()
        self.assertIn(head[:9], self.pr()["body"])
        self.assertNotIn(head[:10], self.pr()["body"])
        self.assertIn("review round 1, open H 0 M 0", self.pr()["body"])

    def test_github_title_follows_a_renamed_task(self):
        iid = self.deliver()
        backlog.update(self.g.e.p, iid, title="New name")
        self.g.e.review(iid)
        self.run_ticks()
        self.assertEqual(self.pr()["title"], f"{iid}: New name")

    def test_github_described_once_per_head(self):
        iid = self.deliver()
        self.g.e.review(iid)
        self.run_ticks(4)
        self.assertEqual(len(self.g.calls("pr", "edit")), 1)

    def test_github_describe_retries_after_a_failure(self):
        iid = self.deliver()
        self.g.e.review(iid)
        self.g.set(fail_once=["pr", "edit"])
        self.run_ticks(4)
        self.assertEqual(self.pr().get("edits"), 1)
        self.assertEqual(self.g.e.issue(iid)["pr"]["described"], git(self.g.e.repo, "rev-parse", self.g.e.issue(iid)["branch"]).strip())

    def test_github_a_failing_description_does_not_hold_back_the_verdict_comment(self):
        iid = self.deliver()
        self.g.e.review(iid)
        self.g.set(fail_always=["pr", "edit"])
        self.run_ticks(3)
        self.assertEqual(len(self.pr()["reviews"]), 1)
        i = self.g.e.issue(iid)
        self.assertIn("HTTP 502", i["pr_error"])
        self.assertFalse(i["pr"].get("described"))


class Template(unittest.TestCase):
    def launched(self, e: Env) -> tuple[str, str]:
        iid = backlog.add(e.p, "x", status="ready", criterion="y")
        loop.tick(e.p)
        return iid, agents.report_file(e.p, iid).read_text()

    def test_a_launched_work_agent_finds_the_template_at_its_report(self):
        e = Env(test="true")
        iid, text = self.launched(e)
        self.assertEqual(text, (agents.BRIEFS / "report.md").read_text(encoding="utf-8").replace("ID", iid))

    def test_a_repo_pr_template_wins_and_review_rounds_is_added(self):
        e = Env(test="true")
        d = e.repo / ".github"
        d.mkdir()
        theirs = "## Their summary\n\n## Their checklist\n"
        (d / "pull_request_template.md").write_text(theirs)
        git(e.repo, "add", ".")
        git(e.repo, "commit", "-qm", "pr template")
        _, text = self.launched(e)
        self.assertTrue(text.startswith(theirs))
        self.assertTrue(text.rstrip().endswith("## Review rounds"))

    def test_an_existing_report_is_not_overwritten_on_relaunch(self):
        e = Env(test="true")
        iid, _ = self.launched(e)
        f = agents.report_file(e.p, iid)
        f.write_text("my work")
        agents.seed_report(e.p, iid, e.repo)
        self.assertEqual(f.read_text(), "my work")
