"""The loop pass, the CLI and the record of `hook mod start` (also after a /reload-plugins), against a session's
own foreman.

Fake plugin roots under the test's temporary directory (cache/m/foreman/<version>/), installs.json written by hand,
heartbeats of foreman's hooks module written by hand.
Nothing reads or writes the real ~/.claude or ~/.local/state/foreman.
"""

import contextlib
import io
import json
import os
import tempfile
import time
from unittest import mock

from test_install import fake_root
from test_loop_notices import NoticesCase
from test_versions import stamp

from foreman import cli, heartbeat, hooks_mod, install, loop, notes, util, versions

ASK = "Tell Owner, in plain words, to type /reload-plugins in this chat"  # foreman_behind: the host loads nothing


class VersionPassCase(NoticesCase):
    grace = 0  # tests past the grace after an install (versions.GRACE_S); GraceTest keeps the real one

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(versions, "GRACE_S", self.grace)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cache = self.tmp / "cache/m/foreman"
        self.old, self.new = self.root("0.1.0"), self.root("0.2.0")
        self.cli("session", "S1", "--name", "fake-1")

    def root(self, version, **kw):
        return fake_root(self.cache / version, version, **kw)

    def env_for(self, root):
        return {"CLAUDE_PLUGIN_ROOT": str(root), "CLAUDE_PLUGIN_DATA": str(self.tmp / "data/foreman-m")}

    def runs(self, root):
        """This process plays a hook of the orchestrator's session running `root` (None: the CLI)."""
        for k, v in (self.env_for(root) if root else {"CLAUDE_PLUGIN_ROOT": None, "CLAUDE_PLUGIN_DATA": None}).items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def installs(self, entries):
        """installs.json: {root: seen epoch}."""
        install.install_dir().mkdir(parents=True, exist_ok=True)
        install.installs_file().write_text(
            json.dumps(
                {
                    str(r): {"version": install.root_version(r), "seen": stamp(t), "last": stamp(t)}
                    for r, t in entries.items()
                }
            )
        )

    def live_file(self, session, started=None):
        """The heartbeat foreman's hooks module writes for a live session (no foreman version in it: estimated)."""
        started = time.time() - 30 if started is None else started
        f = heartbeat.beat_file(self.project, session)
        f.parent.mkdir(parents=True, exist_ok=True)
        at = stamp(time.time())
        util.write_json(f, {"at": at, "started": stamp(started), "busy": False, "cwd": str(self.home)})
        return started

    def free_agents(self):
        f = loop.agents_file(self.project)
        d = util.read_json(f, {})
        for h in d.values():
            h["stopped"] = True
        util.write_json(f, d)

    def text_of(self, kind, session="S1"):
        return next(n["text"] for n in loop.notices_for_pass(self.project, session, mark=False) if n["kind"] == kind)


class OwnSessionTest(VersionPassCase):
    def test_asks_the_owner_once_per_version(self):
        """A host that does not load it by itself (a terminal, `-p`, an SDK host): the pass asks the orchestrator
        once to tell the owner, in plain words, to type /reload-plugins; live agents or other notices do not change
        that, and it never mentions a handover."""
        self.installs({self.old: time.time() - 7200, self.new: time.time()})
        self.runs(self.new)
        self.assertNotIn("foreman_behind", self.kinds("S1"))  # current: nothing
        self.runs(self.old)
        self.agent("a1", "zed7", session="S1")  # a live agent of this session
        notes.inbox_add(self.project, "hello", sender="owner")
        self.assertEqual(self.kinds("S1", mark=False)["foreman_behind"], [True])  # looking does not use it up
        k = self.kinds("S1")
        self.assertEqual((k["foreman_behind"], k["inbox"]), ([True], [True]))
        text = self.text_of("foreman_behind")
        self.assertIn(f"foreman 0.1.0 in this session, 0.2.0 installed: it does not load here by itself. {ASK}", text)
        self.assertNotIn("hand over", text)
        self.assertNotIn("handover", text)
        for word in ("Remote Control", "phone", "refused"):  # /reload-plugins works there too
            self.assertNotIn(word, text)
        self.assertEqual(self.kinds("S1")["foreman_behind"], [False])  # said once
        self.free_agents()
        notes.inbox_read(self.project, [])
        self.assertEqual(self.kinds("S1")["foreman_behind"], [False])  # nothing new to say
        c = self.root("0.3.0")  # a newer install is a new case
        self.installs({self.old: time.time() - 7200, self.new: time.time() - 60, c: time.time()})
        self.assertEqual(self.kinds("S1")["foreman_behind"], [True])
        self.assertIn("0.3.0 installed", self.text_of("foreman_behind"))
        # after /reload-plugins (the root is the installed one): gone, and the case is cleared
        self.runs(c)
        self.assertNotIn("foreman_behind", self.kinds("S1"))
        self.assertFalse((self.project.state / "foreman-behind-notified").exists())

    def test_production_goes_on_while_the_owner_is_asked(self):
        self.installs({self.old: time.time() - 7200, self.new: time.time()})
        self.issue("AB-1", "ready")
        self.runs(self.old)
        k = self.kinds("S1")
        self.assertEqual((k["wip_production"], k["foreman_behind"]), ([True], [True]))
        self.assertIn("launch the next `ready` issue", self.text_of("wip_production"))

    def test_the_desktop_app_loads_it_by_itself(self):
        """Past the grace, a session whose host loads a new version by itself (`entrypoint` claude-desktop):
        information, nothing to ask the owner, production goes on. Another host (sdk-ts) is asked."""
        self.installs({self.old: time.time() - 7200, self.new: time.time()})
        self.issue("AB-1", "ready")
        me = versions.own(self.env_for(self.old))
        versions.record_session("S1", me, "fake", str(self.home), entrypoint="claude-desktop")
        k = self.kinds("S1")
        self.assertEqual((k["foreman_behind"], k["wip_production"]), ([False], [True]))
        text = self.text_of("foreman_behind")
        self.assertIn("the desktop app loads it into this session by itself", text)
        self.assertNotIn("/reload-plugins", text)
        self.assertTrue(loop.reloads_itself("S1"))
        versions.record_session("S1", me, "fake", str(self.home), entrypoint="sdk-ts")
        self.assertFalse(loop.reloads_itself("S1"))
        self.assertEqual(self.kinds("S1")["foreman_behind"], [True])
        self.assertIn(ASK, self.text_of("foreman_behind"))
        self.assertFalse(loop.reloads_itself(None))

    def test_an_estimate_only_informs(self):
        started = self.live_file("S1")
        self.installs({self.old: started - 3600, self.new: started + 60})
        self.issue("AB-1", "ready")
        self.runs(None)  # `foreman loop` from a shell: no record of S1, so its estimate
        k = self.kinds("S1")
        self.assertEqual((k["foreman_behind"], k["wip_production"]), ([False], [True]))
        self.assertIn("foreman ≈0.1.0 in this session, 0.2.0 installed (estimated", self.text_of("foreman_behind"))
        self.assertIn("Owner can type /reload-plugins here", self.text_of("foreman_behind"))
        # a record wins over the estimate (and a record behind decides)
        versions.record_session("S1", versions.own(self.env_for(self.old)), "fake", str(self.home))
        k = self.kinds("S1")
        self.assertEqual((k["foreman_behind"], k["wip_production"]), ([True], [True]))
        self.assertIn("foreman 0.1.0 in this session", self.text_of("foreman_behind"))

    def test_the_pass_says_it_once_then_the_idle_line(self):
        self.installs({self.old: time.time() - 7200, self.new: time.time() - 600})  # the tick runs the real grace
        self.runs(self.old)
        self.old_beat()
        self.assertIn(ASK, self.tick()["submit"])  # the first pass reaches the model
        self.old_beat()
        log = self.tick()["log"]  # then information: the pass stays on screen as one line
        self.assertEqual(log.count("foreman 0.1.0 in this session"), 1, log)


class OtherSessionsTest(VersionPassCase):
    def test_once_per_set(self):
        started = self.live_file("S1")
        self.live_file("S2")
        self.live_file("S3")
        self.installs({self.old: started - 3600, self.new: started + 60})
        self.runs(self.new)
        versions.record_session("S2", versions.own(self.env_for(self.old)), "fake", str(self.home))
        k = self.kinds("S1")
        self.assertEqual(k["sessions_behind"], [True])
        self.assertNotIn("foreman_behind", k)  # the orchestrator itself is current
        text = self.text_of("sessions_behind")
        for part in ("0.2.0 installed", "S2 0.1.0", "S3 ≈0.1.0", "estimated", "/reload-plugins"):
            self.assertIn(part, text)  # sessions with no name for foreman: their short id
        self.assertNotIn("S1", text)
        self.assertEqual(self.kinds("S1")["sessions_behind"], [False])  # once per set
        versions.record_session("S2", versions.own(self.env_for(self.new)), "fake", str(self.home))  # reloaded
        self.assertEqual(self.kinds("S1")["sessions_behind"], [True])  # another set
        self.assertNotIn("S2", self.text_of("sessions_behind"))
        versions.record_session("S3", versions.own(self.env_for(self.new)), "fake", str(self.home))
        self.assertNotIn("sessions_behind", self.kinds("S1"))
        self.assertFalse((self.project.state / "sessions-behind-notified").exists())


class GraceTest(VersionPassCase):
    """The grace after an install: the pass asks for /reload-plugins (foreman_behind, sessions_behind) only for a
    session still on the older foreman GRACE_S after the install appeared; one that loads it by itself (the desktop
    app) never counts."""

    grace = versions.GRACE_S

    def test_other_sessions(self):
        started = self.live_file("S1", started=time.time() - 3600)
        self.live_file("S2", started=started)
        self.live_file("S3", started=started)
        self.installs({self.old: started - 3600, self.new: time.time()})  # 0.2.0 installed just now
        self.runs(self.new)
        for sid in ("S2", "S3"):
            versions.record_session(sid, versions.own(self.env_for(self.old)), "fake", str(self.home))
        self.assertNotIn("sessions_behind", self.kinds("S1"))
        live = {e["session"]: e for e in versions.live_sessions()}
        self.assertEqual((live["S2"]["behind"], live["S2"]["settling"]), (False, True))
        out = self.cli("session", "--all").stdout
        self.assertIn("settling", out)
        self.assertNotIn("BEHIND", out)
        d = json.loads(self.cli("session", "--all", "--behind", "--json").stdout)  # on an older one, settling: listed
        self.assertEqual(
            [(e["session"], e["behind"], e["settling"]) for e in d["sessions"]],
            [("S2", False, True), ("S3", False, True), ("S1", False, True)],  # S1: the orchestrator, ≈0.1.0
        )
        self.assertIn("settling", self.cli("session", "--all", "--behind").stdout)
        # S2 loads 0.2.0 by itself; S3 stays on 0.1.0 past the grace: only S3 is asked for
        versions.record_session("S2", versions.own(self.env_for(self.new)), "fake", str(self.home))
        self.installs({self.old: started - 3600, self.new: time.time() - versions.GRACE_S - 5})
        k = self.kinds("S1")
        self.assertEqual(k["sessions_behind"], [True])
        text = self.text_of("sessions_behind")
        self.assertIn("S3 0.1.0", text)
        self.assertNotIn("S2", text)
        self.assertIn(" S3 ", self.cli("session", "--all", "--behind").stdout)

    def test_own_session(self):
        self.installs({self.old: time.time() - 7200, self.new: time.time()})
        self.runs(self.old)
        self.assertNotIn("foreman_behind", self.kinds("S1"))  # just installed: it may load it by itself
        self.runs(self.new)  # it did
        self.installs({self.old: time.time() - 7200, self.new: time.time() - 3600})
        self.assertNotIn("foreman_behind", self.kinds("S1"))
        self.runs(self.old)  # a session still on 0.1.0 an hour after the install: asked as before
        self.assertIn("foreman_behind", self.kinds("S1"))
        self.assertIn("/reload-plugins", self.text_of("foreman_behind"))

    def test_all_from_a_replaced_folder_just_after_the_install(self):
        """`session --all --behind` run from the session's own replaced folder (its PATH) a minute after the install,
        before any session recorded the new one (installs.json knows only the old root): the installed foreman is the
        newer sibling, not «not known», and the session on the old one is listed as settling, never «no live session
        runs an older foreman» (seen on 2026-11-02 with 0.53.0 → 0.54.0)."""
        started = self.live_file("S1", started=time.time() - 3600)
        self.installs({self.old: started - 3600})
        versions.record_session("S1", versions.own(self.env_for(self.old)), "fake", str(self.home))
        (self.old / ".orphaned_at").write_text("1")  # replaced just now
        saved = install.ROOT
        install.ROOT = self.old
        try:
            for behind in (False, True):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    self.assertEqual(cli.sessions_command(behind), 0)
                lines = out.getvalue().splitlines()
                self.assertEqual(lines[0], f"installed foreman 0.2.0 ({self.new})", behind)
                self.assertEqual(lines[1].split()[:4], ["fake", "fake-1", "0.1.0", "settling"], behind)
                self.assertNotIn("no live session", out.getvalue())
        finally:
            install.ROOT = saved

    def test_orphan_line(self):
        (self.old / ".orphaned_at").write_text("1")  # replaced just now
        self.assertIsNone(cli.orphan_line(["progress", "x"], root=self.old))
        self.assertIn("/reload-plugins", cli.orphan_line(["progress", "x"], root=self.old, now=time.time() + 600))


class CliTest(VersionPassCase):
    def test_session_line_and_sessions(self):
        started = self.live_file("S1", started=time.time() - 3600)  # the CLI runs the real grace: an old install
        self.live_file("S2", started=started)
        self.installs({self.old: started - 3600, self.new: started + 60})
        out = self.cli("session").stdout
        self.assertIn("foreman ≈0.1.0 in that session (estimated: no record of its own), 0.2.0 installed: behind", out)
        versions.record_session("S1", versions.own(self.env_for(self.new)), "fake", str(self.home))
        r = self.cli("session")
        self.assertEqual(r.returncode, 0)
        self.assertIn("foreman 0.2.0 in that session, the installed one", r.stdout)
        out = self.cli("session", "--all").stdout.splitlines()
        self.assertEqual(out[0], f"installed foreman 0.2.0 ({self.new})")
        rows = {ln.split()[1]: ln.split() for ln in out[1:3]}
        self.assertEqual(rows["fake-1"][:4], ["fake", "fake-1", "0.2.0", "orchestrator"])
        self.assertEqual(rows["S2"][:4], ["fake", "S2", "≈0.1.0", "BEHIND"])
        self.assertTrue(any(ln.startswith("≈: estimated") for ln in out))
        out = self.cli("session", "--all", "--behind").stdout
        self.assertIn(" S2 ", out)
        self.assertNotIn("fake-1", out)
        d = json.loads(self.cli("session", "--all", "--behind", "--json").stdout)
        self.assertEqual(d["installed"], {"version": "0.2.0", "root": str(self.new)})
        self.assertEqual(
            [(e["name"], e["version"], e["estimated"], e["behind"]) for e in d["sessions"]],
            [("", "0.1.0", True, True)],
        )
        versions.record_session("S2", versions.own(self.env_for(self.new)), "fake", str(self.home))
        r = self.cli("session", "--all", "--behind")
        self.assertEqual((r.returncode, r.stdout.splitlines()[-1]), (0, "no live session runs an older foreman"))

    def test_acts(self):
        for argv, want in (
            (["lock", "gpu", "run", "x", "true"], True),
            (["integrate", "app", "b", "X-1"], True),
            (["progress", "--done", "x"], True),
            (["backlog", "add", "E1", "t"], True),
            (["backlog", "show", "X-1"], False),
            (["backlog", "show", "--json"], False),
            (["session"], False),
            (["session", "S1"], True),
            (["worktree", "--list"], False),
            (["inbox", "new"], False),
            (["inbox", "read"], True),
            (["agents"], False),
            (["loop"], False),
            (["hook", "loop"], False),
            (["panel"], False),
            (["--version"], False),
            (["session", "--all"], False),
            (["recurring"], False),
            (["recurring", "done", "loop"], True),
            (["permissions", "grants"], False),
            (["permissions", "grants", "off"], True),
            (["check"], True),
            (["note", "-h"], False),
            ([], False),
        ):
            self.assertEqual(cli.acts(argv), want, argv)

    def test_orphan_line(self):
        self.assertIsNone(cli.orphan_line(["progress", "x"], root=self.old))  # not orphaned: one stat, nothing
        (self.old / ".orphaned_at").write_text("1")
        line = cli.orphan_line(["progress", "x"], root=self.old)
        self.assertIn("foreman 0.1.0", line)
        self.assertIn("replaced by 0.2.0", line)  # the sibling: the trace does not know it yet
        self.assertIn("/reload-plugins", line)
        self.assertIsNone(cli.orphan_line(["loop", "--json"], root=self.old))
        self.assertIsNone(cli.orphan_line(["backlog", "show"], root=self.old))
        repo = fake_root(self.tmp / "repo", "0.0.1", git="dir", orphaned=True)
        self.assertIsNone(cli.orphan_line(["progress", "x"], root=repo))  # a working copy runs live code
        saved = install.ROOT
        install.ROOT = self.old
        try:
            for argv, want in (
                (["--project", "fake", "progress", "hi"], True),
                (["--project", "fake", "recurring"], False),
            ):
                err = io.StringIO()
                with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                    cli.main(argv)
                self.assertEqual("replaced by 0.2.0" in err.getvalue(), want, (argv, err.getvalue()))
        finally:
            install.ROOT = saved

    def test_orphan_line_once(self):
        """9 acting commands in a row from a replaced folder: 1 line (per root and version, 10 min or session)."""
        (self.old / ".orphaned_at").write_text("1")
        saved, saved_sid = install.ROOT, os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        install.ROOT = self.old
        try:
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                for i in range(9):
                    cli.main(["--project", "fake", "progress", f"step {i}"])
            self.assertEqual(err.getvalue().count("/reload-plugins"), 1, err.getvalue())
            told = util.read_json(cli.orphan_told_file(), {})
            self.assertEqual(told[str(self.old)]["version"], "0.1.0")
            argv, root = ["progress", "x"], self.old
            later = time.time() + cli.ORPHAN_TOLD_SECONDS + 1
            self.assertIsNotNone(cli.orphan_line(argv, root, once=True, env={}, now=later))  # 10 min later: again
            self.assertIsNone(cli.orphan_line(argv, root, once=True, env={}, now=later + 1))
            a, b = {"CLAUDE_CODE_SESSION_ID": "SA"}, {"CLAUDE_CODE_SESSION_ID": "SB"}
            self.assertIsNotNone(cli.orphan_line(argv, root, once=True, env=a, now=later + 2))  # each session once
            self.assertIsNotNone(cli.orphan_line(argv, root, once=True, env=b, now=later + 3))
            self.assertIsNone(cli.orphan_line(argv, root, once=True, env=a, now=later + 9999))  # same session: never
            self.assertIsNotNone(cli.orphan_line(argv, root))  # without `once`, always
        finally:
            install.ROOT = saved
            if saved_sid is not None:
                os.environ["CLAUDE_CODE_SESSION_ID"] = saved_sid


class ModStartTest(VersionPassCase):
    """`hook mod start` (the hooks module's session.start, again after /reload-plugins) records what the session
    runs: no transcript is read to guess a /reload-plugins."""

    def start(self, session, root, claude_code="2.1.314", cwd=None):
        return hooks_mod.start(
            {"cwd": str(cwd or self.home), "session": session, "claude_code": claude_code, "root": str(root)}
        )

    def test_a_reload_is_recorded_not_estimated(self):
        started = self.live_file("S2")
        self.installs({self.old: started - 3600, self.new: started + 1})
        got = {e["session"]: e for e in versions.live_sessions()}["S2"]
        self.assertEqual((versions.label(got), got["behind"]), ("≈0.1.0", True))
        self.assertNotIn("reloaded", got)
        self.assertEqual(self.start("S2", self.new), {"active": True, "below": False, "min": versions.CLAUDE_CODE_MIN})
        got = {e["session"]: e for e in versions.live_sessions()}["S2"]
        self.assertEqual((versions.label(got), got["behind"], got["estimated"]), ("0.2.0", False, False))
        rec = versions.read_sessions()["S2"]
        self.assertEqual((rec["root"], rec["kind"], rec["claude_code"]), (str(self.new), "installed", "2.1.314"))
        # a classic hook's record later keeps the Claude Code version the module wrote
        versions.record_session("S2", versions.own(self.env_for(self.new)), "fake", str(self.home))
        self.assertEqual(versions.read_sessions()["S2"]["claude_code"], "2.1.314")

    def test_below_the_minimum_and_outside(self):
        got = self.start("S3", self.new, claude_code="2.1.200")
        self.assertEqual((got["active"], got["below"]), (True, True))
        self.assertIn(versions.CLAUDE_CODE_MIN, got["status"])
        self.assertIn("2.1.200", got["status"])
        with tempfile.TemporaryDirectory() as elsewhere:
            self.assertEqual(self.start("S4", self.new, cwd=elsewhere), {"active": False})
        self.assertNotIn("S4", versions.read_sessions())

    def test_an_inline_session_stays_inline(self):
        me = {"version": "0.2.0", "root": str(self.new), "kind": "inline"}
        versions.record_session("S5", me, "fake", str(self.home))
        self.start("S5", self.new)
        self.assertEqual(versions.read_sessions()["S5"]["kind"], "inline")


class TerminalSessionTest(VersionPassCase):
    """A marked orchestrator's session in a terminal (its module's start said `interactive`) behind the installed
    foreman: foreman loads nothing by itself there, so it is told like any other session. The pass asks
    once for /reload-plugins (foreman_behind), production does not wait for anything, a tick never answers a reload
    and the start says nothing of one. An old `[loop] reload` still loads, with a warning."""

    def setUp(self):
        super().setUp()
        self.installs({self.old: time.time() - 7200, self.new: time.time()})
        self.runs(None)  # the module's process: no CLAUDE_PLUGIN_ROOT, its record decides
        notes.beat(self.project, "loop")  # a pass just ran: no tick runs one

    def start(self, root, interactive=True, session="S1"):
        ev = {"cwd": str(self.home), "session": session, "claude_code": "2.1.314", "root": str(root)}
        if interactive is not None:
            ev["interactive"] = interactive
        return hooks_mod.start(ev)

    def tick(self, session="S1", **ev):
        return hooks_mod.tick({"cwd": str(self.home), "session": session, "busy": False, "agents": [], **ev})

    def test_asked_once_like_any_other_session(self):
        self.issue("AB-1", "ready")
        self.assertEqual(self.start(self.old), {"active": True, "below": False, "min": versions.CLAUDE_CODE_MIN})
        self.assertTrue(heartbeat.read(self.project, "S1")["interactive"])  # still recorded: information only
        got = self.tick()
        self.assertNotIn("reload", got)
        self.assertEqual((got["marked"], got["due"]), (True, False))
        k = self.kinds("S1")
        self.assertEqual((k["foreman_behind"], k["wip_production"]), ([True], [True]))
        text = self.text_of("foreman_behind")
        self.assertIn(f"foreman 0.1.0 in this session, 0.2.0 installed: it does not load here by itself. {ASK}", text)
        self.assertIn("launch the next `ready` issue", self.text_of("wip_production"))
        self.assertEqual(self.kinds("S1")["foreman_behind"], [False])  # said once
        self.assertFalse((self.project.state / "reloads.jsonl").exists())
        # the same with no live agent, between ticks and once the turn ends: never a reload
        for ev in ({}, {"busy": True}, {"agents": [{"id": "a1", "status": "completed"}]}):
            self.assertNotIn("reload", self.tick(**ev))
        # the same as a session whose start said nothing of `interactive`, or false (-p, an SDK host)
        for interactive in (False, None):
            self.start(self.old, interactive=interactive)
            self.assertNotIn("reload", self.tick())
            self.assertIn(ASK, self.text_of("foreman_behind"))

    def test_the_marked_session_gets_the_version_notice(self):
        (self.old / ".orphaned_at").write_text("1")
        self.start(self.old)
        me = {"version": "0.1.0", "root": str(self.old), "kind": "installed"}
        with mock.patch.object(versions, "own", lambda *a, **k: dict(me)):
            told = hooks_mod.version_told(self.project, "S1", str(self.home))
        self.assertIsNotNone(told)
        self.assertIn("/reload-plugins", told[1])

    def test_an_old_loop_reload_is_a_warning(self):
        for value in ("false", "true", '"yes"'):
            with self.subTest(value=value):
                self.config(loop_extra=f"reload = {value}")
                self.assertTrue(
                    any("[loop] reload is no longer used" in w for w in self.project.warnings), self.project.warnings
                )
                self.start(self.old)
                self.assertNotIn("reload", self.tick())
                self.assertIn(ASK, self.text_of("foreman_behind"))
        self.config()
        self.assertEqual(self.project.warnings, [])
