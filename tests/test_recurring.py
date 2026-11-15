"""Recurring entries: `[[recurring]]` in fm.toml, due every N merges or every interval, filed once, marked, listed."""

import json
import unittest
from datetime import datetime
from unittest import mock

from helpers import Env, fm
from test_subtasks import owner

from fm import backlog, loop, panel_views, schedule, util
from fm.config import Project, ProjectError

T0 = datetime(2026, 11, 15, 0, 0, 0)
SEEN = "2026-11-15 00:00:00"
CLEANUP = '[[recurring]]\nname = "cleanup"\ntitle = "Prune"\ncriteria = "pruned"\nevery_merges = 2\n'


class Recurring(unittest.TestCase):
    def setUp(self) -> None:
        owner(self)
        self.e = Env()
        self.p = self.e.p

    def entries(self, text: str) -> Project:
        """fm.toml gets these tables; the project reloads."""
        f = self.e.home / "fm.toml"
        base = f.read_text().split("[[recurring]]")[0]
        f.write_text(base + text)
        self.p = Project.load(self.e.home)
        return self.p

    def seen(self, stamp: str = SEEN, name: str = "cleanup") -> None:
        util.write_json(self.p.state / "recurring.json", {name: stamp})

    def merged(self, title: str, at: str, **kw: str) -> str:
        tid = backlog.add(self.p, title, criteria="c", **kw)
        self.close(tid, "done", at)
        return tid

    def close(self, tid: str, status: str, at: str) -> None:
        with mock.patch("fm.util.now", return_value=at):
            backlog.set_status(self.p, tid, status)

    def mine(self) -> list[dict]:
        return [i for i in backlog.tasks(backlog.load(self.p)) if i.get("source") == "recurring:cleanup"]

    def refused(self, text: str, *needles: str) -> None:
        with self.assertRaises(ProjectError) as cm:
            self.entries(text)
        for n in needles:
            self.assertIn(n, str(cm.exception))

    # ---- the table

    def test_all_fields_load(self) -> None:
        p = self.entries(
            '[[recurring]]\nname = "c"\ntitle = "T"\nevery = "2w"\ngoal = "g"\ncriteria = "k"\ndetail = "d"\n'
            'model = "opus"\neffort = "max"\ntype = "bug"\ntriage = false\n'
            '[[recurring]]\nname = "d"\ntitle = "U"\nevery_merges = 3\n'
        )
        c, d = p.cfg["recurring"]["c"], p.cfg["recurring"]["d"]
        keys = ("every", "goal", "criteria", "detail", "model", "effort", "type", "triage")
        self.assertEqual([c[k] for k in keys], ["2w", "g", "k", "d", "opus", "max", "bug", False])
        self.assertEqual((d["every_merges"], d["every"], d["triage"], d["goal"]), (3, None, True, ""))

    def test_refused_at_load(self) -> None:
        head = '[[recurring]]\nname = "cleanup"\ntitle = "t"\n'
        self.refused(head + "every_merges = 1\nbogus = 1\n", '"cleanup"', "bogus")
        self.refused(head + 'every_merges = 1\nevery = "1d"\n', '"cleanup"', "every_merges", "every")
        self.refused(head, '"cleanup"', "every_merges or every")
        for bad in ("7", "1m", "0d", "d", "-1h", ""):
            with self.subTest(every=bad):
                self.refused(head + f'every = "{bad}"\n', '"cleanup"', "every:")
        for bad in ("0", '"10"', "true", "1.5"):
            with self.subTest(every_merges=bad):
                self.refused(head + f"every_merges = {bad}\n", '"cleanup"', "every_merges")
        self.refused(head + "every_merges = 1\ntriage = 1\n", '"cleanup"', "triage")
        self.refused(head + "every_merges = 1\ngoal = 1\n", '"cleanup"', "goal")
        self.refused('[[recurring]]\ntitle = "t"\nevery_merges = 1\n', "#1", "name")
        self.refused(head + "every_merges = 1\n" + head + "every_merges = 1\n", '"cleanup"', "twice")

    # ---- due

    def test_every_merges_files_one_in_triage(self) -> None:
        self.entries(
            '[[recurring]]\nname = "cleanup"\ntitle = "Prune"\ngoal = "g"\ncriteria = "pruned"\ndetail = "Look at modules."\n'
            'model = "opus"\neffort = "max"\ntype = "bug"\nevery_merges = 2\n'
        )
        self.seen()
        a = self.merged("one", "2026-11-15 01:00:00")
        self.assertEqual(schedule.run(self.p, T0), [])
        self.assertEqual(schedule.rows(self.p, T0)[0]["left"], 1)
        b = self.merged("two", "2026-11-15 02:00:00")
        self.assertEqual(len(schedule.run(self.p, T0)), 1)
        (t,) = self.mine()
        self.assertEqual((t["status"], t["title"], t["model"], t["effort"], t["type"]), ("triage", "Prune", "opus", "max", "bug"))
        self.assertEqual(t["acceptance"], {"goal": "g", "criteria": "pruned"})
        self.assertTrue(t["detail"].startswith("Look at modules."))
        self.assertEqual(t["detail"].splitlines()[-2:], [f"- {a}: one", f"- {b}: two"])

    def test_triage_false(self) -> None:
        self.entries(CLEANUP.replace("every_merges = 2", "every_merges = 1\ntriage = false"))
        self.seen()
        self.merged("one", "2026-11-15 01:00:00")
        schedule.run(self.p, T0)
        self.assertEqual(self.mine()[0]["status"], "backlog")
        self.entries('[[recurring]]\nname = "cleanup"\ntitle = "Prune"\nevery_merges = 1\ntriage = false\n')
        self.close(self.mine()[0]["id"], "canceled", "2026-11-15 03:00:00")
        self.merged("two", "2026-11-15 04:00:00")
        schedule.run(self.p, T0)
        self.assertEqual(self.mine()[1]["status"], "triage")  # no criteria: it cannot be let in

    def test_time_trigger(self) -> None:
        self.entries('[[recurring]]\nname = "cleanup"\ntitle = "Prune"\nevery = "12h"\n')
        self.seen()
        self.assertEqual(schedule.run(self.p, datetime(2026, 11, 15, 11, 59)), [])
        self.assertEqual(len(schedule.run(self.p, datetime(2026, 11, 15, 12, 0))), 1)
        self.assertIn(f"from {SEEN} to 2026-11-15 12:00:00", self.mine()[0]["detail"])

    def test_downtime_files_once(self) -> None:
        self.entries('[[recurring]]\nname = "cleanup"\ntitle = "Prune"\nevery = "1d"\n')
        self.seen("2026-01-01 00:00:00")
        now = datetime(2026, 11, 15, 9, 0)
        schedule.run(self.p, now)
        schedule.run(self.p, now)
        self.assertEqual(len(self.mine()), 1)

    # ---- open blocks, the close restarts

    def test_open_task_blocks_and_its_close_restarts(self) -> None:
        for closing in ("done", "canceled"):
            with self.subTest(closing=closing):
                self.setUp()
                self.entries('[[recurring]]\nname = "cleanup"\ntitle = "Prune"\nevery_merges = 1\n')
                self.seen()
                self.merged("one", "2026-11-15 01:00:00")
                (first,) = schedule.run(self.p, T0)
                self.merged("three", "2026-11-15 02:00:00")
                self.assertEqual(schedule.run(self.p, T0), [])
                self.close(first, closing, "2026-11-15 03:00:00")
                self.assertEqual(schedule.run(self.p, T0), [])  # "three" predates the close
                four = self.merged("four", "2026-11-15 04:00:00")
                self.assertEqual(len(schedule.run(self.p, T0)), 1)
                detail = self.mine()[-1]["detail"]
                self.assertIn(four, detail)
                self.assertNotIn("three", detail)

    def test_merges_ignore_entry_tasks_and_parents(self) -> None:
        self.entries(CLEANUP)
        self.seen()
        mine = self.merged("filed by an entry", "2026-11-15 00:30:00", source="recurring:cleanup")
        self.merged("one", "2026-11-15 01:00:00")
        parent = backlog.add(self.p, "parent", criteria="c")
        backlog.add(self.p, "sub", criteria="c", parent=parent)
        self.close(parent, "done", "2026-11-15 03:00:00")
        self.assertEqual(schedule.run(self.p, T0), [])
        two = self.merged("two", "2026-11-15 04:00:00")
        self.assertEqual(len(schedule.run(self.p, T0)), 1)
        detail = self.mine()[-1]["detail"]
        self.assertIn(two, detail)
        self.assertNotIn(mine, detail)
        self.assertNotIn("parent", detail)

    def test_first_run_starts_counting_there(self) -> None:
        self.entries(CLEANUP)
        for n in range(5):
            self.merged(f"old {n}", "2026-11-14 01:00:00")
        self.assertEqual(schedule.run(self.p, T0), [])
        self.assertEqual(util.read_json(self.p.state / "recurring.json", {}), {"cleanup": SEEN})

    # ---- seen from outside

    def test_list_show_and_panel_mark_it(self) -> None:
        self.entries(CLEANUP.replace("every_merges = 2", "every_merges = 1"))
        self.seen()
        self.merged("one", "2026-11-15 01:00:00")
        (tid,) = schedule.run(self.p, T0)
        plain = backlog.add(self.p, "plain", criteria="c")

        def check() -> None:
            line = next(ln for ln in fm(self.e.home, "list").splitlines() if ln.startswith(tid))
            self.assertIn("[recurring: cleanup]", line)
            self.assertEqual(json.loads(fm(self.e.home, "show", tid))["recurring"], "cleanup")
            self.assertNotIn("recurring", json.loads(fm(self.e.home, "show", plain)))
            tasks = {i["id"]: i for i in panel_views.state(self.p)["tasks"]}
            self.assertEqual(tasks[tid]["recurring"], "cleanup")
            self.assertNotIn("recurring", tasks[plain])
            self.assertEqual(panel_views.task(self.p, tid)["task"]["recurring"], "cleanup")

        check()
        self.entries("")
        check()

    def test_health_and_panel_list_entries(self) -> None:
        self.entries(CLEANUP.replace("every_merges = 2", "every_merges = 10") + '[[recurring]]\nname = "weekly"\ntitle = "W"\nevery = "7d"\n')
        util.write_json(self.p.state / "recurring.json", {"cleanup": SEEN, "weekly": "2026-11-01 00:00:00"})
        for n in range(3):
            self.merged(f"m{n}", "2026-11-15 01:00:00")
        out = fm(self.e.home, "health")
        self.assertIn("recurring cleanup: every 10 merges; 7 merges left", out)
        self.assertIn("recurring weekly: every 7d; next due 2026-11-08", out)
        rows = {r["name"]: r for r in panel_views.system(self.p)["recurring"]}
        self.assertEqual(rows["cleanup"]["left"], 7)
        self.assertTrue(rows["weekly"]["next"].startswith("2026-11-08"))
        schedule.run(self.p, datetime(2026, 11, 20))
        weekly = next(i["id"] for i in backlog.tasks(backlog.load(self.p)) if i.get("source") == "recurring:weekly")
        row = next(r for r in schedule.rows(self.p) if r["name"] == "weekly")
        self.assertEqual((row["open"], row["next"]), (weekly, None))
        self.assertIn(f"waits for {weekly} to close", fm(self.e.home, "health"))

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
        self.assertEqual(schedule.run(self.p, T0), [])
        self.assertEqual(list(util.read_json(self.p.state / "recurring.json", {})), ["other"])
        self.assertEqual([i["id"] for i in self.mine()], [before[0]["id"]])

    def test_no_recurring_sees_nothing(self) -> None:
        self.assertEqual(schedule.run(self.p, T0), [])
        self.assertFalse((self.p.state / "recurring.json").exists())
        self.assertNotIn("recurring", fm(self.e.home, "health"))
        self.assertNotIn("recurring", panel_views.system(self.p))

    def test_tick_files_a_due_entry(self) -> None:
        self.entries(CLEANUP.replace("every_merges = 2", "every_merges = 1"))
        self.seen("2000-01-01 00:00:00")
        self.merged("one", "2026-11-15 01:00:00")
        loop.tick(self.p)
        self.assertEqual(len(self.mine()), 1)


if __name__ == "__main__":
    unittest.main()
