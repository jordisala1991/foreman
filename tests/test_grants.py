"""`[deny.grants]`: the commands only the marked orchestrator's main thread runs, from its own process and with the
owner's own yes (grants.py, guard.py why=agent|session|process|grant, approved.py, hooks_mod.grant, `foreman
grants`); and the built-in refusal of `foreman hook mod` in Bash (guard.HOOK_MOD). The Python side, and the
`grant` cases of tests/mod/contract.ts it shares with the module (tests/mod/grant.test.ts): they need a simulated
`ps`, so they run here and not in the golden corpus."""

import contextlib
import io
import json
import re
import shutil
import sys
import threading
import urllib.error
import urllib.request
from datetime import timedelta
from pathlib import Path
from unittest import mock

import helpers
from helpers import FakePs, ProjectCase, config_problems

from foreman import approved, config, grants, guard, hooks_mod, loop, orchestrator, selftest, toml_reader, util

PUSH, PUB = "git " + "push", "npm " + "publish"  # built in pieces: the installed guard reads this file's commands
MARK = "OWNER" + "_OK"
# A project with [deny] patterns and no group (BASE), and with two groups (TOML).
BASE = f"""
[project]
name = "fake"
owner = "Owner"

[resources.docker]
guard = ["make up"]

[deny]
patterns = ["{PUSH}", "{PUB}"]
"""
COPY_PID = 9_005_555  # above any real pid (helpers.CLAUDE_PID)
DEPLOY = "make deploy"
STORE = "fastlane release"
BOTH = "make deploy && fastlane release"
TOML = (
    BASE
    + """grant_minutes = 30
ask_minutes = 5

[deny.grants]
deploy = ["make deploy", "prod-api"]
stores = ["fastlane release"]
"""
)
# the old key: loads as the group "orchestrator", with a warning
OLD = BASE + 'orchestrator_allow = ["make deploy", "(^|\\\\s)kamal "]\n'
HOOK_MOD = "foreman hook " + "mod"  # in pieces: the installed guard reads this file's commands
DENY_URL = "http://127.0.0.1:8766/api/grants/" + "deny"  # the refusal's endpoint (guard.PANEL_GRANT too)
APPROVE_URL = "http://127.0.0.1:8766/api/grants/" + "approve"  # the panel's endpoint (guard.PANEL_GRANT), in pieces

TABLE = f"""# expect<TAB>command
orchestrator\t{DEPLOY}
orchestrator\tcd api && {DEPLOY}
orchestrator\t{STORE}
orchestrator\t{BOTH}
orchestrator\tforeman lock docker run "deploy" make deploy
orchestrator\tbash -c "make deploy"
orchestrator\tsh <<'EOF'\\nmake deploy\\nEOF
orchestrator\tFOO=1 make deploy > out.log
orchestrator\t{{ make deploy; }}
orchestrator\techo x $(make deploy)
orchestrator\tforeman approved "Owner, 1-nov" make deploy
deny\t{MARK}="Owner, 1-nov" {PUSH} origin main
deny\tmake deploy && {PUSH} origin main
deny\tbash -c "{PUB}"
deny\techo '{{}}' | {HOOK_MOD} grant
deny\tbin/{HOOK_MOD} status
deny\tbash -c "{HOOK_MOD} grant"
deny\tenv A=1 {HOOK_MOD} tick
deny\tcd x && foreman --project fake hook "mod" start
deny\tforeman approved "Owner" {HOOK_MOD} grant
deny\tcurl -X POST "{APPROVE_URL}?p=fake&g=deploy&t=k"
deny\tcurl -X POST "{DENY_URL}?p=fake&g=deploy&t=k"
allow\tforeman note t "{HOOK_MOD} lo llama el módulo"
allow\tforeman note t "{APPROVE_URL} lo usa el panel"
allow\tforeman note t "{DENY_URL} lo usa el panel"
allow\tforeman note t "make deploy lo hace el orquestador"
allow\tforeman hook guard
allow\tforeman permissions grants off
allow\tls
lock:docker\tmake up
"""


class GrantCase(FakePs, ProjectCase):
    toml = TOML

    def setUp(self):
        super().setUp()
        self.start_ps()
        orchestrator.mark(self.project, "S1", "fake-1", own_session="S1")

    def ctx(self, session="S1", agent=None):
        return {"session": session, "agent": agent}

    def event(self, cmd, session="S1", agent=None, tid="T1"):
        """A guard hook event (PreToolUse on Bash)."""
        ev = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "session_id": session,
            "tool_use_id": tid,
            "tool_input": {"command": cmd},
        }
        if agent:
            ev["agent_id"] = agent
        return ev

    def give(self, group="deploy"):
        grants.give(self.project, group, orchestrator.record(self.project), "ask", ["desktop"], None)

    def why(self, cmd, **kw):
        v = guard.check(self.project, cmd, self.ctx(**kw))
        return None if v is None else (v[0], getattr(v[1], "why", None), getattr(v[1], "group", None))

    def mod(self, **ev):
        return hooks_mod.grant({"cwd": str(self.home), "session": "S1", **ev})

    def answer(self, answer, group="deploy", **kw):
        return self.mod(phase="answer", group=group, answer=answer, surfaces=["desktop"], command=DEPLOY, **kw)

    def log(self):
        f = approved.log_file(self.project)
        return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []

    def shift(self, group, **delta):
        """Moves the group's grant `until` by `delta` (timedelta's keywords)."""
        d = json.loads(grants.file(self.project).read_text())
        g = d["grants"][group]
        g["until"] = (util.now() + timedelta(**delta)).strftime(grants.ISO)
        grants.file(self.project).write_text(json.dumps(d))


class ConfigTest(GrantCase):
    def test_valid_and_defaults(self):
        raw = toml_reader.loads(TOML)
        self.assertEqual(config_problems(raw), [])
        self.assertEqual(
            grants.groups(self.project), {"deploy": ["make deploy", "prod-api"], "stores": ["fastlane release"]}
        )
        self.write_toml(BASE)
        d = self.project.section("deny")
        self.assertEqual((d["grants"], d["grant_minutes"], d["ask_minutes"]), ({}, 30, 5))
        self.assertEqual(grants.groups(self.project), {})

    def test_problems(self):
        for bad, words in (
            ('[deny.grants]\nDeploy = ["x"]\n', "group name"),
            ('[deny.grants]\nd = "x"\n', "non-empty list"),
            ("[deny.grants]\nd = []\n", "non-empty list"),
            ('[deny.grants]\nd = ["(unclosed"]\n', "[deny.grants] d"),
        ):
            raw = toml_reader.loads(BASE + "\n" + bad)
            self.assertTrue(any(words in e for e in config_problems(raw)), (bad, config_problems(raw)))
        for key, val in (("grant_minutes", 0), ("ask_minutes", -1), ("grant_minutes", "30"), ("ask_minutes", True)):
            raw = toml_reader.loads(BASE)
            raw["deny"][key] = val
            self.assertTrue(any(key in e for e in config_problems(raw)), (key, val))
        raw = toml_reader.loads(BASE)
        raw["deny"]["grants"] = ["x"]
        self.assertTrue(any("must be a table" in e for e in config_problems(raw)))
        raw = toml_reader.loads(BASE)
        raw["deny"]["ask_minutes"] = 0  # 0: until the owner answers
        self.assertEqual(config_problems(raw), [])
        self.write_toml(TOML.replace('stores = ["fastlane', f'stores = ["{PUB}", "fastlane'))
        self.assertTrue(any("patterns win" in w and "stores" in w for w in self.project.warnings))

    def test_removed_orchestrator_allow_is_an_unknown_key(self):
        """`[deny] orchestrator_allow` is no longer translated into a group: a foreman.toml that still has it
        loads, the key ignored, with the unknown-key warning `foreman check` shows; it never becomes a group and
        never allows anything."""
        self.write_toml(OLD)
        self.assertEqual(grants.groups(self.project), {})
        (w,) = self.project.warnings
        self.assertIn("[deny]: unknown keys orchestrator_allow (ignored)", w)
        self.assertFalse(hasattr(config, "migrate_deny"))
        self.assertIn("orchestrator_allow", (self.home / "foreman.toml").read_text())  # the file is never rewritten
        r = self.cli("check")
        self.assertIn("unknown keys orchestrator_allow", r.stdout)
        self.assertEqual(self.why("make deploy", agent="a1"), None)  # not a group: nothing about it is asked


class GuardTest(GrantCase):
    def test_order_of_checks(self):
        """Cases A and B: agent, session and process come first, the grant last, so nothing is asked about a call
        that would be refused anyway. No wrapper: the command is written as it is."""
        self.assertEqual(self.why(DEPLOY, agent="a1"), ("orchestrator", "agent", ""))
        self.assertEqual(self.why(DEPLOY, session="S2"), ("orchestrator", "session", ""))
        self.assertEqual(guard.check(self.project, DEPLOY)[1].why, "agent")  # no ctx: an agent
        self.assertEqual(guard.check(self.project, DEPLOY, {})[1].why, "agent")
        self.reparent(COPY_PID)
        self.assertEqual(self.why(DEPLOY), ("orchestrator", "process", ""))
        self.reparent(COPY_PID, comm="sh")  # back under the mark's own claude
        self.assertEqual(self.why(DEPLOY), ("orchestrator", "grant", "deploy"))
        self.assertEqual(self.why(STORE), ("orchestrator", "grant", "stores"))
        # the old wrapper is no exemption: its text is compared whole, so it is the same group's command
        self.assertEqual(self.why('foreman approved "Owner" make deploy'), ("orchestrator", "grant", "deploy"))
        # [deny] patterns win over a group, the orchestrator with a grant included
        self.give()
        self.write_toml(TOML.replace('"prod-api"', f'"prod-api", "{PUSH}"'))
        self.assertEqual(guard.check(self.project, f"make deploy && {PUSH}", self.ctx())[0], "deny")
        # no session marked: nobody
        (self.project.state / "session").unlink()
        self.assertEqual(self.why(DEPLOY)[1], "session")
        # odd contexts never raise
        for odd in ({"session": 5, "agent": []}, {"session": "S1", "marked": None}, "garbage", 42):
            v = guard.check(self.project, DEPLOY, odd)
            self.assertIn(v[0], ("deny", "orchestrator"), odd)

    def test_live_grant(self):
        grants.give(self.project, "deploy", orchestrator.record(self.project), "ask", ["desktop"], "T0")
        ctx = self.ctx()
        self.assertIsNone(guard.check(self.project, DEPLOY, ctx))
        self.assertEqual(ctx["passed"], "make deploy")
        self.assertEqual([g["group"] for g in ctx["passed"].grant], ["deploy"])
        self.assertEqual(ctx["passed"].grant[0]["via"], "ask")
        # one group's grant does not cover another group, nor a line that runs both
        self.assertEqual(self.why(STORE)[1:], ("grant", "stores"))
        self.assertEqual(self.why(BOTH)[1:], ("grant", "stores"))
        grants.give(self.project, "stores", orchestrator.record(self.project), "ask", [], None)
        ctx = self.ctx()
        self.assertIsNone(guard.check(self.project, BOTH, ctx))
        self.assertEqual(sorted(g["group"] for g in ctx["passed"].grant), ["deploy", "stores"])
        # still refused for an agent, another session, a copy of the process
        self.assertEqual(self.why(DEPLOY, agent="a1")[1], "agent")
        self.assertEqual(self.why(DEPLOY, session="S2")[1], "session")
        self.reparent(COPY_PID)
        self.assertEqual(self.why(DEPLOY)[1], "process")

    def test_grant_ends(self):
        mark = orchestrator.record(self.project)
        grants.give(self.project, "deploy", mark, "ask", [], None)
        self.shift("deploy", seconds=-1)  # expired
        self.assertEqual(self.why(DEPLOY)[1], "grant")
        self.shift("deploy", minutes=5)
        self.assertIsNone(self.why(DEPLOY))
        for key, val in (("session", "S0"), ("pid", COPY_PID), ("pid_start", "Mon Jan 1 00:00:00 2001")):
            d = json.loads(grants.file(self.project).read_text())
            old, d["grants"]["deploy"][key] = d["grants"]["deploy"][key], val
            grants.file(self.project).write_text(json.dumps(d))
            self.assertEqual(self.why(DEPLOY)[1], "grant", key)  # another session's, pid's or start's grant
            d["grants"]["deploy"][key] = old
            grants.file(self.project).write_text(json.dumps(d))
        self.assertIsNone(self.why(DEPLOY))
        # the mark moves to another session: the grant is gone with it
        orchestrator.mark(self.project, "S3", "fake-2", own_session="S3")
        self.assertIsNone(grants.live(self.project, "deploy"))
        # a mark without a process: no grant is live
        orchestrator.mark(self.project, "S1", "fake-1")
        self.assertIsNone(grants.live(self.project, "deploy"))
        grants.file(self.project).write_text("garbage")
        self.assertEqual(grants.read(self.project), {"grants": {}, "cooldowns": {}, "pending": {}, "once": {}})

    def test_table(self):
        f = self.home / "guard.tsv"
        f.write_text(TABLE)
        counts, failures = guard.run_table(self.project, f)
        self.assertEqual(failures, [])
        self.assertEqual(counts, {"orchestrator": 11, "deny": 11, "allow": 7, "lock": 1})
        r = self.cli("guard", "--table", str(f))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("ok (11 deny, 1 lock, 7 allow, 11 orchestrator)", r.stdout)
        # a row that only the orchestrator passes, written as deny or allow, fails and says for whom
        f.write_text(f"deny\t{DEPLOY}\nallow\t{DEPLOY}\n")
        _, failures = guard.run_table(self.project, f)
        self.assertEqual(len(failures), 2)
        self.assertIn("got deny as an agent and allow as the orchestrator", failures[0])
        with self.assertRaises(ValueError):
            guard.parse_table("orchestrators\tls\n")
        # without groups the command is no one's: allowed for everyone, and the table runs one role
        self.write_toml(BASE)
        f.write_text(f"allow\t{DEPLOY}\n")
        self.assertEqual(guard.run_table(self.project, f)[1], [])
        # `guard --as orchestrator` takes every group as granted; without --as, an agent
        self.write_toml(TOML)
        r = self.cli("guard", "--as", "orchestrator", STORE)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("allowed (orchestrator only: «fastlane release»)", r.stdout)
        r = self.cli("guard", STORE)
        self.assertEqual(r.returncode, 2)
        self.assertIn("never by an agent", r.stdout)
        self.assertEqual(self.cli("guard", "--as", "orchestrator", "ls").stdout.strip(), "allowed")

    def test_hook_mod_rule(self):
        """Nobody runs `foreman hook mod` through Bash, the orchestrator included."""
        for cmd in (f"echo '{{}}' | {HOOK_MOD} grant", f"bin/{HOOK_MOD} status", f'sh -c "{HOOK_MOD} tick"'):
            for ctx in (None, self.ctx(), guard.as_role("orchestrator")):
                v = guard.check(self.project, cmd, ctx)
                self.assertEqual((v[0], v[1]), ("deny", guard.HOOK_MOD), (cmd, ctx))
        text = guard.message(self.project, guard.check(self.project, f"{HOOK_MOD} grant"))
        self.assertIn("only by foreman's hooks module", text)
        self.assertIn(f"`{HOOK_MOD} grant`", text)
        self.write_toml(TOML.replace('owner = "Owner"', 'owner = "Owner"\nlanguage = "es"'))
        self.assertIn("ni siquiera del orquestador", guard.message(self.project, guard.check(self.project, HOOK_MOD)))
        # in a project without any [deny] list too
        self.write_toml('[project]\nname = "fake"\n')
        self.assertEqual(guard.check(self.project, HOOK_MOD)[0], "deny")

    def test_messages_and_hook(self):
        text = guard.message(self.project, guard.check(self.project, DEPLOY, self.ctx()))
        for w in ("group «deploy»", "Owner's own yes", "fake-1", "do not try again"):
            self.assertIn(w, text)
        for ctx, words in (
            (None, ("never by an agent", "in your report", "Write or Edit")),
            ({"session": "S2"}, ("fake-1", "not the marked one", "foreman session ID")),
        ):
            text = guard.message(self.project, guard.check(self.project, DEPLOY, ctx))
            for w in words:
                self.assertIn(w, text)
            self.assertNotIn("foreman approved", text)
        self.assertIn("It matched in the command `make deploy`", text)
        self.write_toml(TOML.replace('owner = "Owner"', 'owner = "Owner"\nlanguage = "es"'))
        self.assertIn("nunca un agente", guard.message(self.project, guard.check(self.project, DEPLOY)))
        self.write_toml(TOML)
        # the hook (cases B and C): a refusal until the owner's yes, then a pass with its grant, in the log
        rc, err = self.hook_here(self.event(DEPLOY, tid="T1"))
        self.assertEqual(rc, 2)
        self.assertIn("group «deploy»", err)
        self.assertEqual(self.hook("guard", self.event(DEPLOY, agent="a1", tid="T5")).returncode, 2)
        r = self.hook("guard", self.event(DEPLOY, session="S2", tid="T6"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("not the marked one", r.stderr)
        self.assertNotIn("grant", self.notes_text())
        self.give()
        self.assertEqual(self.hook_here(self.event("cd api && make deploy", tid="T2"))[0], 0)
        log = self.log()
        self.assertEqual(
            [(x["verdict"], x["why"]) for x in log],
            [("deny", "grant"), ("deny", "agent"), ("deny", "session"), ("allow", None)],
        )
        self.assertEqual(log[0]["group"], "deploy")
        self.assertEqual(log[1]["agent"], "agent-a1")
        self.assertEqual(
            (log[3]["orchestrator"], log[3]["pattern"], log[3]["tool_use_id"], log[3]["command"]),
            ("fake-1", "make deploy", "T2", "cd api && make deploy"),
        )
        self.assertEqual(log[3]["grant"][0]["group"], "deploy")
        self.assertNotIn("approved", log[3])
        # O3: each command let through is a line of today's notes, with the grant it used
        self.assertIn(
            "the guard let fake-1 run `cd api && make deploy` (group «deploy» granted by Owner at", self.notes_text()
        )

    def test_wrapper_is_gone(self):
        """Case D: `foreman approved "x" cmd` is no command (exit 2, nothing runs)."""
        probe = self.tmp / "ran"
        r = self.cli("approved", "Owner, 1-nov", "touch", str(probe), env={"CLAUDE_CODE_SESSION_ID": "S1"})
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("invalid choice: 'approved'", r.stderr)
        self.assertFalse(probe.exists())

    def test_notice_once_per_refusal(self):
        self.assertEqual(loop.orchestrator_only_notices(self.project), [])
        self.hook("guard", self.event(DEPLOY, agent="a1"))
        self.hook("guard", self.event(DEPLOY, tid="T2"))  # the orchestrator without a grant: no notice
        self.assertEqual(len(loop.orchestrator_only_notices(self.project, mark=False)), 1)
        (n,) = loop.orchestrator_only_notices(self.project)
        self.assertEqual(n["kind"], "orchestrator_only_denied")
        self.assertTrue(n["actionable"])
        self.assertIn("agent-a1 tried `make deploy`", n["text"])
        self.assertIn("after the owner's yes ([deny.grants])", n["text"])
        self.assertNotIn("foreman approved", n["text"])
        self.assertEqual(loop.orchestrator_only_notices(self.project), [])
        self.hook("guard", self.event(DEPLOY, session="S7", tid="T3"))
        (n,) = loop.orchestrator_only_notices(self.project)
        self.assertIn("session S7", n["text"])


class ModTest(GrantCase):
    def test_outside_and_without_groups(self):
        self.assertEqual(hooks_mod.grant({"cwd": str(self.tmp), "session": "S1", "phase": "check"}), {})
        self.assertEqual(self.mod(session=None, phase="check", command=DEPLOY), {})
        self.assertEqual(
            hooks_mod.start({"cwd": str(self.home), "session": "S1", "claude_code": "2.1.314"})["grants"], True
        )
        self.write_toml(BASE)
        self.assertEqual(self.mod(phase="check", command=DEPLOY), {})
        self.assertNotIn("grants", hooks_mod.start({"cwd": str(self.home), "session": "S1", "claude_code": "2.1.314"}))
        self.write_toml(TOML)
        with self.assertRaises(ValueError):
            self.mod(phase="other")
        with self.assertRaises(ValueError):
            self.answer("maybe")
        with self.assertRaises(ValueError):
            self.answer("yes", group="nope")
        r = self.cli("hook", "mod", "grant", inp=json.dumps({"cwd": str(self.home), "session": "S1", "phase": "x"}))
        self.assertEqual(r.returncode, 1)
        self.assertIn("hook mod grant: ValueError", r.stderr)

    def test_tick_reads_groups_again(self):
        """A group added to [deny.grants] while the session runs: the next tick says so, held or not, marked or not."""
        self.write_toml(BASE)
        ev = {"cwd": str(self.home), "session": "S1", "claude_code": "2.1.314"}
        self.assertNotIn("grants", hooks_mod.start(ev))
        self.assertIs(hooks_mod.tick({**ev, "hold": True})["grants"], False)
        self.write_toml(TOML)
        self.assertIs(hooks_mod.tick({**ev, "hold": True})["grants"], True)
        self.assertIs(hooks_mod.tick({**ev, "session": "S2"})["grants"], True)
        self.write_toml(BASE)  # taken away again
        self.assertIs(hooks_mod.tick({**ev, "session": "S2"})["grants"], False)
        self.assertNotIn("grants", hooks_mod.tick({**ev, "cwd": str(self.tmp)}))  # outside: the module keeps its own

    def test_check(self):
        q = self.mod(phase="check", command=DEPLOY, tool_use_id="T1")
        self.assertEqual((q["group"], q["live"], q["ask_ms"]), ("deploy", False, 300_000))
        self.assertIn("fake-1, the orchestrator of fake", q["question"])
        self.assertIn("«deploy»", q["question"])
        self.assertIn(DEPLOY, q["question"])
        self.assertEqual((q["yes"], q["no"], q["header"]), ("Yes, for 30 min", "No", "foreman"))
        self.assertIn("already waiting for Owner's answer", q["waiting"])
        # nothing to ask: refused anyway ([deny] patterns), not a group command
        for cmd in (f"make deploy && {PUSH}", "kamal app logs", "ls", ""):
            self.assertEqual(self.mod(phase="check", command=cmd), {}, cmd)
        self.assertEqual(self.mod(session="S2", phase="check", command=DEPLOY), {})
        self.reparent(COPY_PID)
        self.assertEqual(self.mod(phase="check", command=DEPLOY), {})
        self.reparent(COPY_PID, comm="sh")
        # a live grant in the file: live, so the module asks only when its own memory does not hold it
        grants.give(self.project, "deploy", orchestrator.record(self.project), "ask", [], None)
        q = self.mod(phase="check", command=DEPLOY)
        self.assertEqual((q["group"], q["live"]), ("deploy", True))
        self.assertEqual(q["until"], grants.live(self.project, "deploy")["until"])
        self.write_toml(
            TOML.replace('owner = "Owner"', 'owner = "Owner"\nlanguage = "es"').replace(
                "ask_minutes = 5", "ask_minutes = 0"
            )
        )
        q = self.mod(phase="check", command=STORE)
        self.assertEqual((q["yes"], q["ask_ms"]), ("Sí, 30 min", 0))
        self.assertIn("¿Le dejas", q["question"])

    def test_check_any_tool(self):
        """Another tool than Bash goes with its `input`: asked about as `cat PLACE` or its command would be."""
        place = "/srv/prod-api/config.json"
        read = {"tool": "Read", "input": {"file_path": place}}
        q = self.mod(phase="check", **read)
        self.assertEqual((q["group"], q["live"]), ("deploy", False))
        self.assertIn(f"wants to use Read on `{place}`, which the group «deploy» covers", q["question"])
        self.assertEqual(q["group"], self.mod(phase="check", command=f"cat {place}")["group"])  # the same as in Bash
        q = self.mod(phase="check", tool="Monitor", input={"command": DEPLOY, "description": "x"})
        self.assertIn(f"wants to use Monitor on `{DEPLOY}`", q["question"])
        q = self.mod(phase="check", tool="Write", input={"file_path": "/srv/prod-api/.envrc", "content": "x"})
        self.assertEqual(q["group"], "deploy")  # writing a place: the verdict of reading it
        # free text is not compared; nothing named; refused anyway; a subagent's never reaches here; not marked
        for inp in ({"prompt": DEPLOY}, {}, {"url": f"https://x.test/{PUSH}"}):
            self.assertEqual(self.mod(phase="check", tool="WebFetch", input=inp), {}, inp)
        self.assertEqual(self.mod(phase="check", tool="Read"), {})  # no input: nothing to judge
        self.assertEqual(self.mod(session="S2", phase="check", **read), {})
        # a Write that names a reserved file of Claude Code goes by its path, as before
        q = self.mod(phase="check", tool="Write", path=str(self.home / ".claude/settings.json"))
        self.assertEqual(q["group"], "file:claude")
        self.assertEqual(
            self.mod(phase="check", tool="Write", input={"file_path": str(self.home / "foreman.toml")}), {}
        )
        # a live grant: the module asks only when its memory does not hold it
        self.give()
        q = self.mod(phase="check", **read)
        self.assertEqual((q["group"], q["live"]), ("deploy", True))
        self.assertIn(place, q["question"])

    def test_answer_any_tool(self):
        read = {"tool": "Read", "input": {"file_path": "/srv/prod-api/config.json"}}
        out = self.mod(phase="answer", group="deploy", answer="yes", surfaces=["desktop"], tool_use_id="T1", **read)
        self.assertTrue(out["granted"])
        self.assertIsNone(guard.check_tool(self.project, "Read", read["input"], self.ctx()))
        out = self.mod(
            phase="answer", group="stores", answer="timeout", surfaces=[], tool="Monitor", input={"command": STORE}
        )
        self.assertFalse(out["granted"])
        self.assertEqual(grants.pending(self.project)["stores"]["command"], f"Monitor {STORE}")
        lines = self.log()
        self.assertEqual(
            [(x["verdict"], x["tool"], x["command"]) for x in lines],
            [("grant", "Read", "Read /srv/prod-api/config.json"), ("deny", "Monitor", f"Monitor {STORE}")],
        )

    def test_yes(self):
        out = self.answer("yes", tool_use_id="T1")
        self.assertTrue(out["granted"])
        self.assertEqual(out["log"], f"«deploy» allowed for fake-1 until {out['until'][11:16]}")
        g = grants.read(self.project)["grants"]["deploy"]
        rec = orchestrator.record(self.project)
        self.assertEqual((g["session"], g["pid"], g["pid_start"]), (rec["session"], rec["pid"], rec["pid_start"]))
        self.assertEqual((g["via"], g["answer"], g["surfaces"], g["tool_use_id"]), ("ask", "yes", ["desktop"], "T1"))
        at, until = util.fromiso(g["at"]), util.fromiso(g["until"])
        self.assertEqual(until - at, timedelta(minutes=30))
        (line,) = self.log()
        self.assertEqual(
            (line["verdict"], line["why"], line["group"], line["answer"]), ("grant", "grant", "deploy", "yes")
        )
        self.assertEqual((line["orchestrator"], line["until"], line["surfaces"]), ("fake-1", g["until"], ["desktop"]))
        self.assertIn("grant «deploy»: Owner said yes (desktop) to fake-1, until", self.notes_text())
        self.assertIsNone(self.why(DEPLOY))

    def test_yes_from_elsewhere_grants_nothing(self):
        out = self.answer("yes", session="S2")
        self.assertEqual(out["granted"], False)
        self.assertIn("no longer the marked orchestrator", out["deny"])
        self.reparent(COPY_PID)
        out = self.answer("yes")
        self.assertIn("not the one that marked", out["deny"])
        self.assertEqual(grants.read(self.project)["grants"], {})
        self.assertEqual([x.get("refused") for x in self.log()], ["session", "process"])
        self.assertEqual({x["verdict"] for x in self.log()}, {"deny"})

    def test_no_and_cooldown(self):
        out = self.answer("no")
        self.assertEqual(out["granted"], False)
        self.assertIn("said no", out["deny"])
        self.assertIn("10 minutes", out["deny"])
        q = self.mod(phase="check", command=DEPLOY)
        self.assertNotIn("question", q)
        self.assertIn("will not ask again before", q["deny"])
        self.assertEqual(self.mod(session="S2", phase="check", command=DEPLOY), {})  # not marked: guard alone
        self.assertIn("question", self.mod(phase="check", command=STORE))  # another group asks
        line = self.log()[-1]
        self.assertEqual((line["verdict"], line["answer"]), ("deny", "no"))
        self.assertEqual(line["cooldown_until"], grants.cooling(self.project, "deploy", "S1"))
        # after the cooldown, it asks again; a yes clears it
        d = json.loads(grants.file(self.project).read_text())
        d["cooldowns"]["deploy"]["until"] = (util.now() - timedelta(seconds=1)).strftime(grants.ISO)
        grants.file(self.project).write_text(json.dumps(d))
        self.assertIn("question", self.mod(phase="check", command=DEPLOY))
        self.answer("no")
        self.answer("yes")
        self.assertEqual(grants.read(self.project)["cooldowns"], {})

    def test_other_answers(self):
        for answer, words in (
            ("dismissed", "closed the question"),
            ("unavailable", "nobody could be asked"),
            ("timeout", "waits for Owner in foreman's panel"),
        ):
            out = self.answer(answer, tool_use_id=f"T-{answer}")
            self.assertEqual(out["granted"], False)
            self.assertIn(words, out["deny"])
            self.assertIn("question", self.mod(phase="check", command=DEPLOY), answer)  # no cooldown
        self.assertEqual([x.get("pending") for x in self.log()], [None, None, True])
        p = grants.pending(self.project)["deploy"]
        self.assertEqual(
            (p["session"], p["command"], p["tool_use_id"], p["orchestrator"]), ("S1", DEPLOY, "T-timeout", "fake-1")
        )
        self.assertEqual(grants.read(self.project)["grants"], {})
        # a late yes to another question (no tool_use_id: not the pending request's): written down, nothing granted,
        # the request still pending (LateAnswerTest: a late yes to the request itself applies)
        out = self.answer("yes", late=True)
        self.assertEqual(out["granted"], False)
        self.assertIn("came after the question had expired", out["log"])
        self.assertEqual(self.log()[-1]["verdict"], "late")
        self.assertEqual(grants.read(self.project)["grants"], {})
        self.assertIn("deploy", grants.pending(self.project))
        # the pending request belongs to the marked session; a yes clears it
        orchestrator.mark(self.project, "S9", "other")
        self.assertEqual(grants.pending(self.project), {})
        orchestrator.mark(self.project, "S1", "fake-1", own_session="S1")
        self.answer("yes")
        self.assertEqual(grants.pending(self.project), {})


class CliTest(GrantCase):
    def test_grants(self):
        r = self.cli("permissions", "grants")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), ["deploy: closed", "stores: closed"])
        self.answer("yes")
        self.answer("no", group="stores")
        self.answer("timeout", group="stores")
        lines = grants.lines(self.project)  # in this process: the simulated ps makes the grant live
        self.assertRegex(lines[0], r"^deploy: granted until \d\d:\d\d \(ask, \d\d:\d\d, to fake-1\)$")
        self.assertRegex(
            lines[1], r"^stores: closed: the owner said no, not asked again until \d\d:\d\d; a request waits"
        )
        r = self.cli("permissions", "grants", "--json")
        self.assertEqual(sorted(json.loads(r.stdout)["grants"]), ["deploy"])
        self.assertEqual(self.cli("permissions", "grants", "off", "nope").returncode, 2)
        self.assertEqual(self.cli("permissions", "grants", "deploy").returncode, 2)
        r = self.cli("permissions", "grants", "off", "deploy", env={"CLAUDE_CODE_SESSION_ID": "S7"})
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "removed: deploy"), r.stderr)
        self.assertEqual(self.log()[-1], {**self.log()[-1], "verdict": "revoke", "group": "deploy", "session": "S7"})
        self.assertIn("deploy", self.notes_text())
        self.assertIn("stores", grants.pending(self.project))
        self.answer("yes")
        r = self.cli("permissions", "grants", "off")
        self.assertEqual(r.stdout.strip(), "removed: deploy")
        self.assertEqual(grants.read(self.project)["pending"], {})
        self.assertEqual(self.cli("permissions", "grants", "off").stdout.strip(), "no grant to remove")
        self.write_toml(BASE)
        self.assertEqual(self.cli("permissions", "grants").stdout.strip(), "no [deny.grants] groups in this project")


class PendCase(GrantCase):
    """A GrantCase with a request left pending (a question's time limit) and a way to age it."""

    def pend(self, group="deploy", tid="T-late"):
        self.answer("timeout", group=group, tool_use_id=tid)
        return grants.pending(self.project)[group]

    def age(self, group, **delta):
        """Moves the group's pending request `at` back by `delta` (timedelta's keywords)."""
        d = json.loads(grants.file(self.project).read_text())
        d["pending"][group]["at"] = (util.now() - timedelta(**delta)).strftime(grants.ISO)
        grants.file(self.project).write_text(json.dumps(d))


class PanelApproveTest(PendCase):
    """A question left unanswered (its time limit) leaves the request pending; the owner approves it from foreman's
    panel (grants.approve, panel.grant_approve): the same grant as a yes in the chat, for the marked session and
    process only, once, before it expires."""

    def test_timeout_records_the_request(self):
        r = self.pend()
        rec = orchestrator.record(self.project)
        self.assertEqual(
            (r["session"], r["pid"], r["pid_start"], r["command"], r["tool_use_id"], r["orchestrator"]),
            ("S1", rec["pid"], rec["pid_start"], DEPLOY, "T-late", "fake-1"),
        )
        self.assertEqual(util.fromiso(r["expires"]) - util.fromiso(r["at"]), timedelta(hours=grants.PENDING_H))
        self.assertRegex(grants.lines(self.project)[0], r"^deploy: closed; a request waits for the owner since")

    def test_approve_writes_the_grant(self):
        self.pend()
        self.assertEqual(self.why(DEPLOY), ("orchestrator", "grant", "deploy"))
        g, r = grants.approve(self.project, "deploy")
        rec = orchestrator.record(self.project)
        self.assertEqual((g["session"], g["pid"], g["pid_start"]), (rec["session"], rec["pid"], rec["pid_start"]))
        self.assertEqual(
            (g["via"], g["answer"], g["surfaces"], g["tool_use_id"]), ("panel", "yes", ["panel"], "T-late")
        )
        self.assertEqual(util.fromiso(g["until"]) - util.fromiso(g["at"]), timedelta(minutes=30))
        self.assertEqual(r["command"], DEPLOY)
        self.assertEqual(grants.read(self.project)["pending"], {})
        self.assertIsNone(self.why(DEPLOY))  # the classic guard passes it
        self.assertEqual(self.why(DEPLOY, agent="a1")[1], "agent")  # still the main thread only
        self.assertEqual(self.why(DEPLOY, session="S2")[1], "session")
        self.assertEqual(oct(grants.panel_key_file().stat().st_mode & 0o777), "0o600")
        # the module does not ask: the signature says the owner gave it from the panel
        self.assertTrue(grants.from_panel(self.project, "deploy"))
        self.assertEqual(self.mod(phase="check", command=DEPLOY), {})
        # one request grants once
        with self.assertRaises(grants.NotApproved) as e:
            grants.approve(self.project, "deploy")
        self.assertEqual(e.exception.why, "none")
        self.assertEqual(grants.read(self.project)["grants"]["deploy"]["until"], g["until"])
        # `foreman permissions grants off` takes it away
        self.assertEqual(self.cli("permissions", "grants", "off", "deploy").stdout.strip(), "removed: deploy")
        self.assertEqual(self.why(DEPLOY)[1], "grant")

    def test_a_forged_panel_grant_is_asked_again(self):
        self.pend()
        grants.approve(self.project, "deploy")
        d = json.loads(grants.file(self.project).read_text())
        for forged in (
            {"sig": "0" * 64},
            {"sig": None},
            {
                "until": d["grants"]["deploy"]["until"][:-1]
                + ("8" if d["grants"]["deploy"]["until"].endswith("9") else "9")
            },
        ):
            g = {**d["grants"]["deploy"], **forged}
            grants.file(self.project).write_text(json.dumps({**d, "grants": {"deploy": g}}))
            self.assertIsNone(grants.from_panel(self.project, "deploy"), forged)
            q = self.mod(phase="check", command=DEPLOY)
            self.assertEqual((q["group"], q["live"]), ("deploy", True), forged)  # the module asks
        grants.file(self.project).write_text(json.dumps(d))
        grants.panel_key_file().unlink()
        self.assertIsNone(grants.from_panel(self.project, "deploy"))  # no key: nothing verifies
        # an ask grant is never taken for a panel one
        grants.give(self.project, "deploy", orchestrator.record(self.project), "ask", [], None)
        self.assertIsNone(grants.from_panel(self.project, "deploy"))

    def test_stale_or_foreign_requests_are_refused(self):
        def refused(why, kept):
            with self.assertRaises(grants.NotApproved) as e:
                grants.approve(self.project, "deploy")
            self.assertEqual(e.exception.why, why)
            self.assertEqual("deploy" in grants.read(self.project)["pending"], kept, why)
            self.assertEqual(grants.read(self.project)["grants"], {}, why)

        self.pend()
        self.age("deploy", hours=24, minutes=1)  # expired: not listed, refused and dropped
        self.assertEqual(grants.pending(self.project), {})
        refused("expired", False)
        # another session marked now, by hand (no process): an orphan, refused and kept for the owner to drop
        self.pend()
        orchestrator.mark(self.project, "S9", "other")
        self.assertEqual(grants.pending(self.project), {})
        self.assertEqual(grants.orphans(self.project)["deploy"]["why"], "process")
        refused("process", True)
        # another session that marked itself from the same process (not carried): refused and dropped
        orchestrator.mark(self.project, "S9", "other", own_session="S9")
        self.assertEqual(grants.orphans(self.project)["deploy"]["why"], "session")
        refused("session", False)
        orchestrator.mark(self.project, "S1", "fake-1", own_session="S1")
        # the same session, but the request came from another process
        self.pend()
        d = json.loads(grants.file(self.project).read_text())
        d["pending"]["deploy"]["pid"] = COPY_PID
        grants.file(self.project).write_text(json.dumps(d))
        self.assertEqual(grants.pending(self.project), {})
        refused("process", True)
        # the same session, but the request records no process: not the mark's
        self.pend()
        d = json.loads(grants.file(self.project).read_text())
        for k in ("pid", "pid_start"):
            del d["pending"]["deploy"][k]
        grants.file(self.project).write_text(json.dumps(d))
        self.assertEqual(grants.pending(self.project), {})
        refused("process", True)
        # the marked process is gone, or `ps` cannot be read: nothing is granted
        self.pend()
        self.ps_rows = [r for r in self.ps_rows if r[0] != helpers.CLAUDE_PID]
        refused("process", True)
        self.ps_rows = None
        refused("process", True)
        self.start_ps()
        g, _ = grants.approve(self.project, "deploy")
        self.assertEqual(g["via"], "panel")

    def test_already_granted_and_late_answers(self):
        """A grant is never given twice, and the old question, still drawn after its timeout, contradicts nothing."""
        self.pend()
        g, _ = grants.approve(self.project, "deploy")
        lines = len(self.log())
        for answer in ("yes", "no"):
            out = self.answer(answer, late=True, tool_use_id="T-late")
            self.assertEqual(out["granted"], False)
            self.assertNotIn("deny", out)
            self.assertIn(f"already allowed until {g['until'][11:16]} (panel)", out["log"])
        self.assertEqual(grants.read(self.project)["grants"]["deploy"], g)
        self.assertEqual(grants.read(self.project)["cooldowns"], {})
        self.assertEqual([x["verdict"] for x in self.log()[lines:]], ["late", "late"])
        # a request pending while a grant of the group is live (an ask grant the module asked about again): dropped
        self.pend()
        with self.assertRaises(grants.NotApproved) as e:
            grants.approve(self.project, "deploy")
        self.assertEqual((e.exception.why, e.exception.grant["until"]), ("live", g["until"]))
        self.assertEqual(grants.read(self.project)["pending"], {})
        # a yes in the chat after the panel's (a new question, not a late one) is a new grant, as before
        self.assertTrue(self.answer("yes")["granted"])

    def test_panel(self):
        """The panel lists the request on the You tab and approves it (POST, with the key); the answer writes the
        log line, the note and a message for the orchestrator."""
        from foreman import notes, panel, panel_registry

        (self.tmp / "projects.toml").write_text(f'[[project]]\nname = "fake"\nhome = "{self.home}"\nmode = "active"\n')
        panel_registry.REG.__init__()
        srv, token = panel.make_server("127.0.0.1", 0, token="k3y")
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        base = f"http://127.0.0.1:{srv.server_address[1]}"

        def req(path, method="GET", key=True):
            url = base + path + (("&" if "?" in path else "?") + "t=" + token if key else "")
            try:
                with urllib.request.urlopen(
                    urllib.request.Request(url, data=b"" if method == "POST" else None, method=method), timeout=20
                ) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                with e:
                    body = e.read()
                return e.code, json.loads(body) if body.startswith(b"{") else body.decode()

        state = lambda: req("/api/state?p=fake")[1]["project"]["grants"]  # noqa: E731
        self.assertEqual(state(), [])
        self.pend()
        (r,) = state()
        self.assertEqual(
            (r["group"], r["pending"], r["command"], r["orchestrator"], r["minutes"]),
            ("deploy", True, DEPLOY, "fake-1", 30),
        )
        self.assertEqual(r["expires"], grants.pending(self.project)["deploy"]["expires"])
        # without the key, or with GET: nothing
        self.assertEqual(req("/api/grants/approve?p=fake&g=deploy", "POST", key=False)[0], 403)
        self.assertEqual(req("/api/grants/approve?p=fake&g=deploy")[0], 404)
        self.assertEqual(req("/api/grants/approve?p=fake&g=nope", "POST")[0], 400)
        self.assertIn("deploy", grants.pending(self.project))
        code, d = req("/api/grants/approve?p=fake&g=deploy", "POST")
        self.assertEqual(code, 200, d)
        self.assertEqual(d["until"], grants.live(self.project, "deploy")["until"])
        (r,) = state()
        self.assertEqual((r["pending"], r["until"], r["via"]), (False, d["until"], "panel"))
        code, d2 = req("/api/grants/approve?p=fake&g=deploy", "POST")
        self.assertEqual((code, d2["why"]), (403, "none"))
        line = self.log()[-1]
        self.assertEqual(
            (line["verdict"], line["via"], line["group"], line["command"]), ("grant", "panel", "deploy", DEPLOY)
        )
        self.assertIn("approved from the panel the request of", self.notes_text())
        msg = notes.inbox(self.project)[-1]
        self.assertEqual((msg["from"], msg["grant"]["group"], msg["read"]), ("owner", "deploy", False))
        self.assertIn(f"You may run again: `{DEPLOY}`", msg["text"])
        # a stale request: refused with its reason, for the page to say it
        self.pend()
        self.age("deploy", hours=24, minutes=1)
        self.assertEqual(req("/api/grants/approve?p=fake&g=deploy", "POST")[1]["why"], "expired")

    def test_deny_only_restricts(self):
        """«No» from the panel (grants.deny, /api/grants/deny): the request is dropped, the group cools down for that
        session (no question, still refused), nothing is granted, a live grant of another group is untouched."""
        self.pend()
        self.assertEqual(self.why(DEPLOY), ("orchestrator", "grant", "deploy"))
        r = grants.deny(self.project, "deploy")
        self.assertEqual(r["command"], DEPLOY)
        d = grants.read(self.project)
        self.assertEqual((d["pending"], d["grants"]), ({}, {}))
        self.assertEqual(d["cooldowns"]["deploy"]["session"], "S1")
        self.assertEqual(self.why(DEPLOY)[1], "grant")  # still refused
        self.assertEqual(self.mod(phase="check", command=DEPLOY).get("live"), None)  # and not asked again
        with self.assertRaises(grants.NotApproved) as e:  # nothing left to approve or deny
            grants.approve(self.project, "deploy")
        self.assertEqual(e.exception.why, "none")
        with self.assertRaises(grants.NotApproved):
            grants.deny(self.project, "deploy")

    def test_deny_of_a_stale_request_does_nothing(self):
        """A request too old or of another session is dropped, not denied: no cooldown, no log line."""
        self.pend()
        self.age("deploy", hours=24, minutes=1)
        with self.assertRaises(grants.NotApproved) as e:
            grants.deny(self.project, "deploy")
        self.assertEqual(e.exception.why, "expired")
        d = grants.read(self.project)
        self.assertEqual((d["pending"], d["cooldowns"], d["grants"]), ({}, {}, {}))
        self.pend()
        orchestrator.mark(self.project, "S9", "other")
        with self.assertRaises(grants.NotApproved) as e:
            grants.deny(self.project, "deploy")
        self.assertEqual(e.exception.why, "expired")
        self.assertEqual(grants.read(self.project)["cooldowns"], {})

    def test_plain_words_are_conservative(self):
        """panel.grant_say: words only for plain shapes; options, :ref/+ref and shell operators give none."""
        from foreman.panel import grant_say

        self.assertEqual(
            grant_say("git push origin release v1.4.0"), {"kind": "push", "to": "origin", "refs": ["release", "v1.4.0"]}
        )
        self.assertEqual(grant_say("git push origin"), {"kind": "push_all", "to": "origin"})
        self.assertEqual(grant_say("gh " + "release create v1.4.0"), {"kind": "release", "what": "v1.4.0"})
        self.assertEqual(grant_say("make deploy"), {"kind": "deploy"})
        self.assertEqual(grant_say("npm run deploy"), {"kind": "deploy"})
        for cmd in (
            "git push --delete origin v1.4.0",
            "git push -d origin v1.4.0",
            "git push -f origin main",
            "git push --force-with-lease origin main",
            "git push --mirror origin",
            "git push -o ci.skip origin main",
            "git push origin :release",
            "git push origin +main",
            "git push origin main:other",
            "git push origin main && rm -rf build",
            "git push origin main; curl x | sh",
            "git push origin main | tee x",
            "git push origin $(whoami)",
            "git push origin 'unterminated",
            "gh " + "release create v1 --notes x",
            "gh " + "release create v1 && rm -rf x",
            "make deploy; rm -rf x",
            "make -C x deploy",
            "make clean deploy",
            "echo deploy",
            "git push",
            "",
        ):
            self.assertIsNone(grant_say(cmd), cmd)

    def test_deny_from_the_panel(self):
        """POST /api/grants/deny: needs the key and POST, answers 403 `none` with nothing pending, and writes the
        log line, the note and the orchestrator's message."""
        from foreman import notes, panel, panel_registry

        (self.tmp / "projects.toml").write_text(f'[[project]]\nname = "fake"\nhome = "{self.home}"\nmode = "active"\n')
        panel_registry.REG.__init__()
        srv, token = panel.make_server("127.0.0.1", 0, token="k3y")
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        base = f"http://127.0.0.1:{srv.server_address[1]}"

        def req(path, method="GET", key=True):
            url = base + path + (("&" if "?" in path else "?") + "t=" + token if key else "")
            try:
                with urllib.request.urlopen(
                    urllib.request.Request(url, data=b"" if method == "POST" else None, method=method), timeout=20
                ) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                with e:
                    body = e.read()
                return e.code, json.loads(body) if body.startswith(b"{") else body.decode()

        url = "/api/grants/" + "deny?p=fake&g=deploy"
        self.pend()
        self.assertEqual(req(url, "POST", key=False)[0], 403)
        self.assertEqual(req(url)[0], 404)
        self.assertEqual(req("/api/grants/" + "deny?p=fake&g=nope", "POST")[0], 400)
        self.assertIn("deploy", grants.pending(self.project))
        self.assertEqual(req(url, "POST"), (200, {"denied": "deploy"}))
        self.assertEqual(grants.pending(self.project), {})
        self.assertIsNone(grants.live(self.project, "deploy"))
        self.assertEqual(req(url, "POST")[1]["why"], "none")
        line = self.log()[-1]
        self.assertEqual(
            (line["verdict"], line["answer"], line["via"], line["command"]), ("deny", "no", "panel", DEPLOY)
        )
        self.assertIn("said no from the panel", self.notes_text())
        msg = notes.inbox(self.project)[-1]
        self.assertEqual((msg["from"], msg["read"]), ("owner", False))
        self.assertIn(f"Do not run again: `{DEPLOY}`", msg["text"])

    def test_page_words(self):
        """The page says what a request does in plain words, keeps the command folded and has a No."""
        root = Path(selftest.__file__).resolve().parents[2]
        js = (root / "panel/app.js").read_text()
        self.assertIn('onclick="grantDeny(this)"', js)
        self.assertIn('<details class="gcmd">', js)
        self.assertIn('onclick="seeGrants()"', js)
        for lang in ("en", "es"):
            st = json.loads((root / f"panel/strings.{lang}.json").read_text())
            for k in (
                "grant_no",
                "grant_req_say",
                "grant_say_push",
                "grant_say_release",
                "grant_see_cmd",
                "grant_wait_say",
            ):
                self.assertTrue(st[k], (lang, k))

    def test_page(self):
        """The You tab's section and its strings (the panel's own style: the page's source and both languages)."""
        root = Path(selftest.__file__).resolve().parents[2]
        js = (root / "panel/app.js").read_text()
        self.assertIn("grants: all('grants')", js)
        self.assertIn("api(`/api/grants/approve?g=${encodeURIComponent(b.dataset.g)}", js)
        self.assertIn("t(`grant_why_${j.why}`)", js)
        self.assertIn("tn('grants_wait', asks.length)", js)
        self.assertIn('<div id="grantreq"></div>', (root / "panel/index.html").read_text())
        for lang in ("en", "es"):
            st = json.loads((root / f"panel/strings.{lang}.json").read_text())
            for why in ("none", "expired", "session", "process", "live"):
                self.assertTrue(st[f"grant_why_{why}"], (lang, why))
            for k in (
                "grant_requests",
                "grant_req_title",
                "grant_req_when",
                "grant_approve",
                "confirm_grant",
                "grant_live",
                "grants_wait_1",
                "grants_wait_n",
            ):
                self.assertTrue(st[k], (lang, k))


class LateAnswerTest(PendCase):
    """A request left unanswered waits up to grants.PENDING_H; the owner's yes (panel, or late in the chat to that
    very question) opens the group for grant_minutes from the yes, only for the marked session and process that
    asked; a late no drops it; a request of another process is an orphan, never granted. Each test names its row of
    the acceptance table of the change's plan."""

    def setUp(self):
        super().setUp()
        self.inbox_before = len(self.inbox())

    def inbox(self):
        from foreman import notes

        return notes.inbox(self.project)

    def late(self, answer="yes", tid="T-late", **kw):
        return self.answer(answer, late=True, tool_use_id=tid, **kw)

    def assert_half_hour_from_now(self, until):
        left = util.fromiso(until) - util.now()
        self.assertTrue(timedelta(minutes=29) < left <= timedelta(minutes=30), left)

    def nothing_granted(self):
        self.assertEqual(grants.read(self.project)["grants"], {})
        self.assertEqual(self.why(DEPLOY)[0], "orchestrator")  # refused (for want of a grant, or earlier)

    def test_row1_a_request_of_8_hours_is_listed_and_its_approval_lasts_30_min_from_now(self):
        from foreman import panel

        self.pend()
        self.age("deploy", hours=8)
        self.assertIn("deploy", grants.pending(self.project))
        (r,) = panel.grant_requests(self.project)
        self.assertEqual((r["group"], r["pending"], r["command"]), ("deploy", True, DEPLOY))
        self.assertEqual(util.fromiso(r["expires"]) - util.fromiso(r["at"]), timedelta(hours=24))
        self.nothing_granted()  # row 13: 8 hours unanswered, nothing granted
        out = panel.grant_approve(self.project, "deploy")
        self.assert_half_hour_from_now(out["until"])
        self.assertIsNone(self.why(DEPLOY))  # the command passes

    def test_row2_row13_a_day_and_a_minute_later_it_is_gone_and_nothing_is_granted(self):
        from foreman import panel

        for answer in (grants.approve, grants.deny):
            self.pend()
            self.age("deploy", hours=24, minutes=1)
            self.assertEqual((grants.pending(self.project), grants.orphans(self.project)), ({}, {}))
            self.assertEqual(panel.grant_requests(self.project), [])
            with self.assertRaises(grants.NotApproved) as e:
                answer(self.project, "deploy")
            self.assertEqual(e.exception.why, "expired")
            self.assertEqual(grants.read(self.project)["pending"], {})  # dropped
            self.assertEqual(grants.read(self.project)["cooldowns"], {})
            self.nothing_granted()
        # nor late in the chat
        self.pend()
        self.age("deploy", hours=24, minutes=1)
        out = self.late()
        self.assertEqual(
            (out["granted"], self.log()[-1]["verdict"], self.log()[-1]["refused"]), (False, "late", "expired")
        )
        self.assertEqual(grants.read(self.project)["pending"], {})
        self.nothing_granted()

    def test_row3_a_late_yes_to_the_pending_request_grants_from_the_yes(self):
        self.pend()
        self.age("deploy", hours=8)
        asked = grants.read(self.project)["pending"]["deploy"]["at"]
        out = self.late()
        self.assertTrue(out["granted"], out)
        self.assert_half_hour_from_now(out["until"])
        self.assertIn("late yes to the request of", out["log"])
        self.assertNotIn("deny", out)
        g = grants.live(self.project, "deploy")
        self.assertEqual(
            (g["until"], g["via"], g["late"], g["asked"], g["tool_use_id"]),
            (out["until"], "ask", True, asked, "T-late"),
        )
        self.assertEqual(grants.read(self.project)["pending"], {})
        self.assertIsNone(self.why(DEPLOY))  # the command passes
        line = self.log()[-1]
        self.assertEqual(
            (line["verdict"], line["late"], line["asked"], line["until"], line["answer"], line["tool_use_id"]),
            ("grant", True, asked, out["until"], "yes", "T-late"),
        )
        self.assertIn("said yes late in the chat", self.notes_text())
        msg = self.inbox()[-1]
        self.assertEqual(
            (msg["from"], msg["grant"], msg["read"]), ("owner", {"group": "deploy", "until": out["until"]}, False)
        )
        self.assertIn(f"You may run again: `{DEPLOY}`", msg["text"])
        # the retry: the check says live (the module, which heard the yes, lets it on without asking)
        q = self.mod(phase="check", command=DEPLOY)
        self.assertEqual((q["live"], q["until"]), (True, out["until"]))
        # one request grants once: the same late yes again changes nothing (row 4)
        again = self.late()
        self.assertEqual(again["granted"], False)
        self.assertIn("already allowed", again["log"])
        self.assertEqual(grants.live(self.project, "deploy")["until"], out["until"])

    def test_row3_only_from_the_marked_process(self):
        """The late yes's call must run in the mark's process (orchestrator.runs_here); `ps` unreadable: nothing."""
        self.pend()
        self.reparent(COPY_PID)  # this call now hangs from another claude
        self.assertEqual(self.late()["granted"], False)
        self.assertEqual(self.log()[-1]["refused"], "process")
        self.ps_rows = None
        self.assertEqual(self.late()["granted"], False)
        self.assertEqual(self.log()[-1]["refused"], "process")
        self.nothing_granted()
        self.assertIn("deploy", grants.read(self.project)["pending"])  # still there for the owner
        self.start_ps()
        self.assertTrue(self.late()["granted"])

    def test_row5_no_request_or_another_question_grants_nothing(self):
        self.pend()
        out = self.late(tid="T-other")  # another question's late yes
        self.assertEqual(
            (out["granted"], self.log()[-1]["verdict"], self.log()[-1]["refused"]), (False, "late", "none")
        )
        self.assertIn("deploy", grants.pending(self.project))
        self.assertEqual(self.late(group="stores")["granted"], False)  # another group, no request of its own
        grants.deny(self.project, "deploy")  # refused from the panel meanwhile
        out = self.late()
        self.assertEqual((out["granted"], self.log()[-1]["verdict"]), (False, "late"))
        self.pend()
        grants.revoke(self.project, "deploy", pending_only=True)  # withdrawn by the orchestrator
        self.assertEqual(self.late()["granted"], False)
        self.nothing_granted()

    def test_row6_row14_a_late_yes_from_a_session_that_is_not_the_mark(self):
        self.pend()
        self.assertEqual(self.late(session="S2")["granted"], False)  # an unmarked session (row 14)
        self.assertEqual(self.log()[-1]["refused"], "session")
        orchestrator.mark(self.project, "S9", "other")  # the mark in another session of another process (row 6)
        self.assertEqual(self.late()["granted"], False)
        self.assertEqual(self.log()[-1]["refused"], "session")
        self.nothing_granted()
        self.assertEqual(len(self.inbox()), self.inbox_before)

    def test_row7_a_late_no_drops_the_request_and_cools_down(self):
        self.pend()
        self.age("deploy", hours=3)
        asked = grants.read(self.project)["pending"]["deploy"]["at"]
        out = self.late("no")
        self.assertEqual(out["granted"], False)
        self.assertNotIn("deny", out)
        self.assertIn("said no (late)", out["log"])
        d = grants.read(self.project)
        self.assertEqual((d["pending"], d["grants"]), ({}, {}))
        self.assertEqual(d["cooldowns"]["deploy"]["session"], "S1")
        self.assertIn("will not ask again before", self.mod(phase="check", command=DEPLOY)["deny"])
        line = self.log()[-1]
        self.assertEqual((line["verdict"], line["late"], line["asked"], line["answer"]), ("deny", True, asked, "no"))
        self.assertEqual(line["cooldown_until"], d["cooldowns"]["deploy"]["until"])
        self.assertIn("said no late in the chat", self.notes_text())
        msg = self.inbox()[-1]
        self.assertEqual(msg["from"], "owner")
        self.assertNotIn("grant", msg)
        self.assertIn("stays closed", msg["text"])

    def test_row7_a_late_no_from_another_session_changes_nothing(self):
        self.pend()
        self.late("no", session="S2")
        self.assertEqual(self.log()[-1]["verdict"], "late")
        self.assertIn("deploy", grants.pending(self.project))
        self.assertEqual(grants.read(self.project)["cooldowns"], {})

    def test_row8_a_late_dismissal_leaves_the_request(self):
        self.pend()
        for answer in ("dismissed", "timeout", "unavailable"):
            out = self.late(answer)
            self.assertEqual((out["granted"], self.log()[-1]["verdict"]), (False, "late"), answer)
            self.assertNotIn("refused", self.log()[-1])
        self.assertIn("deploy", grants.pending(self.project))
        self.assertEqual(grants.read(self.project)["cooldowns"], {})
        self.nothing_granted()

    def test_row9_a_request_of_a_process_that_ended_is_an_orphan(self):
        self.pend()
        self.reparent(COPY_PID)  # the orchestrator restarted: a new claude process marks itself
        orchestrator.mark(self.project, "S2", "fake-2", own_session="S2")
        self.assertEqual(orchestrator.record(self.project)["pid"], COPY_PID)
        self.assertEqual(grants.pending(self.project), {})
        o = grants.orphans(self.project)["deploy"]
        self.assertEqual((o["why"], o["session"], o["command"]), ("process", "S1", DEPLOY))
        self.assertIn("a request of another process since", grants.lines(self.project)[0])
        with self.assertRaises(grants.NotApproved) as e:
            grants.approve(self.project, "deploy")
        self.assertEqual(e.exception.why, "process")
        self.assertIn("deploy", grants.orphans(self.project))  # kept, for the owner to drop
        self.assertEqual(self.late(session="S2")["granted"], False)  # the new session's yes: not its request
        self.assertEqual(self.late(session="S1")["granted"], False)  # the old session: not the mark
        self.nothing_granted()
        # discarding it (the panel's no) drops it and cools nothing
        with self.assertRaises(grants.NotApproved) as e:
            grants.deny(self.project, "deploy")
        self.assertEqual(e.exception.why, "expired")
        d = grants.read(self.project)
        self.assertEqual((d["pending"], d["cooldowns"], d["grants"]), ({}, {}, {}))

    def test_row10_a_clear_in_the_same_process_carries_the_request(self):
        self.pend()
        hooks_mod.carry_mark(self.project, "S1", "S2")  # /clear: the module starts again under S2
        self.assertEqual(orchestrator.record(self.project)["session"], "S2")
        r = grants.pending(self.project)["deploy"]
        self.assertEqual((r["session"], r["tool_use_id"]), ("S2", "T-late"))
        self.assertEqual(grants.orphans(self.project), {})
        self.assertEqual(grants.read(self.project)["pending"]["deploy"]["carried_from"], ["S1"])
        out = self.late(session="S2")  # the module re-reads the session id for a late answer
        self.assertTrue(out["granted"], out)
        self.assertEqual(grants.live(self.project, "deploy")["session"], "S2")
        # and from the panel too
        grants.revoke(self.project, "deploy")
        self.answer("timeout", tool_use_id="T-2", session="S2")
        hooks_mod.carry_mark(self.project, "S2", "S3")
        g, _ = grants.approve(self.project, "deploy")
        self.assertEqual(g["session"], "S3")

    def test_row10_a_late_answer_under_the_id_it_was_asked_with_applies_after_a_clear(self):
        """A module that still names the old id (it asked under it) reaches the carried request: same process and
        question only; the grant is the mark's (S2), and a late no cools down S2, the session that goes on asking."""
        self.pend()
        hooks_mod.carry_mark(self.project, "S1", "S2")
        self.reparent(COPY_PID)  # from another process: nothing, even with the old id
        self.assertEqual(self.late(session="S1")["granted"], False)
        self.assertEqual(self.log()[-1]["refused"], "process")
        self.start_ps()
        self.assertEqual(self.late(session="S1", tid="T-other")["granted"], False)  # another question
        self.assertEqual(self.late(session="S7")["granted"], False)  # an id it was never carried from
        self.assertEqual(self.log()[-1]["refused"], "session")
        self.nothing_granted()
        out = self.late(session="S1")
        self.assertTrue(out["granted"], out)
        g = grants.live(self.project, "deploy")
        self.assertEqual((g["session"], g["pid"]), ("S2", orchestrator.record(self.project)["pid"]))
        # a late no under the old id
        grants.revoke(self.project, "deploy")
        self.answer("timeout", tool_use_id="T-2", session="S2")
        hooks_mod.carry_mark(self.project, "S2", "S3")
        self.assertIn("said no (late)", self.late("no", tid="T-2", session="S2")["log"])
        d = grants.read(self.project)
        self.assertEqual((d["pending"], d["cooldowns"]["deploy"]["session"]), ({}, "S3"))

    def test_row10_an_orphan_is_not_reached_through_its_old_id(self):
        """A carried request whose process is no longer the mark's (the orchestrator restarted after the /clear)."""
        self.pend()
        hooks_mod.carry_mark(self.project, "S1", "S2")
        self.reparent(COPY_PID)
        orchestrator.mark(self.project, "S3", "fake-3", own_session="S3")
        for session in ("S1", "S2", "S3"):
            self.assertEqual(self.late(session=session)["granted"], False, session)
        self.nothing_granted()
        self.assertEqual(grants.orphans(self.project)["deploy"]["why"], "process")

    def test_row10_a_carry_from_another_process_moves_nothing(self):
        self.pend()
        self.reparent(COPY_PID)
        hooks_mod.carry_mark(self.project, "S1", "S2")
        self.assertEqual(orchestrator.record(self.project)["session"], "S1")  # the mark did not move
        self.assertEqual(grants.read(self.project)["pending"]["deploy"]["session"], "S1")
        self.assertEqual(grants.carry(self.project, "S1", "S2"), [])  # the mark is not S2: nothing moves

    def test_row12_the_owners_late_yes_brings_the_pass_at_the_next_tick(self):
        import time
        from datetime import datetime

        beat = datetime.fromtimestamp(time.time() - 120).strftime("%Y-%m-%dT%H:%M:%S")  # a pass 2 min ago
        util.write_json(self.project.state / "heartbeats.json", {"loop": beat})
        o = {"session": "S1", "since": datetime.fromtimestamp(time.time() - 3600).strftime("%Y-%m-%dT%H:%M:%S")}
        self.pend()
        self.assertFalse(hooks_mod.pass_due(self.project, o))
        self.assertTrue(self.late()["granted"])
        self.assertTrue(hooks_mod.pass_due(self.project, o))

    def test_withdraw_a_pending_request(self):
        """`foreman permissions grants off GROUP --pending`: only the request goes; a live grant stays."""
        self.pend()
        self.answer("yes", group="stores")
        r = self.cli("permissions", "grants", "off", "--pending", env={"CLAUDE_CODE_SESSION_ID": "S7"})
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "withdrawn: deploy"), r.stderr)
        d = grants.read(self.project)
        self.assertEqual((d["pending"], sorted(d["grants"])), ({}, ["stores"]))
        self.assertEqual(self.log()[-1], {**self.log()[-1], "verdict": "withdraw", "group": "deploy", "session": "S7"})
        self.assertIn("pending requests withdrawn", self.notes_text())
        r = self.cli("permissions", "grants", "off", "deploy", "--pending")
        self.assertEqual(r.stdout.strip(), "no pending request to withdraw")
        self.assertEqual(self.cli("permissions", "grants", "--pending").returncode, 2)
        self.assertEqual(self.cli("permissions", "--pending").returncode, 2)

    def test_the_timeout_refusal_says_what_comes_next(self):
        out = self.answer("timeout", tool_use_id="T-x")
        self.assertIn("for up to 24 h", out["deny"])
        self.assertIn("comes to your inbox", out["deny"])
        self.assertIn("foreman permissions grants off deploy --pending", out["deny"])


ORCH = "S-orch"  # the session the contract's grant cases mark
# What a case needs beyond the mark, by its name.
CONTRACT_WORLDS = {
    "a live grant in grants.json the module has not heard given: asked again": lambda case: grants.give(
        case.project, "deploy", orchestrator.record(case.project), "ask", [], None
    ),
    "a live grant the owner approved from the panel: no question": lambda case: (
        hooks_mod.grant(
            {"cwd": str(case.home), "session": ORCH, "phase": "answer", "group": "deploy", "answer": "timeout"}
        ),
        grants.approve(case.project, "deploy"),
    ),
    "the group cools down after the owner's no: refused, no question": lambda case: grants.cool(
        case.project, "deploy", ORCH
    ),
}
TIMES = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d|\b\d\d:\d\d\b")


class ContractTest(GrantCase):
    """Every `grant` case of tests/mod/contract.ts with a Python side: its stdin to `hook mod grant` (in this process,
    so the simulated `ps` reaches it), on a fresh state with ORCH marked by its own process and the case's world, must
    answer the case's `run.stdout` byte for byte, times aside (the case says «10:30»)."""

    def run_op(self, stdin):
        out = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(stdin)), contextlib.redirect_stdout(out):
            rc = hooks_mod.run(["grant"])
        return rc, out.getvalue()

    def test_contract(self):
        cases = [c for c in selftest.mod_contract() if c["op"] == "grant" and c["python"]]
        self.assertTrue(cases)
        self.assertEqual(sorted(set(CONTRACT_WORLDS) - {c["name"] for c in cases}), [])
        for c in cases:
            with self.subTest(c["name"]):
                shutil.rmtree(self.project.state, ignore_errors=True)
                orchestrator.mark(self.project, ORCH, "orq", own_session=ORCH)
                if c["name"] in CONTRACT_WORLDS:
                    CONTRACT_WORLDS[c["name"]](self)
                stdin = {
                    k: v.replace("<project>", str(self.home)) if isinstance(v, str) else v
                    for k, v in c["stdin"].items()
                }
                rc, out = self.run_op(json.dumps(stdin))
                out = out.replace(str(self.home), "<project>")  # a file case's question names the file
                self.assertEqual((rc, TIMES.sub("<T>", out)), (0, TIMES.sub("<T>", c["run"]["stdout"])))
