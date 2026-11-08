import json
import os
import stat
import unittest

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
        iid = backlog.add(self.p, "work", status="ready", criterion="x")
        loop.tick(self.p)
        self.assertEqual(self.e.issue(iid)["status"], "in progress")
        return iid

    def queued(self):
        iid = self.running()
        branch = self.e.issue(iid)["branch"]
        wt = self.e.issue(iid)["worktree"]
        open(f"{wt}/new.txt", "w").write("x")
        git(self.e.repo.__class__(wt), "add", "-A")
        git(self.e.repo.__class__(wt), "commit", "-qm", "work")
        backlog.set_status(self.p, iid, "in review", "t", report={"summary": "s"})
        return iid, branch

    def test_no_task_files_triage_once(self):
        sig = {"key": "k1", "title": "ci red", "detail": "d"}
        self.emit(sig, sig)
        self.emit(sig)
        issues = backlog.issues(backlog.load(self.p))
        self.assertEqual([(i["status"], i["source"]) for i in issues], [("triage", "src:k1")])

    def test_note_reaches_the_agent_like_an_answer(self):
        iid = self.running()
        self.emit({"key": "n1", "task": iid, "action": "note", "text": "approved by Ana"})
        self.emit({"key": "n1", "task": iid, "action": "note", "text": "approved by Ana"})  # once
        self.e.set_state(iid.lower(), "done")
        loop.tick(self.p)
        resumes = [c for c in self.e.calls() if "--resume" in c["argv"]]
        self.assertEqual(len(resumes), 1)
        self.assertIn("approved by Ana", resumes[0]["argv"][-1])
        self.assertEqual(self.errors(), [])

    def test_reject_is_fm_reject(self):
        iid, _ = self.queued()
        self.emit({"key": "r1", "task": iid, "action": "reject", "text": "change X"})
        i = self.e.issue(iid)
        self.assertEqual((i["status"], i["rework"]), ("in progress", 1))
        self.assertEqual(backlog.load(self.p)["decisions_resolved"][-1]["answer"], "change X")

    def test_accept_is_fm_accept(self):
        iid, branch = self.queued()
        from fm import integrate
        integrate.check(self.p, iid)
        self.emit({"key": "a1", "task": iid, "action": "accept"})
        self.assertEqual(self.e.issue(iid)["status"], "done")
        self.assertTrue(self.e.issue(iid)["commit"])

    def test_done_closes_as_done(self):
        iid = self.running()
        self.emit({"key": "d1", "task": iid, "action": "done", "text": "merged on GitHub"})
        self.assertEqual(self.e.issue(iid)["status"], "done")
        self.assertEqual(self.errors(), [])

    def test_cancel_is_fm_cancel(self):
        iid = self.running()
        self.emit({"key": "c1", "task": iid, "action": "cancel"})
        self.assertEqual(self.e.issue(iid)["status"], "canceled")

    def test_invalid_signals_change_nothing_and_are_logged(self):
        run = self.running()
        closed = backlog.add(self.p, "old", status="triage")
        backlog.set_status(self.p, closed, "canceled")
        before = json.dumps(backlog.load(self.p), sort_keys=True)
        bad = [
            {"key": "b1", "task": "TY-99", "action": "note", "text": "x"},          # unknown task
            {"key": "b2", "task": closed, "action": "note", "text": "x"},           # closed task
            {"key": "b3", "task": run, "action": "explode"},                         # unknown action
            {"key": "b4", "task": run, "action": "accept"},                          # not in review
            {"key": "b5", "task": run, "action": "reject", "text": "x"},             # not in review
            {"key": "b6", "task": run, "action": "note"},                            # no text
        ]
        self.emit(*bad)
        self.assertEqual(json.dumps(backlog.load(self.p), sort_keys=True), before)
        self.assertEqual(len(self.errors()), len(bad))


if __name__ == "__main__":
    unittest.main()
