"""The orchestrator's mark tied to its Claude Code process (procs.py, orchestrator.mark/runs_here): a copy of the
session (`claude -p --resume ID`) neither takes the mark nor runs orchestrator-only commands. `ps` is simulated
(helpers.FakePs) except in RealPsTest."""

import json
import os
from unittest import mock

import test_grants as tg
from helpers import CLAUDE_PID, PS_START, FakePs, ProjectCase

from foreman import approved, guard, loop, orchestrator, procs

COPY_PID = 9_005_555  # above any real pid (helpers.CLAUDE_PID)
CMD = tg.DEPLOY


class ProcsTest(FakePs, ProjectCase):
    def setUp(self):
        super().setUp()
        self.start_ps()

    def test_parse(self):
        t = procs.parse(
            "  10     1 Sun Nov  1 10:00:00 2026 /opt/x/Application Support/claude\n"
            "garbage\n"
            "  xx     1 Sun Nov  1 10:00:00 2026 bad\n"
        )
        self.assertEqual(list(t), [10])
        self.assertEqual(t[10].start, "Sun Nov 1 10:00:00 2026")
        self.assertEqual(t[10].comm, "/opt/x/Application Support/claude")
        self.assertTrue(procs.is_claude(t[10].comm))
        self.assertFalse(procs.is_claude("/Applications/Claude.app/Contents/MacOS/Claude"))
        with self.assertRaises(procs.Unavailable):
            procs.parse("\n")

    def test_nearest_claude(self):
        self.assertEqual(procs.claude_ancestor().pid, CLAUDE_PID)
        self.reparent(COPY_PID)  # a copy launched from the orchestrator's shell: the nearest one counts
        self.assertEqual(procs.claude_ancestor().pid, COPY_PID)
        self.ps_rows = [(7, 7, PS_START, "sh")]  # a loop, an unknown pid, no claude
        self.assertIsNone(procs.claude_ancestor(7))
        self.assertIsNone(procs.claude_ancestor(8))
        self.ps_rows = [(n, n + 1, PS_START, "sh") for n in range(1, 20)]  # deeper than MAX_DEPTH
        self.ps_rows.append((20, 0, PS_START, "claude"))
        self.assertIsNone(procs.claude_ancestor(1))
        self.assertEqual(procs.claude_ancestor(15).pid, 20)
        self.ps_rows = None
        with self.assertRaises(procs.Unavailable):
            procs.claude_ancestor()

    def test_alive(self):
        self.assertTrue(procs.alive(CLAUDE_PID, PS_START))
        self.assertFalse(procs.alive(CLAUDE_PID, "Mon Nov 2 09:00:00 2026"))  # a recycled pid
        self.assertFalse(procs.alive(COPY_PID, PS_START))
        self.ps_rows = None  # without ps: whether the pid exists at all
        self.assertTrue(procs.alive(os.getpid(), "whatever"))
        self.assertFalse(procs.alive(2**22 + 12345, PS_START))


class MarkTest(FakePs, ProjectCase):
    toml = tg.TOML

    def setUp(self):
        super().setUp()
        self.start_ps()

    def rec(self):
        return json.loads((self.project.state / "orchestrator.json").read_text())

    def test_marks_its_own_process(self):
        orchestrator.mark(self.project, "S1", "o-1", own_session="S1")
        r = self.rec()
        self.assertEqual((r["pid"], r["pid_start"], r["process"]), (CLAUDE_PID, PS_START, None))
        self.assertTrue(orchestrator.runs_here(self.project))
        # again from the same process (a compaction, /reload-plugins, a subagent of it): same mark
        orchestrator.mark(self.project, "S1", own_session="S1")
        self.assertEqual((self.rec()["pid"], self.rec()["name"]), (CLAUDE_PID, "o-1"))
        # marked by hand (the owner's terminal: no session of its own): the process it had is kept
        orchestrator.mark(self.project, "S1")
        self.assertEqual(self.rec()["pid"], CLAUDE_PID)
        # another session marked by hand: no process until it marks itself
        orchestrator.mark(self.project, "S2", "o-2")
        self.assertEqual((self.rec()["pid"], self.rec()["process"]), (None, "marked by hand"))
        self.assertFalse(orchestrator.runs_here(self.project))

    def test_a_copy_does_not_take_the_mark(self):
        orchestrator.mark(self.project, "S1", "o-1", own_session="S1")
        self.reparent(COPY_PID)
        with self.assertRaises(orchestrator.Refused):
            orchestrator.mark(self.project, "S1", "copy", own_session="S1")
        self.assertEqual((self.rec()["pid"], self.rec()["name"]), (CLAUDE_PID, "o-1"))
        # another session taking the mark is not refused (the one before is alive)
        orchestrator.mark(self.project, "S2", "o-2", own_session="S2")
        self.assertEqual(self.rec()["pid"], COPY_PID)

    def test_a_dead_or_recycled_process_is_relieved(self):
        orchestrator.mark(self.project, "S1", "o-1", own_session="S1")
        self.reparent(COPY_PID)
        self.ps_rows = [r for r in self.ps_rows if r[0] != CLAUDE_PID]  # the old process ended (a resume)
        orchestrator.mark(self.project, "S1", own_session="S1")
        self.assertEqual((self.rec()["pid"], self.rec()["name"]), (COPY_PID, "o-1"))
        # its pid reused by another process later: another start time, not the marked one
        self.reparent(COPY_PID + 1)
        self.ps_rows = [(p, pp, "Mon Nov 2 09:00:00 2026" if p == COPY_PID else st, c) for p, pp, st, c in self.ps_rows]
        orchestrator.mark(self.project, "S1", own_session="S1")
        self.assertEqual(self.rec()["pid"], COPY_PID + 1)

    def test_without_ps(self):
        orchestrator.mark(self.project, "S1", own_session="S1")
        self.ps_rows = None
        self.assertFalse(orchestrator.runs_here(self.project))  # fails closed
        # the marked pid still exists (os.kill): the refusal holds
        rec = self.rec()
        rec["pid"] = os.getpid()
        (self.project.state / "orchestrator.json").write_text(json.dumps(rec))
        with self.assertRaises(orchestrator.Refused):
            orchestrator.mark(self.project, "S1", own_session="S1")
        rec["pid"] = 2**22 + 12345  # gone
        (self.project.state / "orchestrator.json").write_text(json.dumps(rec))
        orchestrator.mark(self.project, "S1", own_session="S1")
        self.assertIsNone(self.rec()["pid"])
        self.assertIn("ps unavailable", self.rec()["process"])
        self.assertIn("ps unavailable", orchestrator.process_line(self.project))
        # ps works but no claude above this call
        self.start_ps()
        self.ps_rows = [(os.getpid(), 1, PS_START, "python")]
        orchestrator.mark(self.project, "S1", own_session="S1")
        self.assertEqual(self.rec()["process"], "no claude process above this call")

    def test_session_command(self):
        orchestrator.mark(self.project, "S1", "o-1", own_session="S1")
        r = self.cli("session")
        self.assertIn(f"process: claude pid {CLAUDE_PID}, started {PS_START} UTC", r.stdout)
        orchestrator.mark(self.project, "S2")
        r = self.cli("session")
        self.assertIn("process: none recorded (marked by hand)", r.stdout)
        (self.project.state / "orchestrator.json").unlink()  # a mark written by an older foreman
        self.assertIn("marked by an older foreman", self.cli("session").stdout)


class GuardProcessTest(FakePs, ProjectCase):
    toml = tg.TOML
    event = tg.GrantCase.event
    give = tg.GrantCase.give

    def setUp(self):
        super().setUp()
        self.start_ps()
        orchestrator.mark(self.project, "S1", "fake-1", own_session="S1")
        self.give()  # the owner's yes: what is left to refuse is the process

    def why(self):
        v = guard.check(self.project, CMD, {"session": "S1", "agent": None})
        return v[1].why if v else ""

    def test_why_process(self):
        self.assertEqual(self.why(), "")
        self.reparent(COPY_PID)  # a copy: same session id, no agent id
        self.assertEqual(self.why(), "process")
        self.start_ps()
        self.ps_rows = [
            (p, pp, "Mon Nov 2 09:00:00 2026" if p == CLAUDE_PID else st, c) for p, pp, st, c in self.ps_rows
        ]
        self.assertEqual(self.why(), "process")  # the marked pid, recycled by another claude
        self.ps_rows = None
        self.assertEqual(self.why(), "process")  # no ps: fails closed
        # the earlier reasons come first; a table run never asks ps
        self.assertEqual(guard.check(self.project, CMD, {"session": "S2"})[1].why, "session")
        self.assertIsNone(guard.check(self.project, CMD, guard.as_role("orchestrator")))
        self.start_ps()
        orchestrator.mark(self.project, "S1")  # by hand from a terminal: keeps the process
        self.assertEqual(self.why(), "")
        orchestrator.mark(self.project, "S3")
        orchestrator.mark(self.project, "S1")  # by hand after another session: none recorded
        self.assertEqual(self.why(), "process")

    def test_hook_and_notice(self):
        self.reparent(COPY_PID)
        rc, err = self.hook_here(self.event(CMD, tid="C1"))
        self.assertEqual(rc, 2)
        self.assertIn("from the Claude Code process that marked it", err)
        self.assertIn("It matched in", err)
        rec = json.loads(approved.log_file(self.project).read_text().splitlines()[-1])
        self.assertEqual((rec["why"], rec["marked_pid"]), ("process", CLAUDE_PID))
        (n,) = loop.orchestrator_only_notices(self.project)
        self.assertIn("another Claude Code process of session S1", n["text"])
        # a mark with no process: the orchestrator's own refusal, no notice
        orchestrator.mark(self.project, "S4")
        orchestrator.mark(self.project, "S1")
        self.assertEqual(self.hook_here(self.event(CMD, tid="C2"))[0], 2)
        self.assertEqual(loop.orchestrator_only_notices(self.project), [])
        self.write_toml(tg.TOML.replace('owner = "Owner"', 'owner = "Owner"\nlanguage = "es"'))
        text = guard.message(self.project, guard.check(self.project, CMD, {"session": "S1"}))
        self.assertIn("desde el proceso de Claude Code que se marcó", text)


class RealPsTest(ProjectCase):
    toml = tg.TOML

    def test_real_ps(self):
        t = procs.table()
        me = t[os.getpid()]
        self.assertEqual(me.ppid, os.getppid())
        self.assertTrue(procs.alive(me.pid, me.start))
        with mock.patch.object(procs, "PS_PATHS", ("/no/such/ps",)), self.assertRaises(procs.Unavailable):
            procs.table()
        with mock.patch.object(procs, "PS_PATHS", ("/usr/bin/false",)), self.assertRaises(procs.Unavailable):
            procs.table()
        with mock.patch.object(procs, "PS_ARGS", ("-Q",)), self.assertRaises(procs.Unavailable):
            procs.table()

    def test_session_refuses_from_another_live_process(self):
        # the mark held by this (live) test process; `foreman session S1` from the session S1 runs in a child
        # whose nearest claude, if any, is not this process
        me = procs.table()[os.getpid()]
        rec = {"session": "S1", "name": "o-1", "since": "2026-11-01T10:00:00", "pid": me.pid, "pid_start": me.start}
        (self.project.state).mkdir(parents=True, exist_ok=True)
        (self.project.state / "session").write_text("S1\n")
        (self.project.state / "orchestrator.json").write_text(json.dumps(rec))
        r = self.cli("session", "S1", "--name", "copy", env={"CLAUDE_CODE_SESSION_ID": "S1"})
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("marked by another Claude Code process that still runs", r.stderr)
        self.assertEqual(json.loads((self.project.state / "orchestrator.json").read_text())["name"], "o-1")
        rec["pid_start"] = "Mon Jan 1 00:00:00 2001"  # that pid now belongs to another process: relieved
        (self.project.state / "orchestrator.json").write_text(json.dumps(rec))
        r = self.cli("session", "S1", "--name", "resumed", env={"CLAUDE_CODE_SESSION_ID": "S1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("orchestrated by resumed", r.stdout)


class CarryTest(FakePs, ProjectCase):
    """A /clear or an in-process resume in the marked session: the module's start under the new id names
    the old one (`previous`), and the mark moves to the new id when the old one was marked in this same `claude`
    process; a /clear elsewhere, or a start from another process, moves nothing."""

    toml = tg.TOML

    def setUp(self):
        super().setUp()
        self.start_ps()

    def rec(self):
        return json.loads((self.project.state / "orchestrator.json").read_text())

    def start(self, session, previous):
        from foreman import hooks_mod

        ev = {"cwd": str(self.home), "session": session, "claude_code": "2.1.314", "interactive": True}
        return hooks_mod.start({**ev, "previous": previous})

    def test_same_process_moves_the_mark(self):
        from foreman import heartbeat

        orchestrator.mark(self.project, "S-old", "o-1", own_session="S-old")
        heartbeat.beat(self.project, "S-old", cwd=str(self.home), claude_code="2.1.314", start=True)
        got = self.start("S-new", "S-old")
        self.assertEqual(got["active"], True)
        r = self.rec()
        self.assertEqual((r["session"], r["name"], r["pid"], r["pid_start"]), ("S-new", "o-1", CLAUDE_PID, PS_START))
        self.assertEqual(orchestrator.current(self.project)["session"], "S-new")
        self.assertTrue(orchestrator.runs_here(self.project))  # the pid tie holds under the new id
        self.assertIn("Orchestrator mark moved (session o-1)", self.notes_text())
        self.assertIn("S-old went on as S-new", self.notes_text())
        self.assertTrue(heartbeat.read(self.project, "S-old").get("ended"))  # no session.end seen: ended here
        self.assertTrue(heartbeat.read(self.project, "S-new").get("interactive"))  # Python recorded the new id

    def test_another_process_does_not(self):
        orchestrator.mark(self.project, "S-old", "o-1", own_session="S-old")
        self.reparent(COPY_PID)  # the start comes from another claude process (a copy that resumed S-old)
        self.start("S-new", "S-old")
        self.assertEqual((self.rec()["session"], self.rec()["pid"]), ("S-old", CLAUDE_PID))
        self.assertNotIn("mark moved", self.notes_text())
        self.ps_rows = None  # ps unreadable: fails closed
        self.start("S-new", "S-old")
        self.assertEqual(self.rec()["session"], "S-old")

    def test_unmarked_session_moves_nothing(self):
        orchestrator.mark(self.project, "S-orch", "o-1", own_session="S-orch")
        self.start("S-new", "S-other")
        self.assertEqual(self.rec()["session"], "S-orch")
        self.assertIsNone(orchestrator.carry(self.project, "S-orch", "S-orch"))
        self.assertIsNone(orchestrator.carry(self.project, "", "S-new"))
        self.assertNotIn("mark moved", self.notes_text())

    def test_marked_by_hand_moves_as_it_was(self):
        orchestrator.mark(self.project, "S-old", "o-1")  # no process recorded
        self.ps_rows = None  # no ps needed: there is no pid to check
        self.start("S-new", "S-old")
        r = self.rec()
        self.assertEqual((r["session"], r["pid"], r["process"]), ("S-new", None, "marked by hand"))
