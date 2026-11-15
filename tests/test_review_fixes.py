"""One test per defect found by the review of 2026-11-08."""

import os
from pathlib import Path

from helpers import Env, fm, git

from fm import health, loop
from fm.core.oversight import guard
from fm.core.tasks import backlog, questions
from fm.services.agents import brief, launch


class TestGuardFalsePositives:
    def check(self, cmd: str, cwd: str = "/h") -> str | None:
        return guard.check_bash(cmd, guard.Scope(cwd, [Path("/w")], "fm/ty-1", Path("/h/fm.toml"), []))

    def test_reading_the_config_with_redirects_is_not_a_write(self):
        assert self.check("cat fm.toml 2>/dev/null") is None
        assert self.check("grep -n slots fm.toml 2>&1 | head") is None
        assert self.check("cp fm.toml /tmp/fm.toml.bak") is None
        assert self.check("echo x > other/fm.toml") is None  # not the project's config

    def test_writing_the_config_is_still_asked(self):
        assert self.check("echo x >> fm.toml") == "config"
        assert self.check("cp /tmp/new.toml /h/fm.toml") == "config"
        assert self.check("sed -i '' s/a/b/ fm.toml") == "config"
        assert self.check("echo x | tee fm.toml") == "config"

    def test_separators_inside_quotes_do_not_split(self):
        assert self.check('git commit -m "Docs: x; git push origin main is for the owner"') is None
        assert self.check("cd /w && git push origin main") == "push-main"
        assert self.check("ls\ngit push origin main") == "push-main"


class TestsRunOnTheCommit:
    def test_an_uncommitted_file_cannot_make_the_tests_pass(self):
        e = Env(test="python3 -c 'import helper'", autonomy="low")
        tid = backlog.add(e.p, "x", status="ready", criteria="y")
        loop.tick(e.p)
        wt = Path(e.task(tid)["worktree"])
        (wt / "app.py").write_text("import helper\n")
        (wt / "helper.py").write_text("")  # never committed
        git(wt, "commit", "-qam", "uses helper")
        backlog.set_status(e.p, tid, "in review", report={"summary": "s", "confused": ""})
        e.review(tid)
        loop.tick(e.p)
        assert (e.task(tid)["status"], e.task(tid)["check"]["tests"]) == ("in progress", "fail")  # returned to its agent (FN-67)


class TestSessions:
    def test_a_session_not_listed_at_launch_gets_its_id_later_and_is_not_blocked(self):
        e = Env()
        tid = backlog.add(e.p, "x", status="ready", criteria="y")
        loop.tick(e.p)
        a = e.task(tid)["agent"]
        backlog.update(e.p, tid, agent={**a, "session": None, "started": "2000-01-01 00:00:00"}, sessions=[])
        loop.tick(e.p)
        i = e.task(tid)
        assert (i["agent"]["session"], i.get("blocked")) == (a["session"], "")


class TestIsolation:
    def test_one_broken_queued_task_does_not_stop_the_others(self):
        e = Env(test="true", autonomy="low")
        ids = [backlog.add(e.p, f"x{n}", status="ready", criteria="y") for n in range(2)]
        loop.tick(e.p)
        for tid in ids:
            wt = Path(e.task(tid)["worktree"])
            (wt / f"{tid}.txt").write_text("x\n")
            git(wt, "add", "-A")
            git(wt, "commit", "-qm", "c")
            backlog.set_status(e.p, tid, "in review", report={"summary": "s", "confused": ""})
        loop.tick(e.p)  # both hunters start (two slots)
        for tid in ids:
            e.hunter_done(tid)
        loop.tick(e.p)  # then both reviewers
        for tid in ids:
            os.environ["FM_TASK"] = tid
            fm(e.home, "verdict", "--open", "0", "--file", "r.md")
        os.environ.pop("FM_TASK")
        backlog.update(e.p, ids[0], branch="no/such-branch")
        loop.tick(e.p)
        assert "could not check" in e.task(ids[0])["blocked"]
        assert e.task(ids[1])["status"] == "done"


class TestClosed:
    def test_cancel_closes_the_questions_and_no_answer_reaches_the_cancelled_agent(self):
        e = Env()
        tid = backlog.add(e.p, "x", status="ready", criteria="y")
        loop.tick(e.p)
        questions.ask(e.p, tid, tid.lower(), "A or B?")
        fm(e.home, "cancel", tid)
        d = backlog.load(e.p)
        assert questions.open_questions(d) == []
        assert not Path(e.task(tid)["worktree"]).exists()
        e.set_state(tid.lower(), "done")
        loop.tick(e.p)
        assert len(e.calls()) == 1  # only the launch


class TestOrphans:
    def test_a_ready_task_with_its_worktree_is_not_an_orphan(self):
        e = Env()
        tid = backlog.add(e.p, "x", status="ready", criteria="y")
        loop.tick(e.p)
        fm(e.home, "pause")
        backlog.set_status(e.p, tid, "ready", "relaunch")
        assert health.orphan_worktrees(e.p) == []


class TestLaunchRace:
    def test_a_task_cancelled_while_its_session_starts_stays_cancelled(self):
        e = Env()
        tid = backlog.add(e.p, "x", status="ready", criteria="y")
        real = loop.sessions.start

        def start_and_cancel(*a, **k):
            r = real(*a, **k)
            backlog.set_status(e.p, tid, "canceled", "owner")
            return r

        loop.sessions.start = start_and_cancel
        try:
            launch.launch(e.p, e.task(tid))
        finally:
            loop.sessions.start = real
        assert e.task(tid)["status"] == "canceled"

    def test_only_one_loop_per_registry(self):
        import fcntl
        import subprocess
        import sys

        e = Env()
        os.environ["FM_REGISTRY"] = str(e.tmp / "projects.json")
        try:
            with open(str(e.tmp / "projects.json") + ".loop.lock", "a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                r = subprocess.run(
                    [sys.executable, str(Path(__file__).parent.parent / "bin/fm"), "--home", str(e.home), "loop", "--once"],
                    capture_output=True,
                    text=True,
                    env=os.environ,
                )
            assert "another loop" in r.stderr
        finally:
            os.environ.pop("FM_REGISTRY")


class TestReviewerSeesOwnerNotes:
    def brief(self, e: Env, tid: str) -> str:
        return brief.brief(e.p, e.task(tid), "reviewer")

    def test_every_owner_note_of_the_task_reaches_the_reviewer_oldest_first_with_its_time(self):
        e = Env()
        tid = backlog.add(e.p, "x", status="ready", criteria="y")
        other = backlog.add(e.p, "z", status="ready", criteria="y")
        loop.tick(e.p)
        loop.return_to_agent(e.p, tid, "rejection", "FIRSTNOTE use B")
        loop.return_to_agent(e.p, tid, "review", "REVIEWFIX")
        loop.return_to_agent(e.p, tid, "conflict", "CONFLICTFIX")
        loop.return_to_agent(e.p, tid, "rejection", "SECONDNOTE actually C")
        loop.return_to_agent(e.p, other, "rejection", "OTHERTASK")
        text = self.brief(e, tid)
        assert text.index("FIRSTNOTE") < text.index("SECONDNOTE")
        for gone in ("REVIEWFIX", "CONFLICTFIX", "OTHERTASK"):
            assert gone not in text
        stamp = [q for q in backlog.load(e.p)["decisions_resolved"] if "FIRSTNOTE" in q["answer"]][0]["at"]
        assert stamp in text

    def test_no_owner_notes_no_section(self):
        e = Env()
        tid = backlog.add(e.p, "x", status="ready", criteria="y")
        loop.tick(e.p)
        loop.return_to_agent(e.p, tid, "review", "REVIEWFIX")
        assert brief.owner_notes_section(e.p, tid) == ""
        assert "owner's notes" not in self.brief(e, tid)
