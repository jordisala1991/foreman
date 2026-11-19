"""Recurring entries: `[[recurring]]` in foreman.toml, due every N merges or every interval, filed once, marked, listed."""

import json
from datetime import datetime
from typing import Unpack
from unittest import mock

import pytest
from helpers import fm

from foreman import util
from foreman.core.project.config import Project, ProjectError
from foreman.core.tasks import backlog
from foreman.loop import tick
from foreman.steps import schedule, sources
from foreman.ui.panel import views

T0 = datetime(2026, 11, 15, 0, 0, 0)
SEEN = "2026-11-15 00:00:00"
CLEANUP = '[[recurring]]\nname = "cleanup"\ntitle = "Prune"\ncriteria = "pruned"\nevery_merges = 2\n'


class TestRecurring:
    @pytest.fixture(autouse=True)
    def set_up(self, owner, env) -> None:
        self.e = env
        self.p = self.e.p

    def entries(self, text: str) -> Project:
        """foreman.toml gets these tables; the project reloads."""
        f = self.e.home / "foreman.toml"
        base = f.read_text().split("[[recurring]]")[0]
        f.write_text(base + text)
        self.p = Project.load(self.e.home)
        return self.p

    def seen(self, stamp: str = SEEN, name: str = "cleanup") -> None:
        util.write_json(self.p.state / "recurring.json", {name: stamp})

    def merged(self, title: str, at: str, **kw: Unpack[backlog.NewTask]) -> str:
        kw.setdefault("criteria", "c")
        tid = backlog.add(self.p, title, **kw)
        self.close(tid, "done", at)
        return tid

    def close(self, tid: str, status: str, at: str) -> None:
        with mock.patch("foreman.util.now", return_value=at):
            backlog.set_status(self.p, tid, status)

    def mine(self) -> list[dict]:
        return [i for i in backlog.tasks(backlog.load(self.p)) if i.get("source") == "recurring:cleanup"]

    def refused(self, text: str, *needles: str) -> None:
        with pytest.raises(ProjectError) as cm:
            self.entries(text)
        for n in needles:
            assert n in str(cm.value)

    # ---- the table

    def test_all_fields_load(self) -> None:
        p = self.entries(
            '[[recurring]]\nname = "c"\ntitle = "T"\nevery = "2w"\ngoal = "g"\ncriteria = "k"\ndetail = "d"\n'
            'model = "opus"\neffort = "max"\ntype = "bug"\ntriage = false\n'
            '[[recurring]]\nname = "d"\ntitle = "U"\nevery_merges = 3\n'
        )
        c, d = p.cfg["recurring"]["c"], p.cfg["recurring"]["d"]
        keys = ("every", "goal", "criteria", "detail", "model", "effort", "type", "triage")
        assert [c[k] for k in keys] == ["2w", "g", "k", "d", "opus", "max", "bug", False]
        assert (d["every_merges"], d["every"], d["triage"], d["goal"]) == (3, None, True, "")

    def test_refused_at_load(self) -> None:
        head = '[[recurring]]\nname = "cleanup"\ntitle = "t"\n'
        self.refused(head + "every_merges = 1\nbogus = 1\n", '"cleanup"', "bogus")
        self.refused(head + 'every_merges = 1\nevery = "1d"\n', '"cleanup"', "every_merges", "every")
        self.refused(head, '"cleanup"', "every_merges or every")
        for bad in ("7", "1m", "0d", "d", "-1h", "", "600000w", "999999600000w"):
            self.refused(head + f'every = "{bad}"\n', '"cleanup"', "every:")
        for bad in ("0", '"10"', "true", "1.5"):
            self.refused(head + f"every_merges = {bad}\n", '"cleanup"', "every_merges")
        self.refused(head + "every_merges = 1\ntriage = 1\n", '"cleanup"', "triage")
        self.refused(head + "every_merges = 1\ngoal = 1\n", '"cleanup"', "goal")
        self.refused('[[recurring]]\ntitle = "t"\nevery_merges = 1\n', "#1", "name")
        self.refused(head + "every_merges = 1\n" + head + "every_merges = 1\n", '"cleanup"', "twice")

    def test_refused_at_load_for_lens_entries(self) -> None:
        head = '[[recurring]]\nname = "cleanup"\ntitle = "t"\nevery_merges = 1\n'
        lens = {"lens": '"rules"', "max_steps": "5", "max_lines": "150", "criteria": '"c"'}
        for missing in lens:
            self.refused(head + "".join(f"{k} = {v}\n" for k, v in lens.items() if k != missing), '"cleanup"', missing)
        full = head + "".join(f"{k} = {v}\n" for k, v in lens.items())
        self.refused(full.replace("max_steps = 5", "max_steps = 0"), '"cleanup"', "max_steps")
        self.refused(full.replace("max_lines = 150", 'max_lines = "1"'), '"cleanup"', "max_lines")
        self.refused(full + "triage = true\n", '"cleanup"', "triage")
        assert self.entries(full).cfg["recurring"]["cleanup"]["triage"] is False

    # ---- due

    def test_every_merges_files_one_in_triage(self) -> None:
        self.entries(
            '[[recurring]]\nname = "cleanup"\ntitle = "Prune"\ngoal = "g"\ncriteria = "pruned"\ndetail = "Look at modules."\n'
            'model = "opus"\neffort = "max"\ntype = "bug"\nevery_merges = 2\n'
        )
        self.seen()
        a = self.merged("one", "2026-11-15 01:00:00")
        assert schedule.run(self.p, T0) == []
        assert schedule.rows(self.p, T0)[0]["left"] == 1
        b = self.merged("two", "2026-11-15 02:00:00")
        assert len(schedule.run(self.p, T0)) == 1
        (t,) = self.mine()
        assert (t["status"], t["title"], t["model"], t["effort"], t["type"]) == ("triage", "Prune", "opus", "max", "bug")
        assert t["acceptance"] == {"goal": "g", "criteria": "pruned"}
        assert t["detail"].startswith("Look at modules.")
        assert t["detail"].splitlines()[-2:] == [f"- {a}: one", f"- {b}: two"]

    def test_triage_false(self) -> None:
        self.entries(CLEANUP.replace("every_merges = 2", "every_merges = 1\ntriage = false"))
        self.seen()
        self.merged("one", "2026-11-15 01:00:00")
        schedule.run(self.p, T0)
        assert self.mine()[0]["status"] == "backlog"
        self.entries('[[recurring]]\nname = "cleanup"\ntitle = "Prune"\nevery_merges = 1\ntriage = false\n')
        self.close(self.mine()[0]["id"], "canceled", "2026-11-15 03:00:00")
        self.merged("two", "2026-11-15 04:00:00")
        schedule.run(self.p, T0)
        assert self.mine()[1]["status"] == "triage"  # no criteria: it cannot be let in

    def test_time_trigger(self) -> None:
        self.entries('[[recurring]]\nname = "cleanup"\ntitle = "Prune"\nevery = "12h"\n')
        self.seen()
        assert schedule.run(self.p, datetime(2026, 11, 15, 11, 59)) == []
        assert len(schedule.run(self.p, datetime(2026, 11, 15, 12, 0))) == 1
        assert f"from {SEEN} to 2026-11-15 12:00:00" in self.mine()[0]["detail"]

    def test_downtime_files_once(self) -> None:
        self.entries('[[recurring]]\nname = "cleanup"\ntitle = "Prune"\nevery = "1d"\n')
        self.seen("2026-01-01 00:00:00")
        now = datetime(2026, 11, 15, 9, 0)
        schedule.run(self.p, now)
        schedule.run(self.p, now)
        assert len(self.mine()) == 1

    # ---- open blocks, the close restarts

    @pytest.mark.parametrize("closing", ("done", "canceled"))
    def test_open_task_blocks_and_its_close_restarts(self, closing) -> None:
        self.entries('[[recurring]]\nname = "cleanup"\ntitle = "Prune"\nevery_merges = 1\n')
        self.seen()
        self.merged("one", "2026-11-15 01:00:00")
        (first,) = schedule.run(self.p, T0)
        self.merged("three", "2026-11-15 02:00:00")
        assert schedule.run(self.p, T0) == []
        self.close(first, closing, "2026-11-15 03:00:00")
        assert schedule.run(self.p, T0) == []  # "three" predates the close
        four = self.merged("four", "2026-11-15 04:00:00")
        assert len(schedule.run(self.p, T0)) == 1
        detail = self.mine()[-1]["detail"]
        assert four in detail
        assert "three" not in detail

    def test_merges_ignore_entry_tasks_and_parents(self) -> None:
        self.entries(CLEANUP)
        self.seen()
        mine = self.merged("filed by an entry", "2026-11-15 00:30:00", source="recurring:cleanup")
        self.merged("one", "2026-11-15 01:00:00")
        parent = backlog.add(self.p, "parent", criteria="c")
        backlog.add(self.p, "sub", criteria="c", parent=parent)
        self.close(parent, "done", "2026-11-15 03:00:00")
        assert schedule.run(self.p, T0) == []
        two = self.merged("two", "2026-11-15 04:00:00")
        assert len(schedule.run(self.p, T0)) == 1
        detail = self.mine()[-1]["detail"]
        assert two in detail
        assert mine not in detail
        assert "parent" not in detail

    def test_steps_of_a_recurring_parent_do_not_count(self) -> None:
        self.entries(CLEANUP)
        self.seen()
        parent = backlog.add(self.p, "chain", criteria="c", source="recurring:other")
        self.merged("step", "2026-11-15 01:00:00", parent=parent)
        self.merged("one", "2026-11-15 02:00:00")
        assert schedule.run(self.p, T0) == []
        assert schedule.rows(self.p, T0)[0]["left"] == 1
        self.merged("two", "2026-11-15 03:00:00")
        assert len(schedule.run(self.p, T0)) == 1

    def test_first_run_starts_counting_there(self) -> None:
        self.entries(CLEANUP)
        for n in range(5):
            self.merged(f"old {n}", "2026-11-14 01:00:00")
        assert schedule.run(self.p, T0) == []
        assert util.read_json(self.p.state / "recurring.json", {}) == {"cleanup": SEEN}

    # ---- seen from outside

    def test_list_show_and_panel_mark_it(self) -> None:
        self.entries(CLEANUP.replace("every_merges = 2", "every_merges = 1"))
        self.seen()
        self.merged("one", "2026-11-15 01:00:00")
        (tid,) = schedule.run(self.p, T0)
        plain = backlog.add(self.p, "plain", criteria="c")

        def check() -> None:
            line = next(ln for ln in fm(self.e.home, "list").splitlines() if ln.startswith(tid))
            assert "[recurring: cleanup]" in line
            assert json.loads(fm(self.e.home, "show", tid))["recurring"] == "cleanup"
            assert "recurring" not in json.loads(fm(self.e.home, "show", plain))
            tasks = {i["id"]: i for i in views.state(self.p)["tasks"]}
            assert tasks[tid]["recurring"] == "cleanup"
            assert "recurring" not in tasks[plain]
            assert views.task(self.p, tid)["task"]["recurring"] == "cleanup"

        check()
        self.entries("")
        check()

    def test_health_and_panel_list_entries(self) -> None:
        self.entries(CLEANUP.replace("every_merges = 2", "every_merges = 10") + '[[recurring]]\nname = "weekly"\ntitle = "W"\nevery = "7d"\n')
        util.write_json(self.p.state / "recurring.json", {"cleanup": SEEN, "weekly": "2026-11-01 00:00:00"})
        for n in range(3):
            self.merged(f"m{n}", "2026-11-15 01:00:00")
        out = fm(self.e.home, "health")
        assert "recurring cleanup: every 10 merges; 7 merges left" in out
        assert "recurring weekly: every 7d; next due 2026-11-08" in out
        rows = {r["name"]: r for r in views.system(self.p)["recurring"]}
        assert rows["cleanup"]["left"] == 7
        assert rows["weekly"]["next"].startswith("2026-11-08")
        schedule.run(self.p, datetime(2026, 11, 20))
        weekly = next(i["id"] for i in backlog.tasks(backlog.load(self.p)) if i.get("source") == "recurring:weekly")
        row = next(r for r in schedule.rows(self.p) if r["name"] == "weekly")
        assert (row["open"], row["next"]) == (weekly, None)
        assert f"waits for {weekly} to close" in fm(self.e.home, "health")

    def test_removed_entry_stops(self) -> None:
        self.entries(CLEANUP.replace("every_merges = 2", "every_merges = 1"))
        self.seen()
        self.merged("one", "2026-11-15 01:00:00")
        schedule.run(self.p, T0)
        before = self.mine()
        self.entries('[[recurring]]\nname = "other"\ntitle = "O"\nevery = "7d"\n')  # keeps the state file in play
        self.merged("two", "2026-11-15 04:00:00")
        self.close(before[0]["id"], "done", "2026-11-15 05:00:00")
        self.merged("three", "2026-11-15 06:00:00")
        assert schedule.run(self.p, T0) == []
        assert list(util.read_json(self.p.state / "recurring.json", {})) == ["other"]
        assert [i["id"] for i in self.mine()] == [before[0]["id"]]

    def test_last_entry_removed_then_re_added_starts_afresh(self) -> None:
        daily = '[[recurring]]\nname = "cleanup"\ntitle = "Prune"\nevery = "1d"\n'
        self.entries(daily)
        self.seen("2026-01-01 00:00:00")
        self.entries("")
        assert schedule.run(self.p, datetime(2026, 11, 15)) == []
        assert not (self.p.state / "recurring.json").exists()  # the project is back to seeing nothing
        self.entries(daily)
        assert schedule.run(self.p, datetime(2026, 11, 15)) == []
        assert util.read_json(self.p.state / "recurring.json", {}) == {"cleanup": SEEN}

    def test_a_failing_recurring_job_does_not_stop_the_plug_in_sources(self) -> None:
        src = self.e.home / "sources" / "src"
        src.parent.mkdir()
        src.write_text('#!/bin/sh\necho \'{"key": "k", "title": "from a source"}\'\n')
        src.chmod(0o755)
        with mock.patch("foreman.steps.schedule.run", side_effect=OverflowError("boom")):
            sources.run(self.p)
        assert [i["title"] for i in backlog.tasks(backlog.load(self.p))] == ["from a source"]
        assert [e["step"] for e in util.read_jsonl(self.p.state / "errors.jsonl")] == ["recurring"]

    def test_no_recurring_sees_nothing(self) -> None:
        assert schedule.run(self.p, T0) == []
        assert not (self.p.state / "recurring.json").exists()
        assert "recurring" not in fm(self.e.home, "health")
        assert "recurring" not in views.system(self.p)

    def test_tick_files_a_due_entry(self) -> None:
        self.entries(CLEANUP.replace("every_merges = 2", "every_merges = 1"))
        self.seen("2000-01-01 00:00:00")
        self.merged("one", "2026-11-15 01:00:00")
        tick.tick(self.p)
        assert len(self.mine()) == 1
