from unittest import mock

import pytest
from helpers import Env

from foreman.core.tasks import backlog
from foreman.loop import tick
from foreman.providers.claude.rows import ClaudeRows
from foreman.services.agents import launch
from foreman.steps import metrics

USD = {"A": 1.0, "B": 2.0, "B1": 2.0, "B2": 3.0, "C": 0.5, "L": 4.0}


def stub(sids, seen=None):
    return {"usd": sum(USD[s] for s in sids), "minutes": 0}


class TestPlanVsWork:
    @pytest.fixture(autouse=True)
    def set_up(self, env):
        self.e = env

    def row(self, tid):
        with mock.patch("foreman.core.oversight.costs.of_sessions", stub):
            return next(r for r in metrics.per_task(self.e.p) if r["id"] == tid)

    def make(self, **keys):
        tid = backlog.add(self.e.p, "x", status="ready", criteria="y")
        backlog.update(self.e.p, tid, **keys)
        return tid

    def test_returns_for_failures_are_counted_next_to_conflicts(self):
        r = self.row(self.make(failures=2, conflicts=1))
        assert (r["failures"], r["conflicts"]) == (2, 1)
        assert "confl fail" in metrics.text([r])

    def test_split_by_kind(self):
        tid = self.make(sessions=["A", "B", "C"], session_kinds={"A": "planning", "B": "responsible", "C": "review"}, reviews=[{}])
        r = self.row(tid)
        assert (r["planning_usd"], r["responsible_usd"], r["usd"], r["rounds"]) == (1.0, 2.0, 3.5, 1)

    def test_rework_sessions_add_up(self):
        tid = self.make(sessions=["B1", "B2"], session_kinds={"B1": "responsible", "B2": "responsible"})
        assert self.row(tid)["responsible_usd"] == 5.0

    def test_task_without_session_kinds_keeps_total(self):
        tid = self.make(sessions=["L"])
        r = self.row(tid)
        assert (r["planning_usd"], r["responsible_usd"], r["usd"], r["rounds"]) == (0, 0, 4.0, 0)

    def test_text_has_columns_and_same_total(self):
        tid = self.make(sessions=["A", "B"], session_kinds={"A": "planning", "B": "responsible"})
        t = metrics.text([self.row(tid)])
        assert "planning$" in t
        assert "responsible$" in t
        assert "rounds" in t
        assert "total $3.00" in t

    def test_misled_per_agent_in_metrics(self):
        said = {"planning": ["a"], "responsible": ["b"], "reviewer": ["c"]}
        r = self.row(self.make(misled=said))
        assert r["misled"] == said
        out = metrics.text([r])
        for line in ("misled (planning): a", "misled (responsible): b", "misled (reviewer): c"):
            assert line in out
        assert "misled (" not in metrics.text([self.row(self.make())])


class TestCost:
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
        with mock.patch("foreman.core.oversight.costs.of_sessions", lambda s, seen=None: calls.append(s) or real(s, seen)):
            r = metrics.per_task(e.p)[0]
        assert sorted(calls) == [["s1"], ["s2"]]
        assert r["usd"] == 2.0  # m1 once, as the old single pass counted it
        assert r["usd"] == metrics.costs.of_sessions(["s1", "s2"])["usd"]
        assert (r["planning_usd"], r["responsible_usd"]) == (2.0, 0)  # counted in its first session


class TestRecording:
    def test_launch_and_reviewer_record_kinds(self):
        e = Env()
        d = backlog.add(e.p, "vague", criteria="c")
        w = backlog.add(e.p, "clear", status="ready", criteria="y")
        tick.tick(e.p)
        assert list(e.task(d)["session_kinds"].values()) == ["planning"]
        assert list(e.task(w)["session_kinds"].values()) == ["responsible"]
        launch.launch_reviewer(e.p, e.task(w), "abc123", "hunter")
        launch.launch_reviewer(e.p, e.task(w), "abc123")
        i = e.task(w)
        assert sorted(i["session_kinds"].values()) == ["responsible", "review", "review"]
        assert sorted(i["sessions"]) == sorted(i["session_kinds"])

    def test_fill_sessions_stores_agent_kind_and_review(self):
        e = Env()
        tid = backlog.add(e.p, "clear", status="ready", criteria="y")
        backlog.update(e.p, tid, agent={"short": "aa", "kind": "responsible"}, reviewer={"short": "rr"})
        launch.fill_sessions(e.p, ClaudeRows({"aa-full": {"id": "aa"}, "rr-full": {"id": "rr"}}))
        i = e.task(tid)
        assert i["session_kinds"] == {"aa-full": "responsible", "rr-full": "review"}
        assert i["sessions"] == ["aa-full", "rr-full"]
