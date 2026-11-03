"""The owner's one-off yes for Claude Code's settings and the registry: `hook mod grant` asks about a
main-thread call of any session that writes one (a file tool, or a Bash command), its answer phase writes a one-use
grant (grants.give_once) and the guard spends it on that very call (grants.take_once, from guard.check_file and the
Bash rule): never twice, never for another call, file, session or an agent; config, table and state are never
asked about."""

import contextlib
import io
import json
from datetime import timedelta

from test_guard_files import TABLE, FileCase

from foreman import approved, grants, guard, hooks, hooks_mod, util


class OnceCase(FileCase):
    def setUp(self):
        super().setUp()
        self.mark("S1")
        self.settings = self.home / ".claude/settings.local.json"

    def ctx(self, session="S1", agent=None, tid="T1"):
        return {"session": session, "agent": agent, "cwd": str(self.home), "tool_use_id": tid}

    def give(self, path=None, tid="T1", session="S1", kind="claude", command=None):
        real = None if command is not None else guard.real_path(str(path or self.settings))
        return grants.give_once(self.project, session, tid, kind, real, command)

    def kind(self, path, **kw):
        v = guard.check_file(self.project, str(path), self.ctx(**kw))
        return None if v is None else v[1].kind


class SpendTest(OnceCase):
    def test_one_call_once(self):
        self.give()
        self.assertEqual(self.kind(self.settings, tid="T2"), "claude")  # another call: not this one's
        self.assertIsNone(self.kind(self.settings))  # the call it was given for
        self.assertEqual(self.kind(self.settings), "claude")  # spent: never twice
        self.assertEqual(grants.read(self.project)["once"], {})

    def test_any_session_main_thread_never_an_agent(self):
        self.give(session="S9")
        self.assertEqual(self.kind(self.settings, session="S9", agent="a1"), "claude")
        self.assertEqual(self.kind(self.settings, session="S1"), "claude")  # another session's yes
        self.assertIn("T1", grants.read(self.project)["once"])  # neither spent it
        self.assertIsNone(self.kind(self.settings, session="S9"))  # a session that is not the marked one

    def test_the_same_file_only(self):
        self.give()
        self.assertEqual(self.kind(self.user / ".claude/settings.json"), "claude")
        self.assertIsNone(self.kind(".claude/settings.local.json"))  # relative to the cwd: the same file

    def test_the_registry_too(self):
        self.give(self.tmp / "projects.toml", kind="registry")
        self.assertIsNone(self.kind(self.tmp / "projects.toml"))

    def test_only_settings_and_registry(self):
        for kind in ("config", "table", "state"):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                grants.give_once(self.project, "S1", "T1", kind, "/x", None)
        # a one-use grant of grants.json written by hand for a config file is not spent by it
        d = grants.read(self.project)
        d["once"]["T1"] = {"session": "S1", "kind": "config", "path": guard.real_path(str(self.home / "foreman.toml"))}
        util.write_json(grants.file(self.project), d)
        self.assertEqual(self.kind(self.home / "foreman.toml"), "config")

    def test_stale(self):
        self.give()
        d = grants.read(self.project)
        d["once"]["T1"]["at"] = (util.now() - timedelta(seconds=grants.ONCE_S + 1)).strftime(grants.ISO)
        util.write_json(grants.file(self.project), d)
        self.assertEqual(self.kind(self.settings), "claude")
        self.assertEqual(grants.read(self.project)["once"], {})

    def test_no_tool_use_id_spends_nothing(self):
        self.give()
        self.assertEqual(self.kind(self.settings, tid=None), "claude")  # a table run, `guard --file`, the check phase
        self.assertIn("T1", grants.read(self.project)["once"])

    def test_bash_by_command(self):
        cmd = "echo '{}' > .claude/settings.local.json && cp x ~/.claude.json"
        self.give(command=cmd)
        self.assertEqual(guard.check(self.project, cmd + " ", self.ctx())[0], "file")  # another command
        self.assertIsNone(guard.check(self.project, cmd, self.ctx()))
        self.assertEqual(guard.check(self.project, cmd, self.ctx())[1].kind, "claude")  # spent
        self.give(command=f"{cmd} && cp x foreman.toml")
        v = guard.check(self.project, f"{cmd} && cp x foreman.toml", self.ctx())
        self.assertEqual(v[1].kind, "config")  # the yes covers settings and registry, never the config

    def test_revoke_clears(self):
        self.give()
        grants.revoke(self.project)
        self.assertEqual(grants.read(self.project)["once"], {})

    def test_message_for_a_main_thread(self):
        def msg(**kw):
            return guard.message(self.project, guard.check_file(self.project, str(self.settings), self.ctx(**kw)))

        self.assertIn("question foreman asks in this chat", msg(session="S5"))
        self.assertIn("Only Owner changes it, by hand", msg(agent="a1"))


class ModTest(OnceCase):
    def grant(self, **ev):
        return hooks_mod.grant({"cwd": str(self.home), "session": "S5", **ev})

    def guard_hook(self, tool, path, tid="T1", agent=None):
        ev = {
            "hook_event_name": "PreToolUse",
            "tool_name": tool,
            "session_id": "S5",
            "tool_use_id": tid,
            "cwd": str(self.home),
            "tool_input": {"file_path": str(path), "content": "{}"},
            **({"agent_id": agent} if agent else {}),
        }
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            return hooks.hook_guard(self.project, ev), err.getvalue()

    def test_check_asks_only_for_settings_and_registry(self):
        q = self.grant(phase="check", tool="Write", path=str(self.settings), tool_use_id="T1")
        self.assertEqual((q["group"], q["live"], q["yes"]), ("file:claude", False, "Yes, this change only"))
        self.assertIn(f"write `{self.settings}`, Claude Code's own settings", q["question"])
        q = self.grant(phase="check", tool="Edit", path=str(self.tmp / "projects.toml"), tool_use_id="T1")
        self.assertEqual(q["group"], "file:registry")
        for path in (self.home / "foreman.toml", self.home / TABLE, self.project.state / "x", self.home / "a.md"):
            with self.subTest(path=str(path)):
                self.assertEqual(self.grant(phase="check", tool="Write", path=str(path), tool_use_id="T1"), {})

    def test_yes_then_the_guard_lets_that_call_once(self):
        self.assertEqual(self.guard_hook("Write", self.settings)[0], 2)
        out = self.grant(
            phase="answer",
            tool="Write",
            path=str(self.settings),
            tool_use_id="T1",
            group="file:claude",
            answer="yes",
            surfaces=["desktop"],
        )
        self.assertTrue(out["granted"])
        self.assertEqual(self.guard_hook("Write", self.settings, agent="a1")[0], 2)  # an agent's: never
        self.assertEqual(self.guard_hook("Write", self.settings), (0, ""))
        rc, err = self.guard_hook("Write", self.settings)
        self.assertEqual(rc, 2)
        self.assertIn("question foreman asks in this chat", err)
        log = [json.loads(x) for x in approved.log_file(self.project).read_text().splitlines()]
        grant = [r for r in log if r.get("verdict") == "grant"]
        self.assertEqual(
            [(r["group"], r["answer"], r["file"], r["tool_use_id"]) for r in grant],
            [("file:claude", "yes", str(self.settings), "T1")],
        )

    def test_no_and_unavailable_grant_nothing(self):
        for answer in ("no", "dismissed", "timeout", "unavailable"):
            with self.subTest(answer=answer):
                out = self.grant(
                    phase="answer",
                    tool="Write",
                    path=str(self.settings),
                    tool_use_id="T1",
                    group="file:claude",
                    answer=answer,
                    surfaces=[],
                )
                self.assertFalse(out["granted"])
                self.assertIn("Blocked", out["deny"])
                self.assertEqual(grants.read(self.project)["once"], {})
                self.assertEqual(grants.read(self.project)["cooldowns"], {})  # no cooldown for a file
        self.assertIn("nobody could be asked", out["deny"])

    def test_late_answer_grants_nothing(self):
        out = self.grant(
            phase="answer",
            tool="Write",
            path=str(self.settings),
            tool_use_id="T1",
            group="file:claude",
            answer="yes",
            surfaces=[],
            late=True,
        )
        self.assertEqual(out["granted"], False)
        self.assertIn("nothing was allowed", out["log"])
        self.assertEqual(grants.read(self.project)["once"], {})

    def test_answer_for_a_call_that_no_longer_writes_one(self):
        out = self.grant(
            phase="answer",
            tool="Write",
            path=str(self.home / "a.md"),
            tool_use_id="T1",
            group="file:claude",
            answer="yes",
            surfaces=[],
        )
        self.assertEqual(out, {"granted": False})
        self.assertEqual(grants.read(self.project)["once"], {})

    def test_bad_answers(self):
        with self.assertRaises(ValueError):
            self.grant(phase="answer", tool="Write", path=str(self.settings), group="file:config", answer="yes")
        with self.assertRaises(ValueError):
            self.grant(phase="answer", tool="Write", path=str(self.settings), group="file:claude", answer="maybe")
        with self.assertRaises(ValueError):  # a yes needs the call's id
            self.grant(phase="answer", tool="Write", path=str(self.settings), group="file:claude", answer="yes")

    def test_bash(self):
        cmd = "echo '{}' > .claude/settings.local.json"
        q = self.grant(phase="check", command=cmd, tool_use_id="T1")
        self.assertEqual(q["group"], "file:claude")
        self.assertIn(f"run `{cmd}`", q["question"])
        long = cmd + " # " + "x" * hooks_mod.FILE_COMMAND_CHARS
        self.assertIn("longer than", self.grant(phase="check", command=long, tool_use_id="T1")["deny"])
        self.assertEqual(self.grant(phase="check", command="echo x > foreman.toml", tool_use_id="T1"), {})
        out = self.grant(phase="answer", command=cmd, tool_use_id="T1", group="file:claude", answer="yes", surfaces=[])
        self.assertTrue(out["granted"])
        ev = {"tool_name": "Bash", "session_id": "S5", "tool_use_id": "T1", "cwd": str(self.home)}
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(hooks.hook_guard(self.project, {**ev, "tool_input": {"command": cmd}}), 0)
            self.assertEqual(hooks.hook_guard(self.project, {**ev, "tool_input": {"command": cmd}}), 2)
