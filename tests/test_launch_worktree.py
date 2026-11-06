"""`foreman launch` refuses production work of an issue without its worktree: a dev, a production type or a
mechanic launched with --issue and without --repo/--branch is exit 2 with nothing made (a launch without a worktree
records no branch, so the delivery check would never run for it); --no-production is the way out for a mechanic
that changes no code; planners, reviewers and the reviewer of a branch launch as before. One test per row of the
table of the plan."""

from helpers import ProjectCase, make_repo
from test_identity import TOML

from foreman import util
from foreman.backlog import Backlog

REFUSED = "a production agent needs --repo REPO --branch BRANCH (its worktree); --no-production if it changes no code"


class LaunchNeedsWorktreeTest(ProjectCase):
    toml = TOML

    def setUp(self):
        super().setUp()
        make_repo(self.tmp / "app")
        self.b = Backlog(self.project)
        self.b.init()
        with self.b.mutate() as d:
            self.b.add_project(d, "E1", "One", "intro")
            self.b.add_issue(d, "E1", "AB-1", "feature", status="ready")
            self.b.set_sub(d, "AB-1", "T0", title="the feature")

    def launches(self):
        return util.read_json(self.project.state / "launches.json", {})

    def launch(self, *args, rc=0):
        r = self.cli("launch", *args)
        self.assertEqual(r.returncode, rc, r.stdout + r.stderr)
        return r

    def assert_refused(self, *args):
        before = self.b.data_path.read_bytes()
        r = self.launch(*args, rc=2)
        self.assertIn(REFUSED, r.stderr)
        self.assertNotIn("[foreman launch", r.stdout)
        self.assertEqual(self.launches(), {})
        self.assertEqual(self.b.data_path.read_bytes(), before)

    def record(self, name):
        (rec,) = [v for v in self.launches().values() if v["name"] == name]
        return rec

    # row 1: a dev of a sub-issue without --repo
    def test_a_dev_without_a_worktree_is_refused(self):
        self.assert_refused("d1", "--type", "dev", "--issue", "AB-1", "--sub", "T0")
        self.assert_refused("d1", "--type", "foreman:dev", "--issue", "AB-1", "--sub", "T0", "--no-backlog")
        # --production says so for any type
        self.assert_refused("d1", "--type", "planner", "--issue", "AB-1", "--production")

    # row 2: a mechanic of an issue without --repo
    def test_a_mechanic_without_a_worktree_is_refused(self):
        self.assert_refused("m1", "--type", "mechanic", "--issue", "AB-1")
        self.assert_refused("m1", "--type", "foreman:mechanic", "--issue", "AB-1", "--sub", "T0")

    # row 3: a mechanic that changes no code, said explicitly
    def test_a_mechanic_with_no_production_launches_without_a_worktree(self):
        self.launch("m2", "--type", "mechanic", "--issue", "AB-1", "--no-production")
        rec = self.record("m2")
        self.assertIs(rec["production"], False)
        self.assertIsNone(rec.get("worktree"))
        # a dev told --no-production too: the flag is the explicit way out, whatever the type
        self.launch("d2", "--type", "dev", "--issue", "AB-1", "--no-production", "--no-backlog")
        self.assertIs(self.record("d2")["production"], False)

    # row 4: planners, reviewers and the reviewer of a branch, as before
    def test_planners_and_reviewers_launch_without_a_worktree(self):
        self.launch("p1", "--type", "planner", "--issue", "AB-1")
        self.launch("r1", "--type", "reviewer", "--issue", "AB-1", "--no-backlog")
        self.launch("w1", "--issue", "AB-1", "--sub", "T0", "--repo", "app", "--branch", "ab1/x")
        self.launch("r2", "--review", "app:ab1/x", "--issue", "AB-1", "--sub", "T0")
        for name in ("p1", "r1", "r2"):
            self.assertIsNone(self.record(name).get("worktree"), name)
        # a launch without an issue is not production work of one: unchanged
        self.launch("m3", "--type", "mechanic")
        self.launch("d3", "--type", "dev")

    # row 5: a dev with its worktree
    def test_a_dev_with_its_worktree_launches(self):
        r = self.launch("d4", "--type", "dev", "--issue", "AB-1", "--sub", "T0", "--repo", "app", "--branch", "b")
        rec = self.record("d4")
        self.assertTrue(rec["production"])
        self.assertEqual(rec["worktree"], str(self.tmp / "wt" / "app-d4"))
        self.assertTrue((self.tmp / "wt" / "app-d4").is_dir())
        self.assertIn("· production", r.stdout)
        # a mechanic with its worktree too
        self.launch("m4", "--type", "mechanic", "--issue", "AB-1", "--repo", "app", "--branch", "m")
        self.assertTrue(self.record("m4")["production"])
