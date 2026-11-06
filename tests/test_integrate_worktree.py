import json
import subprocess
import sys
import time
import unittest
from pathlib import Path

from helpers import ProjectCase, git, make_repo, snapshot

from foreman import backlog_check, config, integrate, loop, util
from foreman.backlog import Backlog

HERE = Path(__file__).resolve().parent

TOML = """
[project]
name = "fake"
owner = "Owner"

[paths]
worktrees = "{tmp}/wt"

[repos.app]
path = "{tmp}/app"
github = "acme/app"
merge = "squash"
local_ci = "test -f feature.txt && echo local-ci-ok"
post_create = ["echo made-from {main} > made.txt"]
risky_paths = ["migrations/", "package.json:version"]

[repos.api]
path = "{tmp}/api"
github = "acme/api"
merge = "merge"

[repos.landing]
path = "{tmp}/landing"
merge = "merge"
remote_checks = "none"
publish_on_merge = true
"""


#  Hooks git runs on what integrate and worktree do (merge, ff, rebase, checkout, reset, ref updates, commits).
QUIET_HOOKS = (
    "post-merge",
    "reference-transaction",
    "pre-merge-commit",
    "post-checkout",
    "pre-rebase",
    "post-rewrite",
    "pre-commit",
    "commit-msg",
    "post-commit",
    "pre-auto-gc",
)


def plant_hooks(repo, marker, names=QUIET_HOOKS):
    """Hooks in REPO's .git/hooks (shared with its worktrees) that append their name to MARKER."""
    hooks = Path(repo) / ".git/hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    for name in names:
        h = hooks / name
        h.write_text(f'#!/bin/sh\necho {name} >> "{marker}"\ncat > /dev/null\nexit 0\n')
        h.chmod(0o755)


def fired(marker):
    return sorted(set(marker.read_text().split())) if marker.exists() else []


class IntegrateCase(ProjectCase):
    """Integration through pull requests: three repos with a bare remote each, gh is tests/fake_gh.py."""

    toml = TOML

    def setUp(self):
        super().setUp()
        snapshot("integrate-remotes", self.tmp, self.remotes)
        gh = self.tmp / "gh"
        gh.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{HERE / "fake_gh.py"}" "$@"\n')
        gh.chmod(0o755)
        self.ghenv = {"FOREMAN_GH": str(gh), "FAKE_GH_DIR": str(self.tmp / "ghstate"), "FOREMAN_CHECKS_RETRY_S": "0"}
        self.b = Backlog(self.project)
        self.b.init()
        with self.b.mutate() as d:
            self.b.add_project(d, "E1", "One", "intro")
            self.b.add_issue(d, "E1", "AB-1", "feature", status="in progress", github="acme/app#42")
            self.b.add_issue(d, "E1", "AB-2", "after the feature", status="ready", blocked="waits for AB-1")
            self.b.set_sub(d, "AB-1", "T1", title="the feature", agent="w1", branch="ab1", status="▶")

    @staticmethod
    def remotes(tmp):
        """Each repo: a seed, its bare clone (the remote) and a clone of that (the product's checkout)."""
        for repo in ("app", "api", "landing"):
            seed = make_repo(tmp / f"seed-{repo}", {"README.md": "hi\n", "package.json": '{"version": "1.0.0"}\n'})
            git(tmp, "clone", "-q", "--bare", str(seed), str(tmp / f"{repo}.git"))
            git(tmp, "clone", "-q", str(tmp / f"{repo}.git"), str(tmp / repo))

    def worktree(self, repo="app", name="w1", branch="ab1", files=None):
        r = self.cli("worktree", repo, name, branch)
        self.assertEqual(r.returncode, 0, r.stderr)
        wt = self.tmp / "wt" / f"{repo}-{name}"
        for f, text in (files or {"feature.txt": "new\n"}).items():
            (wt / f).parent.mkdir(parents=True, exist_ok=True)
            (wt / f).write_text(text)
        git(wt, "add", "-A")
        git(wt, "commit", "-q", "-m", "work")
        return wt

    def integrate(self, *args, env=None):
        return self.cli("integrate", *args, env={**self.ghenv, **(env or {})})

    def calls(self):
        f = self.tmp / "ghstate/calls.log"
        return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []

    def remote_log(self, repo="app"):
        return git(self.tmp / f"{repo}.git", "log", "--format=%P|%s", "main").splitlines()

    def pr_state(self):
        return json.loads((self.tmp / "ghstate/state.json").read_text())["prs"]["1"]

    def progress(self):
        f = self.project.state / "progress.jsonl"
        return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []


class WorktreeTest(IntegrateCase):
    """`foreman worktree`: post_create, existing and remote branches, no git hook run."""

    def test_worktree_post_create(self):
        wt = self.worktree()
        self.assertEqual((wt / "made.txt").read_text().strip(), f"made-from {self.tmp / 'app'}")
        self.assertEqual(git(wt, "branch", "--show-current"), "ab1")
        r = self.cli("worktree", "--list")
        self.assertIn("app\tab1", r.stdout)

    def test_worktree_failed_post_create_is_removed(self):
        self.write_toml(TOML.replace('post_create = ["echo made-from {main} > made.txt"]', 'post_create = ["false"]'))
        r = self.cli("worktree", "app", "w2", "ab2")
        self.assertEqual(r.returncode, 1)
        self.assertFalse((self.tmp / "wt/app-w2").exists())
        self.assertNotIn("ab2", git(self.tmp / "app", "branch"))

    def branch_with_commit(self, repo, branch, push_to=None):
        """BRANCH one commit ahead of main in REPO (back on main after), pushed to PUSH_TO if given; its sha."""
        git(repo, "checkout", "-q", "-b", branch)
        (repo / f"{branch}.txt").write_text("pr\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", f"{branch} work")
        sha = git(repo, "rev-parse", "HEAD")
        if push_to:
            git(repo, "push", "-q", str(push_to), branch)
        git(repo, "checkout", "-q", "main")
        return sha

    def test_worktree_on_existing_local_branch(self):
        app = self.tmp / "app"
        sha = self.branch_with_commit(app, "pr1")
        r = self.cli("worktree", "app", "w3", "pr1", "main")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("BASE main ignored", r.stderr)
        self.assertIn("pr1 existing branch at", r.stdout)
        wt = self.tmp / "wt/app-w3"
        self.assertEqual(git(wt, "branch", "--show-current"), "pr1")
        self.assertEqual(git(wt, "rev-parse", "HEAD"), sha)
        self.assertEqual(git(app, "rev-parse", "pr1"), sha)
        self.assertEqual((wt / "made.txt").read_text().strip(), f"made-from {app}")

    def test_worktree_on_remote_only_branch(self):
        sha = self.branch_with_commit(self.tmp / "seed-app", "pr2", push_to=self.tmp / "app.git")
        app = self.tmp / "app"
        self.assertNotIn("pr2", git(app, "branch", "-a"))
        r = self.cli("worktree", "app", "w4", "pr2")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("pr2 tracking origin/pr2", r.stdout)
        wt = self.tmp / "wt/app-w4"
        self.assertEqual(git(wt, "branch", "--show-current"), "pr2")
        self.assertEqual(git(wt, "rev-parse", "HEAD"), sha)
        self.assertEqual(git(wt, "rev-parse", "--abbrev-ref", "pr2@{upstream}"), "origin/pr2")
        self.assertEqual((wt / "made.txt").read_text().strip(), f"made-from {app}")

    def test_worktree_on_branch_open_elsewhere(self):
        wt = self.worktree()
        head = git(wt, "rev-parse", "HEAD")
        r = self.cli("worktree", "app", "w5", "ab1")
        self.assertEqual(r.returncode, 1)
        self.assertIn(f"already checked out in {wt}", r.stderr)
        self.assertFalse((self.tmp / "wt/app-w5").exists())
        self.assertEqual(git(wt, "rev-parse", "HEAD"), head)
        r = self.cli("worktree", "app", "w6", "main")
        self.assertEqual(r.returncode, 1)
        self.assertIn(f"already checked out in {self.tmp / 'app'}", r.stderr)
        self.assertFalse((self.tmp / "wt/app-w6").exists())

    def test_worktree_failed_post_create_keeps_existing_branch(self):
        self.write_toml(TOML.replace('post_create = ["echo made-from {main} > made.txt"]', 'post_create = ["false"]'))
        sha = self.branch_with_commit(self.tmp / "app", "pr3")
        r = self.cli("worktree", "app", "w7", "pr3")
        self.assertEqual(r.returncode, 1)
        self.assertFalse((self.tmp / "wt/app-w7").exists())
        self.assertEqual(git(self.tmp / "app", "rev-parse", "pr3"), sha)

    def test_worktree_runs_no_hook(self):
        marker = self.tmp / "fired.log"
        plant_hooks(self.tmp / "app", marker)
        r = self.cli("worktree", "app", "w1", "ab1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.tmp / "wt/app-w1/made.txt").exists())
        self.assertEqual(fired(marker), [])
        git(self.tmp / "wt/app-w1", "checkout", "-q", "-b", "probe")  # the hooks are live: plain git runs them
        self.assertIn("post-checkout", fired(marker))


class IntegrateTest(IntegrateCase):
    """The pull request path: merge methods, red CI or checks, a main that is not clean, remote branches."""

    def test_check_only(self):
        self.worktree()
        r = self.integrate("app", "ab1", "AB-1", "--check")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("would do: AB-1 T1 ✓ <merge>", r.stdout)
        self.assertEqual(self.calls(), [])

    def test_squash_green_path(self):
        wt = self.worktree()
        r = self.integrate(
            "app",
            "ab1",
            "AB-1",
            "merge msg",
            "--title",
            "Add the feature",
            "--body",
            "Why and how.",
            "--result",
            "feature works",
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("local-ci-ok", r.stdout)
        log = self.remote_log()
        self.assertEqual(len(log), 2)
        self.assertEqual(log[0].split("|")[1], "Add the feature")
        self.assertEqual(len(log[0].split("|")[0].split()), 1)  # squash: one parent
        pr = json.loads((self.tmp / "ghstate/state.json").read_text())["prs"]["1"]
        self.assertIn("Closes #42", pr["body"])
        self.assertRegex(pr["comments"][0], r"^Local CI passed on [0-9a-f]{7,} in \d+ s\.$")  # no new keys: as before
        self.assertFalse(wt.exists())
        self.assertNotIn("ab1", git(self.tmp / "app", "branch", "-a"))
        d = self.b.load()
        i = self.b.issue(d, "AB-1")
        self.assertEqual((i["status"], i["result"], i["subs"][0]["status"]), ("done", "feature works", "done"))
        self.assertEqual(i["subs"][0]["commit"], git(self.tmp / "app", "rev-parse", "--short", "main"))
        self.assertEqual(self.b.issue(d, "AB-2")["blocked"], "")
        note = next((self.home / "notes").glob("*.md")).read_text()
        self.assertIn("Integrated app:ab1 (AB-1)", note)

    def test_measure_with_a_remote_pushes_nothing(self):
        with self.b.mutate() as d:
            self.b.set_sub(d, "AB-1", "T1", kind="measure")
        (self.home / "reviews").mkdir(exist_ok=True)
        (self.home / "reviews/m.md").write_text("figures\n")
        wt = self.worktree()
        git(wt, "push", "-q", "origin", "ab1")  # pushed by hand: the remote branch is never touched
        before = self.remote_log()
        r = self.integrate("app", "ab1", "AB-1 T1", "--measure", "--report", "reviews/m.md")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("local-ci-ok", r.stdout)
        self.assertEqual((self.calls(), self.remote_log()), ([], before))
        self.assertFalse(wt.exists())
        self.assertNotIn("ab1", git(self.tmp / "app", "branch"))
        self.assertIn("ab1", git(self.tmp / "app.git", "branch"))
        sub = self.b.issue(self.b.load(), "AB-1")["subs"][0]
        self.assertEqual((sub["status"], sub.get("commit")), ("done", None))

    def test_merge_commit_method(self):
        with self.b.mutate() as d:
            self.b.set_sub(d, "AB-1", "T1", branch="api1")
        self.worktree("api", "w1", "api1")
        r = self.integrate("api", "api1", "AB-1 T1", "--title", "API change", "--body", "b", "--result", "ok")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        parents = self.remote_log("api")[0].split("|")[0].split()
        self.assertEqual(len(parents), 2)  # a real merge commit
        merge_call = next(c for c in self.calls() if c[:2] == ["pr", "merge"])
        self.assertIn("--merge", merge_call)
        pr = json.loads((self.tmp / "ghstate/state.json").read_text())["prs"]["1"]
        self.assertNotIn("Closes", pr["body"])  # the issue is from another repo

    def test_local_ci_red_pushes_nothing(self):
        self.worktree(files={"other.txt": "x\n"})
        r = self.integrate("app", "ab1", "AB-1", "--title", "t", "--body", "b")
        self.assertEqual(r.returncode, 1)
        self.assertIn("local CI failed", r.stderr)
        self.assertEqual(self.calls(), [])
        self.assertEqual(git(self.tmp / "app.git", "branch", "--list", "ab1"), "")

    def test_remote_checks_red_leaves_pr_open(self):
        wt = self.worktree()
        r = self.integrate("app", "ab1", "AB-1", "--title", "t", "--body", "b", env={"FAKE_GH_CHECKS": "fail"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("the PR stays open", r.stderr)
        self.assertTrue(wt.exists())
        self.assertFalse(any(c[:2] == ["pr", "merge"] for c in self.calls()))
        self.assertEqual(self.b.issue(self.b.load(), "AB-1")["status"], "in progress")

    def test_dirty_or_unsynced_main_stops(self):
        self.worktree()
        (self.tmp / "app/README.md").write_text("dirty\n")
        r = self.integrate("app", "ab1", "AB-1", "--title", "t", "--body", "b")
        self.assertEqual(r.returncode, 1)
        self.assertIn("must be on a clean main", r.stderr)

    def test_branch_without_main_stops_with_a_remote(self):
        wt = self.worktree()
        tip = git(wt, "rev-parse", "HEAD")
        app = self.tmp / "app"
        (app / "ops").mkdir()
        (app / "ops/notes.md").write_text("pass\n")
        git(app, "add", "-A")
        git(app, "commit", "-q", "-m", "pass")
        git(app, "push", "-q", "origin", "main")
        r = self.integrate("app", "ab1", "AB-1", "--title", "t", "--body", "b")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("does not contain main and the repo has a remote (origin)", r.stderr)
        self.assertEqual(git(wt, "rev-parse", "HEAD"), tip)
        self.assertEqual(git(wt, "status", "--porcelain"), "")
        self.assertEqual(git(self.tmp / "app.git", "rev-parse", "main"), git(app, "rev-parse", "main"))
        self.assertFalse(any(c[:2] in (["pr", "create"], ["pr", "merge"]) for c in self.calls()))
        self.assertNotIn("local-ci-ok", r.stdout)

    def test_integrate_with_a_remote_runs_pre_push_and_no_other_hook(self):
        self.worktree()
        marker = self.tmp / "fired.log"
        # not reference-transaction: a push updates its remote-tracking refs and runs it, as it runs pre-push
        plant_hooks(self.tmp / "app", marker, (*(h for h in QUIET_HOOKS if h != "reference-transaction"), "pre-push"))
        r = self.integrate("app", "ab1", "AB-1", "--title", "t", "--body", "b", "--result", "ok")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(fired(marker), ["pre-push"])  # the push of the branch, and its deletion after the merge
        self.assertEqual(marker.read_text().split().count("pre-push"), 2)

    def test_remote_branch_deleted_on_merge_is_silent(self):
        wt = self.worktree()
        r = self.integrate(
            "app", "ab1", "AB-1", "--title", "t", "--body", "b", "--result", "ok", env={"FAKE_GH_DELETE_HEAD": "1"}
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("could not delete", r.stdout + r.stderr)
        self.assertFalse(wt.exists())
        self.assertEqual(self.b.issue(self.b.load(), "AB-1")["status"], "done")

    def test_remote_branch_that_cannot_be_deleted_warns(self):
        hook = self.tmp / "app.git/hooks/pre-receive"  # refuses deleting refs, lets the merge through
        hook.write_text(
            '#!/bin/sh\nwhile read old new ref; do [ "$new" = 0000000000000000000000000000000000000000 ] '
            "&& exit 1; done\nexit 0\n"
        )
        hook.chmod(0o755)
        self.worktree()
        r = self.integrate("app", "ab1", "AB-1", "--title", "t", "--body", "b", "--result", "ok")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("could not delete origin/ab1", r.stdout)


class GateTest(IntegrateCase):
    """Risky paths, publish_on_merge and the verdict: approval first, or nothing merged."""

    def test_risky_path_waits_for_approval_then_merges(self):
        self.worktree(files={"feature.txt": "x\n", "migrations/001.sql": "create table t();\n"})
        r = self.integrate("app", "ab1", "AB-1", "--title", "t", "--body", "b")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("awaiting the owner's approval", r.stderr)
        self.assertIn("migrations/001.sql", r.stdout)
        self.assertFalse(any(c[:2] == ["pr", "merge"] for c in self.calls()))
        progress = (self.project.state / "progress.jsonl").read_text()
        self.assertIn("awaiting approval", progress)
        r = self.integrate(
            "app", "ab1", "AB-1", "--title", "t", "--body", "b", "--approved", "Owner, panel 10:00", "--result", "ok"
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("already open", r.stdout)
        self.assertEqual(sum(1 for c in self.calls() if c[:2] == ["pr", "create"]), 1)
        self.assertEqual(self.b.issue(self.b.load(), "AB-1")["status"], "done")

    def test_risky_needle_in_file(self):
        self.worktree(files={"feature.txt": "x\n", "package.json": '{"version": "1.1.0"}\n'})
        r = self.integrate("app", "ab1", "AB-1", "--check")
        self.assertIn("package.json (package.json:version)", r.stdout)
        self.assertIn("will need the owner's approval", r.stdout)

    def test_publish_on_merge_needs_approval(self):
        with self.b.mutate() as d:
            self.b.set_sub(d, "AB-1", "T1", branch="land1")
        self.worktree("landing", "w1", "land1")
        r = self.integrate("landing", "land1", "AB-1", "--title", "t", "--body", "b")
        self.assertEqual(r.returncode, 3)
        self.assertIn("merging this repo publishes", r.stderr)

    def assert_nothing_merged(self, wt):
        self.assertFalse(any(c[:2] in (["pr", "create"], ["pr", "merge"]) for c in self.calls()))
        self.assertEqual(git(self.tmp / "app.git", "branch", "--list", "ab1"), "")
        self.assertEqual(len(self.remote_log()), 1)
        self.assertTrue(wt.exists())
        self.assertIn("ab1", git(self.tmp / "app", "branch"))
        self.assertEqual(self.b.issue(self.b.load(), "AB-1")["status"], "in progress")

    def test_bad_verdict_merges_nothing(self):
        with self.b.mutate() as d:
            self.b.set_outcome(d, self.b.issue(d, "AB-1"), criterion="the feature works")
        wt = self.worktree()
        r = self.integrate(
            "app", "ab1", "AB-1", "--title", "t", "--body", "b", "--result", "ok", "--verdict", "met: it works"
        )
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("nothing done", r.stderr)
        self.assertIn("bad verdict 'met: it works': one of met, partial", r.stderr)
        self.assertNotIn("local-ci-ok", r.stdout)
        self.assert_nothing_merged(wt)
        r = self.integrate("app", "ab1", "AB-1", "--check", "--verdict", "nope")
        self.assertEqual(r.returncode, 2)
        self.assertIn("bad verdict", r.stderr)

    def test_verdict_without_criterion_or_bad_sub_merges_nothing(self):
        wt = self.worktree()
        r = self.integrate("app", "ab1", "AB-1", "--title", "t", "--body", "b", "--verdict", "met")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("needs its criterion", r.stderr)
        r = self.integrate("app", "ab1", "AB-1 T9", "--title", "t", "--body", "b")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("no sub-issue T9", r.stderr)
        r = self.integrate("app", "ab1", "AB-99", "--title", "t", "--body", "b")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assert_nothing_merged(wt)


class PublicRepoTest(IntegrateCase):
    """A public repo: internal ids and the privacy scan in the summary and the commit messages."""

    def test_public_repo_rejects_internal_ids(self):
        self.write_toml(TOML.replace('merge = "squash"', 'merge = "squash"\npublic = true'))
        self.worktree()
        r = self.integrate("app", "ab1", "AB-1", "--title", "Fix AB-1", "--body", "b")
        self.assertEqual(r.returncode, 1)
        self.assertIn("internal ID", r.stderr)
        # bracketed ids are fine for the owner, never for a public repo; decision and question ids count too
        for title in ("Fix the export (AB-1)", "As J1 said"):
            r = self.integrate("app", "ab1", "AB-1", "--title", title, "--body", "b")
            self.assertEqual(r.returncode, 1, title)
            self.assertIn("internal ID", r.stderr)
        r = self.integrate("app", "ab1", "AB-1", "--title", "Fix the export", "--body", "b", "--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_public_repo_rejects_internal_ids_in_commit_messages(self):
        self.write_toml(TOML.replace('merge = "squash"', 'merge = "squash"\npublic = true'))
        wt = self.worktree()
        (wt / "more.txt").write_text("more\n")
        git(wt, "add", "-A")
        git(wt, "commit", "-q", "-m", "AB-1: x")
        sha = git(wt, "rev-parse", "HEAD")[:12]
        r = self.integrate("app", "ab1", "AB-1", "--title", "Add it", "--body", "b")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn(f"the message of commit {sha} carries an internal ID", r.stderr)
        self.assertIn("nothing pushed", r.stderr)
        self.assertNotIn("AB-1: x", r.stderr)
        self.assertEqual(self.calls(), [])
        self.assertEqual(git(self.tmp / "app.git", "branch", "--list", "ab1"), "")

    def test_public_repo_commit_messages_pass_the_privacy_scan(self):
        self.write_toml(
            TOML.replace('merge = "squash"', 'merge = "squash"\npublic = true\nprivacy_scan = "! grep -q secret"')
        )
        wt = self.worktree()
        git(wt, "commit", "-q", "--allow-empty", "-m", "Add it\n\nsee secret-host")
        r = self.integrate("app", "ab1", "AB-1", "--title", "Add it", "--body", "b")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("fails the privacy scan", r.stderr)
        self.assertIn("nothing pushed", r.stderr)
        self.assertNotIn("secret-host", r.stderr)
        self.assertEqual(self.calls(), [])
        # a private repo never looks at them
        self.write_toml(TOML)
        r = self.integrate("app", "ab1", "AB-1", "--title", "Add it", "--body", "b", "--result", "ok")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class CiRunsTest(IntegrateCase):
    """The local CI runs (--ci, ci_runs, ci_summary) before the pull request."""

    CI_LOCAL = (
        'local_ci = "echo run:{ci} >> {tmp}/runs.log && echo out{ci} && test -f feature.txt && test x{ci} != x--bad"'
    )

    CI_SUMMARY = "ci_summary = \"sed -n 's/^out/summary of /p'\""

    def ci_toml(self, *extra):
        return TOML.replace('local_ci = "test -f feature.txt && echo local-ci-ok"', "\n".join((self.CI_LOCAL,) + extra))

    def runs(self):
        f = self.tmp / "runs.log"
        return f.read_text().splitlines() if f.exists() else []

    def test_two_ci_runs_in_order_with_their_summary(self):
        self.write_toml(self.ci_toml(self.CI_SUMMARY))
        self.worktree()
        r = self.integrate(
            "app", "ab1", "AB-1", "--ci=--toy", "--ci=--real", "--title", "t", "--body", "b", "--result", "ok"
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.runs(), ["run:--toy", "run:--real"])
        self.assertIn("(run 1/2, `--toy`)", r.stdout)
        comment = self.pr_state()["comments"][0]
        first, _, bullets = comment.partition("\n\n")
        self.assertRegex(first, r"^Local CI passed on [0-9a-f]{7,}; runs: `--toy` \(\d+ s\), `--real` \(\d+ s\)\.$")
        self.assertEqual(bullets.splitlines(), ["- `--toy`: summary of --toy", "- `--real`: summary of --real"])
        log = (self.project.state / "integrate/ab1-ci.log").read_text()
        self.assertLess(log.index("out--toy"), log.index("out--real"))
        self.assertEqual(log.count("== rc 0 in"), 2)
        note = next((self.home / "notes").glob("*.md")).read_text()
        self.assertIn("runs: `--toy`", note)
        self.assertNotIn("summary of", note)  # the summary goes to the PR comment only

    def test_ci_runs_from_the_config_and_ci_replaces_them(self):
        self.write_toml(self.ci_toml('ci_runs = ["--a", "--b"]'))
        self.worktree()
        r = self.integrate("app", "ab1", "AB-1", "--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("== local CI: 2 runs (ci_runs)", r.stdout)
        self.assertIn("   1. echo run:--a", r.stdout)
        self.assertIn("   2. echo run:--b", r.stdout)
        self.assertIn("integrate/ab1-ci.log", r.stdout)
        r = self.integrate("app", "ab1", "AB-1", "--check", "--ci=--only")
        self.assertIn("== local CI: 1 run (--ci)", r.stdout)
        self.assertIn("   1. echo run:--only", r.stdout)
        self.assertNotIn("run:--a", r.stdout)
        self.assertEqual((self.runs(), self.calls()), ([], []))  # --check runs nothing
        r = self.integrate("app", "ab1", "AB-1", "--title", "t", "--body", "b")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.runs(), ["run:--a", "run:--b"])
        self.assertEqual(self.pr_state()["comments"][0].count("\n"), 0)  # no ci_summary: one line

    def test_a_failing_run_stops_the_rest_and_the_pr_stays_open(self):
        self.write_toml(self.ci_toml())
        wt = self.worktree()
        r = self.integrate("app", "ab1", "AB-1", "--title", "t", "--body", "b", env={"FAKE_GH_CHECKS": "fail"})
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)  # a first attempt left the PR open
        self.assertEqual(self.pr_state()["state"], "OPEN")
        self.assertEqual(self.runs(), ["run:"])
        n_calls = len(self.calls())
        r = self.integrate(
            "app", "ab1", "AB-1", "--ci=--toy", "--ci=--bad", "--ci=--never", "--title", "t", "--body", "b"
        )
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("local CI failed", r.stderr)
        self.assertIn("(run 2/3, `--bad`)", r.stderr)
        self.assertIn("nothing pushed", r.stderr)
        self.assertEqual(self.runs(), ["run:", "run:--toy", "run:--bad"])
        self.assertEqual(len(self.calls()), n_calls)  # no gh call: no comment, no merge
        self.assertEqual((self.pr_state()["state"], self.pr_state()["comments"]), ("OPEN", []))
        self.assertTrue(wt.exists())
        log = (self.project.state / "integrate/ab1-ci.log").read_text()
        self.assertIn("out--bad", log)
        self.assertNotIn("out--never", log)
        self.assertEqual(self.b.issue(self.b.load(), "AB-1")["status"], "in progress")

    def test_a_summary_with_an_internal_id_stops_before_pushing(self):
        self.write_toml(self.ci_toml('ci_summary = "echo see AB-1"', "public = true"))
        wt = self.worktree()
        r = self.integrate("app", "ab1", "AB-1", "--ci=--toy", "--title", "Add it", "--body", "b")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("the CI comment carries an internal ID", r.stderr)
        self.assertIn("nothing pushed", r.stderr)
        self.assertEqual(self.calls(), [])
        self.assertEqual(git(self.tmp / "app.git", "branch", "--list", "ab1"), "")
        self.assertTrue(wt.exists())

    def test_a_summary_failing_the_privacy_scan_stops(self):
        self.write_toml(
            self.ci_toml('ci_summary = "echo secret-host"', "public = true", 'privacy_scan = "! grep -q secret"')
        )
        self.worktree()
        r = self.integrate("app", "ab1", "AB-1", "--title", "Add it", "--body", "b")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("the CI comment fails the privacy scan", r.stderr)
        self.assertEqual(self.calls(), [])

    def test_ci_without_the_placeholder_is_exit_2(self):
        self.worktree()  # TOML's local_ci has no {ci}
        for extra in ((), ("--check",)):
            r = self.integrate("app", "ab1", "AB-1", "--ci=--toy", "--title", "t", "--body", "b", *extra)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("local_ci has no {ci} placeholder", r.stderr)
        self.write_toml(TOML.replace('merge = "squash"', 'merge = "squash"\nci_runs = ["--x"]'))
        r = self.integrate("app", "ab1", "AB-1", "--title", "t", "--body", "b")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("ci_runs has arguments", r.stderr)
        r = self.integrate("app", "ab1", "AB-1", "--ci=--toy", "--no-ci", "--title", "t", "--body", "b")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("exclusive", r.stderr)
        self.assertEqual(self.calls(), [])
        self.assertFalse((self.project.state / "integrate").exists())

    def test_bad_ci_keys_are_config_errors(self):
        for bad in (
            'ci_runs = "--toy"',
            "ci_runs = []",
            "ci_runs = [1]",
            "ci_summary = 3",
            "post_merge = 3",
            'post_merge = ["a"]',
        ):
            with self.assertRaises(config.ConfigError, msg=bad):
                self.write_toml(TOML.replace('merge = "squash"', f'merge = "squash"\n{bad}'))


class PostMergeTest(IntegrateCase):
    """post_merge after the pull request is merged."""

    POST_OK = 'post_merge = "echo {before}..{head} > {tmp}/post.txt && pwd && echo surface unchanged on {branch}"'

    def test_post_merge_runs_after_the_pr_merge_with_before_and_head(self):
        self.write_toml(TOML.replace('merge = "squash"', 'merge = "squash"\n' + self.POST_OK))
        self.worktree()
        r = self.integrate("app", "ab1", "AB-1", "--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"== post_merge after the merge, in {self.tmp / 'app'}", r.stdout)
        self.assertIn("echo {before}..{head} >", r.stdout)
        self.assertFalse((self.tmp / "post.txt").exists())
        before = git(self.tmp / "app", "rev-parse", "main")
        r = self.integrate("app", "ab1", "AB-1", "--title", "t", "--body", "b", "--result", "ok")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        head = git(self.tmp / "app", "rev-parse", "main")
        self.assertNotEqual(before, head)
        self.assertEqual(head, git(self.tmp / "app.git", "rev-parse", "main"))  # after the pull
        self.assertEqual((self.tmp / "post.txt").read_text().strip(), f"{before}..{head}")
        self.assertIn(str(self.tmp / "app"), r.stdout)  # its output is shown; it ran in the main checkout
        out = r.stdout
        self.assertLess(out.index("== merged ab1"), out.index("== post_merge"))
        self.assertLess(out.index("== post_merge"), out.index("== backlog"))
        note = next((self.home / "notes").glob("*.md")).read_text()
        self.assertIn("post_merge: surface unchanged on ab1", note)
        self.assertEqual(self.b.issue(self.b.load(), "AB-1")["status"], "done")
        self.assertEqual(self.progress(), [])

    def test_a_failing_post_merge_warns_and_exits_0_after_the_pr_merge(self):
        self.write_toml(
            TOML.replace('merge = "squash"', 'merge = "squash"\npost_merge = "echo first; echo broke >&2; exit 3"')
        )
        self.worktree()
        r = self.integrate("app", "ab1", "AB-1", "--title", "t", "--body", "b", "--result", "ok")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("warning: post_merge failed (rc 3)", r.stdout)
        self.assertIn("broke", r.stdout)
        self.assertEqual(len(self.remote_log()), 2)  # the merge stands
        self.assertEqual(self.pr_state()["state"], "MERGED")
        [line] = self.progress()
        self.assertEqual(line["agent"], "orchestrator")
        self.assertIn("post_merge failed (rc 3)", line["text"])
        self.assertIn("app:ab1", line["text"])
        note = next((self.home / "notes").glob("*.md")).read_text()
        self.assertIn("post_merge failed (rc 3): broke", note)
        self.assertEqual(self.b.issue(self.b.load(), "AB-1")["status"], "done")  # the backlog close still happens

    def test_a_post_merge_past_its_cap_is_cut_and_the_note_says_so(self):
        """post_merge runs under time_limit.sh with a fixed cap (600 s; 1 s here through the test-only env)."""
        self.write_toml(TOML.replace('merge = "squash"', 'merge = "squash"\npost_merge = "echo started; sleep 60"'))
        self.worktree()
        t0 = time.monotonic()
        r = self.integrate(
            "app", "ab1", "AB-1", "--title", "t", "--body", "b", "--result", "ok", env={"FOREMAN_POST_MERGE_S": "1"}
        )
        self.assertLess(time.monotonic() - t0, 45)  # far from the 60 s it would take uncut
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("warning: post_merge cut after 1 s", r.stdout)
        self.assertIn("started", r.stdout)
        self.assertEqual(self.pr_state()["state"], "MERGED")  # the merge stands
        [line] = self.progress()
        self.assertIn("post_merge cut after 1 s", line["text"])
        note = next((self.home / "notes").glob("*.md")).read_text()
        self.assertIn("post_merge cut after 1 s (time limit)", note)
        self.assertEqual(self.b.issue(self.b.load(), "AB-1")["status"], "done")
        self.assertEqual(integrate.POST_MERGE_SECONDS, 600)

    def test_post_merge_with_an_unknown_placeholder_is_exit_2(self):
        self.write_toml(TOML.replace('merge = "squash"', 'merge = "squash"\npost_merge = "echo {wt}"'))
        wt = self.worktree()
        r = self.integrate("app", "ab1", "AB-1", "--title", "t", "--body", "b")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("post_merge: bad placeholder", r.stderr)
        self.assertEqual(self.calls(), [])
        self.assertTrue(wt.exists())


LOCAL_TOML = """
[project]
name = "fake"
owner = "Owner"

[paths]
worktrees = "{tmp}/wt"

[repos.app]
path = "{tmp}/local"
remote = "none"
merge = "squash"
local_ci = "test -f feature.txt && test ! -f MAINFAIL && echo \\"$PWD\\" >> {tmp}/ci.log && echo local-ci-ok"
"""


class LocalIntegrateCase(ProjectCase):
    """A repo without a remote whose home lives inside it (ops/), like foreman's own."""

    toml = LOCAL_TOML

    def setUp(self):
        super().setUp()
        self.repo = self.tmp / "local"
        self.home = self.repo / "ops"
        self.home.mkdir(parents=True)
        self.write_toml(LOCAL_TOML)
        (self.tmp / "projects.toml").write_text(f'[[project]]\nname = "fake"\nhome = "{self.home}"\nmode = "active"\n')
        (self.repo / "README.md").write_text("hi\n")
        self.b = Backlog(self.project)
        self.b.init()
        with self.b.mutate() as d:
            self.b.add_project(d, "E1", "One", "intro")
            self.b.add_issue(d, "E1", "AB-1", "feature", status="in progress")
            self.b.add_issue(d, "E1", "AB-2", "after the feature", status="ready", blocked="waits for AB-1")
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "initial")
        with self.b.mutate() as d:  # the orchestrator's uncommitted backlog work under the home
            self.b.set_sub(d, "AB-1", "T1", title="the feature", agent="w1", branch="ab1", status="▶")
        self.assertIn("ops/", git(self.repo, "status", "--porcelain", "--untracked-files=no"))
        gh = self.tmp / "gh"
        gh.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{HERE / "fake_gh.py"}" "$@"\n')
        gh.chmod(0o755)
        self.ghenv = {"FOREMAN_GH": str(gh), "FAKE_GH_DIR": str(self.tmp / "ghstate")}

    def worktree(self, files=None):
        r = self.cli("worktree", "app", "w1", "ab1")
        self.assertEqual(r.returncode, 0, r.stderr)
        wt = self.tmp / "wt" / "app-w1"
        for f, text in (files or {"feature.txt": "new\n"}).items():
            (wt / f).parent.mkdir(parents=True, exist_ok=True)
            (wt / f).write_text(text)
        git(wt, "add", "-A")
        git(wt, "commit", "-q", "-m", "work")
        return wt

    def integrate(self, *args):
        return self.cli("integrate", *args, env=self.ghenv)

    def head(self):
        return git(self.repo, "rev-parse", "main")

    def ci_runs(self):
        f = self.tmp / "ci.log"
        return f.read_text().splitlines() if f.exists() else []

    def assert_no_gh(self):
        self.assertFalse((self.tmp / "ghstate/calls.log").exists())
        self.assertEqual(git(self.repo, "remote"), "")

    def main_commits(self, path, text, msg="pass"):
        """Commits PATH (under the repo) on main, and only it: the fixture leaves ops/backlog.json uncommitted."""
        f = self.repo / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
        git(self.repo, "add", "--", path)
        git(self.repo, "commit", "-q", "-m", msg, "--", path)
        return self.head()

    def assert_branch_untouched(self, wt, tip):
        self.assertEqual(git(wt, "rev-parse", "HEAD"), tip)
        self.assertEqual(git(wt, "status", "--porcelain"), "")
        self.assertNotEqual(
            subprocess.run(
                ["git", "-C", str(wt), "rev-parse", "-q", "--verify", "MERGE_HEAD"], capture_output=True, check=False
            ).returncode,
            0,
        )


class LocalIntegrateTest(LocalIntegrateCase):
    """Integrating without a remote: the fast-forward, --check, a dirty home, a red CI, approvals."""

    def test_fast_forward_green_path(self):
        wt = self.worktree()
        tip = git(wt, "rev-parse", "HEAD")
        backlog_before = (self.home / "backlog.json").read_text()
        r = self.integrate("app", "ab1", "AB-1", "merge msg", "--result", "feature works")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.head(), tip)
        self.assertEqual(git(self.repo, "log", "-1", "--format=%s"), "work")  # no merge commit
        self.assertEqual(self.ci_runs(), [str(wt), str(self.repo)])
        self.assertFalse(wt.exists())
        self.assertNotIn("ab1", git(self.repo, "branch"))
        self.assert_no_gh()
        d = self.b.load()
        i = self.b.issue(d, "AB-1")
        self.assertEqual((i["status"], i["result"], i["subs"][0]["status"]), ("done", "feature works", "done"))
        self.assertEqual(i["subs"][0]["commit"], git(self.repo, "rev-parse", "--short", "main"))
        self.assertEqual(self.b.issue(d, "AB-2")["blocked"], "")
        self.assertNotEqual((self.home / "backlog.json").read_text(), backlog_before)
        note = next((self.home / "notes").glob("*.md")).read_text()
        self.assertIn("Integrated app:ab1 (AB-1)", note)
        self.assertIn("fast-forward (local, no remote)", note)
        self.assertIn("On main: Local CI passed", note)

    def test_check_writes_nothing(self):
        """--check (which an agent may run, unlike the integration itself) leaves main, the worktree and its branch,
        the home and the state as they were."""
        wt = self.worktree()

        def snapshot():
            files = {}
            for root in (self.repo, self.project.state, wt):
                for f in sorted(root.rglob("*")) if root.exists() else ():
                    if f.is_file() and "/.git/" not in str(f):
                        files[str(f)] = f.read_bytes()
            return files, self.head(), git(self.repo, "branch"), git(self.repo, "status", "--porcelain")

        before = snapshot()
        r = self.integrate("app", "ab1", "AB-1", "merge msg", "--result", "ok", "--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(snapshot(), before)
        self.assertEqual(self.ci_runs(), [])
        self.assert_no_gh()

    def test_owner_texts_warn_and_never_block(self):
        self.worktree()
        args = ("app", "ab1", "AB-1", "--title", "Merge fix_widget", "--body", "after T5", "--result", "done in step 3")
        r = self.integrate(*args, "--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for label, flagged in (("PR title", "fix_widget"), ("PR body", "T5"), ("result line", "step 3")):
            self.assertIn(
                f"foreman: warning: the {label} reaches the owner and names without explaining: «{flagged}»", r.stderr
            )
        r = self.integrate(*args)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("the result line reaches the owner", r.stderr)
        self.assertEqual(self.b.issue(self.b.load(), "AB-1")["result"], "done in step 3")

    def test_owner_texts_no_warning_when_plain_or_off(self):
        self.worktree()
        body = self.tmp / "body.md"
        body.write_text("What changes: see T5\n")
        args = ("app", "ab1", "AB-1", "--title", "Export to spreadsheet", "--body-file", str(body), "--check")
        r = self.integrate(*args)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("the PR body reaches the owner", r.stderr)  # read from --body-file
        self.assertNotIn("PR title", r.stderr)

    def test_title_and_body_optional_title_goes_to_note(self):
        self.worktree()
        r = self.integrate("app", "ab1", "AB-1", "--title", "Add the feature")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Add the feature.", next((self.home / "notes").glob("*.md")).read_text())

    def test_dirty_outside_the_home_stops(self):
        self.worktree()
        before = self.head()
        (self.repo / "README.md").write_text("dirty\n")
        r = self.integrate("app", "ab1", "AB-1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("must be on a clean main", r.stderr)
        self.assertEqual(self.head(), before)
        self.assertEqual(self.ci_runs(), [])

    def test_branch_touching_a_dirty_home_file_stops(self):
        self.worktree(files={"feature.txt": "x\n", "ops/backlog.json": "{}\n"})
        before = self.head()
        r = self.integrate("app", "ab1", "AB-1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("uncommitted in", r.stderr)
        self.assertIn("ops/backlog.json", r.stderr)
        self.assertEqual(self.head(), before)

    def test_branch_adding_an_untracked_home_file_stops(self):
        self.worktree(files={"feature.txt": "x\n", "ops/reviews/r.md": "branch\n"})
        (self.home / "reviews").mkdir(exist_ok=True)
        (self.home / "reviews/r.md").write_text("main\n")
        r = self.integrate("app", "ab1", "AB-1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("ops/reviews/r.md", r.stderr)

    def test_ci_red_in_the_branch_merges_nothing(self):
        wt = self.worktree(files={"other.txt": "x\n"})
        before = self.head()
        r = self.integrate("app", "ab1", "AB-1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("local CI failed", r.stderr)
        self.assertIn("nothing merged", r.stderr)
        self.assertEqual(self.head(), before)
        self.assertTrue(wt.exists())
        self.assertEqual(self.b.issue(self.b.load(), "AB-1")["status"], "in progress")
        self.assert_no_gh()

    def test_ci_red_on_main_goes_back_keeping_the_home(self):
        wt = self.worktree()
        before = self.head()
        backlog = (self.home / "backlog.json").read_text()
        (self.repo / "MAINFAIL").write_text("")  # untracked: only the run on main sees it
        r = self.integrate("app", "ab1", "AB-1")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("after the fast-forward", r.stderr)
        self.assertEqual(self.head(), before)
        self.assertEqual(self.ci_runs(), [str(wt)])
        self.assertTrue(wt.exists())
        self.assertIn("ab1", git(self.repo, "branch"))
        self.assertEqual((self.home / "backlog.json").read_text(), backlog)  # the uncommitted home survives
        self.assertEqual(self.b.issue(self.b.load(), "AB-1")["status"], "in progress")

    def test_check_says_it_would_merge_and_writes_nothing(self):
        wt = self.worktree()
        tip = git(wt, "rev-parse", "HEAD")
        before = self.main_commits("ops/notes/x.md", "pass\n")
        status = git(self.repo, "status", "--porcelain")
        r = self.integrate("app", "ab1", "AB-1", "--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("would merge main (1 commit, only under ops/) into ab1: clean", r.stdout)
        self.assertEqual((self.head(), git(self.repo, "status", "--porcelain")), (before, status))
        self.assert_branch_untouched(wt, tip)
        self.assertEqual(self.ci_runs(), [])

    def test_approval_gate_exits_3_without_merging(self):
        self.write_toml(LOCAL_TOML.replace('remote = "none"', 'remote = "none"\nrequire_approval = true'))
        wt = self.worktree()
        before = self.head()
        r = self.integrate("app", "ab1", "AB-1")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("awaiting the owner's approval", r.stderr)
        self.assertEqual(self.head(), before)
        self.assertTrue(wt.exists())
        self.assert_no_gh()
        r = self.integrate("app", "ab1", "AB-1", "--approved", "Owner, panel 10:00", "--result", "ok")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.head(), git(self.repo, "log", "-1", "--format=%H"))
        self.assertEqual(self.b.issue(self.b.load(), "AB-1")["status"], "done")

    def test_bad_verdict_merges_nothing(self):
        with self.b.mutate() as d:
            self.b.set_outcome(d, self.b.issue(d, "AB-1"), criterion="the feature works")
        wt = self.worktree()
        before = self.head()
        backlog = (self.home / "backlog.json").read_text()
        r = self.integrate("app", "ab1", "AB-1", "--result", "ok", "--verdict", "met: it works")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("bad verdict 'met: it works': one of met", r.stderr)
        self.assertEqual(self.head(), before)
        self.assertTrue(wt.exists())
        self.assertIn("ab1", git(self.repo, "branch"))
        self.assertEqual(self.ci_runs(), [])
        self.assertEqual((self.home / "backlog.json").read_text(), backlog)
        self.assertEqual(list((self.home / "notes").glob("*.md")) if (self.home / "notes").exists() else [], [])
        self.assert_no_gh()

    def test_check_shows_a_numbered_check_section(self):
        (self.home / "reviews").mkdir(exist_ok=True)
        for n, heading in enumerate(("## 6. How to check", "## 2 Cómo se comprueba", "## How to check")):
            (self.home / "reviews/plan.md").write_text(
                f"# Plan\n\n## 1. Goal\n\nthe goal\n\n{heading}\n\nrun probe {n}\n\n## 7. Risks\n\nnone\n"
            )
            with self.b.mutate() as d:
                self.b.edit(d, "AB-1", plan=["reviews/plan.md"])
            self.worktree() if n == 0 else None
            r = self.integrate("app", "ab1", "AB-1", "--check")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            shown = r.stdout.split("what the plan asks to check", 1)[1]
            self.assertIn(f"run probe {n}", shown, heading)
            self.assertNotIn("none", shown.split("==", 1)[0], heading)
            self.assertNotIn("section)", shown.split("==", 1)[0], heading)

    def test_check_touches_nothing(self):
        wt = self.worktree()
        before = self.head()
        status = git(self.repo, "status", "--porcelain")
        r = self.integrate("app", "ab1", "AB-1", "--check")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("would do: AB-1 T1", r.stdout)
        self.assertIn("git merge --ff-only ab1", r.stdout)
        self.assertIn("merge = squash does not apply", r.stdout)
        self.assertEqual((self.head(), git(self.repo, "status", "--porcelain")), (before, status))
        self.assertTrue(wt.exists())
        self.assertEqual(self.ci_runs(), [])
        self.assert_no_gh()


class LocalMainMovedTest(LocalIntegrateCase):
    """Main moved since the branch: merged in when only the home changed, stops otherwise."""

    def test_main_moved_outside_the_home_stops(self):
        wt = self.worktree()
        tip = git(wt, "rev-parse", "HEAD")
        before = self.main_commits("README.md", "moved on\n", "main moves")
        r = self.integrate("app", "ab1", "AB-1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("does not contain main", r.stderr)
        self.assertIn("changed outside the home ops/ (README.md)", r.stderr)
        self.assertEqual(self.head(), before)
        self.assert_branch_untouched(wt, tip)
        self.assertEqual(self.ci_runs(), [])

    def test_main_moved_only_in_the_home_is_merged_in(self):
        wt = self.worktree()
        tip = git(wt, "rev-parse", "HEAD")
        self.main_commits("ops/notes/x.md", "pass 1\n")
        main_before = self.main_commits("ops/reviews/r.md", "report\n")
        r = self.integrate("app", "ab1", "AB-1", "--result", "ok")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("git merge --no-ff --no-edit main", r.stdout)
        self.assertIn("2 commits, only under ops/", r.stdout)
        self.assertEqual(git(self.repo, "rev-parse", "main^1"), tip)
        self.assertEqual(git(self.repo, "rev-parse", "main^2"), main_before)
        self.assertEqual(git(self.repo, "log", "-1", "--format=%s", "main"), "Merge branch 'main' into ab1")
        self.assertEqual(self.ci_runs(), [str(wt), str(self.repo)])
        self.assertFalse(wt.exists())
        self.assertEqual(self.b.issue(self.b.load(), "AB-1")["status"], "done")
        # the integration's note, not the x.md the test committed (glob's order is the file system's)
        notes = [f for f in sorted((self.home / "notes").glob("*.md")) if f.name != "x.md"]
        self.assertEqual(len(notes), 1, notes)
        self.assertIn("home commits merged in first (2)", notes[0].read_text())

    def test_main_moved_in_the_home_and_the_branch_too(self):
        wt = self.worktree(files={"feature.txt": "x\n", "ops/tests/t.tsv": "a\tb\n"})
        tip = git(wt, "rev-parse", "HEAD")
        self.main_commits("ops/notes/x.md", "pass\n")
        r = self.integrate("app", "ab1", "AB-1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(git(self.repo, "rev-parse", "main^1"), tip)
        self.assertEqual((self.repo / "ops/tests/t.tsv").read_text(), "a\tb\n")
        self.assertEqual((self.repo / "ops/notes/x.md").read_text(), "pass\n")
        self.assertEqual(self.ci_runs(), [str(wt), str(self.repo)])

    def test_fast_forward_runs_no_hook(self):
        wt = self.worktree()
        marker = self.tmp / "fired.log"
        plant_hooks(self.repo, marker)
        r = self.integrate("app", "ab1", "AB-1", "--result", "ok")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(wt.exists())
        self.assertEqual(fired(marker), [])
        git(self.repo, "checkout", "-q", "-b", "probe")  # the hooks are live: plain git runs them
        self.assertIn("post-checkout", fired(marker))

    def test_merging_main_in_runs_no_hook(self):
        self.worktree()
        self.main_commits("ops/notes/x.md", "pass\n")
        marker = self.tmp / "fired.log"
        plant_hooks(self.repo, marker)
        r = self.integrate("app", "ab1", "AB-1", "--result", "ok")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("git merge --no-ff --no-edit main", r.stdout)
        self.assertEqual(fired(marker), [])

    def test_rebase_from_runs_no_hook(self):
        fork = self.head()
        self.worktree()
        self.main_commits("ops/notes/x.md", "pass\n")
        marker = self.tmp / "fired.log"
        plant_hooks(self.repo, marker)
        r = self.integrate("app", "ab1", "AB-1", "--rebase-from", fork, "--result", "ok")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"git rebase --onto main {fork}", r.stdout)
        self.assertEqual(git(self.repo, "log", "-1", "--format=%s", "main"), "work")  # rebased: no merge commit
        self.assertEqual(fired(marker), [])

    def test_home_conflict_stops_clean(self):
        wt = self.worktree(files={"feature.txt": "x\n", "ops/notes/x.md": "branch\n"})
        tip = git(wt, "rev-parse", "HEAD")
        before = self.main_commits("ops/notes/x.md", "main\n")
        r = self.integrate("app", "ab1", "AB-1")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("conflicts in ops/notes/x.md", r.stderr)
        self.assertEqual(self.head(), before)
        self.assert_branch_untouched(wt, tip)
        self.assertEqual(self.ci_runs(), [])

    def test_rename_out_of_the_home_stops(self):
        wt = self.worktree()
        self.main_commits("ops/a.md", "a\n")
        git(wt, "merge", "-q", "--no-edit", "main")  # the branch has ops/a.md; then main moves it out
        tip = git(wt, "rev-parse", "HEAD")
        git(self.repo, "mv", "ops/a.md", "lib_a.md")
        git(self.repo, "commit", "-q", "-m", "move", "--", "ops/a.md", "lib_a.md")
        before = self.head()
        r = self.integrate("app", "ab1", "AB-1")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("changed outside the home ops/ (lib_a.md)", r.stderr)
        self.assertEqual(self.head(), before)
        self.assert_branch_untouched(wt, tip)

    def test_more_than_five_outside_paths_are_cut(self):
        self.worktree()
        for i in range(7):
            self.main_commits(f"src/f{i}.py", "x\n")
        r = self.integrate("app", "ab1", "AB-1", "--check")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("src/f0.py, src/f1.py, src/f2.py, src/f3.py, src/f4.py and 2 more", r.stderr)

    def test_branch_behind_main_stops(self):
        self.cli("worktree", "app", "w1", "ab1")
        wt = self.tmp / "wt" / "app-w1"
        self.main_commits("ops/notes/x.md", "pass\n")
        r = self.integrate("app", "ab1", "AB-1")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("nothing of its own to integrate", r.stderr)
        self.assertTrue(wt.exists())

    def test_ci_red_after_merging_main_says_so(self):
        wt = self.worktree(files={"other.txt": "x\n"})
        before = self.main_commits("ops/notes/x.md", "pass\n")
        r = self.integrate("app", "ab1", "AB-1")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("main merged into ab1 as", r.stderr)
        self.assertIn("nothing merged into main", r.stderr)
        self.assertEqual(self.head(), before)
        self.assertEqual(git(wt, "rev-parse", "HEAD^2"), before)  # the branch keeps the merge, as by hand


class LocalReportTest(LocalIntegrateCase):
    """Where the report of a delivery is found, and when it is missing."""

    def test_report_outside_reviews_merges_nothing(self):
        wt = self.worktree()
        before = self.head()
        (self.home / "elsewhere.md").write_text("report\n")
        r = self.integrate("app", "ab1", "AB-1", "--report", "elsewhere.md")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("--report must be a path under", r.stderr)
        self.assertEqual(self.head(), before)
        self.assertTrue(wt.exists())

    def test_report_committed_in_the_branch_is_linked(self):
        wt = self.worktree(files={"feature.txt": "new\n", "ops/reviews/r.md": "the agent's report\n"})
        tip = git(wt, "rev-parse", "HEAD")
        self.assertFalse((self.home / "reviews/r.md").exists())
        r = self.integrate("app", "ab1", "AB-1", "--report", "reviews/r.md", "--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("would do: AB-1 T1 ✓ <merge>, report reviews/r.md", r.stdout)
        r = self.integrate("app", "ab1", "AB-1", "--report", "reviews/r.md", "--result", "ok")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.head(), tip)
        self.assertEqual((self.home / "reviews/r.md").read_text(), "the agent's report\n")
        sub = self.b.issue(self.b.load(), "AB-1")["subs"][0]
        self.assertEqual((sub["status"], sub["report"]), ("done", "reviews/r.md"))
        self.assertEqual([e for e in backlog_check.validate(self.b, self.b.load()) if "report" in e], [])

    def test_without_report_the_sub_issue_or_the_delivery_gives_it(self):
        """No --report: the one the agent named at delivery (`progress --done --report`), and the sub-issue's field
        over it; one that exists nowhere is not taken."""
        wt = self.worktree(files={"feature.txt": "new\n", "ops/reviews/r.md": "report\n", "ops/reviews/s.md": "s\n"})
        r = self.cli(
            "--project",
            "fake",
            "progress",
            "--done",
            "x",
            "--report",
            "reviews/r.md",
            cwd=wt,
            env={"FOREMAN_WHO": "w1"},
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        util.write_json(
            loop.agents_file(self.project),
            {"a1": {"name": "w1", "branch": "ab1", "delivered": "2026-11-01T10:00:00", "report": "reviews/r.md"}},
        )
        r = self.integrate("app", "ab1", "AB-1", "--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("== report: reviews/r.md", r.stdout)
        self.assertIn("would do: AB-1 T1 ✓ <merge>, report reviews/r.md", r.stdout)
        with self.b.mutate() as d:
            self.b.set_sub(d, "AB-1", "T1", report="reviews/s.md")
        self.assertIn("report reviews/s.md", self.integrate("app", "ab1", "AB-1", "--check").stdout)
        with self.b.mutate() as d:
            self.b.set_sub(d, "AB-1", "T1", report="reviews/gone.md")
        util.write_json(loop.agents_file(self.project), {})
        r = self.integrate("app", "ab1", "AB-1", "--check")  # the progress line by the sub-issue's agent
        self.assertIn("report reviews/r.md", r.stdout)
        (self.project.state / "progress.jsonl").unlink()
        r = self.integrate("app", "ab1", "AB-1", "--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("would do: AB-1 T1 ✓ <merge>\n", r.stdout)
        self.assertNotIn("== report:", r.stdout)

    def test_report_neither_in_main_nor_in_the_branch_stops(self):
        wt = self.worktree(files={"feature.txt": "new\n", "ops/reviews/other.md": "x\n", "reviews/r.md": "x\n"})
        before = self.head()
        for path in ("reviews/r.md", "reviews"):  # outside the home in the branch; a directory, not a file
            r = self.integrate("app", "ab1", "AB-1", "--report", path)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn(f"--report {path} does not exist", r.stderr)
        self.assertEqual(self.head(), before)
        self.assertTrue(wt.exists())
        self.assertEqual(self.ci_runs(), [])


class LocalCiRunsTest(LocalIntegrateCase):
    """The CI on main after the fast-forward (ci_runs, main_ci) and post_merge."""

    def test_ci_runs_repeat_on_main_after_the_fast_forward(self):
        self.write_toml(
            LOCAL_TOML.replace('echo \\"$PWD\\"', 'echo \\"$PWD {ci}\\"').replace(
                'remote = "none"', 'remote = "none"\nci_summary = "cat"'
            )
        )
        wt = self.worktree()
        r = self.integrate("app", "ab1", "AB-1", "--ci=--a", "--ci=--b", "--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"2 runs (--ci) in {wt}, then the same on main after the fast-forward", r.stdout)
        self.assertIn("ci_summary does not apply without a remote", r.stdout)
        self.assertEqual(self.ci_runs(), [])
        r = self.integrate("app", "ab1", "AB-1", "--ci=--a", "--ci=--b", "--result", "ok")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.ci_runs(), [f"{wt} --a", f"{wt} --b", f"{self.repo} --a", f"{self.repo} --b"])
        log = (self.project.state / "integrate/ab1-ci.log").read_text()
        self.assertEqual(log.count("== rc 0 in"), 4)
        note = next((self.home / "notes").glob("*.md")).read_text()
        self.assertIn("runs: `--a`", note)
        self.assertIn("On main: Local CI passed", note)
        self.assert_no_gh()

    def test_a_failing_run_on_main_goes_back(self):
        self.write_toml(
            LOCAL_TOML.replace("test ! -f MAINFAIL", "test ! -f MAINFAIL{ci}").replace(
                'echo \\"$PWD\\"', 'echo \\"$PWD {ci}\\"'
            )
        )
        wt = self.worktree()
        before = self.head()
        (self.repo / "MAINFAIL-b").write_text("")  # untracked: only the second run on main sees it
        r = self.integrate("app", "ab1", "AB-1", "--ci=-a", "--ci=-b")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("after the fast-forward", r.stderr)
        self.assertIn("(run 2/2, `-b`)", r.stderr)
        self.assertEqual(self.head(), before)
        self.assertEqual(self.ci_runs(), [f"{wt} -a", f"{wt} -b", f"{self.repo} -a"])
        self.assertTrue(wt.exists())

    def test_post_merge_runs_after_the_fast_forward_and_the_ci_on_main(self):
        self.write_toml(
            LOCAL_TOML.replace(
                'remote = "none"',
                'remote = "none"\npost_merge = "echo {before}..{head} > {tmp}/post.txt && echo done {main}"',
            )
        )
        wt = self.worktree()
        before, tip = self.head(), git(wt, "rev-parse", "HEAD")
        r = self.integrate("app", "ab1", "AB-1", "--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"echo {{before}}..{{head}} > {self.tmp}/post.txt && echo done {self.repo}", r.stdout)
        self.assertFalse((self.tmp / "post.txt").exists())
        r = self.integrate("app", "ab1", "AB-1", "--result", "ok")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.tmp / "post.txt").read_text().strip(), f"{before}..{tip}")
        out = r.stdout
        self.assertLess(out.index(f"== local CI in {self.repo}"), out.index("== post_merge"))
        self.assertLess(out.index("== post_merge"), out.index("== backlog"))
        note = next((self.home / "notes").glob("*.md")).read_text()
        self.assertIn(f"post_merge: done {self.repo}", note)
        self.assert_no_gh()

    def test_a_failing_post_merge_warns_and_exits_0_after_the_fast_forward(self):
        self.write_toml(LOCAL_TOML.replace('remote = "none"', 'remote = "none"\npost_merge = "false"'))
        wt = self.worktree()
        tip = git(wt, "rev-parse", "HEAD")
        r = self.integrate("app", "ab1", "AB-1", "--result", "ok")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("warning: post_merge failed (rc 1)", r.stdout)
        self.assertEqual(self.head(), tip)  # never undone
        self.assertFalse(wt.exists())
        lines = [json.loads(x) for x in (self.project.state / "progress.jsonl").read_text().splitlines()]
        self.assertEqual([x["agent"] for x in lines], ["orchestrator"])
        self.assertIn("post_merge failed (rc 1)", lines[0]["text"])
        self.assertIn("post_merge failed (rc 1)", next((self.home / "notes").glob("*.md")).read_text())
        self.assertEqual(self.b.issue(self.b.load(), "AB-1")["status"], "done")

    def with_main_ci(self, value, ci=None):
        toml = LOCAL_TOML.replace('remote = "none"', f'remote = "none"\nmain_ci = {value}')
        self.write_toml(toml.replace("echo local-ci-ok", ci) if ci else toml)

    def main_runs(self):
        f = self.tmp / "main.log"
        return f.read_text().splitlines() if f.exists() else []

    def note(self):
        return next((self.home / "notes").glob("*.md")).read_text()

    def test_main_ci_runs_on_main_instead_of_the_local_ci(self):
        self.with_main_ci('"echo main {main} {branch} >> {tmp}/main.log && echo main-ci-ok"')
        wt = self.worktree()
        tip = git(wt, "rev-parse", "HEAD")
        r = self.integrate("app", "ab1", "AB-1", "--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(
            f"then main_ci on main after the fast-forward (the same runs if main is not the commit that "
            f"passed): echo main {self.repo} ab1 >> {self.tmp}/main.log",
            r.stdout,
        )
        r = self.integrate("app", "ab1", "AB-1", "--result", "ok")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.head(), tip)
        self.assertEqual(self.ci_runs(), [str(wt)])  # the full CI only in the worktree
        self.assertEqual(self.main_runs(), [f"main {self.repo} ab1"])
        self.assertIn(f"== main_ci in {self.repo}", r.stdout)
        self.assertIn("main-ci-ok", (self.project.state / "integrate/ab1-ci.log").read_text())
        self.assertIn(f"On main: main_ci passed on {tip[:7]} in", self.note())
        self.assertFalse(wt.exists())
        self.assert_no_gh()

    def test_main_ci_with_the_ci_placeholder_runs_once_per_run(self):
        self.with_main_ci('"echo main{ci} >> {tmp}/main.log"', ci="echo local-ci-ok {ci}")
        wt = self.worktree()
        r = self.integrate("app", "ab1", "AB-1", "--ci=-a", "--ci=-b", "--result", "ok")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.ci_runs(), [str(wt), str(wt)])
        self.assertEqual(self.main_runs(), ["main-a", "main-b"])
        self.assertIn("On main: main_ci passed on", self.note())

    def test_main_ci_runs_the_full_ci_when_main_is_another_commit(self):
        # the first run in the worktree commits there: the tip that reaches main is not the commit the CI passed on
        self.with_main_ci(
            '"echo main >> {tmp}/main.log"',
            ci="echo local-ci-ok && if [ -f {tmp}/MOVE ]; then rm {tmp}/MOVE && "
            "git -c user.name=t -c user.email=t@t commit -q --allow-empty -m moved; fi",
        )
        (self.tmp / "MOVE").write_text("")
        wt = self.worktree()
        tested = git(wt, "rev-parse", "HEAD")
        r = self.integrate("app", "ab1", "AB-1", "--result", "ok")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotEqual(self.head(), tested)
        self.assertEqual(git(self.repo, "log", "-1", "--format=%s"), "moved")
        self.assertEqual(self.ci_runs(), [str(wt), str(self.repo)])
        self.assertEqual(self.main_runs(), [])
        self.assertIn(f"not {tested[:7]} where the local CI passed", r.stdout)
        self.assertIn("On main: Local CI passed", self.note())
        self.assertIn("full local CI, not main_ci", self.note())

    def test_empty_main_ci_runs_nothing_on_main(self):
        self.with_main_ci('""')
        wt = self.worktree()
        tip = git(wt, "rev-parse", "HEAD")
        r = self.integrate("app", "ab1", "AB-1", "--check")
        self.assertIn("then nothing on main after the fast-forward (main_ci is empty", r.stdout)
        r = self.integrate("app", "ab1", "AB-1", "--result", "ok")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.head(), tip)
        self.assertEqual(self.ci_runs(), [str(wt)])
        self.assertIn(
            f"On main: nothing run (main_ci is empty; {tip[:7]} is the commit the local CI passed on)", self.note()
        )

    def test_a_failing_main_ci_goes_back(self):
        self.with_main_ci('"test ! -f MAINFAIL"')
        wt = self.worktree()
        before = self.head()
        (self.repo / "MAINFAIL").write_text("")  # untracked, only in the main checkout
        r = self.integrate("app", "ab1", "AB-1")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn(f"main_ci failed in {self.repo}", r.stderr)
        self.assertIn("after the fast-forward: main is back at", r.stderr)
        self.assertEqual(self.head(), before)
        self.assertEqual(self.ci_runs(), [str(wt)])
        self.assertTrue(wt.exists())

    def test_main_ci_with_an_unknown_placeholder_is_exit_2(self):
        self.with_main_ci('"echo {nope}"')
        wt = self.worktree()
        before = self.head()
        r = self.integrate("app", "ab1", "AB-1")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("main_ci: bad placeholder", r.stderr)
        self.assertEqual((self.head(), self.ci_runs()), (before, []))
        self.assertTrue(wt.exists())

    def test_main_ci_must_be_a_string(self):
        for bad in ("3", '["a"]', "false"):
            with self.assertRaises(config.ConfigError, msg=bad):
                self.with_main_ci(bad)

    def test_remote_must_be_a_name(self):
        with self.assertRaises(config.ConfigError):
            self.write_toml(LOCAL_TOML.replace('remote = "none"', 'remote = "a b"'))


class LocalMeasureTest(LocalIntegrateCase):
    """Measurements (--measure): closed without merging."""

    def as_measure(self, sid="T1"):
        with self.b.mutate() as d:
            self.b.set_sub(d, "AB-1", sid, kind="measure")

    def test_measure_closes_without_merging(self):
        """Case g: main keeps its SHA, no CI, the worktree and the branch go, the report the branch committed is
        copied into the home, the sub-issue is done without a commit and the note says «Measured»."""
        self.as_measure()
        wt = self.worktree(files={"lab.py": "probe\n", "ops/reviews/m.md": "figures\n"})
        before = self.head()
        r = self.integrate("app", "ab1", "AB-1 T1", "--measure", "--report", "reviews/m.md", "--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("would do: AB-1 T1 ✓, report reviews/m.md", r.stdout)
        self.assertIn(f"would remove the worktree {wt}; branch ab1 deleted (tip ", r.stdout)
        self.assertIn("1 commit not in main)", r.stdout)
        self.assertTrue(wt.exists())
        self.assertFalse((self.home / "reviews/m.md").exists())
        r = self.integrate("app", "ab1", "AB-1 T1", "--measure", "--report", "reviews/m.md", "--result", "measured")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("(copied from the branch into the home)", r.stdout)
        self.assertEqual(self.head(), before)
        self.assertEqual(self.ci_runs(), [])
        self.assert_no_gh()
        self.assertFalse(wt.exists())
        self.assertNotIn("ab1", git(self.repo, "branch"))
        self.assertEqual((self.home / "reviews/m.md").read_text(), "figures\n")
        i = self.b.issue(self.b.load(), "AB-1")
        sub = i["subs"][0]
        self.assertEqual((sub["status"], sub.get("commit"), sub["report"]), ("done", None, "reviews/m.md"))
        self.assertEqual((i["status"], i["result"]), ("done", "measured"))
        note = next((self.home / "notes").glob("*.md")).read_text()
        self.assertIn("Measured app:ab1 (AB-1 T1)", note)
        self.assertIn(f"main {git(self.repo, 'rev-parse', '--short', 'main')} unchanged", note)
        self.assertNotIn("Integrated", note)

    def test_measure_keep_branch_and_a_report_only_in_the_worktree(self):
        self.as_measure()
        wt = self.worktree(files={"lab.py": "probe\n"})
        tip = git(wt, "rev-parse", "HEAD")
        (wt / "ops/reviews").mkdir(parents=True)
        (wt / "ops/reviews/m.md").write_text("uncommitted figures\n")
        (wt / "out.bin").write_text("lab output\n")
        args = ("app", "ab1", "AB-1 T1", "--measure", "--report", "reviews/m.md", "--keep-branch")
        r = self.integrate(*args)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("has uncommitted or untracked files (out.bin)", r.stderr)
        self.assertTrue(wt.exists())
        self.assertFalse((self.home / "reviews/m.md").exists())
        (wt / "out.bin").unlink()
        r = self.integrate(*args)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("(copied from the worktree into the home)", r.stdout)
        self.assertFalse(wt.exists())
        self.assertEqual(git(self.repo, "rev-parse", "ab1"), tip)  # kept
        self.assertEqual((self.home / "reviews/m.md").read_text(), "uncommitted figures\n")
        self.assertEqual(self.b.issue(self.b.load(), "AB-1")["subs"][0]["status"], "done")

    def test_measure_needs_a_measurement_and_its_report(self):
        wt = self.worktree(files={"lab.py": "probe\n", "ops/reviews/m.md": "figures\n"})
        r = self.integrate("app", "ab1", "AB-1 T1", "--keep-branch")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("--keep-branch is for --measure", r.stderr)
        r = self.integrate("app", "ab1", "AB-1 T1", "--measure", "--report", "reviews/m.md")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("not a measurement (AB-1 T1): drop --measure", r.stderr)
        self.assertIn("foreman backlog sub AB-1 T1 --kind measure", r.stderr)
        r = self.integrate("app", "ab1", "AB-1 T1", "--measure", "--no-ci")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("--measure merges nothing", r.stderr)
        # the delivery said --kind measure: --measure is accepted, and a merge gets a warning
        util.write_json(
            loop.agents_file(self.project),
            {"a1": {"name": "w1", "branch": "ab1", "delivered": "2026-11-01T10:00:00", "kind": "measure"}},
        )
        r = self.integrate("app", "ab1", "AB-1 T1", "--measure", "--report", "reviews/m.md", "--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = self.integrate("app", "ab1", "AB-1 T1", "--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("warning: the agent delivered AB-1 T1 as a measurement", r.stdout)
        util.write_json(loop.agents_file(self.project), {})
        self.as_measure()
        r = self.integrate("app", "ab1", "AB-1 T1", "--measure")  # no report anywhere
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("a measurement closes with its report", r.stderr)
        self.assertTrue(wt.exists())

    def test_a_measurement_is_not_merged_without_measure(self):
        """Case h: exit 2 saying how to go on; nothing merged."""
        self.as_measure()
        wt = self.worktree()
        before = self.head()
        r = self.integrate("app", "ab1", "AB-1 T1")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("AB-1 T1 is a measurement: close it with --measure", r.stderr)
        self.assertIn("`foreman backlog sub AB-1 T1 --kind code`", r.stderr)
        self.assertEqual((self.head(), self.ci_runs()), (before, []))
        self.assertTrue(wt.exists())

    def test_a_mixed_branch_merges_and_closes_the_measurement_without_the_commit(self):
        with self.b.mutate() as d:
            self.b.set_sub(d, "AB-1", "T2", title="measure it", agent="w1", branch="ab1", status="▶", kind="measure")
        self.worktree()
        r = self.integrate("app", "ab1", "AB-1", "--result", "done")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        subs = self.b.issue(self.b.load(), "AB-1")["subs"]
        short = git(self.repo, "rev-parse", "--short", "main")
        self.assertEqual(
            [(s["id"], s["status"], s.get("commit")) for s in subs], [("T1", "done", short), ("T2", "done", None)]
        )

    def test_closing_a_code_sub_issue_by_hand_without_a_commit_warns(self):
        self.worktree()
        r = self.cli("backlog", "sub", "AB-1", "T1", "--status", "done")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("warning: AB-1 T1 closed without a commit while app:ab1 is not merged", r.stderr)
        for args in (("sub", "AB-1", "T1", "--status", "done"), ("status", "AB-1/T1", "done")):
            r = self.cli("backlog", *args)  # already done: said once
            self.assertNotIn("warning", r.stderr, args)
        r = self.cli("backlog", "status", "AB-1/T1", "todo")
        r = self.cli("backlog", "status", "AB-1/T1", "done")
        self.assertIn("closed without a commit", r.stderr)
        self.cli("backlog", "sub", "AB-1", "T1", "--status", "todo")
        for args in (("--commit", "abc1234"), ("--kind", "measure")):
            r = self.cli("backlog", "sub", "AB-1", "T1", "--status", "done", *args)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("warning", r.stderr, args)
            self.cli("backlog", "sub", "AB-1", "T1", "--status", "todo", "--commit", "", "--kind", "code")
        git(self.repo, "merge", "-q", "--ff-only", "ab1")
        r = self.cli("backlog", "sub", "AB-1", "T1", "--status", "done")
        self.assertNotIn("warning", r.stderr)  # merged


class LivePermissionsTest(LocalIntegrateCase):
    """A branch that changes the live foreman.toml or guard table."""

    PERM_TOML = (
        LOCAL_TOML
        + """
[deny]
patterns = ["make deploy", "make release"]

[check]
guard_table = "tests/guard.tsv"
"""
    )

    PERM_TABLE = "deny\tmake deploy\nallow\tgit status\n"

    def live_permissions(self):
        """main with a [deny] list and a guard table under the home, committed (the backlog stays uncommitted)."""
        self.write_toml(self.PERM_TOML)
        (self.home / "tests").mkdir(exist_ok=True)
        (self.home / "tests/guard.tsv").write_text(self.PERM_TABLE)
        git(self.repo, "add", "--", "ops/foreman.toml", "ops/tests/guard.tsv")
        git(self.repo, "commit", "-q", "-m", "permissions", "--", "ops/foreman.toml", "ops/tests/guard.tsv")
        return (self.home / "foreman.toml").read_text()

    def progress_lines(self):
        f = self.project.state / "progress.jsonl"
        return [json.loads(x)["text"] for x in f.read_text().splitlines()] if f.exists() else []

    def test_a_branch_that_only_restricts_integrates_and_the_note_says_so(self):
        live = self.live_permissions()
        tip_before = self.head()
        self.worktree(
            files={
                "feature.txt": "x\n",
                "ops/foreman.toml": live.replace('"make release"]', '"make release", "make nuke"]'),
            }
        )
        r = self.integrate("app", "ab1", "AB-1", "--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("== permissions: ab1 changes live files of fake (ops/foreman.toml)", r.stdout)
        self.assertIn("+ «make nuke» (never)", r.stdout)
        self.assertIn("the permissions only get stricter", r.stdout)
        r = self.integrate("app", "ab1", "AB-1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("make nuke", (self.home / "foreman.toml").read_text())
        note = next((self.home / "notes").glob("*.md")).read_text()
        self.assertIn("permissions of fake: 1 rules and 0 row verdicts change, 0 rows added, 0 removed", note)
        self.assertIn(f"`git show {tip_before[:7]}:ops/foreman.toml`", note)

    def test_a_branch_that_opens_stops_before_the_ci_and_passes_with_opens(self):
        live = self.live_permissions()
        before = self.head()
        wt = self.worktree(files={"feature.txt": "x\n", "ops/foreman.toml": live.replace(', "make release"]', "]")})
        r = self.integrate("app", "ab1", "AB-1", "--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("- «make release» (never)", r.stdout)
        self.assertIn("it would need --opens (Owner's yes/no) before the CI", r.stdout)
        r = self.integrate("app", "ab1", "AB-1", "--approved", "Owner, now")  # --approved is not the owner's yes
        self.assertEqual(r.returncode, integrate.AWAITING, r.stdout + r.stderr)
        self.assertIn("allow something that was not allowed before", r.stderr)
        self.assertIn("run it again with --opens", r.stderr)
        self.assertEqual((self.head(), self.ci_runs()), (before, []))  # nothing merged, no CI
        self.assertTrue(wt.exists())
        self.assertIn("waits for Owner's yes", self.progress_lines()[-1])
        r = self.integrate("app", "ab1", "AB-1", "--opens")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("with Owner's yes (--opens): the integration goes on", r.stdout)
        self.assertNotIn("make release", (self.home / "foreman.toml").read_text())
        self.assertIn(
            "allows something new (- «make release» (never)), with Owner's yes",
            next((self.home / "notes").glob("*.md")).read_text(),
        )

    def test_a_candidate_that_does_not_load_stops_with_2(self):
        live = self.live_permissions()
        before = self.head()
        self.worktree(files={"feature.txt": "x\n", "ops/foreman.toml": live.replace('merge = "squash"', "merge = 7")})
        r = self.integrate("app", "ab1", "AB-1", "--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("It does not load: ops/foreman.toml", r.stdout)
        self.assertIn("would stop here, before the CI (exit 2)", r.stdout)
        r = self.integrate("app", "ab1", "AB-1", "--opens")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("would leave the product without a guard", r.stderr)
        self.assertEqual((self.head(), self.ci_runs()), (before, []))

    def test_a_deleted_foreman_toml_is_broken(self):
        self.live_permissions()
        wt = self.worktree()
        git(wt, "rm", "-q", "ops/foreman.toml")
        git(wt, "commit", "-q", "-m", "rm")
        r = self.integrate("app", "ab1", "AB-1")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("ops/foreman.toml is deleted", r.stderr)

    def test_a_table_row_that_fails_stops_with_1(self):
        self.live_permissions()
        before = self.head()
        self.worktree(files={"feature.txt": "x\n", "ops/tests/guard.tsv": self.PERM_TABLE + "allow\tmake deploy\n"})
        r = self.integrate("app", "ab1", "AB-1", "--opens")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("guard.tsv:3: want allow, got deny", r.stdout)
        self.assertIn("would make a row of the guard table fail", r.stderr)
        self.assertEqual((self.head(), self.ci_runs()), (before, []))

    def test_rows_added_or_removed_integrate_and_are_counted(self):
        self.live_permissions()
        self.worktree(files={"feature.txt": "x\n", "ops/tests/guard.tsv": "deny\tmake release\ndeny\tmake deploy\n"})
        r = self.integrate("app", "ab1", "AB-1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("== permissions: ab1 changes live files of fake (ops/tests/guard.tsv)", r.stdout)
        self.assertIn("Guard table: 1 rows added, 1 removed", r.stdout)
        self.assertIn("1 rows added, 1 removed", next((self.home / "notes").glob("*.md")).read_text())

    def test_files_that_are_not_live_have_no_section(self):
        self.live_permissions()
        self.worktree(files={"feature.txt": "x\n", "examples/x/foreman.toml": "[deny]\n", "ops/guard.tsv": "x\n"})
        r = self.integrate("app", "ab1", "AB-1", "--opens")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("== permissions:", r.stdout)
        self.assertIn("--opens: nothing the branch changes allows anything new", r.stdout)

    def test_the_merge_with_mains_home_commits_is_what_is_tested(self):
        live = self.live_permissions()
        wt = self.worktree(
            files={"feature.txt": "x\n", "ops/tests/guard.tsv": self.PERM_TABLE + "deny\tmake release\n"}
        )
        # main (the orchestrator) then removes «make release» from [deny]: the merge has the new row and not the rule
        self.main_commits("ops/foreman.toml", live.replace(', "make release"]', "]"))
        r = self.integrate("app", "ab1", "AB-1", "--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("guard.tsv:3: want deny, got allow", r.stdout)
        r = self.integrate("app", "ab1", "AB-1")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertTrue(wt.exists())

    def test_unknown_keys_warn_and_never_stop(self):
        live = self.live_permissions()
        self.worktree(
            files={
                "feature.txt": "x\n",
                "ops/foreman.toml": live.replace('merge = "squash"', 'merge = "squash"\nshiny = 1'),
            }
        )
        r = self.integrate("app", "ab1", "AB-1", "--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(
            "this foreman does not know it (read without it): foreman.toml: [repos.app]: unknown keys shiny", r.stdout
        )
        self.assertIn("the next integration refuses these [repos.*] keys", r.stdout)
        self.assertIn("the permissions only get stricter", r.stdout)

    def test_a_changed_command_opens_a_stricter_brake_is_shown(self):
        """A command foreman runs on its own, changed, needs --opens; a brake put on is only shown."""
        live = self.live_permissions()
        self.worktree(
            files={
                "feature.txt": "x\n",
                "ops/foreman.toml": live.replace("local_ci = ", "require_approval = true\nlocal_ci = ").replace(
                    "echo local-ci-ok", "echo ci2"
                ),
            }
        )
        r = self.integrate("app", "ab1", "AB-1", "--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Also changes (shown, the owner is not asked): [repos.app] require_approval", r.stdout)
        self.assertIn("[repos.app] local_ci, a command foreman runs on its own", r.stdout)
        self.assertIn("it would need --opens", r.stdout)
        r = self.integrate("app", "ab1", "AB-1")
        self.assertEqual(r.returncode, integrate.AWAITING, r.stdout + r.stderr)
        self.assertIn("run it again with --opens", r.stderr)

    def test_a_brake_taken_off_opens(self):
        live = self.live_permissions()
        self.worktree(
            files={
                "feature.txt": "x\n",
                "ops/foreman.toml": live.replace("local_ci = ", 'remote_checks = "none"\nlocal_ci = '),
            }
        )
        r = self.integrate("app", "ab1", "AB-1", "--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("[repos.app] remote_checks, a brake of the integration comes off: «required» → «none»", r.stdout)
        self.assertIn("it would need --opens", r.stdout)

    def test_other_reserved_files_are_risky(self):
        self.live_permissions()
        self.worktree(files={"feature.txt": "x\n", ".claude/settings.json": "{}\n"})
        r = self.integrate("app", "ab1", "AB-1")
        self.assertEqual(r.returncode, integrate.AWAITING, r.stdout + r.stderr)
        self.assertIn("risky paths: .claude/settings.json (a reserved file: claude)", r.stderr)
        r = self.integrate("app", "ab1", "AB-1", "--approved", "Owner, now")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_opens_does_not_go_with_measure(self):
        self.worktree()
        r = self.integrate("app", "ab1", "AB-1", "--measure", "--opens")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--opens", r.stderr)

    def test_opens_is_never_abbreviated(self):
        self.worktree()
        r = self.integrate("app", "ab1", "AB-1", "--check", "--open")
        self.assertEqual(r.returncode, 2)
        self.assertIn("unrecognized arguments: --open", r.stderr)

    def test_with_a_remote_it_stops_before_the_push(self):
        live = self.live_permissions()
        git(self.repo, "clone", "-q", "--bare", str(self.repo), str(self.tmp / "local.git"))
        git(self.repo, "remote", "add", "origin", str(self.tmp / "local.git"))
        git(self.repo, "fetch", "-q", "origin")
        remote = live.replace('remote = "none"\n', 'github = "acme/app"\n')
        self.write_toml(remote)
        git(self.repo, "add", "-A")  # with a remote the home's uncommitted backlog makes main dirty
        git(self.repo, "commit", "-q", "-m", "remote")
        git(self.repo, "push", "-q", "origin", "main")
        self.worktree(files={"feature.txt": "x\n", "ops/foreman.toml": remote.replace(', "make release"]', "]")})
        r = self.integrate("app", "ab1", "AB-1", "--title", "t", "--body", "b")
        self.assertEqual(r.returncode, integrate.AWAITING, r.stdout + r.stderr)
        self.assertEqual(self.ci_runs(), [])
        self.assertFalse((self.tmp / "ghstate/calls.log").exists())
        self.assertEqual(git(self.tmp / "local.git", "branch", "--list", "ab1"), "")


if __name__ == "__main__":
    unittest.main()
