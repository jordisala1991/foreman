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

    def row(self, iid):
        with mock.patch("fm.costs.of_sessions", stub):
            return next(r for r in metrics.per_issue(self.e.p) if r["id"] == iid)

    def make(self, **keys):
        iid = backlog.add(self.e.p, "x", status="ready", criteria="y")
        backlog.update(self.e.p, iid, **keys)
        return iid

    def test_split_by_kind(self):
        iid = self.make(sessions=["A", "B", "C"], session_kinds={"A": "define", "B": "work", "C": "review"}, reviews=[{}])
        r = self.row(iid)
        self.assertEqual((r["plan_usd"], r["work_usd"], r["usd"], r["rounds"]), (1.0, 2.0, 3.5, 1))

    def test_rework_sessions_add_up(self):
        iid = self.make(sessions=["B1", "B2"], session_kinds={"B1": "work", "B2": "work"})
        self.assertEqual(self.row(iid)["work_usd"], 5.0)

    def test_legacy_issue_keeps_total(self):
        iid = self.make(sessions=["L"])
        r = self.row(iid)
        self.assertEqual((r["plan_usd"], r["work_usd"], r["usd"], r["rounds"]), (0, 0, 4.0, 0))

    def test_text_has_columns_and_same_total(self):
        iid = self.make(sessions=["A", "B"], session_kinds={"A": "define", "B": "work"})
        t = metrics.text([self.row(iid)])
        self.assertIn("plan$", t)
        self.assertIn("work$", t)
        self.assertIn("rounds", t)
        self.assertIn("total $3.00", t)


class Cost(unittest.TestCase):
    def test_each_session_is_read_once_and_shared_message_ids_do_not_double_count(self):
        import json

        e = Env()
        iid = backlog.add(e.p, "x", status="ready", criteria="y")
        backlog.update(e.p, iid, sessions=["s1", "s2"], session_kinds={"s1": "define", "s2": "work"})
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
            r = metrics.per_issue(e.p)[0]
        self.assertEqual(sorted(calls), [["s1"], ["s2"]])
        self.assertEqual(r["usd"], 2.0)  # m1 once, as the old single pass counted it
        self.assertEqual(r["usd"], metrics.costs.of_sessions(["s1", "s2"])["usd"])
        self.assertEqual((r["plan_usd"], r["work_usd"]), (2.0, 0))  # counted in its first session


class Recording(unittest.TestCase):
    def test_launch_and_reviewer_record_kinds(self):
        e = Env()
        d = backlog.add(e.p, "vague", criteria="c")
        w = backlog.add(e.p, "clear", status="ready", criteria="y")
        loop.tick(e.p)
        self.assertEqual(list(e.issue(d)["session_kinds"].values()), ["define"])
        self.assertEqual(list(e.issue(w)["session_kinds"].values()), ["work"])
        agents.launch_reviewer(e.p, e.issue(w), "abc123", "hunter")
        agents.launch_reviewer(e.p, e.issue(w), "abc123")
        i = e.issue(w)
        self.assertEqual(sorted(i["session_kinds"].values()), ["review", "review", "work"])
        self.assertEqual(sorted(i["sessions"]), sorted(i["session_kinds"]))

    def test_fill_sessions_stores_agent_kind_and_review(self):
        e = Env()
        iid = backlog.add(e.p, "clear", status="ready", criteria="y")
        backlog.update(e.p, iid, agent={"short": "aa", "kind": "work"}, reviewer={"short": "rr"})
        agents.fill_sessions(e.p, {"aa-full": {"id": "aa"}, "rr-full": {"id": "rr"}})
        i = e.issue(iid)
        self.assertEqual(i["session_kinds"], {"aa-full": "work", "rr-full": "review"})
        self.assertEqual(i["sessions"], ["aa-full", "rr-full"])
