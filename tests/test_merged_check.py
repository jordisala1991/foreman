"""The merged check (FN-113): an accepted branch merges only if the test command passes on it merged with main as main
is at that moment. One test per row of the task's table. A and B each pass alone and fail together (the FN-99/101
shape): A adds a.py, B adds b.py, and the test command fails when both are there."""

import json
import tempfile
import unittest
from pathlib import Path

from helpers import Env, fm, git
from test_failing import fix
from test_review_rules import commit, events, take_note

from fm import backlog, delivery, loop, mergecheck

TOGETHER_FAIL = "test ! -e a.py -o ! -e b.py"


class Merged(unittest.TestCase):
    def env(self, cmd: str = TOGETHER_FAIL, autonomy: str = "manual") -> Env:
        """An Env whose test command also writes, outside the repo, the name of the worktree of each run."""
        self.log = Path(tempfile.mkdtemp(prefix="fmlog-")) / "runs"
        self.log.write_text("")
        return Env(test=f"basename $PWD >> {self.log}; {cmd}" if cmd else "", autonomy=autonomy, slots=4)

    def runs(self) -> int:
        """The task runs: branch alone or merged (health's runs on main are not counted)."""
        return sum(1 for ln in self.log.read_text().splitlines() if ln != "main")

    def deliver(self, e: Env, files: dict[str, str]) -> list[str]:
        """One task per file, launched together (so each branch starts from the same main), committed, delivered and
        reviewed; a last tick checks them."""
        tids = [backlog.add(e.p, f"x {name}", status="ready", criteria="y") for name in files]
        loop.tick(e.p)
        for tid, (name, text) in zip(tids, files.items(), strict=True):
            commit(e.task(tid)["worktree"], name, text)
        for tid in tids:
            backlog.set_status(e.p, tid, "in review", report={"summary": "s", "confused": ""})
        for tid in tids:
            e.review(tid)
        loop.tick(e.p)
        return tids

    def main(self, e: Env) -> str:
        return git(e.repo, "rev-parse", "main").strip()

    def test_passes_alone_and_merged_merges(self):
        e = self.env("true")
        (tid,) = self.deliver(e, {"a.py": "a\n"})
        delivery.accept(e.p, tid)
        before = self.main(e)
        loop.tick(e.p)
        i = e.task(tid)
        self.assertEqual(i["status"], "done")
        self.assertEqual(i["check"]["merged_with"], before)

    def test_fails_merged_goes_back_saying_so(self):
        e = self.env()
        a, b = self.deliver(e, {"a.py": "a\n", "b.py": "b\n"})
        self.assertEqual(self.runs(), 2)  # one per branch, alone: nothing is about to merge yet
        self.assertNotIn("merged_with", e.task(a)["check"])
        self.assertNotIn("merged_with", e.task(b)["check"])
        delivery.accept(e.p, a)
        delivery.accept(e.p, b)
        loop.tick(e.p)
        self.assertEqual(e.task(a)["status"], "done")
        i = e.task(b)
        self.assertEqual((i["status"], i["failures"], i["failure_streak"]), ("in progress", 1, 1))
        main = self.main(e)
        self.assertEqual(i["check"]["merged_with"], main)  # checked with A already in main
        take_note(e, b)
        note = e.calls()[-1]["argv"][-1]
        self.assertIn(main[:9], note)
        self.assertIn("not on its branch alone", note)
        self.assertFalse((e.repo / "b.py").exists())

    def test_autonomy_path_checks_merged_too(self):
        e = self.env(autonomy="low")
        a, b = self.deliver(e, {"a.py": "a\n", "b.py": "b\n"})
        loop.tick(e.p)
        self.assertEqual(e.task(a)["status"], "done")
        i = e.task(b)
        self.assertEqual((i["status"], i["failures"]), ("in progress", 1))
        self.assertEqual(i["check"]["merged_with"], self.main(e))
        self.assertFalse((e.repo / "b.py").exists())

    def test_main_moved_after_a_pass_checks_again(self):
        e = self.env("true")
        (tid,) = self.deliver(e, {"a.py": "a\n"})
        mergecheck.merged_check(e.p, tid)
        self.assertEqual(self.runs(), 2)
        commit(str(e.repo), "other.py", "o\n")
        delivery.accept(e.p, tid)
        moved = self.main(e)
        loop.tick(e.p)
        self.assertEqual(self.runs(), 3)
        i = e.task(tid)
        self.assertEqual((i["status"], i["check"]["merged_with"]), ("done", moved))

    def test_nothing_moved_merges_without_a_run(self):
        e = self.env("true")
        (tid,) = self.deliver(e, {"a.py": "a\n"})
        mergecheck.merged_check(e.p, tid)
        delivery.accept(e.p, tid)
        runs = self.runs()
        loop.tick(e.p)
        self.assertEqual((e.task(tid)["status"], self.runs()), ("done", runs))

    def test_conflict_goes_back_without_a_merged_run(self):
        e = self.env("true")
        a, b = self.deliver(e, {"app.py": "# from A\n", "other.py": "o\n"})
        commit(e.task(b)["worktree"], "app.py", "# from B\n")
        backlog.set_status(e.p, b, "in review")
        e.review(b)
        loop.tick(e.p)
        delivery.accept(e.p, a)
        delivery.accept(e.p, b)
        runs = self.runs()
        loop.tick(e.p)
        self.assertEqual(e.task(a)["status"], "done")
        i = e.task(b)
        self.assertEqual((i["status"], i["conflicts"]), ("in progress", 1))
        self.assertEqual(self.runs(), runs + 1)  # A's merged run only
        self.assertFalse([t for t in events(e, b) if t.startswith("checked merged")])

    def test_without_a_test_command_it_merges_as_today(self):
        e = self.env("")
        (tid,) = self.deliver(e, {"a.py": "a\n"})
        delivery.accept(e.p, tid)
        loop.tick(e.p)
        i = e.task(tid)
        self.assertEqual(i["status"], "done")
        self.assertNotIn("merged_with", i["check"])

    def test_third_merged_failure_waits_for_the_owner(self):
        e = self.env()
        a, b = self.deliver(e, {"a.py": "a\n", "b.py": "b\n"})
        delivery.accept(e.p, a)
        loop.tick(e.p)
        self.assertEqual(e.task(a)["status"], "done")
        for n in (1, 2):
            delivery.accept(e.p, b)
            loop.tick(e.p)
            i = e.task(b)
            self.assertEqual((i["status"], i["failures"], i["failure_streak"]), ("in progress", n, n))
            fix(e, b)
            loop.tick(e.p)  # its branch alone passes: the row goes on
            self.assertEqual(e.task(b)["failure_streak"], n)
        delivery.accept(e.p, b)
        before = len(e.calls())
        loop.tick(e.p)
        i = e.task(b)
        self.assertEqual((i["status"], i["failures"], i["check"]["tests"]), ("in review", 2, "fail"))
        self.assertIsNone(i.get("accepted"))
        self.assertEqual(len(e.calls()), before)
        self.assertFalse((e.repo / "b.py").exists())

    def test_show_says_what_the_check_ran_on(self):
        e = self.env("true")
        (tid,) = self.deliver(e, {"a.py": "a\n"})
        self.assertEqual(json.loads(fm(e.home, "show", tid))["checked_on"], "branch alone")
        mergecheck.merged_check(e.p, tid)
        self.assertEqual(json.loads(fm(e.home, "show", tid))["checked_on"], "merged with main " + self.main(e)[:9])


if __name__ == "__main__":
    unittest.main()
