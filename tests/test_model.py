"""Model and effort per task: filed, edited, shown, launched with, priced, measured."""

import json
import os
import unittest
from unittest import mock

from helpers import Env, fm
from test_subtasks import owner

from fm import agents, backlog, config, costs, loop, metrics


def flags(c: dict) -> tuple[str, str]:
    a = c["argv"]
    return a[a.index("--model") + 1], a[a.index("--effort") + 1]


class Choice(unittest.TestCase):
    def setUp(self):
        owner(self)
        self.e = Env(slots=3)

    def shown(self, tid: str) -> dict:
        return json.loads(fm(self.e.home, "show", tid))

    def test_add_and_edit_take_every_model_and_effort(self):
        for m in config.MODELS:
            for f in config.EFFORTS:
                tid = fm(self.e.home, "add", "T", "--criteria", "c", "--model", m, "--effort", f).strip()
                i = self.shown(tid)
                self.assertEqual((i["model"], i["effort"]), (m, f))
        fm(self.e.home, "edit", tid, "--model", "haiku", "--effort", "low")
        i = self.shown(tid)
        self.assertEqual((i["model"], i["effort"]), ("haiku", "low"))

    def test_unknown_value_fails_and_changes_nothing(self):
        tid = backlog.add(self.e.p, "t", criteria="c")
        before = self.e.task(tid)
        for args in (("add", "x", "--model", "gpt"), ("edit", tid, "--effort", "extreme"), ("ready", "--model", "gpt")):
            with self.assertRaises(SystemExit):
                fm(self.e.home, *args)
        self.assertEqual(self.e.task(tid), before)
        self.assertEqual(len(backlog.tasks(backlog.load(self.e.p))), 1)

    def test_edit_follows_the_other_fields_rules(self):
        tid = backlog.add(self.e.p, "t", criteria="c")
        for st in ("refining", "in progress", "in review"):
            backlog.update(self.e.p, tid, status=st)
            before = self.e.task(tid)
            with self.assertRaises(SystemExit):
                fm(self.e.home, "edit", tid, "--model", "opus")
            self.assertEqual(self.e.task(tid), before)
        for st in ("triage", "backlog", "ready"):
            backlog.update(self.e.p, tid, status=st, plan_file="/x/p.md")
            fm(self.e.home, "edit", tid, "--model", "opus")
            i = self.e.task(tid)
            self.assertEqual((i["status"], i["plan_file"], i["model"]), (st, "/x/p.md", "opus"))

    def test_ready_sets_model_and_effort(self):
        tid = backlog.add(self.e.p, "t", criteria="c")
        backlog.update(self.e.p, tid, status="refining")
        with mock.patch.dict(os.environ, {"FM_TASK": tid}):
            fm(self.e.home, "ready", "--no-plan", "x", "--model", "opus", "--effort", "high")
        i = self.e.task(tid)
        self.assertEqual((i["status"], i["model"], i["effort"]), ("ready", "opus", "high"))

    def test_responsible_agent_launches_with_the_tasks_choice(self):
        a = backlog.add(self.e.p, "a", status="ready", criteria="c", model="fable", effort="xhigh")
        b = backlog.add(self.e.p, "b", status="backlog", criteria="c", model="opus", effort="max")
        c = backlog.add(self.e.p, "c", status="ready", criteria="c")
        loop.tick(self.e.p)
        got = {tuple(x["argv"][-1].split("\n")[0].strip("[]").split(" ")[1:]): flags(x) for x in self.e.calls()}
        self.assertEqual(got[("responsible", a)], ("fable", "xhigh"))
        self.assertEqual(got[("planning", b)], ("sonnet", "high"))
        self.assertEqual(got[("responsible", c)], ("sonnet", "medium"))
        self.assertEqual(self.e.task(a)["agent"]["effort"], "xhigh")
        ev = (self.e.p.state / "events.jsonl").read_text()
        self.assertIn("(work, fable, xhigh, session", ev)
        self.assertIn("(work, sonnet, medium, session", ev)

    def test_planning_brief_shows_the_choice(self):
        i = {"id": "TY-1", "title": "t", "model": "opus", "effort": "high"}
        self.assertIn("opus / high", agents.brief(self.e.p, i, "planning"))
        self.assertIn("project default / project default", agents.brief(self.e.p, {**i, "model": "", "effort": ""}, "planning"))


class Measured(unittest.TestCase):
    def setUp(self):
        self.e = Env()

    def test_metrics_row_and_line_show_model_and_effort(self):
        a = backlog.add(self.e.p, "a", criteria="c")
        backlog.update(self.e.p, a, agent={"kind": "responsible", "model": "opus", "effort": "high"})
        b = backlog.add(self.e.p, "b", criteria="c", model="haiku")
        c = backlog.add(self.e.p, "c", criteria="c")
        with mock.patch("fm.costs.of_sessions", lambda s, seen=None: {"usd": 3.0, "minutes": 0}):
            rows = {r["id"]: r for r in metrics.per_task(self.e.p)}
        self.assertEqual((rows[a]["model"], rows[a]["effort"]), ("opus", "high"))
        self.assertEqual(rows[b]["model"], "haiku")
        self.assertIsNone(rows[c]["model"])
        line = next(x for x in metrics.text(list(rows.values())).splitlines() if x.startswith(a))
        self.assertEqual(line.split()[3:5], ["opus", "high"])

    def test_fable_is_priced(self):
        d = self.e.tmp / "transcripts/proj"
        d.mkdir(parents=True)
        rec = {
            "type": "assistant",
            "timestamp": "2026-11-07T10:00:00Z",
            "message": {"id": "m1", "model": "claude-fable-5-2", "usage": {"input_tokens": 1_000_000, "output_tokens": 0}},
        }
        (d / "s1.jsonl").write_text(json.dumps(rec) + "\n")
        self.assertEqual(costs.of_sessions(["s1"])["usd"], 8.0)


if __name__ == "__main__":
    unittest.main()
