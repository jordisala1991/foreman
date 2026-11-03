"""The contract of a product's command (lib/foreman/commands.py): environment, placeholders, process-group stop, time
limits, and every point of lib/ going through the one runner."""

import dataclasses
import os
import re
import time
import unittest
from pathlib import Path
from unittest import mock

from helpers import ROOT, ProjectCase, git, make_repo

from foreman import commands, notices, worktree
from foreman.backlog import Backlog


def alive(pid):
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def env_file(path):
    """{name: value} of the FOREMAN_* lines `env` wrote to path."""
    out = {}
    for line in Path(path).read_text().splitlines():
        k, sep, v = line.partition("=")
        if sep and k.startswith("FOREMAN_"):
            out[k] = v
    return out


class PlaceholdersTest(unittest.TestCase):
    def test_known_placeholders_are_replaced_and_shell_braces_left(self):
        v = {"wt": "/w", "main": "/m", "branch": "b1", "ci": "--quick", "repo": "app"}
        cases = {
            "ci.sh {wt} {ci}": "ci.sh /w --quick",
            "echo ${HOME} ${wt}": "echo ${HOME} ${wt}",
            "awk '{print $1}' f": "awk '{print $1}' f",
            "awk '{print}' f": "awk '{print}' f",
            "{ echo a; echo b; } > x": "{ echo a; echo b; } > x",
            "echo {a,b} {}": "echo {a,b} {}",
            "find . -exec echo {} \\;": "find . -exec echo {} \\;",
            "echo '{branch}' {main}": "echo 'b1' /m",  # a known one is replaced inside quotes too, as before
            "jq '{x: .y}' }{": "jq '{x: .y}' }{",
        }
        for cmd, want in cases.items():
            self.assertEqual(commands.expand("repo.local_ci", cmd, v), want, cmd)

    def test_unknown_placeholder_is_an_error_before_anything_runs(self):
        for point, cmd in (
            ("repo.local_ci", "echo {brnach}"),
            ("repo.local_ci", "echo {head}"),
            ("repo.post_merge", "echo {wt}"),
            ("repo.pre_merge_report", "x {ci}"),
            ("repo.post_create", "cp {main}/x {wtt}/"),
            ("repo.local_ci", 'echo "{oops}"'),
        ):
            with self.assertRaises(commands.PlaceholderError, msg=cmd) as e:
                commands.expand(point, cmd)
            self.assertIn("bad placeholder", str(e.exception))
        # inside single quotes an unknown {name} is an awk or jq program, not a placeholder
        self.assertEqual(commands.expand("repo.post_merge", "awk '{print}' {main}", {"main": "/m"}), "awk '{print}' /m")
        with self.assertRaises(commands.PlaceholderError):  # a quote inside double quotes opens nothing
            commands.expand("repo.post_merge", 'echo "it\'s" {oops}')

    def test_post_create_quotes_its_values_and_the_repo_steps_do_not(self):
        v = {"main": "/a b", "wt": "/w x", "repo": "app", "name": "n", "branch": "b"}
        self.assertEqual(commands.expand("repo.post_create", "cp -R {main}/m {wt}/", v), "cp -R '/a b'/m '/w x'/")
        self.assertEqual(commands.expand("repo.local_ci", "ci {ci}", {"ci": "--a --b"}), "ci --a --b")

    def test_single_quoted_spans_follow_bash(self):
        cmd = "a \"it's\" 'q {x}' \\' {y}"
        spans = commands.single_quoted(cmd)
        self.assertEqual([cmd[a : b + 1] for a, b in spans], ["'q {x}'"])


class RunTest(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.tmp = Path(tempfile.mkdtemp(prefix="foreman-cmd-")).resolve()
        self.saved_grace = commands.KILL_GRACE

    def tearDown(self):
        import shutil

        commands.KILL_GRACE = self.saved_grace
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_environment_of_a_point_without_a_project(self):
        r = commands.run(
            "repo.local_ci",
            "env",
            values={"wt": "/w", "ci": "", "branch": "b", "main": "/m", "repo": "app"},
            cwd=self.tmp,
        )
        env = dict(ln.split("=", 1) for ln in r.stdout.splitlines() if ln.startswith("FOREMAN_"))
        self.assertEqual(env["FOREMAN_API"], str(commands.API))
        self.assertEqual(env["FOREMAN_POINT"], "repo.local_ci")
        self.assertEqual(
            (env["FOREMAN_WT"], env["FOREMAN_MAIN"], env["FOREMAN_BRANCH"], env["FOREMAN_CI"], env["FOREMAN_REPO"]),
            ("/w", "/m", "b", "", "app"),
        )
        self.assertTrue(os.access(env["FOREMAN_BIN"], os.X_OK))
        self.assertEqual(Path(env["FOREMAN_BIN"]), ROOT / "bin/foreman")

    def test_stdin_cwd_and_exit_code(self):
        r = commands.run("repo.ci_summary", "pwd; tr a-z A-Z; echo err >&2; exit 3", cwd=self.tmp, stdin="abc\n")
        self.assertEqual(
            (r.rc, r.stdout.split(), r.stderr.strip(), r.timed_out), (3, [str(self.tmp), "ABC"], "err", False)
        )
        r = commands.run("loop.checks", "cat; echo done", cwd=self.tmp)  # no stdin: /dev/null, never the caller's
        self.assertEqual(r.stdout, "done\n")

    def test_a_cut_stops_the_whole_process_group(self):
        """A grandchild that ignores SIGTERM is killed too (SIGKILL after the grace)."""
        commands.KILL_GRACE = 0.5
        pids = self.tmp / "pids"
        cmd = (
            f"bash -c 'trap \"\" TERM; sleep 300' & echo $! >> {pids}; "
            f"(sleep 300; echo never) & echo $! >> {pids}; sleep 300"
        )
        t0 = time.monotonic()
        r = commands.run("repo.local_ci", cmd, cwd=self.tmp, timeout=1)
        self.assertLess(time.monotonic() - t0, 60)
        self.assertTrue(r.timed_out)
        self.assertLess(r.rc, 0)
        children = pids.read_text().split()
        self.assertEqual(len(children), 2)
        deadline = time.monotonic() + 30
        while any(alive(p) for p in children) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual([p for p in children if alive(p)], [])

    def test_a_command_that_ends_keeps_its_children_and_does_not_wait_for_them(self):
        pid = self.tmp / "pid"
        t0 = time.monotonic()
        r = commands.run("repo.post_create", f"sleep 120 & echo $! > {pid}; echo started", cwd=self.tmp)
        try:
            self.assertLess(time.monotonic() - t0, 60)
            self.assertEqual((r.rc, r.stdout, r.timed_out), (0, "started\n", False))
            self.assertTrue(alive(pid.read_text().strip()))
        finally:
            os.kill(int(pid.read_text().strip()), 9)

    def test_log_gets_stdout_and_stderr_as_they_come(self):
        log = self.tmp / "ci.log"
        log.write_text("== header\n")
        r = commands.run("repo.local_ci", "echo out; echo err >&2", cwd=self.tmp, log=log)
        self.assertEqual(r.rc, 0)
        self.assertEqual(sorted(r.stdout.split()), ["err", "out"])
        self.assertTrue(log.read_text().startswith("== header\n"))

    def test_a_command_that_cannot_start(self):
        r = commands.run("loop.checks", "true", cwd=self.tmp / "missing")
        self.assertIsNone(r.rc)
        self.assertTrue(r.stderr)

    def test_time_limits_of_the_points(self):
        """300 s for the report, the summary and the privacy scan, 600 s for post_create and post_merge, no limit for
        the CI runs; no config key."""
        caps = {k: p.timeout for k, p in commands.POINTS.items()}
        self.assertEqual(
            caps,
            {
                "loop.checks": 30,
                "resource.busy": 5,
                "repo.local_ci": None,
                "repo.main_ci": None,
                "repo.ci_summary": 300,
                "repo.privacy_scan": 300,
                "repo.pre_merge_report": 300,
                "repo.post_merge": 600,
                "repo.post_create": 600,
            },
        )

    def test_one_launcher_of_product_commands_in_lib(self):
        """Only commands.py runs `bash -c` from Python; lock.sh runs `busy` itself with FOREMAN_API and
        FOREMAN_POINT, under time_limit.sh."""
        lib = ROOT / "lib/foreman"
        hits = sorted(f.name for f in lib.rglob("*.py") if re.search(r'"bash",\s*"-c"', f.read_text()))
        self.assertEqual(hits, ["commands.py"])
        lock_sh = (lib / "sh/lock.sh").read_text()
        self.assertIn('env ${drop[@]+"${drop[@]}"} FOREMAN_API=1 FOREMAN_POINT=resource.busy', lock_sh)
        self.assertIn('time_limit.sh" "$BUSY_TIMEOUT")', lock_sh)
        self.assertEqual(lock_sh.count('bash -c "$BUSY"'), 1)


TOML = """
[project]
name = "fake"
owner = "Owner"
language = "en"

[paths]
worktrees = "{tmp}/wt"

[loop]
checks = ["env > {tmp}/check.env; pwd >> {tmp}/check.env; echo plain notice"]

[repos.app]
path = "{tmp}/app"
remote = "none"
local_ci = "env > {tmp}/ci-$(basename $PWD).env; echo ci ${HOME:+home-set}"
main_ci = "env > {tmp}/main_ci.env"
post_create = ["env > {tmp}/post_create.env; echo ${HOME:+home-set} > made.txt"]
post_merge = "env > {tmp}/post_merge.env; echo merged {before}..{head}"

[resources.widget]
busy = "env > {tmp}/busy.env"
"""


class PointsTest(ProjectCase):
    """Each point, through foreman itself, gets the contract's environment."""

    toml = TOML

    def setUp(self):
        super().setUp()
        make_repo(self.tmp / "app")
        self.b = Backlog(self.project)
        self.b.init()
        with self.b.mutate() as d:
            self.b.add_project(d, "E1", "One", "intro")
            self.b.add_issue(d, "E1", "AB-1", "feature", status="in progress")
            self.b.set_sub(d, "AB-1", "T1", title="the feature", agent="w1", branch="ab1", status="▶")

    def assert_common(self, env, point):
        self.assertEqual(env["FOREMAN_API"], "1")
        self.assertEqual(env["FOREMAN_POINT"], point)
        self.assertEqual(env["FOREMAN_PROJECT"], "fake")
        self.assertEqual(Path(env["FOREMAN_HOME"]), self.home)
        self.assertEqual(Path(env["FOREMAN_STATE"]), self.project.state)
        self.assertEqual(env["FOREMAN_LANGUAGE"], "en")
        self.assertEqual(Path(env["FOREMAN_BIN"]), ROOT / "bin/foreman")

    def make_worktree(self):
        r = self.cli("worktree", "app", "w1", "ab1")
        self.assertEqual(r.returncode, 0, r.stderr)
        wt = self.tmp / "wt" / "app-w1"
        (wt / "feature.txt").write_text("new\n")
        git(wt, "add", "feature.txt")
        git(wt, "commit", "-q", "-m", "work")
        return wt

    def test_loop_checks(self):
        [n] = notices.run_checks(self.project)
        self.assertEqual(n["text"], "plain notice")
        env = env_file(self.tmp / "check.env")
        self.assert_common(env, "loop.checks")
        self.assertEqual((self.tmp / "check.env").read_text().splitlines()[-1], str(self.home))

    def test_a_loop_check_past_its_limit_is_check_failed_and_leaves_nothing(self):
        pid = self.tmp / "pid"
        self.write_toml(TOML.replace('checks = ["env', f'checks = ["sleep 300 & echo $! > {pid}; wait", "true; env'))
        saved = commands.POINTS["loop.checks"]
        commands.POINTS["loop.checks"] = dataclasses.replace(saved, timeout=0.5)
        try:
            out = notices.run_checks(self.project)
        finally:
            commands.POINTS["loop.checks"] = saved
        self.assertEqual(out[0]["kind"], "check_failed")
        self.assertIn("timed out (0.5 s)", out[0]["text"])
        deadline = time.monotonic() + 30
        while alive(pid.read_text().strip()) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(alive(pid.read_text().strip()))

    def test_post_create_and_the_integration_steps(self):
        wt = self.make_worktree()
        env = env_file(self.tmp / "post_create.env")
        self.assert_common(env, "repo.post_create")
        self.assertEqual(
            (
                Path(env["FOREMAN_WT"]),
                Path(env["FOREMAN_MAIN"]),
                env["FOREMAN_REPO"],
                env["FOREMAN_NAME"],
                env["FOREMAN_BRANCH"],
            ),
            (wt, self.tmp / "app", "app", "w1", "ab1"),
        )
        self.assertEqual((wt / "made.txt").read_text(), "home-set\n")  # ${HOME} reaches bash untouched
        r = self.cli("integrate", "app", "ab1", "AB-1 T1", "--result", "ok")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ci home-set", r.stdout)
        ci = env_file(self.tmp / "ci-app-w1.env")
        self.assert_common(ci, "repo.local_ci")
        self.assertEqual(
            (Path(ci["FOREMAN_WT"]), ci["FOREMAN_BRANCH"], ci["FOREMAN_CI"], ci["FOREMAN_REPO"]), (wt, "ab1", "", "app")
        )
        main_ci = env_file(self.tmp / "main_ci.env")
        self.assert_common(main_ci, "repo.main_ci")
        self.assertEqual(Path(main_ci["FOREMAN_WT"]), self.tmp / "app")
        pm = env_file(self.tmp / "post_merge.env")
        self.assert_common(pm, "repo.post_merge")
        head = git(self.tmp / "app", "rev-parse", "main")
        self.assertEqual((pm["FOREMAN_HEAD"], pm["FOREMAN_BRANCH"]), (head, "ab1"))
        self.assertEqual(
            git(self.tmp / "app", "rev-parse", pm["FOREMAN_BEFORE"] + "^{commit}"),
            git(self.tmp / "app", "rev-parse", "main~1"),
        )

    def test_a_bad_post_create_placeholder_makes_nothing(self):
        self.write_toml(TOML.replace('post_create = ["env', 'post_create = ["echo {wtt}; env'))
        with self.assertRaises(worktree.WorktreeError) as e:
            worktree.create(self.project, "app", "w1", "ab1")
        self.assertIn("bad placeholder {wtt}", str(e.exception))
        self.assertFalse((self.tmp / "wt" / "app-w1").exists())
        self.assertNotIn("ab1", git(self.tmp / "app", "branch"))

    def test_a_failing_pre_merge_report_is_said(self):
        self.write_toml(
            TOML.replace(
                "post_merge =", 'pre_merge_report = "echo partial {branch}; echo it broke >&2; exit 4"\npost_merge ='
            )
        )
        self.make_worktree()
        r = self.cli("integrate", "app", "ab1", "AB-1 T1", "--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("partial ab1", r.stdout)
        self.assertIn("(pre_merge_report failed, rc 4): it broke", r.stdout)

    def test_a_pre_merge_report_past_its_limit_is_said(self):
        self.write_toml(TOML.replace("post_merge =", 'pre_merge_report = "echo partial; sleep 300"\npost_merge ='))
        self.make_worktree()
        from foreman import integrate

        class A:
            check, sub, ci, no_ci = True, None, None, False

        i = integrate.Integration(self.project, "app", "ab1", "AB-1 T1", A(), out=lambda *_: None)
        i.wt = self.tmp / "wt" / "app-w1"
        saved = commands.POINTS["repo.pre_merge_report"]
        commands.POINTS["repo.pre_merge_report"] = dataclasses.replace(saved, timeout=0.5)
        try:
            text = i.pre_merge_report()
        finally:
            commands.POINTS["repo.pre_merge_report"] = saved
        self.assertIn("partial", text)
        self.assertIn("(pre_merge_report cut after 0.5 s)", text)

    def test_unknown_placeholders_in_any_repo_step_are_exit_2(self):
        self.make_worktree()
        for key, cmd in (
            ("local_ci", "echo {head}"),
            ("ci_summary", "echo {before}"),
            ("pre_merge_report", "echo {ci}"),
            ("main_ci", "echo {name}"),
        ):
            toml = "\n".join(ln for ln in TOML.splitlines() if not ln.startswith(f"{key} ="))
            self.write_toml(toml.replace("[repos.app]", f'[repos.app]\n{key} = "{cmd}"'))
            r = self.cli("integrate", "app", "ab1", "AB-1 T1", "--check")
            self.assertEqual(r.returncode, 2, key + r.stdout + r.stderr)
            self.assertIn(f"{key}: bad placeholder", r.stderr)

    def test_lock_busy_gets_the_api_and_the_point(self):
        """Taken from inside another point (a CI that takes a lock), `busy` sees its own point and none of the outer
        one's variables; the command under the lock keeps them (it is part of the outer command)."""
        outer = {"FOREMAN_WHO": "w1", "FOREMAN_POINT": "repo.local_ci", "FOREMAN_WT": "/outer/wt", "FOREMAN_CI": "x"}
        under = self.tmp / "under.env"
        r = self.cli("lock", "widget", "run", "test", "bash", "-c", f"env > {under}", env=outer)
        self.assertEqual(r.returncode, 0, r.stderr)
        env = env_file(self.tmp / "busy.env")
        self.assertEqual((env["FOREMAN_API"], env["FOREMAN_POINT"]), ("1", "resource.busy"))
        self.assertIn("FOREMAN_LOCK_DIR", env)
        self.assertEqual((env["FOREMAN_BIN"], env["FOREMAN_PROJECT"]), (str(commands.BIN), "fake"))
        self.assertNotIn("FOREMAN_WT", env)
        self.assertNotIn("FOREMAN_CI", env)
        self.assertEqual(env_file(under)["FOREMAN_WT"], "/outer/wt")

    def test_a_point_does_not_inherit_an_outer_point(self):
        """commands.environment drops an outer point's variables (POINT_VARIABLES) and keeps the rest."""
        base = {"FOREMAN_POINT": "repo.local_ci", "FOREMAN_WT": "/outer", "FOREMAN_CI": "x", "KEEP": "1"}
        env = commands.environment("loop.checks", base=base)
        self.assertEqual((env["FOREMAN_POINT"], env["KEEP"]), ("loop.checks", "1"))
        self.assertNotIn("FOREMAN_WT", env)
        self.assertNotIn("FOREMAN_CI", env)
        self.assertIn("FOREMAN_WT", commands.POINT_VARIABLES)
        self.assertNotIn("FOREMAN_PROJECT", commands.POINT_VARIABLES)

    def test_a_privacy_scan_cut_at_its_limit_stops(self):
        """privacy_scan fails closed when it is cut (its limit made short here: the real one is 300 s)."""
        from foreman import integrate

        it = integrate.Integration.__new__(integrate.Integration)
        it.p = self.project
        it.repo = {"key": "app", "public": True, "privacy_scan": "exec sleep 600"}
        short = dataclasses.replace(commands.POINTS["repo.privacy_scan"], timeout=0.5)
        with mock.patch.dict(commands.POINTS, {"repo.privacy_scan": short}):
            with self.assertRaises(integrate.Stop) as e:
                it.scan_public("PR body", "plain text")
        self.assertIn("the privacy scan of the PR body did not end in 0.5 s", str(e.exception))

    def test_lock_busy_is_cut_at_its_limit(self):
        """A busy command that never ends is cut (with what it printed) after its 5 s and the resource counts as
        free: the lock is taken without waiting busy_wait; the command under the lock does not get FOREMAN_POINT."""
        self.write_toml(
            TOML.replace('busy = "env > {tmp}/busy.env"', 'busy = "echo in use; exec sleep 600"\nbusy_wait = 600')
        )
        out = self.tmp / "under.env"
        t0 = time.monotonic()
        r = self.cli(
            "lock",
            "widget",
            "run",
            "test",
            "bash",
            "-c",
            f"env > {out}",
            env={"FOREMAN_WHO": "w1"},
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertLess(time.monotonic() - t0, 60)
        self.assertNotIn("still busy", r.stderr)
        self.assertNotIn("FOREMAN_POINT", env_file(out))


if __name__ == "__main__":
    unittest.main()
