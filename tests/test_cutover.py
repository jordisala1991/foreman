"""`foreman backlog cutover` (core/tasks/cutover.py, providers/github/cutover.py) against a fake gh: a dry run writes nothing,
the cutover files every open task with its legacy id in DESIGN's order and resumes after a filing cut short, the closed tasks
and an open task's closed blockers stay in the old file, `foreman list` is the same after it and after the rollback, and the
refusals come before any write."""

import json
import tomllib
from pathlib import Path
from typing import Any

import pytest
from fakegh import FakeGh, board_env
from helpers import Env, fm

from foreman import proc, util
from foreman.core.project.backlog_table import Board, parse, table
from foreman.core.project.config import Project
from foreman.core.tasks import blockers, board, cutover, subtasks
from foreman.core.tasks.backlog import empty, get
from foreman.core.tasks.issues import with_old_blockers
from foreman.providers import registry
from foreman.providers.github import writes
from foreman.providers.github.api import Api
from foreman.providers.github.issues import read_open
from foreman.providers.github.types import RateLimited

CUT = ("backlog", "cutover", "--repo", "o/r", "--project", "1")
QUESTION = {"id": "TY-3 Q1", "task": "TY-3", "agent": "a", "title": "t", "text": "q?", "asked": "2026-11-20 10:00:00", "kind": "question"}


def task(n: int, status: str, **keys: Any) -> dict[str, Any]:
    """A task with the keys `changes.filed` makes, as the old file holds it."""
    return {"id": f"TY-{n}", "title": f"task {n}", "status": status, "detail": "", "priority": "none", "blocked": "", "created": "2026-11-01 10:00:00", **keys}


TASKS = [
    task(1, "done", closed="2026-11-02"),
    task(2, "done", parent="TY-4", closed="2026-11-03"),
    task(3, "triage", priority="high", model="opus"),
    task(
        4,
        "backlog",
        acceptance={"goal": "the goal", "criteria": "one\ntwo"},
        detail="the detail\ndecision.x=y",
        blocked_by=["TY-1", "TY-3"],
        agent={"name": "fm-TY-4", "session": "s4"},
        worktree="/w/ty-4",
        plan=[],
    ),
    task(5, "ready", parent="TY-4", blocked="waits"),
]


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeGh:
    f = FakeGh()
    f.prs = {271}  # DESIGN.md «Ids»: the first issue is #272
    monkeypatch.setattr(proc, "run", f)
    return f


@pytest.fixture
def e(fake: FakeGh) -> Env:
    """A paused project on its old file, with no [backlog]."""
    env = Env()
    util.write_json(env.home / "backlog.json", {**empty(), "tasks": TASKS, "decisions": [QUESTION], "decisions_resolved": [{**QUESTION, "id": "TY-1 Q1"}]})
    env.p.set_paused(True)
    return env


def legacy(e: Env) -> Any:
    return util.read_json(e.home / "legacy-ids.json", None)


def bytes_of(e: Env) -> tuple[bytes, bytes]:
    return (e.home / "foreman.toml").read_bytes(), (e.home / "backlog.json").read_bytes()


def ops(fake: FakeGh) -> list[str]:
    seen: list[str] = []
    fake.hook = seen.append
    return seen


def test_dry_run_prints_each_open_task_and_writes_nothing(fake: FakeGh, e: Env):
    before = bytes_of(e)
    out = fm(e.home, *CUT, "--dry-run")
    lines = out.splitlines()
    assert lines[0] == "dry run: nothing is written; 3 open tasks would be filed as issues numbered after #271, in this order:"
    assert lines[1:4] == [
        "TY-3 → #272  «task 3»  Status=Triage, Priority=high, Model=opus",
        "TY-4 → #273  «task 4»  Status=Backlog  blockers=TY-3  blockers in the old file=TY-1",
        "TY-5 → #274  «task 5»  Status=Ready, Blocked=waits  parent=TY-4",
    ]
    assert "task 1" not in out and "task 2" not in out
    assert fake.mutations == 0 and fake.issues == {}
    assert not (e.home / "legacy-ids.json").exists() and not (e.p.state / "runs.json").exists()
    assert bytes_of(e) == before


def test_cutover_files_every_open_task(fake: FakeGh, e: Env):
    old = (e.home / "backlog.json").read_bytes()
    out = fm(e.home, *CUT)
    assert "TY-3 → #272\nTY-4 → #273\nTY-5 → #274\nforeman.toml: [backlog] added; to go back, delete that table\n" in out
    assert {n: s.body.split("\n")[0] for n, s in fake.issues.items()} == {
        272: "<!-- foreman:legacy-id=TY-3 -->",
        273: "<!-- foreman:legacy-id=TY-4 -->",
        274: "<!-- foreman:legacy-id=TY-5 -->",
    }
    assert legacy(e) == {"TY-3": 272, "TY-4": 273, "TY-5": 274}
    assert {s.title for s in fake.issues.values()} == {"task 3", "task 4", "task 5"}
    assert (e.home / "backlog.json").read_bytes() == old
    runs = util.read_json(e.p.state / "runs.json", {})
    assert runs == {"TY-4": {"agent": {"name": "fm-TY-4", "session": "s4"}, "worktree": "/w/ty-4"}}
    assert fake.issues[272].labels == [board.LABEL] and fake.issues[273].labels == []
    assert util.read_json(e.p.state / "questions.json", {})["decisions"] == [QUESTION]
    assert Project.load(e.home).board == Board("o/r", "o", 1)


def test_refuses_when_the_newest_number_is_below_the_highest_id(fake: FakeGh, e: Env):
    fake.prs = {4}
    before = bytes_of(e)
    with pytest.raises(SystemExit, match=r"foreman: the repo's newest issue or PR is #4, below the old file's highest id TY-5"):
        fm(e.home, *CUT)
    assert fake.mutations == 0 and bytes_of(e) == before
    fake.prs = {5}
    assert "TY-3 → #6" in fm(e.home, *CUT)


def test_closed_subtasks_and_blockers_come_from_the_old_file(fake: FakeGh, e: Env):
    fm(e.home, *CUT)
    b = registry.backlog(Project.load(e.home))
    d = b.load()
    assert {t["id"] for t in subtasks.of(d["tasks"], "TY-4")} == {"TY-5", "TY-2"}
    assert get(d, "TY-4")["blocked_by"] == ["TY-3", "TY-1"] == b.find("TY-4")["blocked_by"]
    assert blockers.pending(get(d, "TY-4"), d["tasks"]) == ["TY-3"]
    assert fake.issues[273].blockers == [272]  # the issue's dependencies stay the open ones


def test_with_old_blockers():
    old = {t["id"]: t for t in TASKS}
    on_github = {"id": "TY-4", "blocked_by": ["TY-3"]}
    assert with_old_blockers(on_github, old) == {"id": "TY-4", "blocked_by": ["TY-3", "TY-1"]}
    assert with_old_blockers({"id": "TY-9", "blocked_by": ["TY-3"]}, old) == {"id": "TY-9", "blocked_by": ["TY-3"]}  # no old task
    assert with_old_blockers({"id": "TY-5"}, old) == {"id": "TY-5"}  # no closed blocker
    assert with_old_blockers({"id": "TY-4", "blocked_by": ["TY-1", "TY-3"]}, old)["blocked_by"] == ["TY-1", "TY-3"]  # no repeat


def test_filing_runs_in_designs_order(fake: FakeGh, e: Env):
    seen = ops(fake)
    fm(e.home, *CUT)
    filing = [op for op in seen if op in ("File", "Card", "Fields", "Issue", "Label")]
    tasks = "|".join(filing).split("|File|")
    assert len(tasks) == 3 and filing[0] == "File"
    assert all(t.removeprefix("File|").split("|")[:3] == ["Card", "Fields", "Issue"] for t in tasks)
    assert seen.index("Setup") < seen.index("File")


def test_a_cut_short_filing_is_skipped_and_named(fake: FakeGh, e: Env):
    files: list[str] = []

    def second_file(op: str) -> None:  # the 2nd createIssue lands, then the limit: its card is never added
        files.extend([op] if op == "File" else [])
        fake.limited = op == "File" and len(files) == 2

    fake.hook = second_file
    with pytest.raises(SystemExit, match="rate limit"):
        fm(e.home, *CUT)
    assert fake.issues[273].carded is False
    errors = [x["error"] for x in util.read_jsonl(e.p.state / "errors.jsonl") if not x["step"].startswith("gh ")]
    assert len(errors) == 1 and errors[0].startswith("#273 was filed but not finished: close it by hand")
    fake.limited = False
    (e.p.state / "github-rate-limit.json").unlink()
    assert [i.number for i in read_open(Api(e.p.state / "x.json"), Board("o/r", "o", 1)).issues] == [272]
    assert legacy(e) == {"TY-3": 272}
    assert Project.load(e.home).board is None
    out = fm(e.home, *CUT)
    assert "TY-3 →" not in out and "TY-4 → #274\nTY-5 → #275\n" in out
    assert legacy(e) == {"TY-3": 272, "TY-4": 274, "TY-5": 275}
    assert Project.load(e.home).board is not None


def show(home: Path, tid: str) -> dict[str, Any]:
    return json.loads(fm(home, "show", tid))


def test_list_is_the_same_after_the_cutover(fake: FakeGh, e: Env):
    before = fm(e.home, "list")
    assert "? TY-3 Q1 (a): q?" in before and "waits for TY-3" in before
    fm(e.home, *CUT)
    assert fm(e.home, "list") == before
    for t in TASKS[2:]:
        shown = show(e.home, t["id"])
        for k, v in t.items():
            if k not in ("created", "plan", "subs"):
                assert (sorted(shown[k]) if k == "blocked_by" else shown[k]) == (sorted(v) if k == "blocked_by" else v), (t["id"], k)


def test_pointing_foreman_toml_back_restores_the_file(fake: FakeGh, e: Env):
    toml = (e.home / "foreman.toml").read_text()
    before = fm(e.home, "list")
    fm(e.home, *CUT)
    assert (e.home / "foreman.toml").read_text() == toml + table(Board("o/r", "o", 1))
    (e.home / "foreman.toml").write_text(toml)
    assert Project.load(e.home).board is None
    assert fm(e.home, "list") == before


def test_the_cutover_sets_the_board_up(fake: FakeGh, e: Env):
    fm(e.home, *CUT)
    assert fake.workflows == []
    assert tuple(o["name"] for o in fake.fields["Status"]["options"]) == board.STATUS
    for f in board.FIELDS[1:]:
        assert (fake.fields[f.name]["dataType"], tuple(o["name"] for o in fake.fields[f.name].get("options", []))) == (f.kind, f.options)
    assert fake.labels == {board.LABEL}


def test_a_migrated_task_is_written_on_github(fake: FakeGh, e: Env):
    fm(e.home, *CUT)
    b = registry.backlog(Project.load(e.home))
    b.set_status("TY-4", "in review")
    assert fake.issues[273].fields["Status"] == "In review"
    assert get(b.load(), "TY-4")["agent"] == {"name": "fm-TY-4", "session": "s4"}


def test_refuses_when_the_loop_is_not_paused(fake: FakeGh, e: Env):
    e.p.set_paused(False)
    with pytest.raises(SystemExit, match="foreman: the loop is not paused: run foreman pause first"):
        fm(e.home, *CUT)
    assert fake.mutations == 0
    assert "would be filed" in fm(e.home, *CUT, "--dry-run")


def test_refuses_when_backlog_is_already_set(fake: FakeGh):
    with pytest.raises(SystemExit, match=r"foreman: \[backlog\] is already in foreman.toml"):
        fm(board_env().home, *CUT)


def test_todo_puts_blockers_and_parents_first():
    d = {"tasks": [task(2, "backlog", blocked_by=["TY-1"]), task(1, "backlog"), task(3, "triage", parent="TY-2"), task(4, "done")]}
    assert [t["id"] for t in cutover.todo(d, {})] == ["TY-1", "TY-2", "TY-3"]
    assert [t["id"] for t in cutover.todo(d, {"TY-1": 9})] == ["TY-2", "TY-3"]
    d["tasks"][0]["blocked_by"] = ["TY-4", "TY-1"]
    assert cutover.todo(d, {})[1]["blocked_by"] == ["TY-1"]


def test_refusal_closed_parent():
    d = {"tasks": [task(1, "done"), task(2, "backlog", parent="TY-1")]}
    assert cutover.refusal(d, "TY", 2) == "TY-2: its parent TY-1 is closed and stays in the old file, so TY-2 cannot become its sub-issue"


def test_refusal_text_the_body_would_not_keep():
    d = {"tasks": [task(1, "backlog", acceptance={"goal": "a\n## b", "criteria": "c"})]}
    assert cutover.refusal(d, "TY", 1) == "TY-1: the issue body would not keep its goal, criteria or detail as they are"
    assert cutover.refusal({"tasks": TASKS}, "TY", 5) == ""


def test_table_parses_back():
    for b in (Board("o/r", "o", 3), Board("o/r", "someone", 3)):
        text = table(b)
        assert ("owner = " in text) == (b.owner != "o")
        assert parse(tomllib.loads(text)) == b


def test_blockers_change_on_a_task_with_an_old_file_blocker(fake: FakeGh, e: Env):
    fm(e.home, *CUT)
    b = registry.backlog(Project.load(e.home))
    with b.edit() as d:
        get(d, "TY-4")["blocked_by"] = ["TY-3", "TY-1", "TY-5"]
    assert fake.issues[273].blockers == [272, 274]
    with b.edit() as d:
        get(d, "TY-4")["blocked_by"] = ["TY-3", "TY-5"]  # TY-1 is no issue: nothing to remove on GitHub, and #1 is never looked up
    assert fake.issues[273].blockers == [272, 274]
    assert b.find("TY-4")["blocked_by"] == ["TY-3", "TY-5", "TY-1"]


def test_a_rerun_after_the_rollback_keeps_what_github_mode_wrote(fake: FakeGh, e: Env):
    toml = (e.home / "foreman.toml").read_text()
    fm(e.home, *CUT)
    b = registry.backlog(Project.load(e.home))
    b.update("TY-4", nudges=1)
    b.answer("TY-3 Q1", "yes")
    (e.home / "foreman.toml").write_text(toml)
    fm(e.home, *CUT)
    assert util.read_json(e.p.state / "runs.json", {})["TY-4"]["nudges"] == 1
    q = util.read_json(e.p.state / "questions.json", {})
    assert q["decisions"] == [] and [x["id"] for x in q["decisions_resolved"]] == ["TY-1 Q1", "TY-3 Q1"]
    assert fake.issues[272].labels == []


def test_a_change_to_the_old_file_during_the_filing_is_refused(fake: FakeGh, e: Env):
    toml = (e.home / "foreman.toml").read_text()
    files: list[str] = []

    def agent_files(op: str) -> None:  # an agent's foreman add lands in the old file while the cutover files
        files.extend([op] if op == "File" else [])
        if op == "File" and len(files) == 2:
            d = util.read_json(e.home / "backlog.json", {})
            util.write_json(e.home / "backlog.json", {**d, "tasks": [*d["tasks"], task(6, "triage")]})

    fake.hook = agent_files
    with pytest.raises(SystemExit, match=r"foreman: the old file changed during the filing \(TY-6\)"):
        fm(e.home, *CUT)
    assert (e.home / "foreman.toml").read_text() == toml and not (e.p.state / "runs.json").exists()
    out = fm(e.home, *CUT)
    assert "TY-6 → #275" in out and Project.load(e.home).board is not None


def test_a_cycle_is_refused_before_any_write(fake: FakeGh, e: Env):
    d = util.read_json(e.home / "backlog.json", {})
    d["tasks"][2]["blocked_by"] = ["TY-4"]  # TY-3 waits for TY-4, which waits for TY-3
    util.write_json(e.home / "backlog.json", d)
    with pytest.raises(SystemExit, match="wait for each other"):
        fm(e.home, *CUT)
    assert fake.mutations == 0 and fake.workflows != []


def test_a_label_cut_short_is_put_on_by_the_rerun(fake: FakeGh, e: Env, monkeypatch: pytest.MonkeyPatch):
    def limited(*a: object) -> None:
        raise RateLimited("spent")

    with monkeypatch.context() as m:
        m.setattr(writes, "label", limited)
        with pytest.raises(SystemExit, match="spent"):
            fm(e.home, *CUT)
    assert legacy(e) == {"TY-3": 272, "TY-4": 273, "TY-5": 274} and fake.issues[272].labels == []
    fm(e.home, *CUT)
    assert fake.issues[272].labels == [board.LABEL]
