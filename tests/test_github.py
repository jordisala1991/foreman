"""The GitHub provider, with a fake `gh` on PATH (it keeps its PRs in gh.json and logs each call) and a bare repo as
origin. The provider is forced with `provider = "github"` because the origin is a local path."""

import json
import os
import subprocess
import textwrap
import unittest

from helpers import Env, fm, git

from fm import backlog, integrate, loop, providers, util
from fm.providers import github, registry

FAKE_GH = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json, os, re, sys, time
    d = os.path.dirname(os.path.abspath(__file__))
    a = sys.argv[1:]
    f = os.path.join(d, "gh.json")
    s = json.load(open(f)) if os.path.exists(f) else {"prs": {}, "ci": "pass", "lag": False}
    open(os.path.join(d, "gh.log"), "a").write(json.dumps(a) + "\\n")
    def flag(n):
        return a[a.index(n) + 1] if n in a else None
    def save():
        json.dump(s, open(f, "w"))
    if s.get("fail_always") == a[:2]:
        print("HTTP 502", file=sys.stderr)
        sys.exit(1)
    if s.get("fail_once") == a[:2]:
        s["fail_once"] = None
        save()
        print("HTTP 502", file=sys.stderr)
        sys.exit(1)
    if a[:2] == ["pr", "create"] and flag("--head") == s.get("reject"):
        s["reject"] = None  # GitHub says this once, right after a push, then it works
        save()
        print("GraphQL: Head sha can't be blank, Base sha can't be blank", file=sys.stderr)
        sys.exit(1)
    if a[:2] == ["pr", "create"] and flag("--head") == s.get("flaky"):
        print("HTTP 502", file=sys.stderr)
        sys.exit(1)
    if a[:2] == ["repo", "view"]:
        print("https://github.com/o/r")
        sys.exit(0)
    if a[:2] == ["api", "graphql"]:
        kv = dict(x.split("=", 1) for x in a if "=" in x and not x.startswith("-"))
        q = kv["query"]
        pr = next(iter(s["prs"].values()))
        threads = pr.setdefault("threads", [])
        if "resolveReviewThread" in q or "addPullRequestReviewThreadReply" in q:
            if "resolveReviewThread" in q and s.get("reject_resolve"):
                print("HTTP 403", file=sys.stderr)
                sys.exit(1)
            t = next(t for t in threads if t["id"] == kv["id"])
            if "resolveReviewThread" in q:
                t["resolved"] = True
            else:
                t["bodies"].append(kv["body"])
            save()
            print("{}")
        else:
            for t in threads:
                print(json.dumps(t))
        sys.exit(0)
    if a[:1] == ["api"]:
        url = next(x for x in a if x.startswith("repos/"))
        parts = url.split("/")
        tail = parts[5:]
        if parts[4].isdigit() and tail[:1] == ["reviews"] and len(tail) == 3:
            bodies = s["prs"][parts[4]].get("review_comments", [])
            for n, b in enumerate(bodies, 1):
                print(n, *re.findall(r"<!-- fm:[a-z0-9-]+ -->", b))
            sys.exit(0)
        if parts[4].isdigit() and tail[:1] == ["reviews"] and len(tail) == 2:
            if s.get("fail_put"):
                print("HTTP 404", file=sys.stderr)
                sys.exit(1)
            pr = s["prs"][parts[4]]
            pr["reviews"][int(tail[1]) - 1]["body"] = json.load(sys.stdin)["body"]
            save()
            print("{}")
            sys.exit(0)
        if parts[4].isdigit() and tail in (["reviews"], ["comments"]):
            pr = s["prs"][parts[4]]
            if "-X" in a:
                body = json.load(sys.stdin)
                if s.get("reject_inline") and body["comments"]:
                    print("HTTP 422", file=sys.stderr)
                    sys.exit(1)
                pr.setdefault("reviews", []).append(body)
                for c in body["comments"]:
                    pr.setdefault("review_comments", []).append(c["body"])
                    pr.setdefault("threads", []).append({"id": "T" + str(len(pr.get("threads", [])) + 1), "resolved": False, "bodies": [c["body"]]})
                save()
                print(json.dumps({"id": len(pr["reviews"])}))
            else:
                bodies = [r["body"] or "" for r in pr.get("reviews", [])] if tail == ["reviews"] else pr.get("review_comments", [])
                for b in bodies:
                    for m in re.findall(r"<!-- fm:[a-z0-9-]+ -->", b):
                        print(m)
            sys.exit(0)
        print(s.get("api_sha") or "null")
        sys.exit(0)
    if a[:2] == ["pr", "create"]:
        n = str(len(s["prs"]) + 1)
        s["prs"][n] = {"draft": "--draft" in a, "state": "OPEN", "comments": [], "head": flag("--head"), "title": flag("--title"), "body": flag("--body")}
        save()
        print("https://github.com/o/r/pull/" + n)
        sys.exit(0)
    if s.get("refuse_merge") and a[:2] == ["pr", "merge"]:
        print(s["refuse_merge"], file=sys.stderr)
        sys.exit(1)
    pr = s["prs"][a[2]]
    if a[:2] == ["pr", "ready"]:
        pr["draft"] = False
    elif a[:2] == ["pr", "edit"]:
        pr["title"] = flag("--title")
        pr["body"] = flag("--body")
        pr["edits"] = pr.get("edits", 0) + 1
    elif a[:2] == ["pr", "view"] and "body" in a:
        print(json.dumps({"body": pr["body"]}))
    elif a[:2] == ["pr", "diff"]:
        print(s.get("diff", ""))
    elif a[:2] == ["pr", "comment"]:
        pr["comments"].append(flag("--body"))
    elif a[:2] == ["pr", "merge"]:
        pr["strategy"] = a[3].lstrip("-")
        pr["subject"] = flag("--subject")
        if not s["lag"]:
            pr["state"] = "MERGED"
    elif a[:2] == ["pr", "checks"]:
        if s["ci"] == "none":
            print("no checks reported", file=sys.stderr)
            sys.exit(1)
        print(json.dumps([{"bucket": s["ci"]}]))
        sys.exit(8 if s["ci"] == "pending" else 0)
    elif a[:2] == ["pr", "view"] and s.get("bad_view"):
        print(json.dumps({"state": "SIDEWAYS", "number": "one"}))
    elif a[:2] == ["pr", "view"]:
        mc = {"oid": "c0ffee"} if pr["state"] == "MERGED" and not s.get("no_oid") else None
        print(json.dumps({"number": int(a[2]), "url": "https://github.com/o/r/pull/" + a[2], "isDraft": pr["draft"], "state": pr["state"], "mergeCommit": mc}))
    save()
""")


class GH:
    """A GitHub project: origin is a bare repo, `gh` is the fake."""

    def __init__(self, extra: str = "", **kw) -> None:
        self.e = Env(**kw)
        e = self.e
        self.bare = e.tmp / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(self.bare)], check=True)
        git(e.repo, "remote", "add", "origin", str(self.bare))
        git(e.repo, "push", "-q", "origin", "main")
        (e.bin / "gh").write_text(FAKE_GH)
        (e.bin / "gh").chmod(0o755)
        self.path = os.environ["PATH"]
        os.environ["PATH"] = f"{e.bin}:{self.path}"
        toml = e.home / "fm.toml"
        toml.write_text(toml.read_text().replace("[integrate]", f'[integrate]\nprovider = "github"\n{extra}'))
        e.p = type(e.p).load(e.home)

    def close(self) -> None:
        os.environ["PATH"] = self.path

    @property
    def state(self) -> dict:
        return json.loads((self.e.bin / "gh.json").read_text()) if (self.e.bin / "gh.json").exists() else {"prs": {}}

    def set(self, **kw) -> None:
        s = {"prs": {}, "ci": "pass", "lag": False, **self.state, **kw}
        (self.e.bin / "gh.json").write_text(json.dumps(s))

    def calls(self, *prefix: str) -> list[list[str]]:
        f = self.e.bin / "gh.log"
        rows = [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []
        return [r for r in rows if r[: len(prefix)] == list(prefix)]

    def work(self, push: bool = True) -> str:
        """A running task with one commit, pushed or not."""
        e = self.e
        iid = backlog.add(e.p, "x", status="ready", criterion="y")
        loop.tick(e.p)
        wt = e.repo.parent / e.issue(iid)["worktree"]
        with open(wt / "app.py", "a") as f:
            f.write("# change\n")
        git(wt, "commit", "-qam", "change")
        if push:
            git(wt, "push", "-q", "origin", e.issue(iid)["branch"])
        return iid

    def deliver(self, iid: str) -> None:
        backlog.set_status(self.e.p, iid, "in review", report={"summary": "s", "confused": ""})
        self.e.review(iid)


class Provider(unittest.TestCase):
    def test_provider_is_detected_from_origin_and_can_be_overridden(self):
        e = Env()
        self.assertEqual(registry.get(e.p).name, "local")
        git(e.repo, "remote", "add", "origin", "git@github.com:o/r.git")
        self.assertEqual(registry.get(e.p).name, "github")
        git(e.repo, "remote", "set-url", "origin", "https://gitlab.com/o/r.git")
        self.assertEqual(registry.get(e.p).name, "local")
        git(e.repo, "remote", "set-url", "origin", "https://github.com/o/r.git")
        e.p.cfg["integrate"]["provider"] = "local"
        self.assertEqual(registry.get(e.p).name, "local")

    def test_local_has_no_rebase_github_has(self):
        e = Env()
        e.p.cfg["integrate"]["merge"] = "rebase"
        with self.assertRaises(providers.ConfigError):
            registry.get(e.p)
        e.p.cfg["integrate"]["provider"] = "github"
        self.assertEqual(registry.get(e.p).name, "github")


class Border(unittest.TestCase):
    """What gh says is turned into fm's own types at the border; a shape that does not fit is a clear ProviderError."""

    GOOD = {"number": 7, "url": "https://github.com/o/r/pull/7", "isDraft": True, "state": "OPEN", "mergeCommit": None}

    def test_a_pull_request_becomes_a_typed_value(self):
        pr = github.pull_request(self.GOOD)
        self.assertEqual((pr.number, pr.url, pr.draft, pr.state, pr.merge_commit), (7, "https://github.com/o/r/pull/7", True, "OPEN", ""))
        merged = github.pull_request({**self.GOOD, "state": "MERGED", "mergeCommit": {"oid": "abc"}})
        self.assertEqual((merged.state, merged.merge_commit), ("MERGED", "abc"))

    def test_a_pull_request_of_the_wrong_shape_is_a_provider_error(self):
        for bad in (
            [],
            "x",
            {**self.GOOD, "state": "SIDEWAYS"},
            {**self.GOOD, "number": "7"},
            {**self.GOOD, "number": True},
            {**self.GOOD, "isDraft": "yes"},
            {**self.GOOD, "mergeCommit": "abc"},
            {**self.GOOD, "mergeCommit": {"oid": 1}},
            {k: v for k, v in self.GOOD.items() if k != "url"},
        ):
            with self.assertRaises(providers.ProviderError, msg=repr(bad)):
                github.pull_request(bad)

    def test_checks_become_buckets_or_a_provider_error(self):
        self.assertEqual(github.buckets([{"bucket": "pass"}, {"bucket": "pending"}]), {"pass", "pending"})
        for bad in ({}, [{"bucket": "weird"}], [{"state": "x"}], ["pass"]):
            with self.assertRaises(providers.ProviderError, msg=repr(bad)):
                github.buckets(bad)

    def test_gh_answering_a_bad_shape_reaches_the_caller_as_a_provider_error(self):
        g = GH()
        self.addCleanup(g.close)
        g.set(prs={"1": {"draft": False, "state": "OPEN", "comments": []}}, bad_view=True)
        with self.assertRaises(providers.ProviderError):
            registry.get(g.e.p).state({"pr": 1})


class Pulls(unittest.TestCase):
    def make(self, extra: str = "", **kw) -> GH:
        g = GH(extra, **kw)
        self.addCleanup(g.close)
        return g

    def origin_tip(self, g: GH, iid: str) -> str:
        ref = "refs/heads/" + g.e.issue(iid)["branch"]
        r = subprocess.run(["git", "--git-dir", str(g.bare), "rev-parse", "--verify", "-q", ref], capture_output=True, text=True)
        return r.stdout.strip()

    def local_tip(self, g: GH, iid: str) -> str:
        return subprocess.run(["git", "-C", str(g.e.repo), "rev-parse", g.e.issue(iid)["branch"]], capture_output=True, text=True).stdout.strip()

    def test_the_loop_pushes_a_running_tasks_commits_and_opens_its_draft(self):
        g = self.make(test="true")
        iid = g.work(push=False)
        self.assertEqual(self.origin_tip(g, iid), "")
        loop.tick(g.e.p)
        loop.tick(g.e.p)
        self.assertEqual(self.origin_tip(g, iid), self.local_tip(g, iid))
        wt = g.e.repo.parent / g.e.issue(iid)["worktree"]
        with open(wt / "app.py", "a") as f:
            f.write("# second\n")
        git(wt, "commit", "-qam", "second")
        loop.tick(g.e.p)
        self.assertEqual(self.origin_tip(g, iid), self.local_tip(g, iid))
        self.assertEqual(len(g.calls("pr", "create")), 1)  # once
        self.assertIn("--draft", g.calls("pr", "create")[0])
        self.assertEqual(g.e.issue(iid)["pr"]["number"], 1)

    def test_nothing_is_pushed_before_the_task_has_commits_and_main_never(self):
        g = self.make(test="true")
        iid = backlog.add(g.e.p, "x", status="ready", criterion="y")
        loop.tick(g.e.p)
        loop.tick(g.e.p)
        self.assertEqual(self.origin_tip(g, iid), "")
        self.assertEqual(g.calls("pr", "create"), [])

    def test_pr_done_pushes_early_but_opens_the_pr_only_at_fm_done(self):
        g = self.make('pr = "done"', test="true")
        iid = g.work(push=False)
        loop.tick(g.e.p)
        self.assertEqual(self.origin_tip(g, iid), self.local_tip(g, iid))
        self.assertEqual(g.calls("pr", "create"), [])

    def test_pr_done_opens_nothing_until_fm_done_and_then_a_ready_pr(self):
        g = self.make('pr = "done"', test="true")
        iid = g.work()
        loop.tick(g.e.p)
        self.assertEqual(g.calls("pr", "create"), [])
        g.deliver(iid)
        loop.tick(g.e.p)
        (create,) = g.calls("pr", "create")
        self.assertNotIn("--draft", create)

    def test_fm_done_readies_the_draft_and_reviews_the_verdict_once(self):
        g = self.make(test="true")
        iid = g.work()
        loop.tick(g.e.p)
        self.assertTrue(g.state["prs"]["1"]["draft"])
        g.deliver(iid)
        loop.tick(g.e.p)
        loop.tick(g.e.p)
        pr = g.state["prs"]["1"]
        self.assertFalse(pr["draft"])
        self.assertEqual(pr["comments"], [])
        self.assertEqual(len(pr["reviews"]), 1)
        self.assertEqual(pr["reviews"][0]["event"], "COMMENT")
        self.assertIn("Why low", pr["reviews"][0]["body"])

    def merged(self, strategy: str | None) -> tuple[GH, str]:
        g = self.make(f'merge = "{strategy}"' if strategy else "", test="true")
        iid = g.work()
        g.deliver(iid)
        loop.tick(g.e.p)
        integrate.accept(g.e.p, iid)
        return g, iid

    def test_the_configured_merge_type_is_used(self):
        for strategy, used in ((None, "squash"), ("merge", "merge"), ("rebase", "rebase")):
            g, iid = self.merged(strategy)
            loop.tick(g.e.p)
            self.assertEqual(g.state["prs"]["1"]["strategy"], used)
            self.assertEqual(g.e.issue(iid)["status"], "done")
            self.assertEqual(g.e.issue(iid)["commit"], "c0ffee")
            self.assertEqual(g.calls("pr", "merge")[0][:3], ["pr", "merge", "1"])

    def main_changes_app(self, g: GH) -> None:
        (g.e.repo / "app.py").write_text("changed on main\n")
        git(g.e.repo, "commit", "-qam", "main moved")

    def test_a_pr_refused_for_conflicts_goes_back_as_a_conflict(self):
        g, iid = self.merged(None)
        self.main_changes_app(g)
        g.set(refuse_merge="Pull Request has merge conflicts")
        loop.tick(g.e.p)
        i = g.e.issue(iid)
        self.assertEqual(i["status"], "in progress")
        self.assertEqual(i["conflicts"], 1)
        self.assertEqual(backlog.load(g.e.p)["decisions_resolved"][-1]["kind"], "conflict")
        self.assertFalse(i.get("blocked"))
        self.assertNotIn("accepted", i)
        self.assertEqual(len(g.calls("pr", "merge")), 1)
        self.assertEqual(git(g.e.repo, "status", "--porcelain", "--untracked-files=no").strip(), "")

    def test_a_pr_refused_for_another_reason_still_blocks(self):
        g, iid = self.merged(None)
        g.set(refuse_merge="Pull request is closed")
        loop.tick(g.e.p)
        i = g.e.issue(iid)
        self.assertTrue(i["blocked"])
        self.assertEqual(i["status"], "in review")
        self.assertNotIn("conflicts", i)

    def test_a_refusal_the_local_check_cannot_reproduce_stays_blocked(self):
        g, iid = self.merged(None)
        g.set(refuse_merge="Pull Request has merge conflicts")
        loop.tick(g.e.p)
        i = g.e.issue(iid)
        self.assertTrue(i["blocked"])
        self.assertEqual(i["status"], "in review")
        self.assertNotIn("conflicts", i)

    def test_only_merge_conflict_text_is_a_conflict(self):
        self.assertIsInstance(github.failure("gh", "Pull Request has merge conflicts"), providers.Conflict)
        for text in ("pull request is closed", "Pull request #3 is not mergeable", "error on branch fix-conflict-ui"):
            self.assertNotIsInstance(github.failure("gh", text), providers.Conflict)

    def test_the_task_closes_only_once_the_pr_is_seen_merged(self):
        g, iid = self.merged(None)
        g.set(lag=True)
        loop.tick(g.e.p)
        self.assertEqual(g.e.issue(iid)["status"], "in review")
        loop.tick(g.e.p)  # not merged twice
        self.assertEqual(len(g.calls("pr", "merge")), 1)
        s = g.state
        s["prs"]["1"]["state"] = "MERGED"
        g.set(prs=s["prs"])
        loop.tick(g.e.p)
        self.assertEqual(g.e.issue(iid)["status"], "done")

    def test_a_pr_closed_unmerged_blocks_the_task(self):
        g, iid = self.merged(None)
        g.set(lag=True)
        loop.tick(g.e.p)
        s = g.state
        s["prs"]["1"]["state"] = "CLOSED"
        g.set(prs=s["prs"])
        loop.tick(g.e.p)
        self.assertIn("closed without merging", g.e.issue(iid)["blocked"])

    def test_failing_ci_is_high_risk(self):
        g = self.make(test="true", autonomy="medium")
        g.set(ci="fail")
        iid = g.work()
        g.deliver(iid)
        loop.tick(g.e.p)
        c = g.e.issue(iid)["check"]
        self.assertEqual((c["risk"], c["ci"]), ("high", "fail"))
        self.assertIn("CI fails [ci]", c["reasons"])
        self.assertEqual(g.e.issue(iid)["status"], "in review")

    def test_ci_replaces_the_local_test_without_a_test_command(self):
        for ci, tests in (("pass", "pass"), ("fail", "fail"), ("none", "none")):
            g = self.make()
            g.set(ci=ci)
            iid = g.work()
            g.deliver(iid)
            loop.tick(g.e.p)
            self.assertEqual(g.e.issue(iid)["check"]["tests"], tests)

    def test_pending_ci_waits_for_the_result(self):
        g = self.make(test="true")
        g.set(ci="pending")
        iid = g.work()
        g.deliver(iid)
        loop.tick(g.e.p)
        self.assertNotIn("check", g.e.issue(iid))
        g.set(ci="pass")
        loop.tick(g.e.p)
        self.assertEqual(g.e.issue(iid)["check"]["ci"], "pass")

    def test_autonomy_merges_only_with_green_ci(self):
        for ci, status in (("none", "in review"), ("pending", "in review"), ("fail", "in review"), ("pass", "done")):
            g = self.make(test="true", autonomy="medium")
            g.set(ci=ci)
            iid = g.work()
            g.deliver(iid)
            loop.tick(g.e.p)
            loop.tick(g.e.p)
            self.assertEqual(g.e.issue(iid)["status"], status, ci)

    def test_ci_is_read_again_at_every_decision_not_cached_with_the_check(self):
        g = self.make(test="true", autonomy="medium")
        g.set(ci="none")
        iid = g.work()
        g.deliver(iid)
        loop.tick(g.e.p)
        self.assertEqual((g.e.issue(iid)["status"], g.e.issue(iid)["check"]["ci"]), ("in review", "none"))
        g.set(ci="pass")  # the CI registered later: no new push needed
        loop.tick(g.e.p)
        self.assertEqual(g.e.issue(iid)["status"], "done")

    def test_ci_turning_red_on_the_same_head_stops_autonomy(self):
        g = self.make(test="true", autonomy="manual")
        iid = g.work()
        g.deliver(iid)
        loop.tick(g.e.p)
        self.assertEqual(g.e.issue(iid)["check"]["ci"], "pass")  # cached, head and main unchanged
        g.set(ci="fail")  # a CI re-run failed on the same head
        g.e.p.cfg["integrate"]["autonomy"] = "medium"
        loop.tick(g.e.p)
        self.assertEqual((g.e.issue(iid)["check"]["ci"], g.e.issue(iid)["status"]), ("fail", "in review"))
        self.assertEqual(g.calls("pr", "merge"), [])

    def test_a_transient_gh_failure_in_the_queue_retries_instead_of_blocking(self):
        for failing in (["pr", "checks"], ["pr", "merge"], ["pr", "view"]):
            g = self.make(test="true", autonomy="medium")
            iid = g.work()
            g.deliver(iid)
            g.set(fail_once=failing)
            loop.tick(g.e.p)
            loop.tick(g.e.p)
            loop.tick(g.e.p)
            i = g.e.issue(iid)
            self.assertFalse(i.get("blocked"), failing)
            self.assertEqual(i["status"], "done", failing)

    def test_one_tasks_failure_never_blocks_it_nor_stops_the_next_task_getting_its_pr(self):
        # "Head sha can't be blank" (once, then fine) and a 502 are both retried; neither blocks the task
        for flag, text in (({"reject": "fm/ty-1"}, "Head sha"), ({"flaky": "fm/ty-1"}, "HTTP 502")):
            g = self.make(test="true")
            g.set(**flag)
            first, second = g.work(), g.work()  # each work() ticks: the first one fails there, once
            events = [e["text"] for e in util.read_jsonl(g.e.p.state / "events.jsonl") if "could not push it or open its PR" in e["text"]]
            self.assertEqual(len(events), 1)
            self.assertIn(text, events[0])
            self.assertFalse(g.e.issue(first).get("blocked"))
            g.set(flaky="")
            loop.tick(g.e.p)
            self.assertIn("pr", g.e.issue(second))  # the failure did not stop the next task
            self.assertIn("pr", g.e.issue(first))
            self.assertEqual(g.e.issue(first)["retry_error"], "")

    def test_only_known_permanent_errors_are_permanent_and_unblock_clears_the_errors(self):
        from fm.providers import github

        cases = (
            ("Head sha can't be blank", github.TransientError),
            ("something new", github.TransientError),
            ("pull request is closed", github.PermanentError),
            ("merge conflict", github.PermanentError),
            ("Pull request #3 is not mergeable", github.PermanentError),
            ("the pull request cannot be merged", github.PermanentError),
            ("read tcp: use of closed network connection", github.TransientError),
            ("connection closed by remote host", github.TransientError),
            ("error on branch fix-conflict-ui", github.TransientError),
        )
        for text, kind in cases:
            self.assertIsInstance(github.failure("gh", text), kind, text)
        g = self.make(test="true")
        iid = g.work()
        backlog.update(g.e.p, iid, blocked="x", pr_error="a", retry_error="b")
        fm(g.e.home, "unblock", iid)
        i = g.e.issue(iid)
        self.assertEqual((i["blocked"], i["pr_error"], i["retry_error"]), ("", "", ""))

    def test_a_merged_pr_without_a_merge_commit_is_read_from_the_api_or_fails_clearly(self):
        g, iid = self.merged(None)
        g.set(no_oid=True)
        loop.tick(g.e.p)
        self.assertEqual(g.e.issue(iid)["status"], "in review")  # no sha anywhere: retried, no invented reference
        self.assertNotIn("merge", g.e.issue(iid))
        self.assertIn("no merge commit", g.e.issue(iid)["retry_error"])
        g.set(api_sha="feed01")
        loop.tick(g.e.p)
        self.assertEqual((g.e.issue(iid)["status"], g.e.issue(iid)["commit"]), ("done", "feed01"))

    def test_the_provider_never_tags_releases_or_forces(self):
        import inspect

        from fm.providers import github

        src = inspect.getsource(github)
        for word in ('"tag"', '"release"', "--force", "--tags", "+refs"):
            self.assertNotIn(word, src)


if __name__ == "__main__":
    unittest.main()
