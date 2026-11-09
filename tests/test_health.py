import json
import os
import time
import unittest

from helpers import Env, fm, git

from fm import agents, backlog, health, loop, util


class Health(unittest.TestCase):
    def setUp(self):
        self.e = Env()
        self.p = self.e.p

    def triage(self):
        return [i for i in backlog.issues(backlog.load(self.p)) if i["status"] == "triage"]

    def test_dead_loop_is_seen_from_outside(self):
        self.assertEqual(health.loop_dead(self.p)[0]["key"], "loop-dead")
        util.write_json(self.p.state / "loop.json", {"epoch": time.time(), "time": util.now()})
        self.assertEqual(health.loop_dead(self.p), [])

    def test_orphan_worktree_is_filed_once(self):
        git(self.e.repo, "worktree", "add", "-q", "-b", "x", str(self.e.home / "worktrees/ghost"), "main")
        health.run(self.p, {})
        health.run(self.p, {})
        t = self.triage()
        self.assertEqual(len(t), 1)
        self.assertIn("ghost", t[0]["title"])

    def test_a_finding_that_goes_away_and_returns_is_filed_again_only_once_its_issue_closed(self):
        git(self.e.repo, "worktree", "add", "-q", "-b", "x", str(self.e.home / "worktrees/ghost"), "main")
        health.run(self.p, {})
        first = self.triage()[0]["id"]
        backlog.set_status(self.p, first, "done")
        health.run(self.p, {})  # still there, seen continuously: not again
        self.assertEqual(self.triage(), [])
        git(self.e.repo, "worktree", "remove", str(self.e.home / "worktrees/ghost"))
        health.run(self.p, {})
        git(self.e.repo, "worktree", "add", "-q", str(self.e.home / "worktrees/ghost"), "x")
        health.run(self.p, {})
        self.assertEqual(len(self.triage()), 1)

    def test_live_worktree_is_not_an_orphan(self):
        backlog.add(self.p, "x", status="ready", criterion="y")
        loop.tick(self.p)
        self.assertEqual(health.orphan_worktrees(self.p), [])

    def test_failing_tests_on_main(self):
        e = Env(test="false")
        f = health.main_tests(e.p)
        self.assertEqual(f[0]["title"], "Tests fail on main")
        self.assertEqual(json.loads((e.home / "state/ci.json").read_text())["result"], "fail")

    def test_stuck_agent(self):
        iid = backlog.add(self.p, "x", status="ready", criterion="y")
        loop.tick(self.p)
        sid = self.e.issue(iid)["agent"]["session"]
        t = self.e.tmp / "transcripts/proj"
        t.mkdir(parents=True)
        (t / f"{sid}.jsonl").write_text("{}\n")
        old = time.time() - 3 * 3600
        os.utime(t / f"{sid}.jsonl", (old, old))
        rows = {sid: {"state": "working"}}
        self.assertEqual(health.agents(self.p, rows)[0]["title"], f"{iid}'s agent looks stuck")

    def test_loop_step_errors_become_findings(self):
        util.append_jsonl(self.p.state / "errors.jsonl", {"time": util.now(), "step": "queue", "error": "boom"})
        self.assertIn("queue", health.loop_errors(self.p)[0]["title"])


class Metrics(unittest.TestCase):
    def test_cost_from_transcripts_counts_each_response_once(self):
        from fm import costs, metrics

        e = Env()
        iid = backlog.add(e.p, "x", status="ready", criterion="y")
        backlog.update(e.p, iid, sessions=["s1"])
        d = e.tmp / "transcripts/proj"
        d.mkdir(parents=True)
        rec = {
            "type": "assistant",
            "timestamp": "2026-11-07T10:00:00Z",
            "message": {"id": "m1", "model": "claude-sonnet-5-5", "usage": {"input_tokens": 1_000_000, "output_tokens": 0}},
        }
        (d / "s1.jsonl").write_text(json.dumps(rec) + "\n" + json.dumps(rec) + "\n")
        self.assertEqual(costs.of_sessions(["s1"])["usd"], 2.0)
        self.assertEqual(metrics.per_issue(e.p)[0]["usd"], 2.0)


class Lessons(unittest.TestCase):
    def test_what_misled_an_agent_goes_to_the_lessons_and_none_does_not(self):
        from fm import loop

        e = Env()
        iid = backlog.add(e.p, "x", status="ready", criterion="y")
        loop.tick(e.p)
        os.environ.update(FM_ISSUE=iid, FM_AGENT="ty-1")
        try:
            fm(e.home, "done", "--summary", "s", "--misled", "brief: the test command is wrong")
            fm(e.home, "verdict", "--open", "0", "--file", "r", "--misled", "none")
        finally:
            os.environ.pop("FM_ISSUE")
            os.environ.pop("FM_AGENT")
        text = e.p.lessons_file.read_text()
        self.assertIn("TY-1 (ty-1): brief: the test command is wrong", text)
        self.assertEqual(text.count("\n- "), 1)
        self.assertIn(str(e.p.lessons_file), agents.brief(e.p, e.issue(iid), "work"))


if __name__ == "__main__":
    unittest.main()
