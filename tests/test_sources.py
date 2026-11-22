import json
import stat
from pathlib import Path

import pytest
from helpers import Env, git

from foreman import util
from foreman.core.tasks import backlog
from foreman.loop import tick
from foreman.providers import registry
from foreman.providers.claude.sessions import Claude
from foreman.services import runsource
from foreman.services.delivery import check
from foreman.steps import sources


class TestSignals:
    @pytest.fixture(autouse=True)
    def set_up(self, env: Env):
        self.e = env
        self.p = self.e.p
        (self.e.home / "sources").mkdir()

    def emit(self, *lines: object) -> None:
        f = self.e.home / "sources" / "src"
        f.write_text("#!/bin/sh\n" + "".join(f"echo '{json.dumps(x)}'\n" for x in lines))
        f.chmod(f.stat().st_mode | stat.S_IXUSR)
        sources.run(self.p)

    def errors(self):
        return util.read_jsonl(self.p.state / "errors.jsonl")

    def running(self):
        """A task with a launched agent."""
        tid = registry.backlog(self.p).add("work", status="ready", criteria="x")
        tick.tick(self.p)
        assert self.e.task(tid)["status"] == "in progress"
        return tid

    def queued(self):
        tid = self.running()
        branch = self.e.task(tid)["branch"]
        wt = self.e.task(tid)["worktree"]
        Path(f"{wt}/new.txt").write_text("x")
        git(self.e.repo.__class__(wt), "add", "-A")
        git(self.e.repo.__class__(wt), "commit", "-qm", "work")
        registry.backlog(self.p).set_status(tid, "in review", "t", report={"summary": "s"})
        return tid, branch

    def test_no_task_files_triage_once(self):
        sig = {"key": "k1", "title": "ci red", "detail": "d"}
        self.emit(sig, sig)
        self.emit(sig)
        tasks = backlog.tasks(registry.backlog(self.p).load())
        assert [(i["status"], i["source"]) for i in tasks] == [("triage", "src:k1")]

    def test_note_reaches_the_agent_like_an_answer(self):
        tid = self.running()
        self.emit({"key": "n1", "task": tid, "action": "note", "text": "approved by Ana"})
        self.emit({"key": "n1", "task": tid, "action": "note", "text": "approved by Ana"})  # once
        self.e.set_state(tid.lower(), "done")
        tick.tick(self.p)
        resumes = [c for c in self.e.calls() if "--resume" in c["argv"]]
        assert len(resumes) == 1
        assert "approved by Ana" in resumes[0]["argv"][-1]
        assert self.errors() == []

    def test_reject_is_fm_reject(self):
        tid, _ = self.queued()
        self.emit({"key": "r1", "task": tid, "action": "reject", "text": "change X"})
        i = self.e.task(tid)
        assert (i["status"], i["rework"]) == ("in progress", 1)
        assert registry.backlog(self.p).load()["decisions_resolved"][-1]["answer"] == "change X"

    def test_accept_is_fm_accept(self):
        tid, _ = self.queued()

        check.check(self.p, tid)
        self.emit({"key": "a1", "task": tid, "action": "accept"})
        assert self.e.task(tid)["accepted"]  # as `fm accept`: the decision; the loop merges
        tick.tick(self.p)
        assert self.e.task(tid)["status"] == "done"
        assert self.e.task(tid)["commit"]

    def test_done_closes_as_done(self):
        tid = self.running()
        self.emit({"key": "d1", "task": tid, "action": "done", "text": "merged on GitHub"})
        assert self.e.task(tid)["status"] == "done"
        assert self.errors() == []

    def test_cancel_is_fm_cancel(self):
        tid = self.running()
        self.emit({"key": "c1", "task": tid, "action": "cancel"})
        assert self.e.task(tid)["status"] == "canceled"

    def test_invalid_signals_change_nothing_and_are_logged(self):
        run = self.running()
        closed = registry.backlog(self.p).add("old", status="triage")
        registry.backlog(self.p).set_status(closed, "canceled")
        before = json.dumps(registry.backlog(self.p).load(), sort_keys=True)
        bad = [
            {"key": "b1", "task": "TY-99", "action": "note", "text": "x"},  # unknown task
            {"key": "b2", "task": closed, "action": "note", "text": "x"},  # closed task
            {"key": "b3", "task": run, "action": "explode"},  # unknown action
            {"key": "b4", "task": run, "action": "accept"},  # not in review
            {"key": "b5", "task": run, "action": "reject", "text": "x"},  # not in review
            {"key": "b6", "task": run, "action": "note"},  # no text
        ]
        self.emit(*bad)
        assert json.dumps(registry.backlog(self.p).load(), sort_keys=True) == before
        assert len(self.errors()) == len(bad)

    # the findings of review 1 (F1-F7)

    def test_failed_accept_is_logged_and_retried_then_applied_once(self):  # F1, F4

        tid, _ = self.queued()
        sig = {"key": "a1", "task": tid, "action": "accept"}
        self.emit(sig)  # no check yet: "moved since it was checked"
        assert self.e.task(tid)["status"] == "in review"
        assert len(self.errors()) == 1
        check.check(self.p, tid)
        self.emit(sig)
        assert self.e.task(tid)["accepted"]
        tick.tick(self.p)  # the loop's merge step closes it
        assert self.e.task(tid)["status"] == "done"
        n = len(self.errors())
        self.emit(sig)  # once
        assert len(self.errors()) == n

    def test_a_retried_signal_leaves_one_error_line_per_key(self):  # F7
        tid, _ = self.queued()
        sig = {"key": "a1", "task": tid, "action": "accept"}
        for _ in range(3):
            self.emit(sig)  # still no check: fails every tick
        assert len(self.errors()) == 1
        assert self.e.task(tid)["status"] == "in review"

    def test_unexpected_failure_is_logged_and_does_not_stop_the_next_signal(self):  # F2
        from unittest import mock

        a, b = self.running(), registry.backlog(self.p).add("second", status="triage")
        registry.backlog(self.p).set_status(b, "in progress")
        with mock.patch.object(Claude, "stop", side_effect=OSError("hung")):
            self.emit({"key": "x1", "task": a, "action": "cancel"}, {"key": "x2", "task": b, "action": "done"})
        assert self.e.task(b)["status"] == "done"
        assert self.e.task(a)["status"] == "in progress"
        assert any("hung" in e["error"] for e in self.errors())

    def test_signal_without_key_or_not_an_object_is_logged(self):  # F3
        tid = self.running()
        self.emit({"task": tid, "action": "cancel"}, [1, 2])
        assert self.e.task(tid)["status"] == "in progress"
        assert len(self.errors()) == 2

    def test_reject_and_done_applied_once(self):  # F4
        tid, _ = self.queued()
        rej = {"key": "r1", "task": tid, "action": "reject", "text": "x"}
        self.emit(rej)
        self.emit(rej)
        assert self.e.task(tid)["rework"] == 1


def test_a_source_that_cannot_start_is_a_source_error(tmp_path: Path):
    exe = tmp_path / "src"
    exe.write_text("#!/nonexistent\n")
    exe.chmod(0o755)
    with pytest.raises(runsource.SourceError):
        runsource.lines(exe, "")
