"""One test per defect found by the review of 2026-11-08."""

import os
from pathlib import Path
from typing import Any
from unittest import mock

from helpers import PLUGIN, Env, fm, git

from foreman.core.oversight import guard
from foreman.core.tasks import questions
from foreman.loop import tick
from foreman.providers import registry
from foreman.providers.claude.sessions import Claude
from foreman.services.agents import brief, launch
from foreman.steps import talk
from foreman.steps.health import checks


class TestGuardFalsePositives:
    def check(self, cmd: str, cwd: str = "/h") -> str | None:
        return guard.check_bash(cmd, guard.Scope(cwd, [Path("/w")], "foreman/ty-1", Path("/h/foreman.toml"), []))

    def test_reading_the_config_with_redirects_is_not_a_write(self):
        assert self.check("cat foreman.toml 2>/dev/null") is None
        assert self.check("grep -n slots foreman.toml 2>&1 | head") is None
        assert self.check("cp foreman.toml /tmp/foreman.toml.bak") is None
        assert self.check("echo x > other/foreman.toml") is None  # not the project's config

    def test_writing_the_config_is_still_asked(self):
        assert self.check("echo x >> foreman.toml") == "config"
        assert self.check("cp /tmp/new.toml /h/foreman.toml") == "config"
        assert self.check("sed -i '' s/a/b/ foreman.toml") == "config"
        assert self.check("echo x | tee foreman.toml") == "config"

    def test_separators_inside_quotes_do_not_split(self):
        assert self.check('git commit -m "Docs: x; git push origin main is for the owner"') is None
        assert self.check("cd /w && git push origin main") == "push-main"
        assert self.check("ls\ngit push origin main") == "push-main"


class TestsRunOnTheCommit:
    def test_an_uncommitted_file_cannot_make_the_tests_pass(self):
        e = Env(test="python3 -c 'import helper'", autonomy="low")
        tid = registry.backlog(e.p).add("x", status="ready", criteria="y")
        tick.tick(e.p)
        wt = Path(e.task(tid)["worktree"])
        (wt / "app.py").write_text("import helper\n")
        (wt / "helper.py").write_text("")  # never committed
        git(wt, "commit", "-qam", "uses helper")
        registry.backlog(e.p).set_status(tid, "in review", report={"summary": "s", "confused": ""})
        e.review(tid)
        tick.tick(e.p)
        assert (e.task(tid)["status"], e.task(tid)["check"]["tests"]) == ("in progress", "fail")  # returned to its agent (FN-67)


class TestSessions:
    def test_a_session_not_listed_at_launch_gets_its_id_later_and_is_not_blocked(self):
        e = Env()
        tid = registry.backlog(e.p).add("x", status="ready", criteria="y")
        tick.tick(e.p)
        a = e.task(tid)["agent"]
        registry.backlog(e.p).update(tid, agent={**a, "session": None, "started": "2000-01-01 00:00:00"}, sessions=[])
        tick.tick(e.p)
        i = e.task(tid)
        assert (i["agent"]["session"], i.get("blocked")) == (a["session"], "")


class TestIsolation:
    def test_one_broken_queued_task_does_not_stop_the_others(self):
        e = Env(test="true", autonomy="low")
        ids = [registry.backlog(e.p).add(f"x{n}", status="ready", criteria="y") for n in range(2)]
        tick.tick(e.p)
        for tid in ids:
            wt = Path(e.task(tid)["worktree"])
            (wt / f"{tid}.txt").write_text("x\n")
            git(wt, "add", "-A")
            git(wt, "commit", "-qm", "c")
            registry.backlog(e.p).set_status(tid, "in review", report={"summary": "s", "confused": ""})
        tick.tick(e.p)  # both hunters start (two slots)
        for tid in ids:
            e.hunter_done(tid)
        tick.tick(e.p)  # then both reviewers
        for tid in ids:
            os.environ["FOREMAN_TASK"] = tid
            fm(e.home, "verdict", "--open", "0", "--file", "r.md")
        os.environ.pop("FOREMAN_TASK")
        registry.backlog(e.p).update(ids[0], branch="no/such-branch")
        tick.tick(e.p)
        assert "could not check" in e.task(ids[0])["blocked"]
        assert e.task(ids[1])["status"] == "done"


class TestClosed:
    def test_cancel_closes_the_questions_and_no_answer_reaches_the_cancelled_agent(self):
        e = Env()
        tid = registry.backlog(e.p).add("x", status="ready", criteria="y")
        tick.tick(e.p)
        registry.backlog(e.p).ask(tid, tid.lower(), "A or B?")
        fm(e.home, "cancel", tid)
        d = registry.backlog(e.p).load()
        assert questions.open_questions(d) == []
        assert not Path(e.task(tid)["worktree"]).exists()
        e.set_state(tid.lower(), "done")
        tick.tick(e.p)
        assert len(e.calls()) == 1  # only the launch


class TestOrphans:
    def test_a_ready_task_with_its_worktree_is_not_an_orphan(self):
        e = Env()
        tid = registry.backlog(e.p).add("x", status="ready", criteria="y")
        tick.tick(e.p)
        fm(e.home, "pause")
        registry.backlog(e.p).set_status(tid, "ready", "relaunch")
        assert checks.orphan_worktrees(e.p) == []


class TestLaunchRace:
    def test_a_task_cancelled_while_its_session_starts_stays_cancelled(self):
        e = Env()
        tid = registry.backlog(e.p).add("x", status="ready", criteria="y")
        real = Claude.start

        def start_and_cancel(self: Claude, *a: Any, **k: Any):
            r = real(self, *a, **k)
            registry.backlog(e.p).set_status(tid, "canceled", "owner")
            return r

        with mock.patch.object(Claude, "start", start_and_cancel):
            launch.launch(e.p, e.task(tid))
        assert e.task(tid)["status"] == "canceled"

    def test_only_one_loop_per_registry(self):
        import fcntl
        import subprocess
        import sys

        e = Env()
        os.environ["FOREMAN_REGISTRY"] = str(e.tmp / "projects.json")
        try:
            with open(str(e.tmp / "projects.json") + ".loop.lock", "a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                r = subprocess.run(
                    [sys.executable, str(PLUGIN / "bin/foreman"), "--home", str(e.home), "loop", "--once"],
                    capture_output=True,
                    text=True,
                    env=os.environ,
                )
            assert "another loop" in r.stderr
        finally:
            os.environ.pop("FOREMAN_REGISTRY")


class TestReviewerSeesOwnerNotes:
    def brief(self, e: Env, tid: str) -> str:
        return brief.brief(e.p, e.task(tid), "reviewer")

    def test_every_owner_note_of_the_task_reaches_the_reviewer_oldest_first_with_its_time(self):
        e = Env()
        tid = registry.backlog(e.p).add("x", status="ready", criteria="y")
        other = registry.backlog(e.p).add("z", status="ready", criteria="y")
        tick.tick(e.p)
        talk.return_to_agent(e.p, tid, "rejection", "FIRSTNOTE use B")
        talk.return_to_agent(e.p, tid, "review", "REVIEWFIX")
        talk.return_to_agent(e.p, tid, "conflict", "CONFLICTFIX")
        talk.return_to_agent(e.p, tid, "rejection", "SECONDNOTE actually C")
        talk.return_to_agent(e.p, other, "rejection", "OTHERTASK")
        text = self.brief(e, tid)
        assert text.index("FIRSTNOTE") < text.index("SECONDNOTE")
        for gone in ("REVIEWFIX", "CONFLICTFIX", "OTHERTASK"):
            assert gone not in text
        stamp = [q for q in registry.backlog(e.p).load()["decisions_resolved"] if "FIRSTNOTE" in q["answer"]][0]["at"]
        assert stamp in text

    def test_no_owner_notes_no_section(self):
        e = Env()
        tid = registry.backlog(e.p).add("x", status="ready", criteria="y")
        tick.tick(e.p)
        talk.return_to_agent(e.p, tid, "review", "REVIEWFIX")
        assert brief.owner_notes_section(e.p, tid) == ""
        assert "owner's notes" not in self.brief(e, tid)
