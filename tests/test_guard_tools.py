"""The guard on any tool but Bash and the write tools (guard.tool_texts, check_tool, check_call): the places and
commands a call names, judged as `cat PLACE` and as the Bash command; free text is never compared; the hook's
refusal, its log lines and the pause; the `tool:` rows of a table."""

import json
import os
import time
from pathlib import Path
from unittest import mock

from helpers import ProjectCase

from foreman import guard

PUB = "npm " + "publish"  # built in pieces: the installed guard reads this file's commands

TOML = r"""
[project]
name = "fake"
owner = "Owner"

[paths]
worktrees = "{tmp}/wt"

[agents]
production_slots = 1
ready_target = 1

[resources.sim]
guard = ["simctl boot"]

[deny]
patterns = ['npm publish', '~/vault/', '(^|[\s/])\.env(\s|$)']

[deny.grants]
secrets = ['\.ssh/']
"""


class ToolCase(ProjectCase):
    toml = TOML

    def setUp(self):
        super().setUp()
        self.saved_home = os.environ.get("HOME")
        self.user = self.tmp / "user"
        self.user.mkdir()
        os.environ["HOME"] = str(self.user)  # the `~/…` form of a path comes from this test's HOME

    def tearDown(self):
        if self.saved_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self.saved_home
        super().tearDown()

    def got(self, tool, ti, role="agent"):
        """allow | deny | orchestrator (a refusal of a group) | lock of a call in a role."""
        v = guard.check_call(self.project, tool, ti, guard.as_role(role, str(self.home)))
        return "allow" if v is None else v[0]


class ToolTextsTest(ToolCase):
    def test_places_commands_and_text(self):
        places, commands = guard.tool_texts(
            "mcp__x__y",
            {
                "file_path": "/a/f",
                "targetPath": "/a/t",
                "URL": "https://x/y",
                "files": {"pub/page.html": "src/page.html", "pub/x.css": {"from": "src/x.css"}},
                "paths": ["/a/p1", "/a/p2"],
                "cwd": "/a",
                "content": "cat .env",
                "prompt": "~/.ssh/id",
                "message": PUB,
                "pattern": "*.env",
                "count": 3,
                "nested": {"input": {"url": "https://n"}, "text": "free"},
                "command": "ls",
            },
        )
        self.assertEqual(
            sorted(places),
            sorted(["/a/f", "/a/t", "https://x/y", "src/page.html", "src/x.css", "/a/p1", "/a/p2", "/a", "https://n"]),
        )
        self.assertEqual(commands, ["ls"])
        self.assertEqual(guard.tool_texts("Bash", {"command": "ls"}), ([], []))  # check() reads Bash's
        self.assertEqual(guard.tool_texts("Monitor", {"cmd": "tail x", "timeout": 5}), ([], ["tail x"]))
        for garbage in (None, "x", 3, [1, None], {"file_path": None, "": "y"}):
            self.assertEqual(guard.tool_texts("T", garbage), ([], []), garbage)

    def test_bounded(self):
        deep = {"a": {"b": {"c": {"d": {"file_path": "/too/deep"}}}}}
        self.assertEqual(guard.tool_texts("T", deep)[0], [])
        self.assertEqual(guard.tool_texts("T", {"a": {"b": {"file_path": "/ok"}}})[0], ["/ok"])
        many = {"paths": [f"/p{i}" for i in range(500)]}
        self.assertEqual(len(guard.tool_texts("T", many)[0]), guard.TOOL_STRINGS)


class CheckToolTest(ToolCase):
    def test_parity_with_cat(self):
        """A place gets the verdict of `cat PLACE`, in both roles."""
        places = (".env", "/x/.env", "/x/.env.example", "/x/.ssh/id_ed25519", "/x/README.md", f"{self.user}/.ssh/k")
        for place in places:
            for role in ("agent", "orchestrator"):
                with self.subTest(place=place, role=role):
                    v = guard.check(self.project, "cat " + place, guard.as_role(role, str(self.home)))
                    want = "allow" if v is None else v[0]
                    self.assertEqual(self.got("Read", {"file_path": place}, role), want)
                    self.assertEqual(self.got("Grep", {"pattern": "TOKEN", "path": place}, role), want)
        self.assertEqual(self.got("Read", {"file_path": "/x/.env"}), "deny")
        self.assertEqual(self.got("Read", {"file_path": "/x/.ssh/id_ed25519"}), "orchestrator")
        self.assertEqual(self.got("Read", {"file_path": "/x/.ssh/id_ed25519"}, "orchestrator"), "allow")
        self.assertEqual(self.got("Read", {"file_path": "/x/.env.example"}), "allow")

    def test_home_form(self):
        """A rule written with `~` sees the absolute path a Read names."""
        self.assertEqual(self.got("Read", {"file_path": f"{self.user}/vault/key"}), "deny")
        self.assertEqual(self.got("Read", {"file_path": f"{self.user}/vaulted/key"}), "allow")
        self.assertEqual(self.got("Read", {"file_path": "/elsewhere/vault/key"}), "allow")
        self.assertEqual(guard._home_form(f"{self.user}/a b/c"), "~/'a b/c'")
        self.assertIsNone(guard._home_form(f"{self.user}x/y"))

    def test_any_tool_one_rule(self):
        self.assertEqual(self.got("mcp__x__y", {"targetPath": "~/.ssh/id_ed25519"}), "orchestrator")
        self.assertEqual(self.got("Monitor", {"command": PUB}), "deny")
        self.assertEqual(self.got("bash", {"command": "simctl boot x"}), "lock")  # a command keeps the lock check
        self.assertEqual(self.got("mcp__x__y", {"file_path": "simctl boot"}), "allow")  # a place runs nothing
        self.assertEqual(self.got("mcp__x__y", {"message": "cat .env"}), "allow")  # free text
        self.assertEqual(self.got("Agent", {"prompt": f"never {PUB}; never read .env", "description": "x"}), "allow")
        self.assertEqual(self.got("WebFetch", {"url": "http://localhost:8766/api/grants/approve"}), "deny")  # built-in
        self.assertEqual(self.got("mcp__ccd_turn__wrap_up", {"recap": "read .env"}), "allow")

    def test_hit_says_where(self):
        v = guard.check_tool(self.project, "Read", {"file_path": "/x/.env"})
        self.assertEqual((v[1].where, v[1].part, v[1].tool, v[1].call), ("tool", "Read /x/.env", "Read", "/x/.env"))
        msg = guard.message(self.project, v)
        self.assertIn("It matched in the place `Read /x/.env` the tool names", msg)
        self.assertIn("is not compared", msg)
        v = guard.check_tool(self.project, "Monitor", {"command": PUB})
        self.assertEqual((v[1].where, v[1].tool, v[1].call), ("command", "Monitor", PUB))

    def test_writing_a_secret_as_reading_it(self):
        """A write tool's file gets the verdict of reading it, after the reserved files; its text is never compared."""
        for tool, ti in (
            ("Write", {"file_path": "/x/.env", "content": "A=1"}),
            ("Edit", {"file_path": "/x/.env", "old_string": "a", "new_string": "b"}),
            ("NotebookEdit", {"notebook_path": "/x/.env", "new_source": "x"}),
        ):
            self.assertEqual(self.got(tool, ti), "deny", tool)
        self.assertEqual(self.got("Write", {"file_path": "/x/.ssh/config", "content": "x"}), "orchestrator")
        self.assertEqual(self.got("Write", {"file_path": "/x/.ssh/config", "content": "x"}, "orchestrator"), "allow")
        self.assertEqual(self.got("Write", {"file_path": "/x/.env.example", "content": "cat .env"}), "allow")
        v = guard.check_call(
            self.project, "Write", {"file_path": "foreman.toml"}, guard.as_role("agent", str(self.home))
        )
        self.assertEqual(v[0], "file")  # the reserved files answer first

    def test_failure_never_blocks_a_read(self):
        with mock.patch.object(guard, "check", return_value=("deny", "(the guard could not check this command: X)")):
            self.assertIsNone(guard.check_tool(self.project, "Read", {"file_path": "/x/README.md"}))
            v = guard.check_tool(self.project, "Read", {"file_path": "/x/.env"})  # its raw text still matches
            self.assertEqual((v[0], v[1].where), ("deny", "tool"))
        with mock.patch.object(guard, "tool_texts", side_effect=RuntimeError):
            self.assertIsNone(guard.check_tool(self.project, "Read", {"file_path": "/x/.env"}))

    def test_cost(self):
        """In process, a long Agent prompt (free text) costs nothing; a Read, two short checks."""
        prompt = {"prompt": "word " * 4000, "description": "x", "subagent_type": "dev"}
        for ti, tool in ((prompt, "Agent"), ({"file_path": f"{self.user}/a/b.py"}, "Read")):
            t = time.perf_counter()
            for _ in range(20):
                guard.check_tool(self.project, tool, ti)
            self.assertLess((time.perf_counter() - t) / 20, 0.05, tool)  # ~0.1-0.5 ms measured; a wide bound


class ToolHookTest(ToolCase):
    def event(self, tool, ti, **extra):
        return {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": ti, **extra}

    def lines(self, name):
        f = self.project.state / name
        return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []

    def test_refusal_and_logs(self):
        r = self.hook("guard", self.event("Read", {"file_path": "/x/.env"}, session_id="S1"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("Read /x/.env", r.stderr)
        log = self.lines("guard.jsonl")
        self.assertEqual((log[-1]["tool"], log[-1]["where"], log[-1]["kind"]), ("Read", "tool", "deny"))
        r = self.hook(
            "guard",
            self.event("Grep", {"path": "/x/.ssh/", "pattern": "secret words"}, session_id="S1", agent_id="a1"),
        )
        self.assertEqual(r.returncode, 2)
        rec = self.lines("approved.jsonl")[-1]
        self.assertEqual((rec["tool"], rec["place"], rec["why"]), ("Grep", "/x/.ssh/", "agent"))
        self.assertNotIn("secret words", json.dumps(rec))
        self.assertNotIn("command", rec)
        r = self.hook("guard", self.event("Monitor", {"command": PUB, "description": "d"}, agent_id="a1"))
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.lines("guard.jsonl")[-1]["tool"], "Monitor")

    def test_passes(self):
        for tool, ti in (
            ("Read", {"file_path": "/x/README.md"}),
            ("mcp__ccd_turn__wrap_up", {"recap": f"no {PUB}"}),
            ("Write", {"file_path": str(self.home / "notes.md"), "content": f"cat .env; {PUB}"}),
            ("Edit", {"file_path": str(self.home / "notes.md"), "old_string": "a", "new_string": PUB}),
            ("Read", "not a dict"),
        ):
            r = self.hook("guard", self.event(tool, ti))
            self.assertEqual((r.returncode, r.stderr), (0, ""), tool)

    def test_pause_holds_a_tool_command(self):
        self.project.state.mkdir(parents=True, exist_ok=True)
        (self.project.state / "pause").write_text("paused at 21:07\n")
        r = self.hook("guard", self.event("Monitor", {"command": "foreman integrate app br AB-1"}, session_id="S"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("paused", r.stderr)
        self.assertEqual(self.hook("guard", self.event("Read", {"file_path": "/x/a"})).returncode, 0)


class ToolTableTest(ToolCase):
    def test_rows(self):
        table = Path(self.home / "guard.tsv")
        table.write_text(
            'deny\ttool:Read {"file_path": "/x/.env"}\n'
            'orchestrator\ttool:Grep {"path": "~/.ssh/", "pattern": "k"}\n'
            'allow\ttool:Read {"file_path": "/x/.env.example"}\n'
            f'deny\ttool:Monitor {{"command": "{PUB}"}}\n'
            'allow\ttool:mcp__x__y {"message": "cat .env"}\n'
            'deny\ttool:mcp__x__y {"targetPath": "{state}/../.env"}\n'
            "allow\ttool:mcp__ccd_turn__wrap_up\n"
            'deny\ttool:Write {"file_path": "foreman.toml", "content": "x"}\n'
        )
        r = self.cli("guard", "--table", str(table))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("ok (4 deny, 0 lock, 3 allow, 1 orchestrator)", r.stdout)
        for bad in ("tool:Read {nope", "tool:Read [1]", "tool:"):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(ValueError, "line 1: expected 'tool:NAME"):
                    guard.parse_table(f"allow\t{bad}\n")
