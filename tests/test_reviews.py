"""`foreman reviews --json --task ID`: a task's review rounds with the text of each review file."""

import json
from typing import Any

from helpers import Env, fm
from test_events import as_agent
from test_json_contract import SHAPES, conforms

from foreman.loop import tick
from foreman.providers import registry
from foreman.steps import talk


def reviews(e: Env, tid: str) -> list[dict[str, Any]]:
    return json.loads(fm(e.home, "reviews", "--json", "--task", tid))


def in_review(e: Env) -> str:
    tid = registry.backlog(e.p).add("x", status="ready", criteria="y")
    tick.tick(e.p)
    e.change(tid)
    as_agent(e, tid, "done", "--summary", "s")
    return tid


def verdict(e: Env, tid: str, name: str, text: str, open_findings: int) -> None:
    e.hunt_then_review(tid)
    f = e.home / name
    f.write_text(text)
    as_agent(e, tid, "verdict", "--open", str(open_findings), "--file", str(f))


def test_reviews_json_gives_each_round_with_its_text():
    e = Env(test="true")
    tid = in_review(e)
    verdict(e, tid, "r1.md", "## To fix\n1. a\n", 1)
    talk.return_to_agent(e.p, tid, "review", "fix")
    as_agent(e, tid, "done", "--summary", "s")
    verdict(e, tid, "r2.md", "Clean.\n", 0)
    rows = reviews(e, tid)
    assert all(conforms(r, SHAPES["reviews"]) for r in rows)
    assert [r["round"] for r in rows] == [1, 2]
    assert [r["open"] for r in rows] == [1, 0]
    assert [r["text"] for r in rows] == ["## To fix\n1. a\n", "Clean.\n"]
    stored = e.task(tid)["reviews"]
    assert [(r["tip"], r["at"]) for r in rows] == [(v["tip"], v["at"]) for v in stored]


def test_reviews_json_is_empty_without_reviews():
    e = Env()
    tid = registry.backlog(e.p).add("x", status="ready", criteria="y")
    assert reviews(e, tid) == []
    assert reviews(e, "TY-99") == []


def test_a_review_file_that_cannot_be_read_gives_empty_text():
    e = Env(test="true")
    tid = in_review(e)
    e.hunt_then_review(tid)
    as_agent(e, tid, "verdict", "--open", "2", "--file", str(e.home / "gone.md"))
    rows = reviews(e, tid)
    assert [(r["open"], r["text"]) for r in rows] == [(2, "")]
