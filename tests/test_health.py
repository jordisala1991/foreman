import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from helpers import Env, fm, git

from fm import agents, backlog, health, loop, sessions, util


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

    def test_an_answer_not_delivered_after_ten_minutes_is_a_finding(self):
        iid = backlog.add(self.p, "x", status="ready", criterion="y")
        qid = backlog.ask(self.p, iid, "a", "A or B?")
        backlog.answer(self.p, qid, "B")
        self.assertEqual(health.answers_undelivered(self.p), [])
        with backlog.edit(self.p) as d:
            d["decisions_resolved"][0]["answered_at"] = "2020-01-01 00:00:00"
        (f,) = health.answers_undelivered(self.p)
        self.assertIn(iid, f["title"])
        self.assertIn("«B»", f["detail"])
        with backlog.edit(self.p) as d:  # an answer made before the stamp existed: its `by` still has the time
            del d["decisions_resolved"][0]["answered_at"]
            d["decisions_resolved"][0]["by"] = "owner (panel), 2020-01-01 00:00:00"
        self.assertEqual(len(health.answers_undelivered(self.p)), 1)

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

    def test_usage_skips_lines_that_are_not_objects(self):
        from fm import costs

        good = {"type": "assistant", "message": {"id": "m1", "model": "claude-sonnet-5-5", "usage": {"input_tokens": 1000, "output_tokens": 0}}}
        f = Path(tempfile.mkdtemp()) / "s.jsonl"
        f.write_text("\n".join(["[1, 2]", '"x"', "null", "not json", json.dumps(good)]) + "\n")
        u = costs.usage([f])
        self.assertEqual((u["calls"], u["tokens"]), (1, 1000))


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


class Copies(unittest.TestCase):
    def setUp(self):
        self.e = Env()
        self.p = self.e.p
        self.iid = backlog.add(self.p, "x", status="ready", criterion="y")
        loop.tick(self.p)
        self.agent = self.e.issue(self.iid)["agent"]
        self.sid = self.agent["session"]

    def rows(self, state="done", *extra, drop=False):
        self.e.set_state("ty-1", state)
        f = self.e.bin / "agents.json"
        rows = json.loads(f.read_text())
        if drop:
            rows = [r for r in rows if r["name"] != "ty-1"]
        base = {"id": "c0ffee01", "sessionId": "c0ffee01-0000-0000", "name": "ty-1", "cwd": self.agent["cwd"], "state": "working"}
        for k, over in enumerate(extra):
            row = {**base, **over}
            row["id"] = f"c0ffee0{k + 1}"
            row["sessionId"] = f"c0ffee0{k + 1}-0000-0000"
            rows.append({key: v for key, v in row.items() if v is not None})
        f.write_text(json.dumps(rows))
        return sessions.listing(self.p)

    def test_copy_working_while_recorded_is_idle_is_one_finding(self):
        rows = self.rows("done", {})
        found = health.agent_copies(self.p, rows)
        self.assertEqual([f["key"] for f in found], [f"copy:{self.iid}:{self.sid[:8]}"])
        self.assertIn("c0ffee01", found[0]["detail"])
        self.assertIn(found[0], health.findings(self.p, rows))

    def test_no_finding_when_the_rows_agree(self):
        self.assertEqual(health.agent_copies(self.p, self.rows("done")), [])
        self.assertEqual(health.agent_copies(self.p, self.rows("working")), [])
        self.assertEqual(health.agent_copies(self.p, self.rows("working", {})), [])
        self.assertEqual(health.agent_copies(self.p, self.rows("done", {"state": "done"})), [])
        self.assertEqual(health.agent_copies(self.p, {}), [])
        self.rows("done", {})
        backlog.update(self.p, self.iid, agent={**self.agent, "session": None})
        self.assertEqual(health.agent_copies(self.p, sessions.listing(self.p)), [])

    def test_other_name_or_folder_is_not_a_copy(self):
        self.assertEqual(health.agent_copies(self.p, self.rows("done", {"name": "ty-2"})), [])
        self.assertEqual(health.agent_copies(self.p, self.rows("done", {"cwd": "/elsewhere"})), [])
        self.assertEqual(health.agent_copies(self.p, self.rows("done", {"cwd": None})), [])

    def test_stopped_or_missing_recorded_session_counts_as_idle(self):
        self.assertEqual(len(health.agent_copies(self.p, self.rows("stopped", {}))), 1)
        self.assertEqual(len(health.agent_copies(self.p, self.rows("done", {}, drop=True))), 1)

    def test_a_working_row_with_status_idle_is_idle_and_the_detail_says_so(self):
        rows = self.rows("done", {})
        rows[self.sid] = {**rows.get(self.sid, {}), "state": "working", "status": "idle"}
        found = health.agent_copies(self.p, rows)
        self.assertEqual(len(found), 1)
        self.assertIn("which is idle (state working)", found[0]["detail"])

    def test_two_copies_are_one_finding(self):
        found = health.agent_copies(self.p, self.rows("done", {}, {}))
        self.assertEqual(len(found), 1)
        self.assertIn("c0ffee01", found[0]["detail"])
        self.assertIn("c0ffee02", found[0]["detail"])

    def test_closed_task_has_no_finding(self):
        rows = self.rows("done", {})
        backlog.set_status(self.p, self.iid, "done")
        self.assertEqual(health.agent_copies(self.p, rows), [])

    def test_the_finding_is_filed_once(self):
        rows = self.rows("done", {})
        health.run(self.p, rows)
        health.run(self.p, rows)
        t = [i for i in backlog.issues(backlog.load(self.p)) if i["status"] == "triage"]
        self.assertEqual(len(t), 1)
        self.assertEqual(t[0]["source"], f"health:copy:{self.iid}:{self.sid[:8]}")


if __name__ == "__main__":
    unittest.main()
