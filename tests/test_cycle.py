from datetime import datetime, timedelta
from typing import Any
from unittest import mock

from helpers import Env

from foreman import util
from foreman.providers import registry
from foreman.steps import metrics


def ev(*pairs: tuple[str, str]) -> list[dict[str, Any]]:
    return [{"time": f"2026-11-10 {t}", "text": text, "task": "FN-1"} for t, text in pairs]


FULL = [
    ("10:30:00", "→ backlog: let in by the owner"),
    ("10:40:00", "launched fn-1-planning (planning, sonnet, high, session a)"),
    ("11:00:00", "→ ready"),
    ("11:05:00", "launched fn-1 (responsible, opus, medium, session b)"),
    ("11:35:00", "→ in review: x"),
    ("11:45:00", "review round 1 of a1: 2 open (f)"),
    ("11:46:00", "→ in progress: returned to its agent (review): n"),
    ("12:16:00", "→ in review: y"),
    ("12:26:00", "review round 2 of b2: 0 open (f)"),
    ("12:56:00", "accepted by owner: waiting for the merge"),
    ("12:58:00", "→ done: merged, accepted by owner, as c3"),
]
SKIPPED = [
    ("09:00:00", "filed (ready): y"),
    ("09:10:00", "launched fn-2 (responsible, opus, medium, session b)"),
    ("09:40:00", "→ in review: x"),
    ("09:50:00", "review round 1 of a: 0 open (f)"),
    ("10:20:00", "accepted by owner: waiting for the merge"),
    ("10:25:00", "→ done: merged, accepted by owner, as c"),
]
NONE: dict[str, str | None] = {**{k: None for k in (*metrics.PARTS, "total")}, "merged_at": None}


class TestCycle:
    def test_parts_add_up_to_total(self):
        c = metrics.cycle(ev(*FULL))
        assert c == {"slot": 15, "planning": 20, "working": 60, "review": 21, "owner": 30, "merging": 2, "total": 148, "merged_at": "2026-11-10 12:58:00"}

    def test_part_never_gone_through_is_none(self):
        c = metrics.cycle(ev(*SKIPPED))
        assert (c["planning"], c["slot"], c["working"], c["review"], c["owner"], c["merging"], c["total"]) == (None, 10, 30, 10, 30, 5, 85)

    def test_part_entered_for_zero_minutes_is_zero(self):
        c = metrics.cycle(ev(*SKIPPED[:3], ("09:40:00", "review round 1 of a: 0 open (f)"), *SKIPPED[4:]))
        assert c["review"] == 0.0

    def test_events_before_the_rename_are_measured_the_same(self):
        old = [(t, x.replace("(planning,", "(define,").replace("(responsible,", "(work,").replace("returned to its agent", "sent back")) for t, x in FULL]
        old[-1] = ("12:58:00", "→ done: integrated, accepted by owner, as c3")
        assert metrics.cycle(ev(*old)) == metrics.cycle(ev(*FULL))

    def test_canceled_and_parent_done_have_no_cycle(self):
        for last in ("→ canceled: by the owner", "→ done: its subtasks are closed"):
            assert metrics.cycle(ev(*FULL[:5], ("12:00:00", last))) == NONE

    def test_hunter_and_reviewer_launches_change_nothing(self):
        hunt = ("11:36:00", "launched fn-1-hunt (hunter, sonnet, high, session h)")
        rev = ("11:37:00", "launched fn-1-rev (reviewer, x, y, z)")
        extra = [*FULL[:5], hunt, rev, *FULL[5:]]
        assert metrics.cycle(ev(*extra)) == metrics.cycle(ev(*FULL))


def row(tid: str, status: str, merged_days: int | None = None, **parts: float) -> dict[str, Any]:
    c: dict[str, Any] = dict(NONE)
    if merged_days is not None:
        c["merged_at"] = (datetime.now() - timedelta(days=merged_days)).strftime("%Y-%m-%d %H:%M:%S")
    c.update(parts)
    return {"id": tid, "status": status, "cycle": c}


class TestLastWeek:
    def test_median_and_worst(self):
        rows = [
            row("A", "done", 1, total=60, working=30),
            row("B", "done", 3, total=100, working=50, planning=20),
            row("C", "done", 5, total=200, working=90, planning=40),
            row("D", "done", 10, total=900),
            row("E", "canceled"),
            row("F", "in review"),
        ]
        median, worst = metrics.last_week(rows)
        assert (median["total"], median["working"], median["planning"], median["owner"]) == (100, 50, 30, None)
        assert (worst and worst["id"]) == "C"

    def test_no_merged_task(self):
        assert metrics.last_week([row("E", "canceled"), row("F", "in review")]) == ({k: None for k in (*metrics.PARTS, "total")}, None)


def no_costs(s: object, seen: object = None) -> dict[str, int]:
    return {"usd": 0, "minutes": 0}


class TestText:
    def rows(self):
        e = Env()
        tid = registry.backlog(e.p).add("x", status="ready", criteria="y")
        registry.backlog(e.p).update(tid, status="done")
        (e.p.state / "events.jsonl").unlink(missing_ok=True)  # the add wrote its own events, stamped now
        for x in ev(*SKIPPED):
            util.append_jsonl(e.p.state / "events.jsonl", {**x, "task": tid})
        with mock.patch("foreman.core.oversight.costs.of_sessions", no_costs):
            return metrics.per_task(e.p)

    def test_cycle_section(self):
        lines = metrics.text(self.rows()).splitlines()
        at = lines.index(metrics.CYCLE_TITLE)
        got = dict(zip(lines[at + 1].split(), lines[at + 2].split(), strict=True))
        assert (got["planning"], got["total"], got["merging"]) == ("-", "85", "5")

    def test_old_columns_unchanged(self):
        head = metrics.text(self.rows()).splitlines()[0].split()
        old = "id status $ model effort planning$ responsible$ rounds min rework confl fail ask stop risk"
        assert head == old.split()
