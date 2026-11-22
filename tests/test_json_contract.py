"""The `--json` reads (`projects`, `list`, `needs`, `health`, `show`) are the public contract for other programs (the
dashboard): the shapes below are frozen by hand from the task's criteria, never read from the code. Renaming, removing or
retyping a field fails; adding one does not."""

import json
import time
from typing import Any

from fakerepo import FakeRepo
from helpers import Env, fm
from test_watch import CHECK, in_review, listing, parent, row, task

from foreman import util
from foreman.core.project import registry as homes
from foreman.core.project.config import Project
from foreman.core.tasks import recurring
from foreman.providers import registry
from foreman.providers.claude.rows import ClaudeRows
from foreman.steps import watch

Shape = dict[str, Any]

TEXT_OR_NULL = (str, type(None))
INT_OR_NULL = (int, type(None))
DICT_OR_NULL = (dict, type(None))

SHAPES: dict[str, Shape] = {
    "projects": {"name": str, "prefix": str, "home": str, "repo": str, "main": str, "autonomy": str, "loop_running": bool},
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
        "chain": DICT_OR_NULL,
    },
    "needs": {"kind": str, "task": str, "since": TEXT_OR_NULL, "text": str, "key": str},
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
        "chain": DICT_OR_NULL,
    },
    "health.findings": {"key": str, "title": str, "meaning": str, "evidence": str, "hypothesis": str, "repo": str},
    "show": {"id": str, "title": str, "status": str, "blocked": str, "priority": str},
}


def conforms(obj: dict[str, Any], shape: Shape) -> bool:
    """Every frozen field is present with its type; other fields are free."""
    return all(k in obj and isinstance(obj[k], t) for k, t in shape.items())


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


def test_projects_lists_every_registered_project(env: Env):
    e = env
    homes.register(e.home)
    entry = next(x for x in read(e, "projects", "--json") if x["home"] == str(e.home))
    assert conforms(entry, SHAPES["projects"])
    assert (entry["name"], entry["prefix"], entry["repo"], entry["main"], entry["autonomy"]) == ("toy", "TY", str(e.repo), "main", "manual")
    assert entry["loop_running"] is False
    util.write_json(e.p.state / "loop.json", {"epoch": time.time(), "time": util.now()})
    entry = next(x for x in read(e, "projects", "--json") if x["home"] == str(e.home))
    assert entry["loop_running"] is True


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
    assert closed in {r["id"] for r in read(e, "list", "--json", "--all")}
    assert all(conforms(r, SHAPES["list"]) for r in rows.values())
    f = rows[first]
    assert (f["priority"], f["model"], f["effort"], f["type"], f["parent"], f["chain"]) == ("high", "opus", "high", "change", "", None)
    assert rows[step]["parent"] == chain
    assert rows[weekly]["recurring"] == "weekly"
    assert rows[chain]["chain"] == {"lens": "l", "step": 1, "cap": 3, "open": step}


def test_needs_is_watchs_set_with_its_facts(fake_git: FakeRepo):
    e = Env(repo=fake_git)
    asking = task(e)
    qid = registry.backlog(e.p).ask(asking, "agent", "which   way?")
    asked = registry.backlog(e.p).load()["decisions"][0]["asked"]
    stuck = task(e, blocked="why")
    review = in_review(e, check={**CHECK, "at": "2026-11-22 10:00:00"}, report={"summary": "line one\nmore"})
    util.write_json(e.p.state / "ci.json", {"result": "fail", "since": "2026-11-22 09:00:00"})
    idle = parent(e)
    listing(e, row())
    found = read(e, "needs", "--json")
    assert all(conforms(n, SHAPES["needs"]) for n in found)
    by_kind = {n["kind"]: n for n in found}
    assert [n["kind"] for n in found] == ["question", "accept", "blocked", "main", "parent"]
    assert by_kind["question"] == {"kind": "question", "task": asking, "since": asked, "text": "which way?", "key": f"q:{qid}"}
    assert (by_kind["blocked"]["task"], by_kind["blocked"]["text"], by_kind["blocked"]["since"]) == (stuck, "why", None)
    assert (by_kind["accept"]["task"], by_kind["accept"]["text"], by_kind["accept"]["since"]) == (review, "line one", "2026-11-22 10:00:00")
    assert (by_kind["main"]["task"], by_kind["main"]["since"]) == ("", "2026-11-22 09:00:00")
    assert (by_kind["parent"]["task"], by_kind["parent"]["since"]) == (idle, None)
    assert {n["key"] for n in found} == set(watch.needs(e.p, ClaudeRows({"s1": row()})))  # the same set as `watch --once`


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


def test_show_keeps_its_json(env: Env):
    tid = registry.backlog(env.p).add("a task", criteria="c")
    assert conforms(read(env, "show", tid), SHAPES["show"])
