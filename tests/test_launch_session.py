"""`foreman launch --session`: the agent starts as a Claude Code session of its own (`claude --bg`),
tested with a fake `claude` in PATH that records its command line. With `[agents] sessions` false it is refused,
naming the key, and nothing changes; with it true the command carries the launch line and the brief, the launch
token in `--settings` env (FOREMAN_LAUNCH, the lasting mark), no Remote Control, and the session id from `--bg`
is tied to the launch."""

import json
import os
import re

from helpers import ProjectCase, git, make_repo
from test_identity import TOML

from foreman import guard, identity, util
from foreman.backlog import Backlog

SID = "8e28877c-fe43-4d24-8cc9-849cbae066ce"
FAKE = """#!/bin/sh
if [ "$1" = agents ]; then
  echo '[{"id":"8e28877c","sessionId":"%(sid)s","name":"s1","cwd":"x"}]'
  exit 0
fi
printf '%%s\\n' "$PWD" > "%(log)s.cwd"
for a in "$@"; do printf '%%s\\0' "$a"; done > "%(log)s"
%(tail)s
"""
OK = 'echo "backgrounded · 8e28877c · s1"'


class SessionLaunchCase(ProjectCase):
    toml = TOML

    def setUp(self):
        super().setUp()
        make_repo(self.tmp / "app")
        b = Backlog(self.project)
        b.init()
        with b.mutate() as d:
            b.add_project(d, "E1", "One", "intro")
            b.add_issue(d, "E1", "AB-1", "feature", status="ready")
            b.set_sub(d, "AB-1", "T1", title="part")
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.log = self.tmp / "claude.args"

    def fake(self, tail=OK):
        f = self.bin / "claude"
        f.write_text(FAKE % {"sid": SID, "log": self.log, "tail": tail})
        f.chmod(0o755)

    def on(self, value="true"):
        self.write_toml(self.toml.replace("[agents]\n", f"[agents]\nsessions = {value}\n", 1))

    def run_launch(self, *args, rc=0):
        env = {"PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}"}
        r = self.cli("launch", *args, env=env)
        self.assertEqual(r.returncode, rc, r.stdout + r.stderr)
        return r

    def argv(self):
        return self.log.read_bytes().decode().split("\0")[:-1]

    def prod(self, *extra, name="s1"):
        return [
            name,
            "--type",
            "foreman:dev",
            "--issue",
            "AB-1",
            "--sub",
            "T1",
            "--repo",
            "app",
            "--branch",
            "b/s1",
            *extra,
        ]


class SwitchTest(SessionLaunchCase):
    def snapshot(self):
        return (
            self.launches(),
            (self.project.state / "agents.json").exists(),
            Backlog(self.project).data_path.read_bytes(),
            (self.tmp / "wt").exists(),
        )

    def test_default_is_false_and_the_switch_is_the_products_own(self):
        self.assertIs(self.project.section("agents")["sessions"], False)
        from foreman import config

        self.assertIn("sessions", config.PRODUCT_SETTINGS["agents"])
        self.assertNotIn("sessions", config.MACHINE_SETTINGS["agents"])

    def test_key_false_refuses_naming_it_and_changes_nothing(self):
        self.fake()
        before = self.snapshot()
        r = self.run_launch(*self.prod("--session"), rc=2)
        self.assertIn("[agents] sessions", r.stderr)
        self.assertNotIn("[foreman launch", r.stdout)
        self.assertFalse(self.log.exists(), "claude must not run")
        self.assertEqual(self.snapshot(), before)

    def test_without_session_the_launch_is_what_it_was_even_with_the_key_true(self):
        self.on()
        self.fake()
        self.run_launch(*self.prod())
        self.assertFalse(self.log.exists())
        (rec,) = self.launches().values()
        self.assertNotIn("kind", rec)

    def test_a_non_boolean_key_is_an_error(self):
        from foreman import config

        err, _ = config.problems({"agents": {"sessions": "yes"}})
        self.assertTrue(any("[agents] sessions" in e for e in err))

    def test_brief_file_needs_session(self):
        self.on()
        self.fake()
        r = self.run_launch(*self.prod("--brief-file", "x"), rc=2)
        self.assertIn("--brief-file goes with --session", r.stderr)


class StartTest(SessionLaunchCase):
    def setUp(self):
        super().setUp()
        self.on()
        self.fake()
        self.brief = self.tmp / "brief.md"
        self.brief.write_text("Do the part; it says `git push` in prose.\n")

    def launched(self, *extra):
        r = self.run_launch(*self.prod("--session", "--brief-file", str(self.brief), "--model", "sonnet", *extra))
        ((token, rec),) = self.launches().items()
        return r, token, rec

    def test_the_command_line(self):
        r, token, rec = self.launched()
        a = self.argv()
        self.assertEqual(a[:3], ["--bg", "-n", "s1"])
        self.assertEqual(a[a.index("--agent") + 1], "foreman:dev")
        self.assertEqual(a[a.index("--model") + 1], "sonnet")
        self.assertEqual(a[a.index("--permission-mode") + 1], "bypassPermissions")
        self.assertNotIn("--remote-control", a)
        self.assertNotIn("--session-id", a)
        self.assertEqual(
            os.path.realpath(self.log.with_suffix(".args.cwd").read_text().strip()), os.path.realpath(rec["worktree"])
        )
        prompt = a[-1]
        self.assertTrue(prompt.startswith(f"[foreman launch {token}] s1 · AB-1 T1 · branch b/s1 · worktree "))
        self.assertTrue(prompt.endswith("Do the part; it says `git push` in prose.\n"))
        self.assertIn(f"[foreman launch {token}]", r.stdout)

    def test_the_launch_token_goes_into_the_sessions_environment(self):
        _, token, _ = self.launched()
        a = self.argv()
        s = json.loads(a[a.index("--settings") + 1])
        self.assertEqual(s["env"][identity.LAUNCH_ENV], token)
        self.assertEqual(s["worktree"]["bgIsolation"], "none")

    def test_the_session_id_is_tied_to_the_launch(self):
        _, token, rec = self.launched()
        self.assertEqual(rec["kind"], identity.SESSION_KIND)
        self.assertEqual(rec["session_id"], SID)
        agents = util.read_json(self.project.state / "agents.json", {})
        self.assertTrue(agents[SID][identity.SESSION_MARK])
        self.assertEqual(agents[SID]["launch"], token)
        self.assertEqual(agents[SID]["issue"], "AB-1")

    def test_the_guard_then_treats_the_session_as_an_agent_by_id_and_by_token_alone(self):
        _, token, _ = self.launched()
        cmd = 'foreman integrate fake b/s1 "AB-1 T1"'
        self.assertIsNotNone(guard.check(self.project, cmd, {"session": SID, "agent": None}))
        self.assertEqual(guard._who(self.project, {"session": SID, "agent": None}, process=False), "agent")
        # the id forgotten (pruned): the token in the environment still marks it
        util.write_json(self.project.state / "agents.json", {})
        util.write_json(self.project.state / "launches.json", {})
        self.assertEqual(
            guard._who(self.project, {"session": SID, "agent": None, "launch": token}, process=False), "agent"
        )

    def test_json_names_the_session(self):
        r = self.run_launch(*self.prod("--session", "--json"))
        out = json.loads(r.stdout)
        self.assertEqual(out["session"], {"id": "8e28877c", "session_id": SID})
        self.assertEqual(out["kind"], identity.SESSION_KIND)

    def test_a_bare_type_is_foremans_and_no_type_means_no_agent_flag(self):
        self.run_launch("p1", "--session", "--no-backlog", "--type", "planner")
        a = self.argv()
        self.assertEqual(a[a.index("--agent") + 1], "foreman:planner")
        self.run_launch("p2", "--session", "--no-backlog")
        self.assertNotIn("--agent", self.argv())

    def test_the_price_check_still_applies(self):
        b = Backlog(self.project)
        with b.mutate() as d:
            b.set_sub(d, "AB-1", "T1", type="dev/sonnet")
        r = self.run_launch(*self.prod("--session", "--model", "opus"), rc=2)
        self.assertIn("costs more than the plan's dev/sonnet", r.stderr)
        self.assertFalse(self.log.exists())
        self.assertEqual(self.launches(), {})

    def test_a_paused_product_refuses_a_session_with_a_worktree(self):
        self.project.state.mkdir(parents=True, exist_ok=True)
        (self.project.state / "pause").write_text("paused at 10:05\n")
        before = self.launches()
        r = self.run_launch(*self.prod("--session"), rc=2)
        self.assertIn("10:05", r.stderr)
        self.assertFalse(self.log.exists())
        self.assertEqual(self.launches(), before)
        # a planner without a worktree is not held by the pause, as with the guard
        self.run_launch("p1", "--session", "--no-backlog", "--type", "planner")
        self.assertTrue(self.log.exists())

    def test_claude_failing_drops_the_launch(self):
        self.fake(tail='echo "boom" >&2; exit 3')
        r = self.run_launch(*self.prod("--session"), rc=1)
        self.assertIn("boom", r.stderr)
        self.assertEqual(self.launches(), {})

    def test_no_claude_in_path_is_refused_before_anything_is_made(self):
        (self.bin / "claude").unlink()
        r = self.cli("launch", *self.prod("--session"), env={"PATH": "/usr/bin:/bin"})
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("no `claude` command", r.stderr)
        self.assertEqual(self.launches(), {})
        self.assertFalse((self.tmp / "wt").exists())

    def test_the_short_id_alone_when_agents_json_cannot_be_read(self):
        s = (
            (self.bin / "claude")
            .read_text()
            .replace('if [ "$1" = agents ]; then', 'if [ "$1" = agents ]; then\n  echo nope; exit 1', 1)
        )
        (self.bin / "claude").write_text(s)
        r = self.run_launch(*self.prod("--session"))
        ((_, rec),) = self.launches().items()
        self.assertNotIn("session_id", rec)
        self.assertIn("8e28877c", r.stdout)
        self.assertTrue(re.search(r"\[foreman launch", r.stdout))


class ReviewSessionTest(SessionLaunchCase):
    """A reviewer launched as a session starts where the reviewed branch is checked out, never in the home (Claude Code
    does not trust it): its worktree, or the repo's main checkout when the branch has none."""

    def setUp(self):
        super().setUp()
        self.on()
        self.fake()

    def cwd(self):
        return os.path.realpath(self.log.with_suffix(".args.cwd").read_text().strip())

    def test_the_reviewer_starts_in_the_branchs_worktree(self):
        self.run_launch(*self.prod("--session"))
        self.run_launch("r1", "--review", "app:b/s1", "--session", "--no-backlog")
        self.assertEqual(self.cwd(), os.path.realpath(self.tmp / "wt" / "app-s1"))

    def test_without_a_worktree_it_starts_in_the_repos_main_checkout(self):
        git(self.tmp / "app", "branch", "lone")
        self.run_launch("r1", "--review", "app:lone", "--session", "--no-backlog")
        self.assertEqual(self.cwd(), os.path.realpath(self.tmp / "app"))
        self.assertNotEqual(self.cwd(), os.path.realpath(self.home))
