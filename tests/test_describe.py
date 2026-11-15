"""The report as the task's description: pure text rules, the PR on GitHub, and the panel."""

import pytest
from helpers import Env, git
from test_github import GH

from fm import agents, loop, pulls
from fm.core.review import describe
from fm.core.tasks import backlog


class TestText:
    def test_merge_body_replaces_the_block_and_keeps_outside_text(self):
        body = "note\n" + describe.wrap("old") + "\ntail"
        out = describe.merge_body(body, "new")
        assert out.startswith("note\n") and out.endswith("\ntail")
        assert "new" in out
        assert "old" not in out

    def test_merge_body_without_markers_appends(self):
        out = describe.merge_body("someone's text", "new")
        assert out.startswith("someone's text")
        assert describe.wrap("new") in out
        assert describe.merge_body("  \n", "new") == describe.wrap("new")

    def test_render_folds_review_rounds(self):
        out = describe.render("Body\n\n## Review rounds\nF1 fixed", "FOOT")
        assert out.index("Body") < out.index("FOOT")
        assert out.index("FOOT") < out.index("<details>")
        assert out.index("<details>") < out.index("F1 fixed")
        assert "<details>" not in describe.render("Body", "FOOT")

    def test_footer_and_review_line(self):
        f = describe.footer("0123456789abc", describe.review_line({"round": 1, "open": 0}), "t")
        assert "commit 012345678 " in f + " "
        assert "9a" not in f
        assert "review round 1, 0 open" in f
        assert describe.review_line(None) == "no review"

    def test_render_description_falls_back_to_the_goal(self):
        g = GH()
        try:
            tid = backlog.add(g.e.p, "T", goal="The goal", criteria="c")
            for rep in ({}, {"file": ""}, {"file": str(g.e.tmp / "nope.md")}):
                i = {**g.e.task(tid), "report": rep}
                assert "The goal" in pulls.description(g.e.p, i, "abcdef0123", "now")
            blank = g.e.tmp / "blank.md"
            blank.write_text("  \n")
            i = {**g.e.task(tid), "report": {"file": str(blank)}}
            assert "The goal" in pulls.description(g.e.p, i, "abcdef0123", "now")
        finally:
            g.close()


class TestOnGitHub:
    @pytest.fixture(autouse=True)
    def set_up(self, request):
        self.g = GH(test="true")
        request.addfinalizer(self.g.close)

    def deliver(self, text="Did X"):
        g = self.g
        tid = g.work()
        path = g.e.home / "reports" / f"{tid}.md"
        path.parent.mkdir(exist_ok=True)
        path.write_text(text)
        backlog.set_status(g.e.p, tid, "in review", report={"summary": "s", "confused": "", "file": str(path)})
        return tid

    def pr(self):
        return self.g.state["prs"]["1"]

    def run_ticks(self, n=2):
        for _ in range(n):
            loop.tick(self.g.e.p)

    def test_github_pr_body_is_the_report(self):
        tid = self.deliver()
        self.g.e.review(tid)
        self.run_ticks()
        body = self.pr()["body"]
        assert "Did X" in body.split(describe.OPEN)[1].split(describe.CLOSE)[0]

    def test_github_human_text_outside_the_block_survives(self):
        tid = self.deliver()
        self.g.e.review(tid)
        self.run_ticks()
        prs = self.g.state["prs"]
        prs["1"]["body"] = "my note\n" + describe.wrap("stale block") + "\nmy tail"
        self.g.set(prs=prs)
        i = self.g.e.task(tid)
        backlog.update(self.g.e.p, tid, pr={**i["pr"], "described": ""})
        self.run_ticks()
        body = self.pr()["body"]
        assert body.startswith("my note\n") and body.endswith("\nmy tail")
        assert "Did X" in body
        assert "stale block" not in body

    def test_github_footer_has_the_commit(self):
        tid = self.deliver()
        self.g.e.review(tid)
        self.run_ticks()
        head = git(self.g.e.repo, "rev-parse", self.g.e.task(tid)["branch"]).strip()
        assert head[:9] in self.pr()["body"]
        assert head[:10] not in self.pr()["body"]
        assert "review round 1, 0 open" in self.pr()["body"]

    def test_github_title_follows_a_renamed_task(self):
        tid = self.deliver()
        backlog.update(self.g.e.p, tid, title="New name")
        self.g.e.review(tid)
        self.run_ticks()
        assert self.pr()["title"] == f"{tid}: New name"

    def test_github_described_once_per_head(self):
        tid = self.deliver()
        self.g.e.review(tid)
        self.run_ticks(4)
        assert len(self.g.calls("pr", "edit")) == 1

    def test_github_describe_retries_after_a_failure(self):
        tid = self.deliver()
        self.g.e.review(tid)
        self.g.set(fail_once=["pr", "edit"])
        self.run_ticks(4)
        assert self.pr().get("edits") == 1
        assert self.g.e.task(tid)["pr"]["described"] == git(self.g.e.repo, "rev-parse", self.g.e.task(tid)["branch"]).strip()

    def test_github_a_failing_description_does_not_hold_back_the_verdict_comment(self):
        tid = self.deliver()
        self.g.e.review(tid)
        self.g.set(fail_always=["pr", "edit"])
        self.run_ticks(3)
        assert len(self.pr()["reviews"]) == 1
        i = self.g.e.task(tid)
        assert "HTTP 502" in i["pr_error"]
        assert not i["pr"].get("described")


class TestTemplate:
    def launched(self, e: Env) -> tuple[str, str]:
        tid = backlog.add(e.p, "x", status="ready", criteria="y")
        loop.tick(e.p)
        return tid, agents.report_file(e.p, tid).read_text()

    def test_a_launched_work_agent_finds_the_template_at_its_report(self):
        e = Env(test="true")
        tid, text = self.launched(e)
        assert text == (agents.BRIEFS / "report.md").read_text(encoding="utf-8").replace("ID", tid)

    def test_a_repo_pr_template_wins_and_review_rounds_is_added(self):
        e = Env(test="true")
        d = e.repo / ".github"
        d.mkdir()
        theirs = "## Their summary\n\n## Their checklist\n"
        (d / "pull_request_template.md").write_text(theirs)
        git(e.repo, "add", ".")
        git(e.repo, "commit", "-qm", "pr template")
        _, text = self.launched(e)
        assert text.startswith(theirs)
        assert text.rstrip().endswith("## Review rounds")

    def test_an_existing_report_is_not_overwritten_on_relaunch(self):
        e = Env(test="true")
        tid, _ = self.launched(e)
        f = agents.report_file(e.p, tid)
        f.write_text("my work")
        agents.seed_report(e.p, tid, e.repo)
        assert f.read_text() == "my work"
