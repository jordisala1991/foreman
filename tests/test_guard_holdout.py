"""The built-in rule for an agent but a planner (guard._holdout, HOLDOUT): the folder of hidden acceptance checks of
every product of the registry ([paths] holdout, default ~/.local/share/foreman/<name>/holdout) is out of reach of a
production agent, a reviewer and an agent of unknown type, by any tool and by a walk from a folder above it; the
orchestrator, the owner's other sessions and a planner reach it; `foreman holdout` but --help is refused to those
agents always; nothing changes while no such folder exists. Every test runs in a temporary HOME."""

import json
import re
import shutil
from unittest import mock

from test_guard_files import FileCase

from foreman import config, guard, hooks, permissions


class HoldoutCase(FileCase):
    """FileCase («fake» and «other», HOME in the test's folder) with fake's folder of hidden checks at its default
    place and a file in it; `elsewhere`, a folder of «other» set with [paths] holdout outside HOME."""

    def setUp(self):
        super().setUp()
        self.hold = self.user / ".local/share/foreman/fake/holdout"
        (self.hold / "AI-4/files").mkdir(parents=True)
        (self.hold / "AI-4/holdout.toml").write_text('issue = "AI-4"\n')
        self.work = self.tmp / "wt/fake-a1"
        self.work.mkdir(parents=True)
        (self.work / "src").mkdir()

    def v(self, cmd, cwd=None, role="agent"):
        """ "refused" (the HOLDOUT rule) | "allow" | the verdict's kind, for a Bash command (default: the worktree)."""
        got = guard.check(self.project, cmd, guard.as_role(role, str(cwd or self.work)))
        return self._kind(got)

    def t(self, tool, ti, role="agent", cwd=None):
        """The same, for a call of another tool."""
        got = guard.check_call(self.project, tool, ti, guard.as_role(role, str(cwd or self.work)))
        return self._kind(got)

    @staticmethod
    def _kind(got):
        if got is None:
            return "allow"
        if got[0] == "orchestrator" and got[1] == guard.HOLDOUT:
            return "refused"
        return f"{got[0]}:{got[1]}"


H = "~/.local/share/foreman/fake/holdout"
REFUSED = (
    f"cat {H}/AI-4/holdout.toml",
    "cat $HOME/.local/share/foreman/fake/holdout/AI-4/holdout.toml",
    "cat ${HOME}/.local/share/foreman/fake/holdout/AI-4/holdout.toml",
    f"ls {H}",
    f"ls -la {H}/AI-4/",
    f"grep -rn issue {H}",
    f"rg issue {H}",
    f"cp -R {H} /tmp/x",
    f"cp {H}/AI-4/holdout.toml /tmp/x",
    f"python3 x.py {H}/AI-4/holdout.toml",
    f"head < {H}/AI-4/holdout.toml",
    f"cd {H} && ls",
    f"cd {H}/AI-4 && cat holdout.toml",
    "cd ~/.local/share/foreman/fake && cat holdout/AI-4/holdout.toml",
    "cd ~/.local/share/foreman && grep -r issue .",
    "cd ~/.local/share && grep -R issue",
    "grep -r issue ~/.local",
    "grep --recursive issue ~",
    "grep -d recurse issue ~/.local/share",
    "rg issue ~",
    "rg issue /",
    "find ~ -name '*.toml'",
    "find ~/.local -type f",
    "ls -R ~/.local/share/foreman",
    "du -a ~/.local/share",
    "tar czf /tmp/x.tgz ~/.local",
    "rsync -a ~/.local/share/ /tmp/x/",
    "cp -a ~/.local /tmp/x",
    "tree ~/.local/share/foreman",
    "ls ~/.local/share/foreman/*/holdout",
    "cat ~/.local/share/foreman/fake/hold*/AI-4/holdout.toml",
    "ls ~/.local/share/foreman/**",
    f"X={H}; cat $X/AI-4/holdout.toml",
    f"sh -c 'cat {H}/AI-4/holdout.toml'",
    f"echo $(cat {H}/AI-4/holdout.toml)",
    f"xargs cat <<< {H}/AI-4/holdout.toml",
    f"python3 -c \"print(open('{H}/AI-4/holdout.toml'.replace('~', '/u')).read())\"",
    f"python3 - <<'EOF'\nprint(open('{H}/AI-4/holdout.toml').read())\nEOF",
    f"cat '{H}/AI-4/holdout.toml",  # the lexer cannot read it: its text names the folder
    "foreman holdout status",
    "bin/foreman --project fake holdout seal AI-4",
    "python3 bin/foreman holdout run api fo1/x AI-4 --full",
    "sh -c 'foreman holdout status'",
    "foreman holdout status 'x",  # the lexer cannot read it
)
ALLOWED = (
    "ls",
    "cat src/x.py",
    "grep -rn foo src",
    "rg foo",
    "find . -name '*.py'",
    "ls ~",
    "ls ~/.local/share/foreman",
    "ls ~/.local/share/foreman/fake",
    "cat ~/.local/share/foreman/fake/holdout.md",
    "cd ~ && git status",
    "cd ~/.local/share/foreman && grep -rn x fake/notes.md",
    "foreman holdout --help",
    "foreman holdout -h",
    "foreman holdout seal --help",
    'foreman note "a hidden check runs at integration"',
    "grep -rn holdout src",
)


class HoldoutRule(HoldoutCase):
    def test_refused_to_an_agent_and_allowed_to_the_orchestrator(self):
        for cmd in REFUSED:
            with self.subTest(cmd=cmd):
                self.assertEqual(self.v(cmd), "refused")
                self.assertEqual(self.v(cmd, role="orchestrator"), "allow")
                self.assertEqual(self.v(cmd, role="planner"), "allow")

    def test_what_stays_allowed_to_an_agent(self):
        (self.user / ".local/share/foreman/fake/notes.md").write_text("x\n")
        for cmd in ALLOWED:
            with self.subTest(cmd=cmd):
                self.assertEqual(self.v(cmd), "allow")

    def test_in_the_folder_any_command(self):
        self.assertEqual(self.v("ls", cwd=self.hold / "AI-4"), "refused")
        self.assertEqual(self.v("cat files/x", cwd=self.hold / "AI-4"), "refused")
        self.assertEqual(self.v("grep -r x", cwd=self.user), "refused")  # a walk from HOME, given no path
        self.assertEqual(self.v("grep -r x src", cwd=self.user / ".local"), "refused")  # src missing: the cwd
        (self.user / ".local/src").mkdir()
        self.assertEqual(self.v("grep -r x src", cwd=self.user / ".local"), "allow")  # a path given: that one

    def test_who(self):
        cmd = f"cat {self.hold}/AI-4/holdout.toml"
        for ctx, want in (
            ({"session": "s1", "agent": "a1", "agent_type": "foreman:dev"}, "refused"),
            ({"session": "s1", "agent": "a1", "agent_type": "dev"}, "refused"),
            ({"session": "s1", "agent": "a1", "agent_type": "foreman:mechanic"}, "refused"),
            ({"session": "s1", "agent": "a1", "agent_type": "foreman:reviewer"}, "refused"),
            ({"session": "s1", "agent": "a1", "agent_type": "general-purpose"}, "refused"),
            ({"session": "s1", "agent": "a1"}, "refused"),  # a type foreman does not know
            ({"session": "s1", "agent": "a1", "agent_type": "foreman:planner"}, "allow"),
            ({"session": "s1", "agent": "a1", "agent_type": "planner"}, "allow"),
            ({"session": "s2", "agent": None}, "allow"),  # another session of the product (the owner's chat)
            (None, "refused"),
        ):
            with self.subTest(ctx=ctx):
                self.assertEqual(self._kind(guard.check(self.project, cmd, ctx)), want)

    def test_type_from_agents_json(self):
        """An event without agent_type: the agent's entry in <state>/agents.json says it."""
        cmd = f"cat {self.hold}/AI-4/holdout.toml"
        self.project.state.mkdir(parents=True, exist_ok=True)
        (self.project.state / "agents.json").write_text(
            json.dumps({"p1": {"type": "foreman:planner"}, "d1": {"type": "foreman:dev"}, "x": "bad"})
        )
        for aid, want in (("p1", "allow"), ("d1", "refused"), ("x", "refused"), ("nobody", "refused")):
            with self.subTest(aid=aid):
                ctx = {"session": "s1", "agent": aid}
                self.assertEqual(self._kind(guard.check(self.project, cmd, ctx)), want)
        # the event's type wins over agents.json
        ctx = {"session": "s1", "agent": "p1", "agent_type": "foreman:dev"}
        self.assertEqual(self._kind(guard.check(self.project, cmd, ctx)), "refused")

    def test_other_products_and_a_configured_folder(self):
        elsewhere = self.tmp / "secret-place"
        (elsewhere / "AI-1").mkdir(parents=True)
        (self.other / "foreman.toml").write_text(
            f'[project]\nname = "other"\nowner = "Owner"\n\n[paths]\nstate = "{self.tmp}/other-state"\n'
            f'holdout = "{elsewhere}"\n'
        )
        self.assertEqual(self.v(f"cat {elsewhere}/AI-1/x"), "refused")
        self.assertEqual(self.v(f"ls {elsewhere}"), "refused")
        self.assertEqual(self.v(f"grep -r x {self.tmp}"), "refused")
        self.assertEqual(self.v("cat secret-place/AI-1/x", cwd=self.tmp), "refused")
        got = guard.check(self.project, f"cat {elsewhere}/AI-1/x", guard.as_role("agent", str(self.work)))
        self.assertEqual(got[1].name, "other")
        self.assertIn("other", guard.message(self.project, got))
        self.assertEqual(config.load_project(self.other).holdout_dir, elsewhere)

    def test_a_link_to_the_folder(self):
        link = self.work / "peek"
        link.symlink_to(self.hold)
        self.assertEqual(self.v("cat peek/AI-4/holdout.toml"), "refused")
        self.assertEqual(self.t("Read", {"file_path": str(link / "AI-4/holdout.toml")}), "refused")

    def test_nothing_while_no_folder_exists(self):
        shutil.rmtree(self.user / ".local")
        for cmd in REFUSED:
            if re.search(guard.HOLDOUT_CALL, cmd):
                continue
            with self.subTest(cmd=cmd):
                self.assertNotEqual(self.v(cmd), "refused")
        self.assertEqual(self.v("foreman holdout status"), "refused")  # the command is the orchestrator's always
        self.assertEqual(self.v("rg x ~"), "allow")
        self.assertEqual(self.t("Grep", {"pattern": "x", "path": str(self.user)}), "allow")

    def test_a_failure_compares_the_text(self):
        with mock.patch.object(guard, "_walk", side_effect=RuntimeError("boom")):
            self.assertEqual(self.v(f"cat {self.hold}/AI-4/holdout.toml"), "refused")
            self.assertEqual(self.v("cat src/x.py"), "allow")
        with mock.patch.object(guard, "holdout_dirs", side_effect=[RuntimeError("registry"), []]):
            self.assertEqual(self.v("cat src/x.py"), "allow")
        with mock.patch.object(guard, "_holdout_named", side_effect=RuntimeError("boom")):
            self.assertEqual(self.v("cat src/x.py"), "allow")  # not even the text: it passes

    def test_the_refusal(self):
        got = guard.check(self.project, f"cat {H}/AI-4/holdout.toml", guard.as_role("agent", str(self.work)))
        msg = guard.message(self.project, got)
        self.assertIn("hidden acceptance checks of fake", msg)
        self.assertIn("meet the contract", msg)
        self.assertEqual(got[1].why, "agent")


class HoldoutTools(HoldoutCase):
    def test_tools(self):
        hold = str(self.hold)
        for tool, ti in (
            ("Read", {"file_path": f"{hold}/AI-4/holdout.toml"}),
            ("Write", {"file_path": f"{hold}/AI-4/holdout.toml", "content": "x"}),
            ("Edit", {"file_path": f"{hold}/AI-4/holdout.toml", "old_string": "a", "new_string": "b"}),
            ("Grep", {"pattern": "issue", "path": hold}),
            ("Grep", {"pattern": "issue", "path": str(self.user)}),
            ("Grep", {"pattern": "issue", "path": str(self.user / ".local")}),
            ("Grep", {"pattern": "issue", "path": "/"}),
            ("Grep", {"pattern": "issue", "cwd": True}),  # no path: the call's folder (HOME here)
            ("Glob", {"pattern": "**/*.toml", "path": str(self.user)}),
            ("Glob", {"pattern": f"{self.user}/.local/**/*.toml"}),
            ("Glob", {"pattern": f"{self.user}/.local/share/foreman/*/holdout/*"}),
            ("Glob", {"pattern": "*", "path": hold}),
            ("LS", {"path": hold}),
            ("mcp__x__read", {"file_path": f"{hold}/AI-4/holdout.toml"}),
        ):
            cwd = self.user if ti.pop("cwd", None) else None
            with self.subTest(tool=tool, ti=ti):
                self.assertEqual(self.t(tool, ti, cwd=cwd), "refused")
                self.assertEqual(self.t(tool, ti, role="orchestrator", cwd=cwd), "allow")
                self.assertEqual(self.t(tool, ti, role="planner", cwd=cwd), "allow")

    def test_tools_allowed(self):
        for tool, ti in (
            ("Read", {"file_path": str(self.work / "src/x.py")}),
            ("Grep", {"pattern": "holdout", "path": str(self.work)}),
            ("Grep", {"pattern": "x"}),
            ("Glob", {"pattern": "**/*.py", "path": str(self.work)}),
            ("Glob", {"pattern": "*.toml", "path": str(self.user / ".local/share/foreman")}),
            ("Read", {"file_path": str(self.user / ".local/share/foreman/fake/notes.md")}),
            ("Grep", {"pattern": "x", "path": str(self.user / ".local/share/foreman/other")}),
        ):
            with self.subTest(tool=tool, ti=ti):
                self.assertEqual(self.t(tool, ti), "allow")

    def test_the_tool_refusal(self):
        got = guard.check_call(
            self.project, "Grep", {"pattern": "x", "path": str(self.user)}, guard.as_role("agent", str(self.work))
        )
        self.assertEqual((got[1].where, got[1].tool), ("tool", "Grep"))
        self.assertIn("`Grep /", guard.message(self.project, got))
        with mock.patch.object(guard, "holdout_dirs", side_effect=RuntimeError("boom")):
            self.assertIsNone(guard._holdout_tool(self.project, "Grep", {"path": "/"}, None))


class HoldoutHook(HoldoutCase):
    """Through the guard hook: the event's agent_type decides, the refusal goes to guard.jsonl and to the
    orchestrator's notice (approved.jsonl)."""

    def ev(self, tool, ti, **more):
        return {
            "session_id": "s1",
            "agent_id": "a1",
            "cwd": str(self.home),
            "tool_name": tool,
            "tool_input": ti,
            **more,
        }

    def test_hook(self):
        hold = str(self.hold)
        r = self.hook("guard", self.ev("Bash", {"command": f"cat {hold}/AI-4/holdout.toml"}, agent_type="foreman:dev"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("hidden acceptance checks", r.stderr)
        r = self.hook("guard", self.ev("Read", {"file_path": f"{hold}/AI-4/holdout.toml"}, agent_type="foreman:dev"))
        self.assertEqual(r.returncode, 2)
        r = self.hook(
            "guard", self.ev("Read", {"file_path": f"{hold}/AI-4/holdout.toml"}, agent_type="foreman:planner")
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        log = [json.loads(x) for x in (self.project.state / "guard.jsonl").read_text().splitlines()]
        self.assertEqual([x["rule"] for x in log], [guard.HOLDOUT, guard.HOLDOUT])
        self.assertEqual({x["kind"] for x in log}, {"builtin"})
        self.assertTrue((self.project.state / "approved.jsonl").exists())

    def test_ctx(self):
        ctx = hooks._guard_ctx({"session_id": "s", "agent_id": "a", "agent_type": "foreman:planner"})
        self.assertEqual(ctx["agent_type"], "foreman:planner")
        self.assertIsNone(hooks._guard_ctx({"session_id": "s"})["agent_type"])


class HoldoutCli(HoldoutCase):
    def test_guard_as(self):
        cmd = f"cat {self.hold}/AI-4/holdout.toml"
        r = self.cli("guard", cmd, "--cwd", str(self.work))
        self.assertEqual(r.returncode, 2)
        self.assertIn("hidden acceptance checks", r.stdout)
        for role in ("planner", "orchestrator"):
            with self.subTest(role=role):
                self.assertEqual(self.cli("guard", cmd, "--as", role, "--cwd", str(self.work)).returncode, 0)

    def test_permissions_view_and_table(self):
        (self.home / "tests/guard.tsv").write_text(
            f"orchestrator\tcat {self.hold}/AI-4/holdout.toml\n"
            f"orchestrator\tgrep -r x {self.user}\n"
            f"orchestrator\tforeman holdout status\n"
            "allow\tforeman holdout --help\n"
            f'orchestrator\ttool:Grep {{"pattern": "x", "path": "{self.user}"}}\n'
            f'orchestrator\ttool:Read {{"file_path": "{self.hold}/AI-4/holdout.toml"}}\n'
        )
        counts, failures = guard.run_table(self.project, self.home / "tests/guard.tsv")
        self.assertEqual(failures, [])
        self.assertEqual(counts, {"orchestrator": 5, "allow": 1})
        rules = permissions.rules(self.project)
        self.assertIn(guard.HOLDOUT, [r["pattern"] for r in rules if r.get("only")])
        r = self.cli("permissions")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("hidden acceptance checks", r.stdout)
        self.assertNotIn(f"«{guard.HOLDOUT}»: no row", r.stdout)

    def test_config(self):
        self.assertEqual(self.project.holdout_dir, self.hold)
        d = self.project.as_dict()
        self.assertEqual(d["config"]["paths"]["holdout"], str(self.hold))
        self.write_toml(self.toml.replace("[paths]\n", '[paths]\nholdout = "../hidden"\n', 1))
        self.assertEqual(self.project.holdout_dir.resolve(), self.home.parent / "hidden")
        self.assertEqual(self.project.warnings, [])


if __name__ == "__main__":
    import unittest

    unittest.main()
