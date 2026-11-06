"""A session's own foreman against the installed one (lib/foreman/versions.py and its hook notices).

Fake plugin roots under the test's temporary directory (cache/m/foreman/<version>/ with a manifest and bin/foreman),
`.orphaned_at` written by the test, installs.json written by hand, heartbeats of foreman's hooks module written by hand.
Nothing reads or writes the real ~/.claude or ~/.local/state/foreman.
"""

import json
import os
import time
from datetime import datetime
from unittest import mock

from helpers import ProjectCase
from test_install import fake_root

from foreman import config, heartbeat, install, util, versions


def stamp(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t))


class VersionsCase(ProjectCase):
    grace = 0  # tests past the grace after an install (versions.GRACE_S); GraceTest keeps the real one

    def setUp(self):
        super().setUp()
        self.cache = self.tmp / "cache/m/foreman"
        patcher = mock.patch.object(versions, "GRACE_S", self.grace)
        patcher.start()
        self.addCleanup(patcher.stop)

    def root(self, version, **kw):
        return self.aged(fake_root(self.cache / version, version, **kw))

    def aged(self, root, ago=3600):
        """`root` with its `.orphaned_at` mark (if any) `ago` seconds old: the hooks this test runs as processes
        keep the real grace (versions.GRACE_S), so a replaced install is an old one unless a test says otherwise."""
        mark = root / ".orphaned_at"
        if mark.exists():
            t = time.time() - ago
            os.utime(mark, (t, t))
        return root

    def write_installs(self, *roots, seen=None):
        """installs.json with `roots` first seen at `seen` (default an hour ago: past the grace, see aged())."""
        install.install_dir().mkdir(parents=True, exist_ok=True)
        t = stamp(seen or time.time() - 3600)
        install.installs_file().write_text(
            json.dumps({str(r): {"version": install.root_version(r), "seen": t, "last": t} for r in roots})
        )

    def event(self, event, session="S2", **kw):
        return {"cwd": str(self.home), "hook_event_name": event, "session_id": session, **kw}

    def run_hook(self, name, ev, root, data="foreman-m"):
        r = self.cli("hook", name, inp=json.dumps(ev), env=self.env_for(root, data))
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        out = r.stdout.strip()
        if out:
            self.assertEqual(len(out.splitlines()), 1, out)  # one JSON object, never two
        return json.loads(out) if out else {}

    def who(self, root, session="S2", **kw):
        return self.run_hook("who", self.event("SessionStart", session, source="startup"), root, **kw)

    def prompt(self, root, session="S2", text="hello", **kw):
        return self.run_hook("loop", self.event("UserPromptSubmit", session, prompt=text), root, **kw)

    def tick(self, root, session="S2"):
        """What the hooks module's tick (`foreman hook mod tick`, run from `root`) answers for `session`."""
        ev = {"cwd": str(self.home), "session": session}
        r = self.cli("hook", "mod", "tick", inp=json.dumps(ev), env=self.env_for(root))
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        return json.loads(r.stdout)

    @staticmethod
    def ctx(out):
        return (out.get("hookSpecificOutput") or {}).get("additionalContext", "")


class CoreTest(VersionsCase):
    def test_own_kinds(self):
        a = self.root("0.1.0")
        self.assertEqual(versions.own(self.env_for(a)), {"version": "0.1.0", "root": str(a), "kind": "installed"})
        self.assertEqual(versions.own(self.env_for(a, "foreman-inline"))["kind"], "inline")
        repo = fake_root(self.tmp / "repo", "0.3.0", git="dir")
        self.assertEqual(versions.own(self.env_for(repo))["kind"], "working_copy")
        self.assertEqual(versions.own(self.env_for(self.tmp))["kind"], "none")
        cli = versions.own({})  # the CLI: the folder this code runs from
        self.assertEqual(cli["root"], str(install.ROOT))

    def test_installed_and_behind(self):
        a, b = self.root("0.1.0"), self.root("0.2.0")
        me = versions.own(self.env_for(a))
        self.assertIsNone(versions.installed(me))  # no trace, root not orphaned: nothing known
        self.write_installs(a, b)
        self.assertEqual(versions.installed(me), {"version": "0.2.0", "root": str(b)})
        self.assertEqual(
            versions.behind(me, versions.installed(me)),
            {"mine": "0.1.0", "root": str(a), "installed": "0.2.0", "installed_root": str(b), "older": True},
        )
        self.assertIsNone(versions.behind(versions.own(self.env_for(b)), versions.installed()))
        # orphaned with a newer sibling the trace does not know yet
        self.write_installs(a)
        (a / ".orphaned_at").write_text("1")
        c = self.root("0.3.0")
        self.root("0.4.0", orphaned=True)  # orphaned siblings do not count
        fake_root(self.cache / "0.9.0", "0.9.0", git="dir")  # nor working copies
        self.assertEqual(versions.installed(me), {"version": "0.3.0", "root": str(c)})
        # orphaned and no sibling: replaced, version not known
        for v in ("0.2.0", "0.3.0", "0.4.0", "0.9.0"):
            import shutil

            shutil.rmtree(self.cache / v)
        self.assertIsNone(versions.installed(me))
        bb = versions.behind(me, None)
        self.assertEqual((bb["installed"], bb["older"], versions.told_key(bb)), (None, False, "?"))
        # a downgrade: orphaned, the installed one lower
        low = self.root("0.0.9")
        self.assertEqual(versions.behind(me, versions.installed(me))["installed"], "0.0.9")
        self.assertFalse(versions.behind(me, versions.installed(me))["older"])
        # never for a working copy or an inline plugin
        self.assertIsNone(
            versions.behind(
                {"version": "0.0.1", "root": str(low), "kind": "working_copy"}, {"version": "9.0.0", "root": "/x"}
            )
        )

    def test_record_session_writes_only_on_change_and_prunes(self):
        a = self.root("0.1.0")
        me = versions.own(self.env_for(a))
        now = time.time()
        rec = versions.record_session("S1", me, "fake", str(self.home), now=now)
        self.assertEqual(
            {k: rec[k] for k in ("version", "root", "kind", "project", "cwd", "told")},
            {
                "version": "0.1.0",
                "root": str(a),
                "kind": "installed",
                "project": "fake",
                "cwd": str(self.home),
                "told": None,
            },
        )
        f = versions.sessions_file()
        sig = (f.stat().st_mtime_ns, f.stat().st_ino)
        time.sleep(0.01)
        versions.record_session("S1", me, "fake", str(self.home), now=now + 60)
        self.assertEqual(sig, (f.stat().st_mtime_ns, f.stat().st_ino))
        versions.record_session("S1", me, "fake", str(self.home), now=now + 3700)  # `last` once an hour
        self.assertEqual(versions.read_sessions()["S1"]["last"], stamp(now + 3700))
        # /reload-plugins: a new root and version, `told` kept
        versions.mark_told("S1", "0.2.0")
        b = self.root("0.2.0")
        rec = versions.record_session("S1", versions.own(self.env_for(b)), "fake", str(self.home), now=now + 3800)
        self.assertEqual((rec["version"], rec["seen"], rec["told"]), ("0.2.0", stamp(now + 3800), "0.2.0"))
        # old records go after SESSIONS_DAYS, and at most SESSIONS_KEEP stay
        versions.record_session("OLD", me, "fake", str(self.home), now=now - 3 * 86400)
        versions.record_session("S3", me, "fake", str(self.home), now=now + 3900)
        self.assertEqual(sorted(versions.read_sessions()), ["S1", "S3"])
        keep = versions.SESSIONS_KEEP
        try:
            versions.SESSIONS_KEEP = 2
            versions.record_session("S4", me, "fake", str(self.home), now=now + 4000)
        finally:
            versions.SESSIONS_KEEP = keep
        self.assertEqual(sorted(versions.read_sessions()), ["S3", "S4"])

    def test_estimate_and_label(self):
        now = time.time()
        old, new = self.root("0.1.0"), self.root("0.2.0")
        repo = fake_root(self.tmp / "repo", "0.9.0", git="dir")
        installs = {
            str(old): {"version": "0.1.0", "seen": stamp(now - 7200)},
            str(new): {"version": "0.2.0", "seen": stamp(now - 600)},
            str(repo): {"version": "0.9.0", "seen": stamp(now - 9000)},
        }
        self.assertEqual(versions.estimate(now - 3600, installs), "0.1.0")
        self.assertEqual(versions.estimate(now, installs), "0.2.0")
        self.assertIsNone(versions.estimate(now - 8000, installs))
        self.assertIsNone(versions.estimate(None, installs))
        self.assertEqual(versions.label({"version": "0.1.0", "estimated": True}), "≈0.1.0")
        self.assertEqual(versions.label({"version": "0.1.0", "estimated": False}), "0.1.0")
        self.assertEqual(versions.label({"version": None}), "?")

    def live_file(self, session, cwd=None, started=None):
        """The heartbeat foreman's hooks module writes for a live session (no foreman version in it: estimated)."""
        project = config.project_for_cwd(str(cwd or self.home), active_only=True) or self.project
        started = time.time() - 30 if started is None else started
        f = heartbeat.beat_file(project, session)
        f.parent.mkdir(parents=True, exist_ok=True)
        at = datetime.now().isoformat(timespec="seconds")
        util.write_json(f, {"at": at, "started": stamp(started), "busy": False, "cwd": str(cwd or self.home)})
        return started

    def test_live_sessions(self):
        old, new = self.root("0.1.0"), self.root("0.2.0")
        started = self.live_file("S1")
        self.live_file("S2")
        self.live_file("S4", started=started - 3600)
        f = heartbeat.beat_file(self.project, "S4")
        util.write_json(f, {**util.read_json(f, {}), "ended": stamp(started)})  # ended: left out
        install.install_dir().mkdir(parents=True, exist_ok=True)
        install.installs_file().write_text(
            json.dumps(
                {
                    str(old): {"version": "0.1.0", "seen": stamp(started - 3600), "last": stamp(started)},
                    str(new): {"version": "0.2.0", "seen": stamp(started + 60), "last": stamp(started + 60)},
                }
            )
        )
        # S1 has a record on 0.1.0 (behind); S2 has none: estimated ≈0.1.0, behind too
        versions.record_session("S1", versions.own(self.env_for(old)), "fake", str(self.home))
        self.cli("session", "S1", "--name", "fake-a")
        got = {e["session"]: e for e in versions.live_sessions()}
        self.assertEqual(sorted(got), ["S1", "S2"])
        self.assertEqual(
            (
                got["S1"]["version"],
                got["S1"]["estimated"],
                got["S1"]["behind"],
                got["S1"]["orchestrator"],
                got["S1"]["project"],
                got["S1"]["name"],
            ),
            ("0.1.0", False, True, True, "fake", "fake-a"),
        )
        self.assertEqual(
            (versions.label(got["S2"]), got["S2"]["behind"], got["S2"]["orchestrator"]), ("≈0.1.0", True, False)
        )
        # after /reload-plugins S1 records 0.2.0: not behind
        versions.record_session("S1", versions.own(self.env_for(new)), "fake", str(self.home))
        self.assertFalse({e["session"]: e for e in versions.live_sessions()}["S1"]["behind"])


class HookNoticeTest(VersionsCase):
    def test_once_per_installed_version(self):
        a, b = self.root("0.1.0"), self.root("0.2.0")
        self.write_installs(b)
        out = self.who(a)
        ctx = self.ctx(out)
        self.assertIn("no orchestrator now", ctx)  # joined to the hook's own text, one object
        for part in (
            "runs foreman 0.1.0",
            "0.2.0 is installed",
            "`/reload-plugins`",
            "only they can",
            f"{b}/CHANGELOG.md",
        ):
            self.assertIn(part, ctx)
        self.assertEqual(out["systemMessage"], "foreman 0.1.0 in this session, 0.2.0 installed: type /reload-plugins")
        rec = versions.read_sessions()["S2"]
        self.assertEqual((rec["version"], rec["told"], rec["project"]), ("0.1.0", "0.2.0", "fake"))
        # said once: not again on the next start or prompt of that session
        self.assertNotIn("systemMessage", self.who(a))
        self.assertEqual(self.prompt(a), {})
        # another session on the same old version hears it once too
        self.assertIn("runs foreman 0.1.0", self.ctx(self.prompt(a, session="S5")))
        # a newer install: once more
        c = self.root("0.3.0")
        self.write_installs(b, c)
        out = self.prompt(a)
        self.assertIn("0.3.0 is installed", self.ctx(out))
        self.assertIn("0.3.0 installed", out["systemMessage"])
        self.assertEqual(self.prompt(a), {})

    def test_nothing_when_current_working_copy_or_inline(self):
        b = self.root("0.2.0")
        self.write_installs(b)
        self.assertNotIn("systemMessage", self.who(b))
        repo = fake_root(self.tmp / "repo", "0.1.0", git="dir")
        self.assertNotIn("systemMessage", self.who(repo, session="S6"))
        self.assertEqual(self.prompt(repo, session="S6"), {})
        old = self.root("0.1.0")
        self.assertEqual(self.prompt(old, session="S7", data="foreman-inline"), {})
        self.assertEqual(versions.read_sessions()["S7"]["kind"], "inline")

    def test_orphaned_root_before_the_trace_knows_the_new_one(self):
        a = self.root("0.1.0", orphaned=True)
        self.write_installs(a)
        self.root("0.2.0")  # installed by Claude Code, no session recorded it yet
        out = self.prompt(a)
        self.assertIn("0.2.0 is installed", self.ctx(out))
        self.assertNotIn("CHANGELOG.md of the new install", self.ctx(out))
        # no sibling at all: replaced, version not known
        z = self.aged(fake_root(self.tmp / "other/m/foreman/0.1.0", "0.1.0", orphaned=True))
        out = self.prompt(z, session="S8")
        self.assertIn("replaced by a newer one", self.ctx(out))
        self.assertEqual(
            out["systemMessage"], "foreman 0.1.0 in this session was replaced by a newer install: type /reload-plugins"
        )
        self.assertEqual(versions.read_sessions()["S8"]["told"], "?")

    def test_the_tick_only_stats_the_root(self):
        """A session with no other prompt (a non-interactive session) hears it from the module's tick, once; the classic
        hook leaves tool calls alone."""
        a = self.root("0.1.0")
        self.write_installs(self.root("0.2.0"))
        # not orphaned: a tick reads nothing else and records nothing, even though 0.1.0 is behind
        self.assertEqual(self.tick(a), {"marked": False, "grants": False})
        self.assertFalse(versions.sessions_file().exists())
        # orphaned: once, then nothing
        (a / ".orphaned_at").write_text("1")
        self.aged(a)
        self.assertEqual(self.run_hook("loop", self.event("PostToolUse", "S2", tool_name="Bash"), a), {})
        out = self.tick(a)
        self.assertEqual(set(out), {"marked", "grants", "version", "version_owner"})
        self.assertIn("runs foreman 0.1.0", out["version"])
        self.assertIn("/reload-plugins", out["version_owner"])
        self.assertEqual(self.tick(a), {"marked": False, "grants": False})
        self.assertEqual(versions.read_sessions()["S2"]["told"], "0.2.0")

    def test_subagents_get_nothing_and_are_not_recorded(self):
        a = self.root("0.1.0")
        self.write_installs(self.root("0.2.0"))
        ev = self.event("UserPromptSubmit", "S2", prompt="hello", agent_id="sub1")
        self.assertEqual(self.run_hook("loop", ev, a), {})
        self.assertNotIn("S2", versions.read_sessions())


class GraceTest(VersionsCase):
    """The grace after an install: a session on an older foreman is asked for /reload-plugins only once it is still
    on it GRACE_S after the install appeared; a host that loads the new version by itself (the desktop app) is never
    asked. The host (CLAUDE_CODE_ENTRYPOINT) is recorded as information only."""

    grace = versions.GRACE_S

    def test_a_session_that_catches_up_by_itself_is_never_asked(self):
        os.environ["CLAUDE_CODE_ENTRYPOINT"] = "claude-desktop"
        a, b = self.root("0.1.0"), self.root("0.2.0")
        self.write_installs(b, seen=time.time())  # just installed
        self.assertNotIn("systemMessage", self.who(a))
        self.assertEqual(self.prompt(a), {})  # still within the grace: nothing yet
        rec = versions.read_sessions()["S2"]
        self.assertEqual((rec["version"], rec["told"], rec["entrypoint"]), ("0.1.0", None, "claude-desktop"))
        self.assertEqual(self.prompt(b), {})  # the host loaded 0.2.0 by itself
        self.write_installs(b, seen=time.time() - 3600)  # long after the install: still nothing to ask
        self.assertEqual(self.prompt(b), {})
        self.assertEqual(versions.read_sessions()["S2"]["version"], "0.2.0")
        self.assertIsNone(versions.read_sessions()["S2"]["told"])

    def test_a_session_still_behind_after_the_grace_is_asked(self):
        os.environ["CLAUDE_CODE_ENTRYPOINT"] = "cli"
        a, b = self.root("0.1.0"), self.root("0.2.0")
        self.write_installs(b, seen=time.time())
        self.assertNotIn("systemMessage", self.who(a))  # the host does not decide: the grace holds for every one
        self.write_installs(b, seen=time.time() - versions.GRACE_S - 5)
        out = self.prompt(a)
        self.assertEqual(out["systemMessage"], "foreman 0.1.0 in this session, 0.2.0 installed: type /reload-plugins")
        rec = versions.read_sessions()["S2"]
        self.assertEqual((rec["told"], rec["entrypoint"]), ("0.2.0", "cli"))

    def test_the_tick_waits_for_the_grace_too(self):
        a = self.root("0.1.0")
        self.write_installs(self.root("0.2.0"), seen=time.time())
        (a / ".orphaned_at").write_text("1")  # replaced just now
        self.assertEqual(self.tick(a), {"marked": False, "grants": False})
        self.assertIsNone(versions.read_sessions()["S2"]["told"])  # not told: a later tick will
        self.aged(a)  # the mark is now older than the grace (the trace is not: the earliest of the two counts)
        out = self.tick(a)
        self.assertIn("/reload-plugins", out["version_owner"])

    def test_settling_and_appeared(self):
        a, b = self.root("0.1.0", orphaned=True), self.root("0.2.0")
        self.write_installs(b, seen=time.time() - 60)
        t = versions.appeared(str(b), str(a))
        self.assertAlmostEqual(t, time.time() - 3600, delta=5)  # the earlier of the mark and the trace
        bh = {"root": str(a), "installed_root": str(b)}
        self.assertFalse(versions.settling(bh, time.time()))
        self.assertTrue(versions.settling(bh, t + versions.GRACE_S - 1))
        self.assertFalse(versions.settling(None, time.time()))
        self.assertFalse(versions.settling(bh, None))  # not observed: counted as behind, as before
        self.assertFalse(versions.settling({"root": None, "installed_root": "/nowhere"}, time.time()))  # unknown
        self.assertIsNone(versions.appeared(None, None))


class ClaudeMinimumTest(VersionsCase):
    """The `who` gate on the minimum Claude Code version (versions.CLAUDE_CODE_MIN)."""

    def who(self, version, session="S9"):
        env = {"CLAUDE_CODE_EXECPATH": self.fake_claude(version)}
        ev = {"hook_event_name": "SessionStart", "session_id": session, "source": "startup", "cwd": str(self.home)}
        r = self.cli("hook", "who", inp=json.dumps(ev), cwd=self.home, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout) if r.stdout.strip() else {}

    def says_min(self, out):
        return versions.CLAUDE_CODE_MIN in json.dumps(out)

    def test_at_or_above_the_minimum_says_nothing(self):
        out = self.who("2.1.300 (Claude Code)")
        self.assertFalse(self.says_min(out), out)
        self.assertNotIn("systemMessage", out)

    def test_below_says_one_line_to_each(self):
        out = self.who("2.1.200 (Claude Code)")
        self.assertIn("claude update", out["systemMessage"])
        self.assertIn("2.1.200", out["systemMessage"])
        self.assertEqual(out["systemMessage"].count("claude update"), 1)
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(ctx.count("claude update"), 1)
        self.assertIn(versions.CLAUDE_CODE_MIN, ctx)

    def test_garbage_says_nothing(self):
        for text in ("garbage", "", "claude code"):
            self.assertFalse(self.says_min(self.who(text)), text)

    def test_missing_binary_says_nothing(self):
        ev = {"hook_event_name": "SessionStart", "session_id": "S9", "source": "startup", "cwd": str(self.home)}
        r = self.cli(
            "hook", "who", inp=json.dumps(ev), cwd=self.home,
            env={"CLAUDE_CODE_EXECPATH": str(self.tmp / "nowhere"), "PATH": str(self.tmp / "empty")},
        )  # fmt: skip
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("claude update", r.stdout)

    def test_once_per_session(self):
        first = self.who("2.1.200 (Claude Code)", session="A")
        self.assertTrue(self.says_min(first))
        self.assertFalse(self.says_min(self.who("2.1.200 (Claude Code)", session="A")))
        self.assertTrue(self.says_min(self.who("2.1.200 (Claude Code)", session="B")))

    def test_the_text_follows_the_project_language(self):
        self.write_toml(self.toml.replace('language = "en"', 'language = "es"'))
        out = self.who("2.1.200 (Claude Code)")
        self.assertIn("necesita", out["systemMessage"])

    def test_inert_outside_registered_projects(self):
        ev = {"hook_event_name": "SessionStart", "session_id": "S9", "source": "startup", "cwd": str(self.tmp)}
        r = self.cli(
            "hook", "who", inp=json.dumps(ev), cwd=self.tmp, env={"CLAUDE_CODE_EXECPATH": self.fake_claude("2.1.1")}
        )
        self.assertEqual(r.stdout.strip(), "")

    def test_check_prints_the_installed_version(self):
        r = self.cli("check", env={"CLAUDE_CODE_EXECPATH": self.fake_claude("2.1.200 (Claude Code)")})
        self.assertIn(f"claude code 2.1.200 (foreman needs >= {versions.CLAUDE_CODE_MIN}): below", r.stdout)
        r = self.cli("check")
        self.assertIn("claude code 2.1.300 (foreman needs >= 2.1.299): ok", r.stdout)
