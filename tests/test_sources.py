import json
import stat
import unittest
from pathlib import Path

from helpers import Env, git

from fm import backlog, loop, sources, util


class Signals(unittest.TestCase):
    def setUp(self):
        self.e = Env()
        self.p = self.e.p
        (self.e.home / "sources").mkdir()

    def emit(self, *lines):
        f = self.e.home / "sources" / "src"
        f.write_text("#!/bin/sh\n" + "".join(f"echo '{json.dumps(x)}'\n" for x in lines))
        f.chmod(f.stat().st_mode | stat.S_IXUSR)
        sources.run(self.p)

    def errors(self):
        return util.read_jsonl(self.p.state / "errors.jsonl")

    def running(self):
        """A task with a launched agent."""
        tid = backlog.add(self.p, "work", status="ready", criteria="x")
        loop.tick(self.p)
        self.assertEqual(self.e.task(tid)["status"], "in progress")
        return tid

    def queued(self):
        tid = self.running()
        branch = self.e.task(tid)["branch"]
        wt = self.e.task(tid)["worktree"]
        Path(f"{wt}/new.txt").write_text("x")
        git(self.e.repo.__class__(wt), "add", "-A")
        git(self.e.repo.__class__(wt), "commit", "-qm", "work")
        backlog.set_status(self.p, tid, "in review", "t", report={"summary": "s"})
        return tid, branch

    def test_no_task_files_triage_once(self):
        sig = {"key": "k1", "title": "ci red", "detail": "d"}
        self.emit(sig, sig)
        self.emit(sig)
        tasks = backlog.tasks(backlog.load(self.p))
        self.assertEqual([(i["status"], i["source"]) for i in tasks], [("triage", "src:k1")])

    def test_note_reaches_the_agent_like_an_answer(self):
        tid = self.running()
        self.emit({"key": "n1", "task": tid, "action": "note", "text": "approved by Ana"})
        self.emit({"key": "n1", "task": tid, "action": "note", "text": "approved by Ana"})  # once
        self.e.set_state(tid.lower(), "done")
        loop.tick(self.p)
        resumes = [c for c in self.e.calls() if "--resume" in c["argv"]]
        self.assertEqual(len(resumes), 1)
        self.assertIn("approved by Ana", resumes[0]["argv"][-1])
        self.assertEqual(self.errors(), [])

    def test_reject_is_fm_reject(self):
        tid, _ = self.queued()
        self.emit({"key": "r1", "task": tid, "action": "reject", "text": "change X"})
        i = self.e.task(tid)
        self.assertEqual((i["status"], i["rework"]), ("in progress", 1))
        self.assertEqual(backlog.load(self.p)["decisions_resolved"][-1]["answer"], "change X")

    def test_accept_is_fm_accept(self):
        tid, branch = self.queued()
        from fm import delivery

        delivery.check(self.p, tid)
        self.emit({"key": "a1", "task": tid, "action": "accept"})
        self.assertTrue(self.e.task(tid)["accepted"])  # as `fm accept`: the decision; the loop merges
        loop.tick(self.p)
        self.assertEqual(self.e.task(tid)["status"], "done")
        self.assertTrue(self.e.task(tid)["commit"])

    def test_done_closes_as_done(self):
        tid = self.running()
        self.emit({"key": "d1", "task": tid, "action": "done", "text": "merged on GitHub"})
        self.assertEqual(self.e.task(tid)["status"], "done")
        self.assertEqual(self.errors(), [])

    def test_cancel_is_fm_cancel(self):
        tid = self.running()
        self.emit({"key": "c1", "task": tid, "action": "cancel"})
        self.assertEqual(self.e.task(tid)["status"], "canceled")

    def test_invalid_signals_change_nothing_and_are_logged(self):
        run = self.running()
        closed = backlog.add(self.p, "old", status="triage")
        backlog.set_status(self.p, closed, "canceled")
        before = json.dumps(backlog.load(self.p), sort_keys=True)
        bad = [
            {"key": "b1", "task": "TY-99", "action": "note", "text": "x"},  # unknown task
            {"key": "b2", "task": closed, "action": "note", "text": "x"},  # closed task
            {"key": "b3", "task": run, "action": "explode"},  # unknown action
            {"key": "b4", "task": run, "action": "accept"},  # not in review
            {"key": "b5", "task": run, "action": "reject", "text": "x"},  # not in review
            {"key": "b6", "task": run, "action": "note"},  # no text
        ]
        self.emit(*bad)
        self.assertEqual(json.dumps(backlog.load(self.p), sort_keys=True), before)
        self.assertEqual(len(self.errors()), len(bad))


if __name__ == "__main__":
    unittest.main()


class Review1(Signals):
    def test_failed_accept_is_logged_and_retried_then_applied_once(self):  # F1, F4
        from fm import delivery

        tid, _ = self.queued()
        sig = {"key": "a1", "task": tid, "action": "accept"}
        self.emit(sig)  # no check yet: "moved since it was checked"
        self.assertEqual(self.e.task(tid)["status"], "in review")
        self.assertEqual(len(self.errors()), 1)
        delivery.check(self.p, tid)
        self.emit(sig)
        self.assertTrue(self.e.task(tid)["accepted"])
        loop.tick(self.p)  # the loop's merge step closes it
        self.assertEqual(self.e.task(tid)["status"], "done")
        n = len(self.errors())
        self.emit(sig)  # once
        self.assertEqual(len(self.errors()), n)

    def test_a_retried_signal_leaves_one_error_line_per_key(self):  # F7
        tid, _ = self.queued()
        sig = {"key": "a1", "task": tid, "action": "accept"}
        for _ in range(3):
            self.emit(sig)  # still no check: fails every tick
        self.assertEqual(len(self.errors()), 1)
        self.assertEqual(self.e.task(tid)["status"], "in review")

    def test_unexpected_failure_is_logged_and_does_not_stop_the_next_signal(self):  # F2
        from unittest import mock

        a, b = self.running(), backlog.add(self.p, "second", status="triage")
        backlog.set_status(self.p, b, "in progress")
        with mock.patch("fm.sessions.stop", side_effect=OSError("hung")):
            self.emit({"key": "x1", "task": a, "action": "cancel"}, {"key": "x2", "task": b, "action": "done"})
        self.assertEqual(self.e.task(b)["status"], "done")
        self.assertEqual(self.e.task(a)["status"], "in progress")
        self.assertTrue(any("hung" in e["error"] for e in self.errors()))

    def test_signal_without_key_or_not_an_object_is_logged(self):  # F3
        tid = self.running()
        self.emit({"task": tid, "action": "cancel"}, [1, 2])
        self.assertEqual(self.e.task(tid)["status"], "in progress")
        self.assertEqual(len(self.errors()), 2)

    def test_reject_and_done_applied_once(self):  # F4
        tid, _ = self.queued()
        rej = {"key": "r1", "task": tid, "action": "reject", "text": "x"}
        self.emit(rej)
        self.emit(rej)
        self.assertEqual(self.e.task(tid)["rework"], 1)
