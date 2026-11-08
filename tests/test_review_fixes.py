"""One test per defect found by the code review (2026-11-08)."""

import os
import unittest
from pathlib import Path

from helpers import Env, git

from fm import agents, backlog, cli, guard, health, loop


class GuardFalsePositives(unittest.TestCase):
    def check(self, cmd: str, cwd: str = "/h") -> str | None:
        return guard.check_bash(cmd, guard.Scope(cwd, [Path("/w")], "fm/ty-1", Path("/h/fm.toml"), []))

    def test_reading_the_config_with_redirects_is_not_a_write(self):
        self.assertIsNone(self.check("cat fm.toml 2>/dev/null"))
        self.assertIsNone(self.check("grep -n slots fm.toml 2>&1 | head"))
        self.assertIsNone(self.check("cp fm.toml /tmp/fm.toml.bak"))
        self.assertIsNone(self.check("echo x > other/fm.toml"))  # not the project's config

    def test_writing_the_config_is_still_asked(self):
        self.assertEqual(self.check("echo x >> fm.toml"), "config")
        self.assertEqual(self.check("cp /tmp/new.toml /h/fm.toml"), "config")
        self.assertEqual(self.check("sed -i '' s/a/b/ fm.toml"), "config")
        self.assertEqual(self.check("echo x | tee fm.toml"), "config")

    def test_separators_inside_quotes_do_not_split(self):
        self.assertIsNone(self.check('git commit -m "Docs: x; git push origin main is for the owner"'))
        self.assertEqual(self.check("cd /w && git push origin main"), "push-main")
        self.assertEqual(self.check("ls\ngit push origin main"), "push-main")


class TestsRunOnTheCommit(unittest.TestCase):
    def test_an_uncommitted_file_cannot_make_the_tests_pass(self):
        e = Env(test="python3 -c 'import helper'", autonomy="low")
        iid = backlog.add(e.p, "x", status="ready", criterion="y")
        loop.tick(e.p)
        wt = Path(e.issue(iid)["worktree"])
        (wt / "app.py").write_text("import helper\n")
        (wt / "helper.py").write_text("")  # never committed
        git(wt, "commit", "-qam", "uses helper")
        backlog.set_status(e.p, iid, "in review", report={"summary": "s", "confused": ""})
        e.review(iid)
        loop.tick(e.p)
        self.assertEqual((e.issue(iid)["status"], e.issue(iid)["check"]["tests"]), ("in review", "fail"))


class Sessions(unittest.TestCase):
    def test_a_session_not_listed_at_launch_gets_its_id_later_and_is_not_blocked(self):
        e = Env()
        iid = backlog.add(e.p, "x", status="ready", criterion="y")
        loop.tick(e.p)
        a = e.issue(iid)["agent"]
        backlog.update(e.p, iid, agent={**a, "session": None, "started": "2000-01-01 00:00:00"}, sessions=[])
        loop.tick(e.p)
        i = e.issue(iid)
        self.assertEqual((i["agent"]["session"], i.get("blocked")), (a["session"], ""))


class Isolation(unittest.TestCase):
    def test_one_broken_queued_issue_does_not_stop_the_others(self):
        e = Env(test="true", autonomy="low")
        ids = [backlog.add(e.p, f"x{n}", status="ready", criterion="y") for n in range(2)]
        loop.tick(e.p)
        for iid in ids:
            wt = Path(e.issue(iid)["worktree"])
            (wt / f"{iid}.txt").write_text("x\n")
            git(wt, "add", "-A")
            git(wt, "commit", "-qm", "c")
            backlog.set_status(e.p, iid, "in review", report={"summary": "s", "confused": ""})
        loop.tick(e.p)  # both reviewers start (two slots)
        for iid in ids:
            os.environ["FM_ISSUE"] = iid
            cli.main(["--home", str(e.home), "verdict", "--high", "0", "--medium", "0", "--file", "r.md"])
        os.environ.pop("FM_ISSUE")
        backlog.update(e.p, ids[0], branch="no/such-branch")
        loop.tick(e.p)
        self.assertIn("could not check", e.issue(ids[0])["blocked"])
        self.assertEqual(e.issue(ids[1])["status"], "done")


class Closed(unittest.TestCase):
    def test_cancel_closes_the_questions_and_no_answer_reaches_the_cancelled_agent(self):
        e = Env()
        iid = backlog.add(e.p, "x", status="ready", criterion="y")
        loop.tick(e.p)
        backlog.ask(e.p, iid, iid.lower(), "A or B?")
        cli.main(["--home", str(e.home), "cancel", iid])
        d = backlog.load(e.p)
        self.assertEqual(backlog.open_questions(d), [])
        self.assertFalse(Path(e.issue(iid)["worktree"]).exists())
        e.set_state(iid.lower(), "done")
        loop.tick(e.p)
        self.assertEqual(len(e.calls()), 1)  # only the launch


class Orphans(unittest.TestCase):
    def test_a_ready_task_with_its_worktree_is_not_an_orphan(self):
        e = Env()
        iid = backlog.add(e.p, "x", status="ready", criterion="y")
        loop.tick(e.p)
        cli.main(["--home", str(e.home), "pause"])
        backlog.set_status(e.p, iid, "ready", "relaunch")
        self.assertEqual(health.orphan_worktrees(e.p), [])


class LaunchRace(unittest.TestCase):
    def test_a_task_cancelled_while_its_session_starts_stays_cancelled(self):
        e = Env()
        iid = backlog.add(e.p, "x", status="ready", criterion="y")
        real = loop.sessions.start

        def start_and_cancel(*a, **k):
            r = real(*a, **k)
            backlog.set_status(e.p, iid, "canceled", "owner")
            return r

        loop.sessions.start = start_and_cancel
        try:
            agents.launch(e.p, e.issue(iid))
        finally:
            loop.sessions.start = real
        self.assertEqual(e.issue(iid)["status"], "canceled")

    def test_only_one_loop_per_registry(self):
        import fcntl
        import subprocess
        import sys

        e = Env()
        os.environ["FM_REGISTRY"] = str(e.tmp / "projects.json")
        try:
            with open(str(e.tmp / "projects.json") + ".loop.lock", "a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                r = subprocess.run(
                    [sys.executable, str(Path(__file__).parent.parent / "bin/fm"), "--home", str(e.home), "loop", "--once"],
                    capture_output=True,
                    text=True,
                    env=os.environ,
                )
            self.assertIn("another loop", r.stderr)
        finally:
            os.environ.pop("FM_REGISTRY")


if __name__ == "__main__":
    unittest.main()
