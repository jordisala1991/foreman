"""The GitHub provider, with a fake `gh` on PATH (it keeps its PRs in gh.json and logs each call) and a bare repo as
origin. The provider is forced with `provider = "github"` because the origin is a local path."""

import json
import os
import subprocess
import textwrap
from dataclasses import replace

import pytest
from helpers import Env, fm, git

from foreman import providers, util
from foreman.core.rules import decide
from foreman.core.tasks import backlog
from foreman.loop import tick
from foreman.providers import registry
from foreman.providers.github import provider, types
from foreman.services.delivery import merge
from foreman.ui.cli import main

FAKE_GH = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json, os, re, sys
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
        print("o/r" if "nameWithOwner" in a else "https://github.com/o/r")
        sys.exit(0)
    if a[:2] == ["api", "graphql"]:
        import subprocess
        def node(n, pr):
            sha = subprocess.run(["git", "ls-remote", "origin", "refs/heads/" + pr["head"]], capture_output=True, text=True).stdout.split()[:1]
            runs = [{"__typename": "CheckRun", "name": c, "conclusion": "FAILURE", "detailsUrl": u} for c, u in s.get("failed", [])]
            runs.append({"__typename": "CheckRun", "name": "build", "conclusion": "SUCCESS", "detailsUrl": "https://ci/build"})
            roll = None if s["ci"] == "none" else {"state": {"pass": "SUCCESS", "fail": "FAILURE", "pending": "PENDING"}[s["ci"]], "contexts": {"nodes": runs}}
            return {"number": int(n), "headRefOid": sha[0] if sha else "", "commits": {"nodes": [{"commit": {"statusCheckRollup": roll}}]}}
        one = [x for x in a if x.startswith("number=")]
        if one:  # a PR on its own, open or not
            print(json.dumps({"data": {"repository": {"pullRequest": node(one[0][7:], s["prs"][one[0][7:]])}}}))
        else:
            nodes = [node(n, pr) for n, pr in s["prs"].items() if pr["state"] == "OPEN" and n not in s.get("hidden", [])]
            print(json.dumps({"data": {"repository": {"pullRequests": {"nodes": nodes}}}}))
        sys.exit(0)
    if a[:1] == ["api"]:
        url = next(x for x in a if x.startswith("repos/"))
        parts = url.split("/")
        tail = parts[5:]
        if parts[4].isdigit() and tail[:1] == ["reviews"] and len(tail) == 3:
            bodies = s["prs"][parts[4]].get("review_comments", [])
            for n, b in enumerate(bodies, 1):
                print(n, *re.findall(r"<!-- foreman:[a-z0-9-]+ -->", b))
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
                save()
                print(json.dumps({"id": len(pr["reviews"])}))
            else:
                bodies = [r["body"] or "" for r in pr.get("reviews", [])] if tail == ["reviews"] else pr.get("review_comments", [])
                for b in bodies:
                    for m in re.findall(r"<!-- foreman:[a-z0-9-]+ -->", b):
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
    if a[:2] == ["pr", "merge"] and pr["draft"]:
        print("Pull Request is still a draft", file=sys.stderr)
        sys.exit(1)
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
    elif a[:2] == ["pr", "close"]:
        pr["state"] = "CLOSED"
        pr["comments"].append(flag("--comment"))
    elif a[:2] == ["pr", "merge"]:
        pr["strategy"] = a[3].lstrip("-")
        pr["subject"] = flag("--subject")
        if not s["lag"]:
            pr["state"] = "MERGED"
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
        toml = e.home / "foreman.toml"
        toml.write_text(toml.read_text().replace("[merge]", f'[merge]\nprovider = "github"\n{extra}'))
        e.p = type(e.p).load(e.home)
        self.poll, provider.POLL = provider.POLL, 0  # a pending answer is read again at every call, unless a test says otherwise

    def close(self) -> None:
        os.environ["PATH"] = self.path
        provider.POLL = self.poll

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

    def seed(self, n: int = 1, branch: str = "foreman/ty-1", **pr) -> providers.Change:  # the branch pushed and PR `n` open: no task, no tick
        git(self.e.repo, "push", "-q", "origin", f"main:refs/heads/{branch}")
        fields = {"draft": False, "state": "OPEN", "comments": [], "head": branch, "title": "x", "body": "", **pr}
        self.set(prs={**self.state["prs"], str(n): fields})
        return providers.Change(id=f"TY-{n}", title="x", branch=branch, head=git(self.e.repo, "rev-parse", "main").strip(), pr=n)

    def work(self, push: bool = True) -> str:
        """A running task with one commit, pushed or not."""
        e = self.e
        tid = backlog.add(e.p, "x", status="ready", criteria="y")
        tick.tick(e.p)
        wt = e.repo.parent / e.task(tid)["worktree"]
        with open(wt / "app.py", "a") as f:
            f.write("# change\n")
        git(wt, "commit", "-qam", "change")
        if push:
            git(wt, "push", "-q", "origin", e.task(tid)["branch"])
        return tid

    def deliver(self, tid: str) -> None:
        backlog.set_status(self.e.p, tid, "in review", report={"summary": "s", "confused": ""})
        self.e.review(tid)


class TestProvider:
    def test_provider_is_detected_from_origin_and_can_be_overridden(self):
        e = Env()
        assert registry.get(e.p).name == "local"
        git(e.repo, "remote", "add", "origin", "git@github.com:o/r.git")
        assert registry.get(e.p).name == "github"
        git(e.repo, "remote", "set-url", "origin", "https://gitlab.com/o/r.git")
        assert registry.get(e.p).name == "local"
        git(e.repo, "remote", "set-url", "origin", "https://github.com/o/r.git")
        e.p.cfg["merge"]["provider"] = "local"
        assert registry.get(e.p).name == "local"

    def test_local_has_no_rebase_github_has(self):
        e = Env()
        e.p.cfg["merge"]["strategy"] = "rebase"
        with pytest.raises(providers.ConfigError):
            registry.get(e.p)
        e.p.cfg["merge"]["provider"] = "github"
        assert registry.get(e.p).name == "github"


class TestPulls:
    @pytest.fixture(autouse=True)
    def keep_request(self, request):
        self.request = request  # for the cleanups of helpers

    def make(self, extra: str = "", **kw) -> GH:
        g = GH(extra, **kw)
        self.request.addfinalizer(g.close)
        return g

    def origin_tip(self, g: GH, tid: str) -> str:
        ref = "refs/heads/" + g.e.task(tid)["branch"]
        r = subprocess.run(["git", "--git-dir", str(g.bare), "rev-parse", "--verify", "-q", ref], capture_output=True, text=True)
        return r.stdout.strip()

    def local_tip(self, g: GH, tid: str) -> str:
        return subprocess.run(["git", "-C", str(g.e.repo), "rev-parse", g.e.task(tid)["branch"]], capture_output=True, text=True).stdout.strip()

    def test_the_loop_pushes_a_running_tasks_commits_opens_its_draft_and_fm_done_readies_it(self):
        g = self.make(test="true")
        tid = g.work(push=False)
        assert self.origin_tip(g, tid) == ""
        tick.tick(g.e.p)
        tick.tick(g.e.p)
        assert self.origin_tip(g, tid) == self.local_tip(g, tid)
        wt = g.e.repo.parent / g.e.task(tid)["worktree"]
        with open(wt / "app.py", "a") as f:
            f.write("# second\n")
        git(wt, "commit", "-qam", "second")
        tick.tick(g.e.p)
        assert self.origin_tip(g, tid) == self.local_tip(g, tid)
        assert len(g.calls("pr", "create")) == 1  # once
        assert "--draft" in g.calls("pr", "create")[0]
        assert g.e.task(tid)["pr"]["number"] == 1
        assert g.state["prs"]["1"]["draft"]
        g.deliver(tid)
        tick.tick(g.e.p)
        tick.tick(g.e.p)
        pr = g.state["prs"]["1"]
        assert not pr["draft"]
        assert pr["comments"] == []
        assert len(pr["reviews"]) == 1
        assert pr["reviews"][0]["event"] == "COMMENT"
        assert "Why low" in pr["reviews"][0]["body"]

    def test_nothing_is_pushed_before_the_task_has_commits_and_main_never(self):
        g = self.make(test="true")
        tid = backlog.add(g.e.p, "x", status="ready", criteria="y")
        tick.tick(g.e.p)
        tick.tick(g.e.p)
        assert self.origin_tip(g, tid) == ""
        assert g.calls("pr", "create") == []

    def test_pr_done_pushes_early_but_opens_the_pr_only_at_fm_done(self):
        g = self.make('pr = "done"', test="true")
        tid = g.work(push=False)
        tick.tick(g.e.p)
        assert self.origin_tip(g, tid) == self.local_tip(g, tid)
        assert g.calls("pr", "create") == []
        g.deliver(tid)
        tick.tick(g.e.p)
        (create,) = g.calls("pr", "create")
        assert "--draft" not in create

    def test_cancel_closes_the_pr_with_the_reason_and_a_task_without_one_is_canceled_as_before(self):
        g = self.make(test="true")
        tid = g.work()
        tick.tick(g.e.p)
        fm(g.e.home, "cancel", tid, "not needed")
        pr = g.state["prs"]["1"]
        assert pr["state"] == "CLOSED"
        assert "not needed" in pr["comments"][-1]
        assert g.e.task(tid)["status"] == "canceled"
        bare = g.work(push=False)
        fm(g.e.home, "cancel", bare)
        assert g.e.task(bare)["status"] == "canceled"
        assert len(g.calls("pr", "close")) == 1

    def test_cancel_with_a_failing_gh_changes_nothing_and_a_closed_pr_is_left_alone(self):
        g = self.make(test="true")
        tid = g.work()
        tick.tick(g.e.p)
        wt = g.e.task(tid)["worktree"]
        g.set(fail_always=["pr", "close"])
        with pytest.raises(SystemExit):
            main.main(["--home", str(g.e.home), "cancel", tid])
        assert g.e.task(tid)["status"] == "in progress"
        assert (g.e.repo.parent / wt).exists()
        g.set(fail_always=None)
        tried = len(g.calls("pr", "close"))
        pr = g.state["prs"]["1"]
        pr["state"] = "CLOSED"
        g.set(prs={"1": pr})
        fm(g.e.home, "cancel", tid)
        assert g.e.task(tid)["status"] == "canceled"
        assert len(g.calls("pr", "close")) == tried

    def test_cancel_of_a_pr_merged_by_hand_with_no_merge_commit_just_cancels(self):
        g = self.make(test="true")
        tid = g.work()
        tick.tick(g.e.p)
        pr = g.state["prs"]["1"]
        pr["state"] = "MERGED"
        g.set(prs={"1": pr}, no_oid=True)
        fm(g.e.home, "cancel", tid)
        assert g.e.task(tid)["status"] == "canceled"
        assert g.calls("pr", "close") == []
        assert g.calls("api") == []

    def merged(self, strategy: str | None) -> tuple[GH, str]:
        g = self.make(f'strategy = "{strategy}"' if strategy else "", test="true")
        tid = g.work()
        g.deliver(tid)
        tick.tick(g.e.p)
        merge.accept(g.e.p, tid)
        return g, tid

    def test_the_configured_merge_type_is_used(self):
        g, tid = self.merged(None)
        tick.tick(g.e.p)
        assert g.state["prs"]["1"]["strategy"] == "squash"
        assert g.e.task(tid)["status"] == "done"
        assert g.e.task(tid)["commit"] == "c0ffee"
        assert g.calls("pr", "merge")[0][:3] == ["pr", "merge", "1"]

    @pytest.mark.parametrize("strategy", ["merge", "rebase"])
    def test_the_configured_merge_type_reaches_gh(self, strategy):
        g = self.make(f'strategy = "{strategy}"', test="true")
        c = g.seed()
        tid = backlog.add(g.e.p, "x", status="ready", criteria="y")
        ref = merge.request_merge(g.e.p, {"id": tid, "branch": c.branch}, registry.get(g.e.p), replace(c, id=tid))
        assert ref == "c0ffee"
        assert g.state["prs"]["1"]["strategy"] == strategy
        assert g.calls("pr", "merge")[0][:3] == ["pr", "merge", "1"]

    def main_changes_app(self, g: GH) -> None:
        (g.e.repo / "app.py").write_text("changed on main\n")
        git(g.e.repo, "commit", "-qam", "main moved")

    def test_a_pr_refused_for_conflicts_goes_back_as_a_conflict(self):
        g, tid = self.merged(None)
        self.main_changes_app(g)
        g.set(refuse_merge="Pull Request has merge conflicts")
        tick.tick(g.e.p)
        i = g.e.task(tid)
        assert i["status"] == "in progress"
        assert i["conflicts"] == 1
        assert backlog.load(g.e.p)["decisions_resolved"][-1]["kind"] == "conflict"
        assert not i.get("blocked")
        assert "accepted" not in i
        assert len(g.calls("pr", "merge")) == 1
        assert git(g.e.repo, "status", "--porcelain", "--untracked-files=no").strip() == ""

    def test_a_pr_refused_for_another_reason_still_blocks(self):
        g, tid = self.merged(None)
        g.set(refuse_merge="Pull request is closed")
        tick.tick(g.e.p)
        i = g.e.task(tid)
        assert i["blocked"]
        assert i["status"] == "in review"
        assert "conflicts" not in i
        fm(g.e.home, "unblock", tid)
        # a refusal the local check cannot reproduce (main did not move) stays blocked too
        g.set(refuse_merge="Pull Request has merge conflicts")
        tick.tick(g.e.p)
        i = g.e.task(tid)
        assert i["blocked"]
        assert i["status"] == "in review"
        assert "conflicts" not in i

    def test_only_merge_conflict_text_is_a_conflict(self):
        assert isinstance(types.failure("gh", "Pull Request has merge conflicts"), providers.Conflict)
        for text in ("pull request is closed", "Pull request #3 is not mergeable", "error on branch fix-conflict-ui"):
            assert not isinstance(types.failure("gh", text), providers.Conflict)

    def test_the_task_closes_only_once_the_pr_is_seen_merged(self):
        g, tid = self.merged(None)
        s = g.state
        s["prs"]["1"]["draft"] = True  # an accepted task whose PR is still a draft is readied, then merged
        g.set(prs=s["prs"], lag=True)
        backlog.update(g.e.p, tid, pr={**g.e.task(tid)["pr"], "draft": True})
        tick.tick(g.e.p)
        assert g.e.task(tid)["status"] == "in review"
        assert len(g.calls("pr", "ready")) == 1
        assert len(g.calls("pr", "merge")) == 1
        tick.tick(g.e.p)  # not merged twice
        assert len(g.calls("pr", "merge")) == 1
        s = g.state
        s["prs"]["1"]["state"] = "MERGED"
        g.set(prs=s["prs"])
        tick.tick(g.e.p)
        assert g.e.task(tid)["status"] == "done"

    def test_a_pr_closed_unmerged_blocks_the_task(self):
        g, tid = self.merged(None)
        g.set(lag=True)
        tick.tick(g.e.p)
        s = g.state
        s["prs"]["1"]["state"] = "CLOSED"
        g.set(prs=s["prs"])
        tick.tick(g.e.p)
        assert "closed without merging" in g.e.task(tid)["blocked"]

    def test_failing_ci_is_high_risk(self):
        g = self.make(test="true", autonomy="medium")
        g.set(ci="fail")
        tid = g.work()
        g.deliver(tid)
        tick.tick(g.e.p)
        c = g.e.task(tid)["check"]
        assert (c["risk"], c["ci"]) == ("high", "fail")
        assert "CI fails [ci]" in c["reasons"]
        assert g.e.task(tid)["status"] == "in progress"  # returned to its agent (FN-67)

    def test_ci_replaces_the_local_test_without_a_test_command(self):
        for ci, tests in (("fail", "fail"),):  # the wiring; every case is test_decide TestTestsOrCi
            g = self.make()
            g.set(ci=ci)
            tid = g.work()
            g.deliver(tid)
            tick.tick(g.e.p)
            assert g.e.task(tid)["check"]["tests"] == tests

    def test_pending_ci_waits_for_the_result(self):
        g = self.make(test="true")
        g.set(ci="pending")
        tid = g.work()
        g.deliver(tid)
        tick.tick(g.e.p)
        assert "check" not in g.e.task(tid)
        g.set(ci="pass")
        tick.tick(g.e.p)
        assert g.e.task(tid)["check"]["ci"] == "pass"

    def test_autonomy_merges_only_with_green_ci(self):
        g = self.make(test="true", autonomy="medium")
        tid = g.work()
        g.deliver(tid)
        tick.tick(g.e.p)
        tick.tick(g.e.p)
        assert g.e.task(tid)["status"] == "done"

    def test_autonomy_waits_for_green_ci_decision(self):
        c = {"risk": "low", "review": "clean", "tests": "pass", "ci": "none", "conflict": False}
        assert decide.after_check(c, "medium", provider.GitHub.has_ci) == "owner"  # stays in review
        assert decide.after_check({**c, "ci": "fail"}, "medium", provider.GitHub.has_ci) == "failing"  # back to its agent: in progress
        assert decide.waits_for_ci("pending")  # stays in review

    def test_ci_is_read_again_at_every_decision_not_cached_with_the_check(self):
        g = self.make(test="true", autonomy="medium")
        g.set(ci="none")
        tid = g.work()
        g.deliver(tid)
        tick.tick(g.e.p)
        assert (g.e.task(tid)["status"], g.e.task(tid)["check"]["ci"]) == ("in review", "none")
        g.set(ci="pass")  # the CI registered later: no new push needed
        tick.tick(g.e.p)
        assert g.e.task(tid)["status"] == "done"

    def graphql(self, g: GH) -> int:
        return len(g.calls("api", "graphql"))

    def test_a_settled_ci_is_not_read_again_until_the_branch_gets_a_commit(self):
        g = self.make(test="true")
        c, ci = g.seed(), registry.get(g.e.p).ci
        assert (ci(c), self.graphql(g)) == ("pass", 1)
        for _ in range(3):
            ci(c)
        assert self.graphql(g) == 1
        git(g.e.repo, "commit", "-q", "--allow-empty", "-m", "fix")
        git(g.e.repo, "push", "-q", "origin", f"main:refs/heads/{c.branch}")
        g.set(ci="fail")
        assert (ci(replace(c, head=git(g.e.repo, "rev-parse", "main").strip())), self.graphql(g)) == ("fail", 2)

    def test_a_pr_missing_from_the_open_list_is_read_on_its_own_and_kept(self):
        g = self.make(test="true")
        g.set(hidden=["1"])  # still there, but not in the open list (closed outside fm, or past the first 100)
        c, ci = g.seed(), registry.get(g.e.p).ci
        assert ci(c) == "pass"
        before = self.graphql(g)
        ci(c)
        assert self.graphql(g) == before

    def test_pending_ci_of_several_tasks_is_one_call_per_poll(self):
        g = self.make(test="true")
        g.set(ci="pending")
        provider.POLL = 3600
        first, second, ci = g.seed(1), g.seed(2, "foreman/ty-2"), registry.get(g.e.p).ci
        assert (ci(first), ci(second)) == ("pending", "pending")
        assert self.graphql(g) == 1
        ci(first)
        assert self.graphql(g) == 1

    def test_a_transient_gh_failure_in_the_queue_retries_instead_of_blocking(self):
        g = self.make(test="true", autonomy="medium")
        g.set(ci="pending")
        tid = g.work()
        g.deliver(tid)
        g.set(ci="pass")
        for failing in (["api", "graphql"], ["pr", "merge"], ["pr", "view"]):
            fm(g.e.home, "unblock", tid)  # each stage counts its own failures in a row
            g.set(fail_once=failing)
            for _ in range(4):
                tick.tick(g.e.p)
                assert not g.e.task(tid).get("blocked")
                if g.state["fail_once"] is None:
                    break
            assert g.state["fail_once"] is None, failing
        for _ in range(3):
            if g.e.task(tid)["status"] == "done":
                break
            tick.tick(g.e.p)
        i = g.e.task(tid)
        assert not i.get("blocked")
        assert i["status"] == "done"

    # "Head sha can't be blank" (once, then fine) and a 502 are both retried; neither blocks the task
    @pytest.mark.parametrize(("flag", "text"), [({"reject": "foreman/ty-1"}, "Head sha"), ({"flaky": "foreman/ty-1"}, "HTTP 502")], ids=["reject", "flaky"])
    def test_one_tasks_failure_never_blocks_it_nor_stops_the_next_task_getting_its_pr(self, flag, text):
        g = self.make(test="true")
        g.set(**flag)
        first, second = g.work(), g.work()  # each work() ticks: the first one fails there, once
        events = [e["text"] for e in util.read_jsonl(g.e.p.state / "events.jsonl") if "could not push it or open its PR" in e["text"]]
        assert len(events) == 1
        assert text in events[0]
        assert not g.e.task(first).get("blocked")
        g.set(flaky="")
        tick.tick(g.e.p)
        assert "pr" in g.e.task(second)  # the failure did not stop the next task
        assert "pr" in g.e.task(first)
        assert g.e.task(first)["retry_error"] == ""

    def test_only_known_permanent_errors_are_permanent_and_unblock_clears_the_errors(self):
        cases = (
            ("Head sha can't be blank", types.TransientError),
            ("something new", types.TransientError),
            ("pull request is closed", types.PermanentError),
            ("merge conflict", types.PermanentError),
            ("Pull request #3 is not mergeable", types.PermanentError),
            ("the pull request cannot be merged", types.PermanentError),
            ("read tcp: use of closed network connection", types.TransientError),
            ("connection closed by remote host", types.TransientError),
            ("error on branch fix-conflict-ui", types.TransientError),
        )
        for text, kind in cases:
            assert isinstance(types.failure("gh", text), kind), text
        g = self.make(test="true")
        tid = g.work()
        backlog.update(g.e.p, tid, blocked="x", pr_error="a", retry_error="b")
        fm(g.e.home, "unblock", tid)
        i = g.e.task(tid)
        assert (i["blocked"], i["pr_error"], i["retry_error"]) == ("", "", "")

    def test_a_merged_pr_without_a_merge_commit_is_read_from_the_api_or_fails_clearly(self):
        g, tid = self.merged(None)
        g.set(no_oid=True)
        tick.tick(g.e.p)
        assert g.e.task(tid)["status"] == "in review"  # no sha anywhere: retried, no invented reference
        assert "merge" not in g.e.task(tid)
        assert "no merge commit" in g.e.task(tid)["retry_error"]
        g.set(api_sha="feed01")
        tick.tick(g.e.p)
        assert (g.e.task(tid)["status"], g.e.task(tid)["commit"]) == ("done", "feed01")

    def test_the_provider_never_tags_releases_or_forces(self):
        import inspect

        from foreman.providers.github import checks, review

        src = "".join(inspect.getsource(m) for m in (provider, types, checks, review))
        for word in ('"tag"', '"release"', "--force", "--tags", "+refs"):
            assert word not in src
