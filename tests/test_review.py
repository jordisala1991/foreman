"""The review rule and integrate's review gate (review.py): which branches need a review by another agent, how a
review file's header is read, when it covers the branch's tip, and what `foreman integrate` lets in. The reviews are
written by hand here, as a reviewer agent would write them."""

from helpers import ProjectCase, git, make_repo

from foreman import config, review
from foreman.backlog import Backlog

TOML = """
[project]
name = "fake"
owner = "Owner"

[paths]
worktrees = "{tmp}/wt"

[repos.app]
path = "{tmp}/app"
remote = "none"
local_ci = "true"
review = "auto"
review_paths = ["lib/guard.py"]
risky_paths = ["deploy/"]
review_lines = 20
"""

SEED = {"README.md": "hi\n", "lib/guard.py": "x = 1\n", "lib/core.py": "a = 1\n"}


class ReviewCase(ProjectCase):
    toml = TOML

    def setUp(self):
        super().setUp()
        make_repo(self.tmp / "app", SEED)
        self.b = Backlog(self.project)
        self.b.init()
        with self.b.mutate() as d:
            self.b.add_project(d, "E1", "One", "intro")
            self.b.add_issue(d, "E1", "AB-1", "feature", status="in progress")
            self.b.set_sub(d, "AB-1", "T1", title="the feature", agent="w1", branch="ab1", status="▶")
        self.wt = None

    def commit(self, files, msg="work"):
        """Writes FILES in the branch's worktree (made the first time) and commits them; the new tip."""
        if self.wt is None:
            r = self.cli("worktree", "app", "w1", "ab1")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.wt = self.tmp / "wt/app-w1"
        for f, text in files.items():
            (self.wt / f).parent.mkdir(parents=True, exist_ok=True)
            (self.wt / f).write_text(text)
        git(self.wt, "add", "-A")
        git(self.wt, "commit", "-q", "-m", msg)
        return git(self.wt, "rev-parse", "HEAD")

    def drop_branch(self):
        """Removes the branch and its worktree, to start it again."""
        if self.wt is not None:
            git(self.tmp / "app", "worktree", "remove", "--force", str(self.wt))
            git(self.tmp / "app", "branch", "-D", "ab1")
            self.wt = None

    def write_review(self, tip, rnd=1, high=0, medium=0, name=None, branch="app:ab1", extra=""):
        name = name or f"2026-11-05-review-AB-1-T1{'-r2' if rnd > 1 else ''}.md"
        path = self.home / "reviews" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"# Review of {branch}\n\nBranch: {branch}\nTip: {tip[:7]}\nRound: {rnd}\nOpen: H {high}, M {medium}\n"
            f"{extra}\n## Findings\n\n| id | sev |\n"
        )
        return path

    def repo(self):
        return config.load_project(self.home).repo("app")

    def state(self, outcome=None, reviewer=""):
        return review.state(config.load_project(self.home), self.repo(), "ab1", outcome, reviewer)

    def integrate(self, *args):
        return self.cli("integrate", "app", "ab1", "AB-1 T1", *args)

    def note(self):
        return "".join(p.read_text() for p in (self.home / "notes").glob("*.md"))


class ReviewRule(ReviewCase):
    def test_reasons(self):
        self.commit({"lib/core.py": "a = 2\n"})
        self.assertEqual(review.reasons(self.project, self.repo(), "ab1"), [])
        self.assertEqual(
            review.reasons(self.project, self.repo(), "ab1", "partial"), [review.Reason("partial", "delivered partial")]
        )
        self.assertEqual(review.reasons(self.project, self.repo(), "ab1", "failed"), [])
        self.commit({"lib/guard.py": "x = 2\n", "deploy/run.sh": "go\n"})
        why = review.reasons(self.project, self.repo(), "ab1")
        self.assertEqual(why, [review.Reason("paths", "delicate paths: deploy/run.sh (deploy/), lib/guard.py")])
        # tests, docs, JSON and lock files do not count for the size; code does
        self.commit(
            {"tests/test_x.py": "t\n" * 50, "docs/a.md": "d\n" * 50, "x.json": "{}\n" * 50, "uv.lock": "l\n" * 50}
        )
        self.assertEqual([r.kind for r in review.reasons(self.project, self.repo(), "ab1")], ["paths"])
        self.commit({"lib/new.py": "n\n" * 30})
        why = review.reasons(self.project, self.repo(), "ab1", "partial")
        self.assertEqual([r.kind for r in why], ["paths", "size", "partial"])
        self.assertEqual(why[1].text, "35 lines of code, over 20")

    def test_off_and_size_off(self):
        self.commit({"lib/guard.py": "x = 2\n", "lib/new.py": "n\n" * 30})
        self.write_toml(TOML.replace("review_lines = 20", "review_lines = 0"))
        self.assertEqual([r.kind for r in review.reasons(self.project, self.repo(), "ab1")], ["paths"])
        self.write_toml(TOML.replace('review = "auto"', 'review = "off"'))
        self.assertEqual(review.reasons(self.project, self.repo(), "ab1", "partial"), [])
        self.assertEqual(self.state("partial").status, review.NOT_NEEDED)
        self.write_toml(TOML.replace('review = "auto"\n', ""))  # off by default
        self.assertEqual(self.state("partial").line(), review.NOT_NEEDED)

    def test_excluded_and_renames(self):
        for path in ("tests/a.py", "pkg/test/b.py", "README.md", "a/b.json", "Cargo.lock", "pnpm-lock.yaml", "go.sum"):
            self.assertTrue(review.excluded(path), path)
        for path in ("lib/tests.py", "src/testing/a.py", "lib/core.py"):
            self.assertFalse(review.excluded(path), path)
        git(self.tmp / "app", "checkout", "-q", "-b", "mv")
        git(self.tmp / "app", "mv", "lib/core.py", "lib/moved.py")
        (self.tmp / "app/lib/moved.py").write_text("a = 1\nb = 2\n")
        (self.tmp / "app/logo.bin").write_bytes(b"\0\1\2")
        git(self.tmp / "app", "add", "-A")
        git(self.tmp / "app", "commit", "-q", "-m", "move")
        self.assertEqual(review.code_lines(self.tmp / "app", "main", "mv"), 1)  # the moved file's one new line

    def test_paths_touched_needle(self):
        self.commit({"lib/guard.py": "x = 1\ny = 2\n"})
        app = self.tmp / "app"
        self.assertEqual(
            review.paths_touched(app, "main", "ab1", ["lib/guard.py:y ="]), ["lib/guard.py (lib/guard.py:y =)"]
        )
        self.assertEqual(review.paths_touched(app, "main", "ab1", ["lib/guard.py:x ="]), [])
        self.assertEqual(review.paths_touched(app, "main", "ab1", ["lib/*.py"]), ["lib/guard.py (lib/*.py)"])
        self.assertEqual(review.paths_touched(app, "main", "ab1", []), [])


class ReviewState(ReviewCase):
    """§2.2 of the plan: findings, a round 2 on the new tip, a stale review, no third round."""

    def test_rounds(self):
        tip = self.commit({"lib/guard.py": "x = 2\n"})
        st = self.state()
        self.assertEqual((st.status, st.round, st.review), (review.NEEDED, 1, None))
        self.assertEqual(st.line(), "needed, round 1 (delicate paths: lib/guard.py)")
        self.assertEqual(self.state(reviewer="rv1").line(), "in review by rv1 (delicate paths: lib/guard.py)")
        self.write_review(tip, high=1)
        st = self.state()
        self.assertEqual((st.status, st.review.counts()), (review.FINDINGS, "H 1, M 0"))
        self.assertIn("findings: H 1, M 0 open (reviews/2026-11-05-review-AB-1-T1.md, round 1 of 2)", st.line())
        self.assertEqual(self.state(reviewer="rv1").round, 2)
        new = self.commit({"lib/guard.py": "x = 3\n"}, "fix")
        st = self.state()
        self.assertEqual((st.status, st.round), (review.FIXED, 2))
        self.assertIn("fixed, round 2 needed", st.line())
        self.write_review(new, rnd=2)
        st = self.state()
        self.assertEqual(st.status, review.CLEAN)
        self.assertTrue(st.passes)
        self.assertIn("clean (reviews/2026-11-05-review-AB-1-T1-r2.md, round 2, H 0, M 0 open", st.line())

    def test_stale_clean_review_does_not_count(self):
        old = self.commit({"lib/guard.py": "x = 2\n"})
        self.write_review(old)
        self.assertEqual(self.state().status, review.CLEAN)
        self.commit({"lib/guard.py": "x = 4\n"}, "more")
        st = self.state()
        self.assertEqual((st.status, st.round), (review.NEEDED, 2))
        self.assertIn(f"reviewed {old[:7]}, another diff", st.line())

    def test_main_merged_in_keeps_the_review(self):
        tip = self.commit({"lib/guard.py": "x = 2\n"})
        self.write_review(tip)
        app = self.tmp / "app"
        (app / "other.txt").write_text("main moved\n")
        git(app, "add", "-A")
        git(app, "commit", "-q", "-m", "main moves")
        git(self.wt, "merge", "-q", "--no-edit", "main")
        self.assertNotEqual(git(self.wt, "rev-parse", "HEAD"), tip)
        self.assertEqual(self.state().status, review.CLEAN)
        self.assertTrue(review.covers(app, "main", "ab1", tip))
        self.assertFalse(review.covers(app, "main", "ab1", "deadbeef"))

    def test_round_two_with_findings_goes_to_the_owner(self):
        tip = self.commit({"lib/guard.py": "x = 2\n"})
        self.write_review(tip, high=1)
        new = self.commit({"lib/guard.py": "x = 3\n"}, "fix")
        self.write_review(new, rnd=2, medium=1)
        st = self.state()
        self.assertEqual(st.status, review.OWNER)
        self.assertIn("round 2 of 2 left H 0, M 1 open", st.line())
        self.commit({"lib/guard.py": "x = 5\n"}, "another try")
        self.assertEqual(self.state().status, review.OWNER)  # no third round, whatever the branch does

    def test_reviews_of_other_branches_and_bad_headers(self):
        tip = self.commit({"lib/guard.py": "x = 2\n"})
        self.write_review(tip, branch="app:other", name="2026-11-05-review-other.md")
        self.write_review(tip, branch="api:ab1", name="2026-11-05-review-api.md")
        (self.home / "reviews/2026-11-05-notes.md").write_text(
            f"Branch: app:ab1\nTip: {tip}\nRound: 1\nOpen: H 0, M 0\n"
        )
        bad = self.home / "reviews/old/2026-11-04-review-bad.md"
        bad.parent.mkdir(parents=True)
        bad.write_text("Branch: app:ab1\nTip: HEAD\nRound: one\nOpen: none\n")
        st = self.state()
        self.assertEqual((st.status, st.review), (review.NEEDED, None))
        self.assertEqual(
            st.problems, ["reviews/old/2026-11-04-review-bad.md (header: Branch, Tip SHA, Round N, Open: H n, M n)"]
        )
        self.assertIn("unreadable: reviews/old/2026-11-04-review-bad.md", st.line())
        self.assertIsNone(review.reviews(self.project, "app", "ab1")[0][-1:] or None)
        good = self.write_review(tip)
        self.assertEqual(review.reviews(self.project, "app", "ab1")[0][-1].path, str(good.relative_to(self.home)))
        self.assertEqual(review.parse("Branch: a:b\nbranch: c:d\nTip: 1\n"), {"branch": "a:b", "tip": "1"})


class ReviewGate(ReviewCase):
    """§2.1 (integrate's part) and §2.2 through `foreman integrate`."""

    def test_small_branch_goes_in_as_today(self):
        self.commit({"lib/core.py": "a = 2\n"})
        r = self.integrate("--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("== review: not needed", r.stdout)
        r = self.integrate("--result", "ok")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("review", self.note().split("Integrated", 1)[1])

    def test_each_trigger_stops_with_nothing_done(self):
        main = git(self.tmp / "app", "rev-parse", "main")
        cases = {
            "paths": ({"lib/guard.py": "x = 2\n"}, None, "delicate paths: lib/guard.py"),
            "size": ({"lib/new.py": "n\n" * 30}, None, "30 lines of code, over 20"),
            "partial": ({"lib/core.py": "a = 2\n"}, "partial", "delivered partial"),
        }
        for kind, (files, outcome, why) in cases.items():
            with self.subTest(kind=kind):
                self.drop_branch()
                self.commit(files)
                if outcome:
                    r = self.cli("progress", "--done", "half", "--outcome", outcome, env={"FOREMAN_WHO": "w1"})
                    self.assertEqual(r.returncode, 0, r.stderr)
                for args in (("--check",), ("--result", "ok")):
                    r = self.integrate(*args)
                    self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                    self.assertIn(f"== review: needed, round 1 ({why})", r.stdout)
                    self.assertIn("app:ab1 needs its review first", r.stderr)
                    self.assertIn("«Branch: app:ab1», «Tip: SHA», «Round: 1»", r.stderr)
                self.assertEqual(git(self.tmp / "app", "rev-parse", "main"), main)
                self.assertFalse((self.home / "notes").exists() and "Integrated" in self.note())

    def test_findings_then_round_two_clean_goes_in(self):
        tip = self.commit({"lib/guard.py": "x = 2\n"})
        self.write_review(tip, high=1)
        r = self.integrate("--result", "ok")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("findings: H 1, M 0 open", r.stdout)
        self.assertIn("send the findings back to the agent that made the branch", r.stderr)
        new = self.commit({"lib/guard.py": "x = 3\n"}, "fix")
        r = self.integrate("--check")
        self.assertEqual(r.returncode, 2)
        self.assertIn("round 2: the reviewer checks the fixes", r.stderr)
        self.write_review(tip, rnd=2, name="2026-11-05-review-AB-1-T1-old-tip.md")  # an old tip, another diff
        self.assertEqual(self.integrate("--check").returncode, 2)
        self.write_review(new, rnd=2)
        r = self.integrate("--result", "ok")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("== review: clean (reviews/2026-11-05-review-AB-1-T1-r2.md, round 2", r.stdout)
        self.assertIn("review clean: reviews/2026-11-05-review-AB-1-T1-r2.md, round 2", self.note())
        self.assertEqual(git(self.tmp / "app", "rev-parse", "main"), new)

    def test_no_third_round_only_the_owner_lets_it_in(self):
        tip = self.commit({"lib/guard.py": "x = 2\n"})
        self.write_review(tip, high=1)
        new = self.commit({"lib/guard.py": "x = 3\n"}, "fix")
        self.write_review(new, rnd=2, high=1)
        r = self.integrate("--result", "ok")
        self.assertEqual(r.returncode, 2)
        self.assertIn("no third round: ask the owner", r.stderr)
        r = self.integrate("--result", "ok", "--no-review", "small")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--no-review is only for a branch whose one reason is its size", r.stderr)
        r = self.integrate("--result", "ok", "--approved", "Owner, chat 10:00")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("the owner's yes (Owner, chat 10:00) lets it in with H 1, M 0 open", r.stdout)
        self.assertIn("review: the owner lets it in with H 1, M 0 open", self.note())

    def test_no_review_only_for_size(self):
        self.commit({"lib/new.py": "n\n" * 30})
        r = self.integrate("--result", "ok", "--no-review", " ")
        self.assertEqual(r.returncode, 2)
        r = self.integrate("--result", "ok", "--no-review", "a split, the golden corpus unchanged")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("== --no-review: app:ab1 goes in without a review", r.stdout)
        self.assertIn(
            "no review, size only (30 lines of code, over 20): a split, the golden corpus unchanged", self.note()
        )

    def test_no_review_refused_for_paths_and_partial(self):
        self.commit({"lib/new.py": "n\n" * 30, "lib/guard.py": "x = 2\n"})
        r = self.integrate("--check", "--no-review", "big")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--no-review is only for a branch whose one reason is its size", r.stderr)
        self.drop_branch()
        self.commit({"lib/new.py": "n\n" * 30})
        self.cli("progress", "--done", "half", "--outcome", "partial", env={"FOREMAN_WHO": "w1"})
        r = self.integrate("--check", "--no-review", "big")
        self.assertEqual(r.returncode, 2)
        self.assertIn("delivered partial", r.stderr)

    def test_no_review_ignored_when_not_needed_or_off(self):
        tip = self.commit({"lib/guard.py": "x = 2\n"})
        self.write_review(tip)
        r = self.integrate("--check", "--no-review", "x")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("== --no-review ignored: the review is clean", r.stdout)
        self.write_toml(TOML.replace('review = "auto"', 'review = "off"'))
        r = self.integrate("--check", "--no-review", "x")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("== --no-review ignored: review is off for app", r.stdout)
        self.assertNotIn("== review:", r.stdout)
        r = self.cli("integrate", "app", "ab1", "AB-1 T1", "--measure", "--no-review", "x")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--no-review", r.stderr)

    def test_config_checks_the_keys(self):
        for bad, msg in (
            ('review = "maybe"', "review must be auto or off"),
            ('review_paths = "lib/"', "review_paths must be a list"),
            ("review_lines = -1", "review_lines must be a whole number"),
            ("review_lines = true", "review_lines must be a whole number"),
        ):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(config.ConfigError, msg):
                    self.write_toml(self.toml_with(bad))

    def toml_with(self, line):
        key = line.split(" =")[0]
        lines = [x for x in TOML.splitlines() if not x.startswith(key + " =")]
        return "\n".join(lines) + f"\n{line}\n"
