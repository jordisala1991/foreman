"""The guard on Write, Edit, MultiEdit and NotebookEdit (guard.check_file): the reserved files of the
machine (config, table, state, registry, claude), who writes none of them (the orchestrator its own board only), how a
path is compared (relative, symbolic and hard links, case), what a failure of the check does (O7), the hook's
refusal with its log lines and the orchestrator's notice, `foreman guard --file` and the `file:` rows of a table."""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

from helpers import ProjectCase

from foreman import approved, config, guard, loop, orchestrator, permissions

TABLE = "tests/guard.tsv"


class FileCase(ProjectCase):
    """A fake project with a guard table, a second registered project with its state outside the state root, and a
    fake HOME (so ~/.claude and the default registry are this test's)."""

    def setUp(self):
        super().setUp()
        self.saved_home = {k: os.environ.get(k) for k in ("HOME", "CLAUDE_CONFIG_DIR")}
        os.environ.pop("CLAUDE_CONFIG_DIR", None)
        self.user = self.tmp / "user"
        (self.user / ".claude/projects/p/memory").mkdir(parents=True)
        (self.user / ".claude/plans").mkdir()
        os.environ["HOME"] = str(self.user)
        (self.home / "tests").mkdir()
        (self.home / TABLE).write_text("allow\tls\n")
        self.write_toml(self.toml + f'\n[check]\nguard_table = "{TABLE}"\n')
        self.other = self.tmp / "other"
        self.other.mkdir()
        (self.other / "foreman.toml").write_text(
            f'[project]\nname = "other"\nowner = "Owner"\n\n[paths]\nstate = "{self.tmp}/other-state"\n'
        )
        with open(self.tmp / "projects.toml", "a") as f:
            f.write(f'\n[[project]]\nname = "other"\nhome = "{self.other}"\nmode = "active"\n')
        self.state = Path(os.environ["FOREMAN_STATE_HOME"])

    def tearDown(self):
        for k, v in self.saved_home.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        super().tearDown()

    def verdict(self, path, role="agent", cwd=None):
        """("allow" | "deny", class) of a write of path in a role (a table run's ctx)."""
        v = guard.check_file(self.project, str(path), guard.as_role(role, str(cwd or self.home)))
        if v is None:
            return "allow", ""
        return "deny", v[1].kind if isinstance(v[1], guard.Hit) else "error"

    def mark(self, session="S1"):
        orchestrator.mark(self.project, session, "fake-1", own_session=session)


class ReservedTest(FileCase):
    def test_each_class_for_agent_and_orchestrator(self):
        cases = {
            "config": [self.home / "foreman.toml", self.other / "foreman.toml"],
            "table": [self.home / TABLE],
            "state": [self.state / "fake/grants.json", self.state / "callers.json", self.tmp / "other-state/x.md"],
            "registry": [self.tmp / "projects.toml"],
            "claude": [
                self.user / ".claude/settings.json",
                self.user / ".claude/plugins/x.json",
                self.user / ".claude.json",
                self.tmp / "repo/.claude/settings.local.json",
                self.tmp / "repo/.claude/settings.json",
            ],
        }
        for kind, paths in cases.items():
            for p in paths:
                for role in ("agent", "orchestrator"):
                    with self.subTest(path=str(p), role=role):
                        self.assertEqual(self.verdict(p, role), ("deny", kind))

    def test_what_stays_free(self):
        for p in (
            self.user / ".claude/projects/p/memory/MEMORY.md",
            self.user / ".claude/plans/plan.md",
            self.tmp / "repo/.claude/worktrees/w/foreman.py",  # a worktree inside a .claude folder
            self.tmp / "repo/.claude/agents.md",
            self.tmp / "wt/fake-a1/foreman.toml",  # a copy in a worktree is not the live one
            self.tmp / "wt/fake-a1/tests/guard.tsv",
            self.home / "reviews/2020-01-01-x.md",
            self.home / "rules.md",
            self.tmp / "projects.toml.bak",
            self.tmp / "user/.config/foreman/projects.toml",  # FOREMAN_REGISTRY names another file
        ):
            for role in ("agent", "orchestrator"):
                with self.subTest(path=str(p), role=role):
                    self.assertEqual(self.verdict(p, role), ("allow", ""))
        self.assertIsNone(guard.check_file(self.project, ""))

    def test_board_only_for_the_orchestrator(self):
        board = self.project.board
        self.assertEqual(self.verdict(board, "orchestrator"), ("allow", ""))
        self.assertEqual(self.verdict(board, "agent"), ("deny", "state"))
        self.mark("S1")
        ev = {"cwd": str(self.home)}
        self.assertIsNone(guard.check_file(self.project, str(board), {"session": "S1", "agent": None, **ev}))
        for ctx, why in (
            ({"session": "S1", "agent": "a1", **ev}, "agent"),
            ({"session": "S2", "agent": None, **ev}, "session"),
            (None, "agent"),
        ):
            v = guard.check_file(self.project, str(board), ctx)
            self.assertEqual((v[0], v[1].why), ("file", why))
        # the other product's board is state, also for this orchestrator
        other = config.load_project(self.other)
        self.assertEqual(self.verdict(other.board, "orchestrator"), ("deny", "state"))

    def test_default_registry_reserves_its_folder(self):
        os.environ.pop("FOREMAN_REGISTRY")
        folder = self.user / ".config/foreman"
        folder.mkdir(parents=True)
        (folder / "projects.toml").write_text((self.tmp / "projects.toml").read_text())
        for p in (folder / "projects.toml", folder / "bin/gpu-busy.sh"):
            self.assertEqual(self.verdict(p), ("deny", "registry"))
        self.assertEqual(self.verdict(self.user / ".config/other.toml"), ("allow", ""))

    def test_claude_config_dir(self):
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.tmp / "cc")
        self.assertEqual(self.verdict(self.tmp / "cc/settings.json"), ("deny", "claude"))
        self.assertEqual(self.verdict(self.tmp / "cc/projects/p/x.jsonl"), ("allow", ""))

    def test_paths_as_the_file_they_reach(self):
        # relative to the call's cwd, `..` and `~`
        self.assertEqual(self.verdict("foreman.toml"), ("deny", "config"))
        self.assertEqual(self.verdict("../x/../foreman.toml", cwd=self.home / "tests"), ("deny", "config"))
        self.assertEqual(self.verdict("foreman.toml", cwd=self.tmp / "wt"), ("allow", ""))
        self.assertEqual(self.verdict("~/.claude/settings.json"), ("deny", "claude"))
        # a new file under a reserved folder that does not exist yet
        self.assertEqual(self.verdict(self.state / "fake/new/dir/x.json"), ("deny", "state"))
        # a symbolic link to a reserved file, and to a reserved folder
        (self.tmp / "links").mkdir()
        (self.tmp / "links/cfg").symlink_to(self.home / "foreman.toml")
        (self.tmp / "links/st").symlink_to(self.state, target_is_directory=True)
        self.state.mkdir(exist_ok=True)
        self.assertEqual(self.verdict(self.tmp / "links/cfg"), ("deny", "config"))
        self.assertEqual(self.verdict(self.tmp / "links/st/fake/grants.json"), ("deny", "state"))
        # a hard link made earlier
        os.link(self.home / TABLE, self.tmp / "links/table-copy.tsv")
        self.assertEqual(self.verdict(self.tmp / "links/table-copy.tsv"), ("deny", "table"))
        (self.tmp / "links/free").write_text("x")
        os.link(self.tmp / "links/free", self.tmp / "links/free2")
        self.assertEqual(self.verdict(self.tmp / "links/free2"), ("allow", ""))

    @unittest.skipUnless(sys.platform == "darwin", "APFS ignores case; elsewhere case is a different file")
    def test_case_is_ignored_on_macos(self):
        self.assertEqual(self.verdict(str(self.home / "FOREMAN.toml")), ("deny", "config"))
        self.assertEqual(self.verdict(str(self.user / ".Claude/Settings.json")), ("deny", "claude"))

    def test_a_failure_denies_only_what_looks_reserved(self):
        with mock.patch.object(guard, "reserved", side_effect=RuntimeError("boom")):
            for p in (
                self.home / "foreman.toml",
                self.tmp / "x/guard.tsv",
                self.state / "fake/a.json",
                self.user / ".claude/settings.json",
                self.tmp / "projects.toml",
            ):
                v = guard.check_file(self.project, str(p))
                self.assertEqual(v, ("deny", "(the guard could not check this write: RuntimeError)"), p)
            for p in (self.home / "reviews/x.md", self.user / ".claude/projects/p/memory/m.md"):
                self.assertIsNone(guard.check_file(self.project, str(p)), p)
            msg = guard.message(self.project, guard.check_file(self.project, str(self.state / "x")))
            self.assertIn("could not check this write", msg)
        with mock.patch.object(guard, "_machine", side_effect=RuntimeError("boom")):
            self.assertIsNone(guard.check_file(self.project, str(self.state / "a.json")))  # not even the text
            self.assertIsNotNone(guard.check_file(self.project, str(self.state / "foreman.toml")))  # the name

    def test_unregistered_project_and_candidate(self):
        # a candidate of a registered project (permissions --candidate): the live files stay the reserved ones
        cand_file = self.tmp / "cand.toml"
        cand_file.write_text((self.home / "foreman.toml").read_text())
        cand = config.load_project(self.home, config_file=cand_file)
        self.assertIsNone(guard.check_file(cand, str(cand_file)))
        self.assertEqual(guard.check_file(cand, str(self.home / "foreman.toml"))[1].kind, "config")
        # a project the registry does not list is still its own config
        (self.tmp / "projects.toml").write_text("")
        self.assertEqual(self.verdict(self.home / "foreman.toml"), ("deny", "config"))

    def test_tool_path(self):
        self.assertEqual(guard.tool_path("Write", {"file_path": "/a"}), "/a")
        self.assertEqual(guard.tool_path("NotebookEdit", {"notebook_path": "/n.ipynb"}), "/n.ipynb")
        self.assertEqual(guard.tool_path("MultiEdit", None), "")
        self.assertIsNone(guard.tool_path("Bash", {"command": "ls"}))
        self.assertIsNone(guard.tool_path(None, {}))

    def test_messages(self):
        def msg(path, role):
            return guard.message(self.project, guard.check_file(self.project, str(path), guard.as_role(role)))

        self.assertIn("the foreman configuration of other", msg(self.other / "foreman.toml", "agent"))
        self.assertIn("foreman permissions --candidate FILE", msg(self.home / "foreman.toml", "agent"))
        self.assertIn("Never by hand", msg(self.home / TABLE, "orchestrator"))
        self.assertIn("your own scratch folder", msg(self.state / "fake/x.py", "orchestrator"))
        self.assertIn("Only Owner changes it, by hand", msg(self.user / ".claude.json", "agent"))
        self.assertIn("(the registry)", msg(self.tmp / "projects.toml", "agent"))


class HookTest(FileCase):
    def event(self, tool, path, agent=None, session="S1", tid="T1", **more):
        key = "notebook_path" if tool == "NotebookEdit" else "file_path"
        return {
            "hook_event_name": "PreToolUse",
            "tool_name": tool,
            "session_id": session,
            "tool_use_id": tid,
            **({"agent_id": agent} if agent else {}),
            "tool_input": {key: str(path), "content": "SECRET-CONTENT", **more},
        }

    def test_hook_on_write_tools(self):
        self.mark("S1")
        for tool in guard.FILE_TOOLS:
            with self.subTest(tool=tool):
                r = self.hook("guard", self.event(tool, self.home / "foreman.toml", agent="a1"))
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertIn("is the foreman configuration of fake", r.stderr)
        r = self.hook("guard", self.event("Write", self.home / "reviews/x.md", agent="a1"))
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        r = self.hook("guard", self.event("Write", self.project.board))  # the orchestrator's board
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        r = self.hook("guard", self.event("Edit", "foreman.toml"))  # relative to the event's cwd
        self.assertEqual(r.returncode, 2)
        self.assertIn("foreman permissions apply FILE", r.stderr)
        r = self.hook("guard", self.event("Write", self.home / "foreman.toml"), cwd=self.tmp)  # not a project
        self.assertEqual((r.returncode, r.stderr), (0, ""))

    def test_logs_and_notice(self):
        self.mark("S1")
        self.hook("guard", self.event("Write", self.state / "fake/grants.json", agent="a1", tid="T1"))
        self.hook("guard", self.event("Edit", self.home / TABLE, session="S7", tid="T2"))
        self.hook("guard", self.event("Edit", self.home / TABLE, tid="T3"))  # the orchestrator: no notice
        log = [json.loads(x) for x in approved.log_file(self.project).read_text().splitlines()]
        self.assertEqual(
            [(r["why"], r["pattern"], r["tool_use_id"]) for r in log],
            [("agent", "file:state", "T1"), ("session", "file:table", "T2")],
        )
        self.assertEqual(log[0]["file"], str(self.state / "fake/grants.json"))
        self.assertNotIn("command", log[0])
        refused = [json.loads(x) for x in permissions.log_file(self.project).read_text().splitlines()]
        self.assertEqual(
            [(r["kind"], r["rule"], r["where"], r["who"], r.get("why")) for r in refused],
            [
                ("file", "file:state", "file", "agent", "agent"),
                ("file", "file:table", "file", "other", "session"),
                ("file", "file:table", "file", "orchestrator", None),
            ],
        )
        for path in (approved.log_file(self.project), permissions.log_file(self.project)):
            self.assertNotIn("SECRET-CONTENT", path.read_text())
        (n,) = loop.orchestrator_only_notices(self.project)
        self.assertIn("Orchestrator-only commands and reserved files refused (2)", n["text"])
        self.assertIn("state/fake/grants.json` at", n["text"])
        self.assertIn("session S7 tried to write", n["text"])
        self.assertIn("`foreman permissions apply`", n["text"])
        view = permissions.refused_view(self.project, "1d")
        self.assertEqual({c["band"] for c in view["rules"]}, {"file"})
        self.assertIn("reserved file", permissions.render_refused(self.project, view))


class CliAndTableTest(FileCase):
    def test_guard_file(self):
        r = self.cli("guard", "--file", "foreman.toml")
        self.assertEqual(r.returncode, 2)
        self.assertIn("the foreman configuration of fake", r.stdout)
        r = self.cli("guard", "--file", str(self.project.board), "--as", "orchestrator")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "allowed"))
        r = self.cli("guard", "--file", "foreman.toml", "--cwd", str(self.tmp))
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "allowed"))
        r = self.cli("guard", "--file", "x", "ls")
        self.assertEqual(r.returncode, 2)
        self.assertIn("usage", r.stderr)

    def test_file_rows(self):
        (self.tmp / "wt").mkdir()
        rows = [
            "deny\tfile:foreman.toml",
            f"deny\tfile:{TABLE}",
            "deny\tfile:{state}/grants.json",
            "deny\tfile:{state_root}/callers.json",
            "deny\tfile:{registry}",
            "deny\tfile:~/.claude/settings.json",
            "orchestrator\tfile:{state}/board.md",
            "allow\tfile:~/.claude/projects/p/memory/MEMORY.md",
            "allow@../wt\tfile:foreman.toml",
            "allow\tfile:reviews/x.md",
            "deny\tls",  # a wrong row, to see it fail
        ]
        (self.home / TABLE).write_text("\n".join(rows) + "\n")
        r = self.cli("check")
        self.assertIn("1 wrong (7 deny, 0 lock, 3 allow, 1 orchestrator)", r.stdout)
        self.assertIn("want deny, got allow: 'ls'", r.stdout)
        (self.home / TABLE).write_text("\n".join(rows[:-1]) + "\nallow\tfile:{state}/board.md\n")
        r = self.cli("check")
        self.assertIn("want allow, got deny as an agent and allow as the orchestrator", r.stdout)
        # a registry that does not read: each file row tries again and denies what looks reserved
        (self.home / TABLE).write_text("deny\tfile:foreman.toml\nallow\tfile:reviews/x.md\n")
        with mock.patch.object(config, "registered_projects", side_effect=config.ConfigError("bad")):
            self.assertEqual(guard.run_table(self.project, self.home / TABLE)[1], [])

    def test_table_from_a_copy_of_the_home(self):
        """`foreman integrate` runs `check` from the worktree's copy of the home: its rows judge as in the live one,
        while a write call of the registered project (the hook) still leaves the copy free."""
        import shutil

        rows = [
            "deny\tfile:foreman.toml",
            f"deny\tfile:{TABLE}",
            "deny\tfile:{state}/grants.json",
            "orchestrator\tfile:{state}/board.md",
            "allow\tfile:reviews/x.md",
        ]
        (self.home / TABLE).write_text("\n".join(rows) + "\n")
        (self.home / "rules.md").write_text("rules\n")
        copy = self.tmp / "wt/fake-a1/home"
        shutil.copytree(self.home, copy)
        for home in (self.home, copy):
            r = self.cli("check", env={"FOREMAN_HOME": str(home)}, cwd=home)
            self.assertIn(f"guard table {home / TABLE}: ok (3 deny, 0 lock, 1 allow, 1 orchestrator)", r.stdout)
        ev = {"hook_event_name": "PreToolUse", "tool_name": "Edit", "session_id": "S1", "agent_id": "a1"}
        for f in (copy / "foreman.toml", copy / TABLE):
            r = self.hook("guard", {**ev, "tool_input": {"file_path": str(f)}}, cwd=copy)
            self.assertEqual((r.returncode, r.stderr), (0, ""), f)
        r = self.hook("guard", {**ev, "tool_input": {"file_path": str(self.home / "foreman.toml")}}, cwd=copy)
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
