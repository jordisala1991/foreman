import itertools
import json
import os
import tempfile
import time
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from helpers import Env, fm, git

from fm import agents, backlog, delivery, health, loop, token, util


class Health(unittest.TestCase):
    def setUp(self):
        self.e = Env()
        self.p = self.e.p

    def triage(self):
        return [i for i in backlog.tasks(backlog.load(self.p)) if i["status"] == "triage"]

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

    def test_a_finding_that_goes_away_and_returns_is_filed_again_only_once_its_task_closed(self):
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

    def test_an_answer_not_delivered_after_ten_minutes_is_a_finding(self):
        tid = backlog.add(self.p, "x", status="ready", criteria="y")
        qid = backlog.ask(self.p, tid, "a", "A or B?")
        backlog.answer(self.p, qid, "B")
        self.assertEqual(health.answers_undelivered(self.p), [])
        with backlog.edit(self.p) as d:
            d["decisions_resolved"][0]["answered_at"] = "2020-01-01 00:00:00"
        (f,) = health.answers_undelivered(self.p)
        self.assertIn(tid, f["title"])
        self.assertIn("«B»", f["detail"])
        with backlog.edit(self.p) as d:  # an answer made before the stamp existed: its `by` still has the time
            del d["decisions_resolved"][0]["answered_at"]
            d["decisions_resolved"][0]["by"] = "owner (panel), 2020-01-01 00:00:00"
        self.assertEqual(len(health.answers_undelivered(self.p)), 1)

    def test_live_worktree_is_not_an_orphan(self):
        backlog.add(self.p, "x", status="ready", criteria="y")
        loop.tick(self.p)
        self.assertEqual(health.orphan_worktrees(self.p), [])

    def test_failing_tests_on_main(self):
        e = Env(test="false")
        f = health.main_tests(e.p)
        self.assertEqual(f[0]["title"], "Tests fail on main")
        self.assertEqual(json.loads((e.home / "state/ci.json").read_text())["result"], "fail")

    def test_stuck_agent(self):
        tid = backlog.add(self.p, "x", status="ready", criteria="y")
        loop.tick(self.p)
        sid = self.e.task(tid)["agent"]["session"]
        t = self.e.tmp / "transcripts/proj"
        t.mkdir(parents=True)
        (t / f"{sid}.jsonl").write_text("{}\n")
        old = time.time() - 3 * 3600
        os.utime(t / f"{sid}.jsonl", (old, old))
        backlog.update(self.p, tid, agent={**self.e.task(tid)["agent"], "started": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(old))})
        rows = {sid: {"state": "working"}}
        self.assertEqual(health.agents(self.p, rows)[0]["title"], f"{tid}'s agent looks stuck")

    def test_loop_step_errors_become_findings(self):
        util.append_jsonl(self.p.state / "errors.jsonl", {"time": util.now(), "step": "queue", "error": "boom"})
        self.assertIn("queue", health.loop_errors(self.p)[0]["title"])


class Metrics(unittest.TestCase):
    def test_cost_from_transcripts_counts_each_response_once(self):
        from fm import costs, metrics

        e = Env()
        tid = backlog.add(e.p, "x", status="ready", criteria="y")
        backlog.update(e.p, tid, sessions=["s1"])
        d = e.tmp / "transcripts/proj"
        d.mkdir(parents=True)
        rec = {
            "type": "assistant",
            "timestamp": "2026-11-07T10:00:00Z",
            "message": {"id": "m1", "model": "claude-sonnet-5-5", "usage": {"input_tokens": 1_000_000, "output_tokens": 0}},
        }
        (d / "s1.jsonl").write_text(json.dumps(rec) + "\n" + json.dumps(rec) + "\n")
        self.assertEqual(costs.of_sessions(["s1"])["usd"], 2.0)
        self.assertEqual(metrics.per_task(e.p)[0]["usd"], 2.0)

    def test_usage_skips_lines_that_are_not_objects(self):
        from fm import costs

        good = {"type": "assistant", "message": {"id": "m1", "model": "claude-sonnet-5-5", "usage": {"input_tokens": 1000, "output_tokens": 0}}}
        f = Path(tempfile.mkdtemp()) / "s.jsonl"
        f.write_text("\n".join(["[1, 2]", '"x"', "null", "not json", json.dumps(good)]) + "\n")
        u = costs.usage([f])
        self.assertEqual(u["tokens"], 1000)


class Lessons(unittest.TestCase):
    def test_what_misled_an_agent_goes_to_the_lessons_and_none_does_not(self):
        from fm import loop

        e = Env()
        tid = backlog.add(e.p, "x", status="ready", criteria="y")
        loop.tick(e.p)
        os.environ.update(FM_TASK=tid, FM_AGENT="ty-1")
        try:
            fm(e.home, "done", "--summary", "s", "--misled", "brief: the test command is wrong")
            fm(e.home, "verdict", "--open", "0", "--file", "r", "--misled", "none")
        finally:
            os.environ.pop("FM_TASK")
            os.environ.pop("FM_AGENT")
        text = e.p.lessons_file.read_text()
        self.assertIn("TY-1 (ty-1): brief: the test command is wrong", text)
        self.assertEqual(text.count("\n- "), 1)
        self.assertIn(str(e.p.lessons_file), agents.brief(e.p, e.task(tid), "responsible"))


class Evidence(unittest.TestCase):
    def commit(self, e, name, text="x"):
        (e.repo / name).write_text(text)
        git(e.repo, "add", "-A")
        git(e.repo, "commit", "-qm", name)

    def triage(self, e):
        return [i for i in backlog.tasks(backlog.load(e.p)) if i["status"] == "triage"]

    def test_failing_main_is_one_task_until_it_passes(self):
        e = Env(test="test ! -e broken")
        stamps = (f"2026-01-01 00:00:{n:02d}" for n in itertools.count(1))
        with mock.patch.object(util, "now", lambda: next(stamps)):
            self.commit(e, "broken")
            health.run(e.p, {})
            self.assertEqual(len(self.triage(e)), 1)
            self.commit(e, "other")
            health.run(e.p, {})
            self.assertEqual(len(self.triage(e)), 1)
            git(e.repo, "rm", "-q", "broken")
            git(e.repo, "commit", "-qm", "fix")
            health.run(e.p, {})
            self.assertEqual(len(self.triage(e)), 1)
            self.commit(e, "broken")
            health.run(e.p, {})
        self.assertEqual(len(self.triage(e)), 2)

    def test_failing_tests_task_names_commit_step_and_tail(self):
        e = Env(test="echo lint ok; echo boom 1>&2; exit 1")
        health.run(e.p, {})
        (i,) = self.triage(e)
        head = delivery.git(e.repo, "rev-parse", "main").out.strip()[:12]
        for text in (head, "echo lint ok", "boom"):
            self.assertIn(text, i["detail"])

    def test_run_tests_keeps_the_last_twenty_lines(self):
        e = Env(test="seq 1 30; exit 1")
        self.assertEqual(delivery.run_tests(e.p, e.repo), ("fail", "\n".join(map(str, range(11, 31)))))

    def test_failing_step_is_the_make_target(self):
        out = "lint ok\nFAIL line\nmake[1]: *** [test] Error 1\nmake: *** [check] Error 2\n"
        self.assertEqual(health.failing_step(out, "make check"), "test")
        self.assertEqual(health.failing_step("make[1]: *** [Makefile:25: lint] Error 1", "make check"), "lint")
        self.assertEqual(health.failing_step("boom", "make check"), "make check")

    def test_every_finding_has_meaning_evidence_and_a_pending_hypothesis(self):
        e = Env()
        git(e.repo, "worktree", "add", "-q", "-b", "x", str(e.home / "worktrees/ghost"), "main")
        (e.p.state).mkdir(parents=True, exist_ok=True)
        (e.p.state / "errors.jsonl").write_text(json.dumps({"step": "s", "error": "bad", "time": util.now()}) + "\n")
        tid = backlog.add(e.p, "x", status="ready", criteria="y")
        backlog.update(e.p, tid, blocked="its agent stopped: x")
        env = {k: v for k, v in os.environ.items() if k != token.ENV}

        def no(c):
            return token.Login(False)

        with mock.patch.dict(os.environ, env, clear=True):
            tokens = [
                *health.claude_token(e.p, read=lambda: "", login=no),
                *health.claude_token(e.p, read=lambda: "t", works=lambda c, t: False, login=no),
                *health.claude_token(e.p, read=lambda: "t", works=lambda c, t: True, login=no),
            ]
        found = [
            *health.loop_dead(Env().p),
            *health.orphan_worktrees(e.p),
            *health.loop_errors(e.p),
            *health.agents(e.p, {}),
            *health.main_tests(Env(test="false").p),
            *tokens,
            *health.login_expiring(token.Login(True, datetime.now(UTC) + timedelta(hours=1))),
        ]
        self.assertGreaterEqual(len(found), 9)
        for f in found:
            self.assertTrue(f["meaning"] and f["evidence"], f["key"])
            self.assertEqual(f["hypothesis"], "pending")
            for part in (f["meaning"], f["evidence"], "pending"):
                self.assertIn(part, f["detail"])

    def test_a_symptom_with_an_open_task_is_not_filed_twice(self):
        e = Env()
        wt = e.home / "worktrees/ghost"
        errors = e.p.state / "errors.jsonl"
        errors.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"step": "s", "error": "bad", "time": util.now()}) + "\n"
        git(e.repo, "worktree", "add", "-q", "-b", "x", str(wt), "main")
        errors.write_text(line)
        health.run(e.p, {})
        git(e.repo, "worktree", "remove", str(wt))
        errors.write_text("")
        health.run(e.p, {})
        git(e.repo, "worktree", "add", "-q", str(wt), "x")
        errors.write_text(line)
        health.run(e.p, {})
        self.assertEqual(len(self.triage(e)), 2)  # one orphan, one error: each filed once
        self.assertEqual(sorted(i["source"] for i in self.triage(e)), ["health:error:s:bad", "health:orphan:ghost"])


class Upgrade(unittest.TestCase):
    def test_an_open_ci_task_from_before_since_is_not_duplicated(self):
        e = Env(test="false")
        head = delivery.git(e.repo, "rev-parse", "main").out.strip()
        util.write_json(e.p.state / "ci.json", {"head": head, "at": "2020-01-01 00:00:00", "result": "fail"})
        backlog.add(e.p, "Tests fail on main", status="triage", source=f"health:ci:{head[:12]}")
        health.run(e.p, {})
        self.assertEqual(len(backlog.tasks(backlog.load(e.p))), 1)


if __name__ == "__main__":
    unittest.main()
