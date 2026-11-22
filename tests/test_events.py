"""`foreman events --json --task ID`: a task's path with the kinds written where each transition happens."""

import json
import os

from helpers import Env, fm
from test_json_contract import SHAPES, conforms

from foreman import util
from foreman.loop import tick
from foreman.providers import registry
from foreman.steps import talk


def as_agent(e: Env, tid: str, *args: str) -> None:
    os.environ["FOREMAN_TASK"] = tid
    try:
        fm(e.home, *args)
    finally:
        os.environ.pop("FOREMAN_TASK")


def events(e: Env, tid: str) -> list[dict[str, str]]:
    return json.loads(fm(e.home, "events", "--json", "--task", tid))


def test_events_json_follows_a_task_through_its_transitions():
    e = Env(test="true")
    tid = registry.backlog(e.p).add("x", status="backlog", criteria="y")
    registry.backlog(e.p).update(tid, status="refining")
    plan = e.home / "plan.md"
    plan.write_text("the plan\n")
    as_agent(e, tid, "ready", "--plan", str(plan))
    tick.tick(e.p)
    e.change(tid)
    as_agent(e, tid, "done", "--summary", "s")
    fm(e.home, "reject", tid, "first line\nsecond line")
    talk.return_to_agent(e.p, tid, "conflict", "CONFLICT in app.py")
    as_agent(e, tid, "done", "--summary", "s")
    e.review(tid, 0)
    tick.tick(e.p)
    fm(e.home, "accept", tid)
    tick.tick(e.p)
    rows = events(e, tid)
    assert all(conforms(r, SHAPES["events"]) for r in rows)
    named = [r for r in rows if r["kind"] != "other"]
    assert [r["kind"] for r in named] == ["planned", "built", "rejected", "conflict", "built", "review", "accepted", "merged"]
    by = {r["kind"]: r["text"] for r in named}
    assert "first line\nsecond line" in by["rejected"]
    assert "CONFLICT in app.py" in by["conflict"]
    assert "round 1" in by["review"] and "0 open" in by["review"]
    assert [r["at"] for r in rows] == sorted(r["at"] for r in rows)


def test_rows_without_a_kind_are_other_and_other_tasks_are_left_out():
    e = Env()
    tid = registry.backlog(e.p).add("x", status="ready", criteria="y")
    util.append_jsonl(e.p.state / "events.jsonl", {"time": "2020-01-01 00:00:00", "task": tid, "text": "old"})
    util.append_jsonl(e.p.state / "events.jsonl", {"time": "2020-01-01 00:00:01", "task": "TY-99", "text": "other task"})
    assert [r for r in events(e, tid) if r["at"].startswith("2020")] == [{"at": "2020-01-01 00:00:00", "kind": "other", "text": "old"}]
