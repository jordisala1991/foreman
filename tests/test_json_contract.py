"""The `--json` reads (`projects`, `list`, `needs`, `agents`, `runs`, `events`, `reviews`, `health`, `show`) are the public contract for other programs (the
dashboard): the shapes below are frozen by hand from the task's criteria, never read from the code. Renaming, removing or
retyping a field fails; adding one does not."""

import json
import re
import time
from typing import Any, NamedTuple

from fakerepo import FakeRepo
from helpers import Env, fm
from test_watch import CHECK, in_review, listing, parent, row, task

from foreman import util
from foreman.core.project import registry as homes
from foreman.core.project.config import Project
from foreman.core.tasks import recurring, runs
from foreman.providers import registry
from foreman.steps import watch

Shape = dict[str, Any]


class OrNull(NamedTuple):
    """A nested object frozen field by field, or null."""

    shape: Shape


CHAIN = {"lens": str, "step": int, "cap": int, "open": str}
KINDS = frozenset({"question", "accept", "blocked", "main"})  # a field frozen to these values
AGENT_KINDS = frozenset({"planning", "responsible", "review", "hunter", "parent", "chain", "mechanic"})
TEXT_OR_NULL = (str, type(None))
INT_OR_NULL = (int, type(None))

SHAPES: dict[str, Shape] = {
    "projects": {
        "name": str,
        "prefix": str,
        "home": str,
        "repo": str,
        "main": str,
        "autonomy": str,
        "strategy": frozenset({"squash", "merge", "rebase"}),
        "loop_running": bool,
        "slots": int,
    },
    "list": {
        "id": str,
        "status": str,
        "type": str,
        "title": str,
        "agent": str,
        "repo": str,
        "parent": str,
        "priority": str,
        "model": str,
        "effort": str,
        "blocked": str,
        "waiting": str,
        "recurring": str,
        "chain": OrNull(CHAIN),
        "closed": TEXT_OR_NULL,
        "rework": int,
    },
    "needs": {"kind": KINDS, "task": str, "since": TEXT_OR_NULL, "text": str, "key": str},
    "agents": {"task": str, "kind": AGENT_KINDS, "model": str, "effort": str, "started": str, "round": INT_OR_NULL, "session": str, "handle": str},
    "runs": {
        "id": str,
        "task": str,
        "kind": AGENT_KINDS,
        "attempt": int,
        "session": str,
        "handle": str,
        "model": str,
        "effort": str,
        "started": str,
        "ended": TEXT_OR_NULL,
        "outcome": frozenset({"done", "failed", "canceled", None}),
        "failure_reason": str,
    },
    "health": {"loop": dict, "versions": dict, "recurring": list, "findings": list, "warnings": list},
    "health.loop": {"time": TEXT_OR_NULL, "alive": bool},
    "health.versions": {"loop": TEXT_OR_NULL, "this": str, "installed": TEXT_OR_NULL},
    "health.recurring": {
        "name": str,
        "title": str,
        "trigger": str,
        "open": str,
        "left": INT_OR_NULL,
        "next": TEXT_OR_NULL,
        "chain": OrNull(CHAIN),
    },
    "health.findings": {"key": str, "title": str, "meaning": str, "evidence": str, "hypothesis": str, "repo": str},
    "events": {
        "at": str,
        "kind": frozenset({"planned", "built", "rejected", "conflict", "failing", "review", "accepted", "merged", "other"}),
        "text": str,
    },
    "reviews": {"round": int, "tip": str, "at": str, "open": int, "text": str},
    "show": {"id": str, "title": str, "status": str, "blocked": str, "priority": str},
}


def _fits(value: object, t: Any) -> bool:
    if isinstance(t, frozenset):
        return value in t
    if isinstance(t, OrNull):
        return value is None or ((obj := util.json_object(value)) is not None and conforms(obj, t.shape))
    return isinstance(value, t) and not (isinstance(value, bool) and bool not in (t if isinstance(t, tuple) else (t,)))


def conforms(obj: dict[str, Any], shape: Shape) -> bool:
    """Every frozen field is present with its type (a bool is no int; a nested shape is checked field by field); other fields are free."""
    return all(k in obj and _fits(obj[k], t) for k, t in shape.items())


def read(e: Env, *args: str) -> Any:
    return json.loads(fm(e.home, *args))


def test_the_freeze_catches_a_rename_a_removal_and_allows_an_addition():
    good = {"kind": "question", "task": "TY-1", "since": None, "text": "t", "key": "q:TY-1 Q1"}
    assert conforms(good, SHAPES["needs"])
    assert conforms({**good, "extra": 1}, SHAPES["needs"])
    renamed = {**{k: v for k, v in good.items() if k != "text"}, "body": "t"}
    assert not conforms(renamed, SHAPES["needs"])
    assert not conforms({k: v for k, v in good.items() if k != "kind"}, SHAPES["needs"])
    assert not conforms({**good, "since": 3}, SHAPES["needs"])
    assert not conforms({**good, "kind": "go"}, SHAPES["needs"])
    agent = {"task": "TY-1", "kind": "chain", "model": "m", "effort": "e", "started": "s", "round": None, "session": "s1", "handle": "h"}
    assert conforms(agent, SHAPES["agents"])
    assert conforms({**agent, "extra": 1}, SHAPES["agents"])
    assert not conforms({**{k: v for k, v in agent.items() if k != "handle"}, "id": "h"}, SHAPES["agents"])
    assert not conforms({**agent, "kind": "reviewer"}, SHAPES["agents"])
    run = {"id": "R-1", "task": "TY-1", "kind": "hunter", "attempt": 1, "session": "s", "handle": "h", "model": "m", "effort": "e"}
    run |= {"started": "t", "ended": None, "outcome": None, "failure_reason": ""}
    assert conforms(run, SHAPES["runs"]) and conforms({**run, "extra": 1}, SHAPES["runs"])
    assert not conforms({**{k: v for k, v in run.items() if k != "outcome"}, "result": None}, SHAPES["runs"])
    assert not conforms({**run, "attempt": "1"}, SHAPES["runs"])
    assert not conforms({**run, "outcome": "merged"}, SHAPES["runs"])
    entry = {"name": "n", "title": "t", "trigger": "every 2 merges", "open": "", "left": 2, "next": None, "chain": None}
    chain = {"lens": "l", "step": 1, "cap": 3, "open": ""}
    assert conforms({**entry, "chain": chain}, SHAPES["health.recurring"])
    assert conforms({**entry, "chain": {**chain, "extra": 1}}, SHAPES["health.recurring"])
    renamed_cap = {**{k: v for k, v in chain.items() if k != "cap"}, "max": 3}
    assert not conforms({**entry, "chain": renamed_cap}, SHAPES["health.recurring"])
    assert not conforms({**entry, "left": True}, SHAPES["health.recurring"])  # a bool is no int


def test_projects_lists_every_registered_project(env: Env):
    e = env
    homes.register(e.home)
    entry = next(x for x in read(e, "projects", "--json") if x["home"] == str(e.home))
    assert conforms(entry, SHAPES["projects"])
    assert (entry["name"], entry["prefix"], entry["repo"], entry["main"], entry["autonomy"]) == ("toy", "TY", str(e.repo), "main", "manual")
    assert entry["strategy"] == "squash"  # the default
    assert entry["loop_running"] is False
    util.write_json(e.p.state / "loop.json", {"epoch": time.time(), "time": util.now()})
    entry = next(x for x in read(e, "projects", "--json") if x["home"] == str(e.home))
    assert entry["loop_running"] is True


def test_projects_strategy_is_the_configured_one():
    e = Env()
    toml = e.home / "foreman.toml"
    toml.write_text(toml.read_text().replace("[merge]", '[merge]\nstrategy = "merge"'), encoding="utf-8")
    homes.register(e.home)
    entry = next(x for x in read(e, "projects", "--json") if x["home"] == str(e.home))
    assert conforms(entry, SHAPES["projects"])
    assert entry["strategy"] == "merge"


def printed(e: Env) -> int:
    return int(fm(e.home, "project").split("slots: ")[1].split()[0])


def test_projects_slots_is_what_project_prints():
    configured = Env(slots=3)
    default = Env()
    toml = default.home / "foreman.toml"
    toml.write_text(toml.read_text().replace("slots = 2\n", ""), encoding="utf-8")
    for e, expected in ((configured, 3), (default, None)):
        homes.register(e.home)
        row = next(x for x in read(e, "projects", "--json") if x["home"] == str(e.home))
        assert type(row["slots"]) is int
        assert row["slots"] == printed(e)
        assert expected is None or row["slots"] == expected


def test_list_gives_every_task_with_the_fields_list_prints(env: Env):
    e = env
    b = registry.backlog(e.p)
    first = b.add("first", criteria="c", priority="high", model="opus", effort="high")
    chain = b.add("chain", criteria="c", chain={"lens": "l", "max_steps": 3, "max_lines": 100})
    step = b.add("step", criteria="c", parent=chain)
    weekly = b.add("weekly", criteria="c", source=recurring.SOURCE + "weekly")
    closed = b.add("closed", criteria="c")
    b.set_status(closed, "done")
    rows = {r["id"]: r for r in read(e, "list", "--json")}
    assert closed not in rows and {first, chain, step, weekly} <= set(rows)
    every = {r["id"]: r for r in read(e, "list", "--json", "--all")}
    assert closed in every
    assert all(conforms(r, SHAPES["list"]) for r in every.values())
    assert all(r["closed"] is None for r in rows.values())
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", every[closed]["closed"])
    f = rows[first]
    assert (f["priority"], f["model"], f["effort"], f["type"], f["parent"], f["chain"]) == ("high", "opus", "high", "change", "", None)
    assert rows[step]["parent"] == chain
    assert rows[weekly]["recurring"] == "weekly"
    assert rows[chain]["chain"] == {"lens": "l", "step": 1, "cap": 3, "open": step}


def test_a_done_tasks_closed_in_list_is_the_closed_show_prints(env: Env):
    b = registry.backlog(env.p)
    tid = b.add("t", criteria="c")
    b.set_status(tid, "done")
    listed = {r["id"]: r for r in read(env, "list", "--json", "--all")}
    assert listed[tid]["closed"] is not None
    assert listed[tid]["closed"] == read(env, "show", tid)["closed"]


def test_a_done_task_sent_through_go_has_no_closed_in_list(env: Env):
    b = registry.backlog(env.p)
    tid = b.add("t", criteria="c")
    b.set_status(tid, "done")
    fm(env.home, "go", tid)
    row = {r["id"]: r for r in read(env, "list", "--json", "--all")}[tid]
    assert row["status"] == "backlog"
    assert row["closed"] is None


def test_list_counts_only_owner_rejections_as_rework(fake_git: FakeRepo):
    e = Env(repo=fake_git)
    rejected = in_review(e)
    untouched = task(e)
    returned = task(e, failures=2, conflicts=1)
    fm(e.home, "reject", rejected, "name it better")
    for flags in (("list", "--json"), ("list", "--json", "--all")):
        rows = {r["id"]: r for r in read(e, *flags)}
        assert (rows[rejected]["rework"], rows[untouched]["rework"], rows[returned]["rework"]) == (1, 0, 0)
        assert rows[rejected]["rework"] == read(e, "show", rejected)["rework"]


def test_needs_is_watchs_set_with_its_facts(fake_git: FakeRepo):
    e = Env(repo=fake_git)
    asking = task(e)
    qid = registry.backlog(e.p).ask(asking, "agent", "which   way?\n\nthis or that")
    asked = registry.backlog(e.p).load()["decisions"][0]["asked"]
    stuck = task(e, blocked="why")
    review = in_review(e, check={**CHECK, "at": "2026-11-22 10:00:00"}, report={"summary": "\n\nline one\nmore"})
    util.write_json(e.p.state / "ci.json", {"result": "fail", "since": "2026-11-22 09:00:00"})
    parent(e)  # idle, with no open question: not a need
    listing(e, row())
    found = read(e, "needs", "--json")
    assert all(conforms(n, SHAPES["needs"]) for n in found)
    by_kind = {n["kind"]: n for n in found}
    assert [n["kind"] for n in found] == ["question", "accept", "blocked", "main"]
    assert by_kind["question"] == {"kind": "question", "task": asking, "since": asked, "text": "which   way?\n\nthis or that", "key": f"q:{qid}"}
    assert (by_kind["blocked"]["task"], by_kind["blocked"]["text"], by_kind["blocked"]["since"]) == (stuck, "why", None)
    assert (by_kind["accept"]["task"], by_kind["accept"]["text"], by_kind["accept"]["since"]) == (review, "line one", "2026-11-22 10:00:00")
    assert (by_kind["main"]["task"], by_kind["main"]["since"]) == ("", "2026-11-22 09:00:00")
    assert {n["key"] for n in found} == set(watch.needs(e.p))  # the same set as `watch --once`


def test_agents_lists_what_is_live_now(fake_git: FakeRepo):
    e = Env(repo=fake_git)
    n = iter(range(1, 20))

    def live(status: str, records: dict[str, tuple[str, dict[str, Any]]], **f: object) -> str:
        """A task with each record's session, and an active run of the given kind for it (`runs.start`, as a launch does)."""
        stored: dict[str, Any] = {}
        for role, (_, fields) in records.items():
            sid = f"s{next(n)}"
            stored[role] = {"short": sid[:2], "session": sid, "model": "opus", "effort": "high", "started": "2000-01-01 00:00:00", **fields}
        tid = task(e, status=status, **stored, **f)
        for role, (kind, _) in records.items():
            runs.start(e.p, tid, kind, runs.Session(stored[role]["session"], stored[role]["short"], "opus", "high"))
        return tid

    responsible = {"agent": ("responsible", {"kind": "responsible"})}
    first = live("in progress", responsible, reviews=[{"round": 1, "tip": "t", "open": 1}])
    second = live("in progress", responsible)
    third = live("in review", {"hunter": ("hunter", {"round": 1}), "reviewer": ("review", {"round": 2})})
    chained = live("backlog", {"agent": ("chain", {"kind": "parent"})}, chain={"lens": "l", "max_steps": 3, "max_lines": 100})
    plain = live("backlog", {"agent": ("parent", {"kind": "parent"})})
    fixing = live("triage", {"mechanic": ("mechanic", {"kind": "mechanic"})})
    planning = live("refining", {"agent": ("planning", {"kind": "planning"})})
    live("in progress", responsible)  # s9: its session ended
    closed = live("in progress", responsible)  # s10: alive, its run active, its task closed
    registry.backlog(e.p).set_status(closed, "done")
    live("in progress", responsible)  # s11: no row
    finished = live("in review", responsible)  # s12: alive, its run ended at foreman done
    runs.end(e.p, finished, ("responsible",), "done")
    rows = [row(f"s{i}") for i in (1, 2, 3, 4, 5, 6, 7, 8, 10, 12)]
    listing(e, *rows, row("s9", state="stopped"))
    found = read(e, "agents", "--json")
    assert all(conforms(x, SHAPES["agents"]) for x in found)
    assert [(x["task"], x["kind"], x["round"]) for x in found] == [
        (first, "responsible", 2),
        (second, "responsible", None),
        (third, "hunter", 1),
        (third, "review", 2),
        (chained, "chain", None),
        (plain, "parent", None),
        (fixing, "mechanic", None),
        (planning, "planning", None),
    ]
    assert [(x["session"], x["handle"]) for x in found][:2] == [("s1", "s1"), ("s2", "s2")]
    started = next(r.started for r in runs.load(e.p) if r.task == first)
    assert (found[0]["model"], found[0]["effort"], found[0]["started"]) == ("opus", "high", started)


def test_runs_lists_the_ledger(env: Env):
    e = env
    a = runs.start(e.p, "TY-1", "responsible", runs.Session("s1", "h1", "opus", "high"))
    b = runs.start(e.p, "TY-2", "hunter", runs.Session("s2", "h2", "opus", "high"))
    c = runs.start(e.p, "TY-1", "review", runs.Session("s3", "h3", "opus", "high"))
    runs.end(e.p, "TY-1", ("responsible",), "failed", "why")
    every = read(e, "runs", "--json")
    assert all(conforms(r, SHAPES["runs"]) for r in every)
    assert [r["id"] for r in every] == [a.id, b.id, c.id]
    assert (every[0]["outcome"], every[0]["failure_reason"]) == ("failed", "why") and every[0]["ended"]
    assert [r["id"] for r in read(e, "runs", "--json", "--task", "TY-1")] == [a.id, c.id]
    assert [r["id"] for r in read(e, "runs", "--json", "--active")] == [b.id, c.id]
    assert [r["id"] for r in read(e, "runs", "--json", "--task", "TY-1", "--active")] == [c.id]


def test_health_gives_what_health_prints(env: Env):
    e = env
    f = e.home / "foreman.toml"
    f.write_text(f.read_text() + '[[recurring]]\nname = "cleanup"\ntitle = "Prune"\ncriteria = "pruned"\nevery_merges = 2\n')
    e.p = Project.load(e.home)
    h = read(e, "health", "--json")
    assert conforms(h, SHAPES["health"])
    assert h["loop"] == {"time": None, "alive": False} and conforms(h["loop"], SHAPES["health.loop"])
    assert conforms(h["versions"], SHAPES["health.versions"]) and h["versions"]["loop"] is None
    assert all(conforms(x, SHAPES["health.findings"]) for x in h["findings"])
    assert "loop-dead" in {x["key"] for x in h["findings"]}
    assert all(isinstance(w, str) for w in h["warnings"])
    (r,) = h["recurring"]
    assert conforms(r, SHAPES["health.recurring"]) and (r["name"], r["left"], r["chain"]) == ("cleanup", 2, None)
    b = registry.backlog(e.p)
    parent_id = b.add("Prune", criteria="c", source=recurring.SOURCE + "cleanup", chain={"lens": "l", "max_steps": 3, "max_lines": 100})
    step = b.add("step", criteria="c", parent=parent_id)
    (r,) = read(e, "health", "--json")["recurring"]
    assert conforms(r, SHAPES["health.recurring"]) and r["chain"] == {"lens": "l", "step": 1, "cap": 3, "open": step}


def test_show_keeps_its_json(env: Env):
    tid = registry.backlog(env.p).add("a task", criteria="c")
    assert conforms(read(env, "show", tid), SHAPES["show"])
