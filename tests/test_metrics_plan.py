import unittest
from unittest import mock

from helpers import Env

from fm import agents, backlog, loop, metrics

USD = {"A": 1.0, "B": 2.0, "B1": 2.0, "B2": 3.0, "C": 0.5, "L": 4.0}


def stub(sids, seen=None):
    return {"usd": sum(USD[s] for s in sids), "minutes": 0}


class PlanVsWork(unittest.TestCase):
    def setUp(self):
        self.e = Env()

    def row(self, tid):
        with mock.patch("fm.costs.of_sessions", stub):
            return next(r for r in metrics.per_task(self.e.p) if r["id"] == tid)

    def make(self, **keys):
        tid = backlog.add(self.e.p, "x", status="ready", criteria="y")
        backlog.update(self.e.p, tid, **keys)
        return tid

    def test_split_by_kind(self):
        tid = self.make(sessions=["A", "B", "C"], session_kinds={"A": "planning", "B": "responsible", "C": "review"}, reviews=[{}])
        r = self.row(tid)
        self.assertEqual((r["planning_usd"], r["responsible_usd"], r["usd"], r["rounds"]), (1.0, 2.0, 3.5, 1))

    def test_rework_sessions_add_up(self):
        tid = self.make(sessions=["B1", "B2"], session_kinds={"B1": "responsible", "B2": "responsible"})
        self.assertEqual(self.row(tid)["responsible_usd"], 5.0)

    def test_legacy_task_keeps_total(self):
        tid = self.make(sessions=["L"])
        r = self.row(tid)
        self.assertEqual((r["planning_usd"], r["responsible_usd"], r["usd"], r["rounds"]), (0, 0, 4.0, 0))

    def test_text_has_columns_and_same_total(self):
        tid = self.make(sessions=["A", "B"], session_kinds={"A": "planning", "B": "responsible"})
        t = metrics.text([self.row(tid)])
        self.assertIn("planning$", t)
        self.assertIn("responsible$", t)
        self.assertIn("rounds", t)
        self.assertIn("total $3.00", t)


class Cost(unittest.TestCase):
    def test_each_session_is_read_once_and_shared_message_ids_do_not_double_count(self):
        import json

        e = Env()
        tid = backlog.add(e.p, "x", status="ready", criteria="y")
        backlog.update(e.p, tid, sessions=["s1", "s2"], session_kinds={"s1": "planning", "s2": "responsible"})
        d = e.tmp / "transcripts/proj"
        d.mkdir(parents=True)
        rec = {
            "type": "assistant",
            "timestamp": "2026-11-07T10:00:00Z",
            "message": {"id": "m1", "model": "claude-sonnet-5-5", "usage": {"input_tokens": 1_000_000, "output_tokens": 0}},
        }
        for s in ("s1", "s2"):  # s2 replays s1's message id
            (d / f"{s}.jsonl").write_text(json.dumps(rec) + "\n")
        calls = []
        real = metrics.costs.of_sessions
        with mock.patch("fm.costs.of_sessions", lambda s, seen=None: calls.append(s) or real(s, seen)):
            r = metrics.per_task(e.p)[0]
        self.assertEqual(sorted(calls), [["s1"], ["s2"]])
        self.assertEqual(r["usd"], 2.0)  # m1 once, as the old single pass counted it
        self.assertEqual(r["usd"], metrics.costs.of_sessions(["s1", "s2"])["usd"])
        self.assertEqual((r["planning_usd"], r["responsible_usd"]), (2.0, 0))  # counted in its first session


class Recording(unittest.TestCase):
    def test_launch_and_reviewer_record_kinds(self):
        e = Env()
        d = backlog.add(e.p, "vague", criteria="c")
        w = backlog.add(e.p, "clear", status="ready", criteria="y")
        loop.tick(e.p)
        self.assertEqual(list(e.task(d)["session_kinds"].values()), ["planning"])
        self.assertEqual(list(e.task(w)["session_kinds"].values()), ["responsible"])
        agents.launch_reviewer(e.p, e.task(w), "abc123", "hunter")
        agents.launch_reviewer(e.p, e.task(w), "abc123")
        i = e.task(w)
        self.assertEqual(sorted(i["session_kinds"].values()), ["review", "review", "responsible"])
        self.assertEqual(sorted(i["sessions"]), sorted(i["session_kinds"]))

    def test_fill_sessions_stores_agent_kind_and_review(self):
        e = Env()
        tid = backlog.add(e.p, "clear", status="ready", criteria="y")
        backlog.update(e.p, tid, agent={"short": "aa", "kind": "responsible"}, reviewer={"short": "rr"})
        agents.fill_sessions(e.p, {"aa-full": {"id": "aa"}, "rr-full": {"id": "rr"}})
        i = e.task(tid)
        self.assertEqual(i["session_kinds"], {"aa-full": "responsible", "rr-full": "review"})
        self.assertEqual(i["sessions"], ["aa-full", "rr-full"])
