"""The merged check (FN-113): an accepted branch merges only if the test command passes on it merged with main as main
is at that moment. One test per row of the task's table. A and B each pass alone and fail together (the FN-99/101
shape): A adds a.py, B adds b.py, and the test command fails when both are there."""

import json
import tempfile
from pathlib import Path

import pytest
from helpers import Env, fm, git
from test_failing import fix
from test_review_rules import commit, events, take_note

from fm.core.tasks import backlog
from fm.loop import tick
from fm.services.delivery import check, merge, mergecheck
from fm.services.delivery.git import DeliveryError

TOGETHER_FAIL = "test ! -e a.py -o ! -e b.py"


class TestMerged:
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
        tick.tick(e.p)
        for tid, (name, text) in zip(tids, files.items(), strict=True):
            commit(e.task(tid)["worktree"], name, text)
        for tid in tids:
            backlog.set_status(e.p, tid, "in review", report={"summary": "s", "confused": ""})
        for tid in tids:
            e.review(tid)
        tick.tick(e.p)
        return tids

    def main(self, e: Env) -> str:
        return git(e.repo, "rev-parse", "main").strip()

    def test_passes_alone_and_merged_merges(self):
        e = self.env("true")
        (tid,) = self.deliver(e, {"a.py": "a\n"})
        merge.accept(e.p, tid)
        before = self.main(e)
        tick.tick(e.p)
        i = e.task(tid)
        assert i["status"] == "done"
        assert i["check"]["merged_with"] == before

    def test_fails_merged_goes_back_saying_so(self):
        e = self.env()
        a, b = self.deliver(e, {"a.py": "a\n", "b.py": "b\n"})
        assert self.runs() == 2  # one per branch, alone: nothing is about to merge yet
        assert "merged_with" not in e.task(a)["check"]
        assert "merged_with" not in e.task(b)["check"]
        merge.accept(e.p, a)
        merge.accept(e.p, b)
        tick.tick(e.p)
        assert e.task(a)["status"] == "done"
        i = e.task(b)
        assert (i["status"], i["failures"], i["failure_streak"]) == ("in progress", 1, 1)
        main = self.main(e)
        assert i["check"]["merged_with"] == main  # checked with A already in main
        take_note(e, b)
        note = e.calls()[-1]["argv"][-1]
        assert main[:9] in note
        assert "not on its branch alone" in note
        assert not (e.repo / "b.py").exists()

    def test_autonomy_path_checks_merged_too(self):
        e = self.env(autonomy="low")
        a, b = self.deliver(e, {"a.py": "a\n", "b.py": "b\n"})
        tick.tick(e.p)
        assert e.task(a)["status"] == "done"
        i = e.task(b)
        assert (i["status"], i["failures"]) == ("in progress", 1)
        assert i["check"]["merged_with"] == self.main(e)
        assert not (e.repo / "b.py").exists()

    def test_main_moved_after_a_pass_checks_again(self):
        e = self.env("true")
        (tid,) = self.deliver(e, {"a.py": "a\n"})
        mergecheck.merged_check(e.p, tid)
        assert self.runs() == 2
        commit(str(e.repo), "other.py", "o\n")
        merge.accept(e.p, tid)
        moved = self.main(e)
        tick.tick(e.p)
        assert self.runs() == 3
        i = e.task(tid)
        assert (i["status"], i["check"]["merged_with"]) == ("done", moved)

    def test_nothing_moved_merges_without_a_run(self):
        e = self.env("true")
        (tid,) = self.deliver(e, {"a.py": "a\n"})
        mergecheck.merged_check(e.p, tid)
        merge.accept(e.p, tid)
        runs = self.runs()
        tick.tick(e.p)
        assert (e.task(tid)["status"], self.runs()) == ("done", runs)

    def test_conflict_goes_back_without_a_merged_run(self):
        e = self.env("true")
        a, b = self.deliver(e, {"app.py": "# from A\n", "other.py": "o\n"})
        commit(e.task(b)["worktree"], "app.py", "# from B\n")
        backlog.set_status(e.p, b, "in review")
        e.review(b)
        tick.tick(e.p)
        merge.accept(e.p, a)
        merge.accept(e.p, b)
        runs = self.runs()
        tick.tick(e.p)
        assert e.task(a)["status"] == "done"
        i = e.task(b)
        assert (i["status"], i["conflicts"]) == ("in progress", 1)
        assert self.runs() == runs + 1  # A's merged run only
        assert not [t for t in events(e, b) if t.startswith("checked merged")]

    def test_without_a_test_command_it_merges_as_today(self):
        e = self.env("")
        (tid,) = self.deliver(e, {"a.py": "a\n"})
        merge.accept(e.p, tid)
        tick.tick(e.p)
        i = e.task(tid)
        assert i["status"] == "done"
        assert "merged_with" not in i["check"]

    def test_third_merged_failure_waits_for_the_owner(self):
        e = self.env()
        a, b = self.deliver(e, {"a.py": "a\n", "b.py": "b\n"})
        merge.accept(e.p, a)
        tick.tick(e.p)
        assert e.task(a)["status"] == "done"
        for n in (1, 2):
            merge.accept(e.p, b)
            tick.tick(e.p)
            i = e.task(b)
            assert (i["status"], i["failures"], i["failure_streak"]) == ("in progress", n, n)
            fix(e, b)
            tick.tick(e.p)  # its branch alone passes: the row goes on
            assert e.task(b)["failure_streak"] == n
        merge.accept(e.p, b)
        before = len(e.calls())
        tick.tick(e.p)
        i = e.task(b)
        assert (i["status"], i["failures"], i["check"]["tests"]) == ("in review", 2, "fail")
        assert i.get("accepted") is None
        assert len(e.calls()) == before
        assert not (e.repo / "b.py").exists()

    def test_a_git_failure_at_the_merge_is_not_a_test_failure(self):
        e = self.env("true")
        with pytest.raises(DeliveryError):
            check.tests_at(e.p, self.main(e), "x", merge="no-such-commit")

    def test_a_failed_worktree_add_in_the_merged_check_raises(self):
        e = self.env("true")
        with pytest.raises(DeliveryError):
            check.tests_at(e.p, "no-such-commit", "x", merge=self.main(e))
        assert check.tests_at(e.p, "no-such-commit", "x")[0] == "fail"  # without merge: as before

    def test_show_says_what_the_check_ran_on(self):
        e = self.env("true")
        (tid,) = self.deliver(e, {"a.py": "a\n"})
        assert json.loads(fm(e.home, "show", tid))["checked_on"] == "branch alone"
        mergecheck.merged_check(e.p, tid)
        assert json.loads(fm(e.home, "show", tid))["checked_on"] == "merged with main " + self.main(e)[:9]
