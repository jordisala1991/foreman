import json
import os
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from helpers import BIN, ROOT, ProjectCase, config

from foreman import cli, release, toml_reader, util
from foreman_dev import snapshot


class ConfigTest(ProjectCase):
    def test_examples_validate(self):
        for f in (ROOT / "examples").glob("*.toml"):
            with self.subTest(f=f.name):
                self.assertEqual(config.validate(toml_reader.loads(f.read_text())), [])

    def test_machine_examples(self):
        """examples/machine/: the example registry reads without warnings and its gpu is a machine resource; the
        example product that uses it loads strictly with only its own guard; the example busy command parses and
        keeps the contract (stdout only when a listed executable runs; exit 0 busy, 1 free)."""
        machine = ROOT / "examples/machine"
        os.environ["FOREMAN_REGISTRY"] = str(machine / "projects.toml")
        entries, broken, warnings = config.registry_scan()
        self.assertEqual(([e["name"] for e in entries], broken, warnings), (["tidepool", "lantern"], [], []))
        resources, errors, mwarnings = config.machine_resources()
        self.assertEqual((sorted(resources), errors, mwarnings), (["gpu"], {}, []))
        self.assertEqual(resources["gpu"]["dir"], str(Path("~/.gpu-lock").expanduser()))
        self.assertEqual(resources["gpu"]["busy"], "~/.config/foreman/bin/gpu-busy.sh")
        p = config.load_project(self.tmp, config_file=ROOT / "examples/tidepool.toml", strict=True)
        self.assertEqual((p.name, p.warnings), ("tidepool", []))
        gpu = p.section("resources")["gpu"]
        self.assertEqual((gpu["dir"], gpu["busy"]), (resources["gpu"]["dir"], resources["gpu"]["busy"]))
        self.assertIn("xcodebuild", gpu["guard"])
        script = machine / "gpu-busy.sh"
        self.assertEqual(subprocess.run(["bash", "-n", str(script)], capture_output=True, check=False).returncode, 0)
        self.assertTrue(os.access(script, os.X_OK))
        env = {**os.environ, "GPU_BUSY_EXES": "no-such-gpu-exe"}
        r = subprocess.run(
            ["bash", "-c", str(script)], env=env, capture_output=True, text=True, timeout=30, check=False
        )
        self.assertEqual((r.returncode, r.stdout), (1, ""))
        child = subprocess.Popen(["sleep", "30"])
        try:
            r = subprocess.run(
                ["bash", "-c", str(script)],
                env={**env, "GPU_BUSY_EXES": "no-such-gpu-exe sleep"},
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        finally:
            child.kill()
            child.wait()
        self.assertEqual(r.returncode, 0)
        self.assertRegex(r.stdout, rf"(?m)^pid {child.pid}, running [0-9:-]+, in .*: sleep 30$")

    def test_extend_example(self):
        """examples/extend/ (docs/extending.md): its foreman.toml loads strictly; its own agent type has foreman's
        header, sends the agent to foreman's procedure and counts as production; its loop check, run as the loop runs
        it, gives an informing notice of its own kind from FOREMAN_STATE (and check_failed outside foreman); its CI
        summary turns a run's output into one line."""
        from foreman import commands, identity, notices

        ex = ROOT / "examples/extend"
        shutil.copytree(ex / "ops", self.home, dirs_exist_ok=True)
        p = config.load_project(self.home, config_file=self.home / "foreman.toml", strict=True)
        self.assertEqual((p.name, p.warnings), ("quayside", []))
        agent = ex / ".claude/agents/quayside-dev.md"
        head, body = agent.read_text().split("\n---\n", 1)
        self.assertTrue(head.startswith("---\n") and "\ndescription: " in head)
        self.assertEqual(identity.agent_definition_name(agent), "quayside-dev")
        self.assertIn("quayside-dev", p.section("agents")["production_types"])
        self.assertEqual(identity.role(p, "quayside-dev"), "production")
        self.assertIn("(`foreman docs agent`)", " ".join(body.split()))
        for script in (
            self.home / "checks/state-size.sh",
            self.home / "ci-summary.sh",
            self.home / "checks/worktrees.py",
        ):
            with self.subTest(script=script.name):
                self.assertTrue(os.access(script, os.X_OK))
                syntax = ["bash", "-n"] if script.suffix == ".sh" else [sys.executable, "-m", "py_compile"]
                self.assertEqual(subprocess.run([*syntax, str(script)], check=False).returncode, 0)

        p.state.mkdir(parents=True, exist_ok=True)
        (p.state / "agents.json").write_text("{}")
        self.assertEqual(notices.run_checks(p), [])  # the example's own limits: 500 MB before it says anything
        toml = self.home / "foreman.toml"
        toml.write_text(toml.read_text().replace("STATE_INFO_MB=500", "STATE_INFO_MB=0"))
        p = config.load_project(self.home, config_file=toml, strict=True)
        got = [(n["kind"], n["actionable"], n["text"]) for n in notices.run_checks(p)]
        self.assertEqual(got, [("state_size", False, "foreman state of quayside is 0 MB")])
        r = subprocess.run(["checks/state-size.sh"], cwd=self.home, capture_output=True, text=True, check=False)
        self.assertEqual((r.returncode, r.stdout), (2, ""))  # outside foreman: a check_failed for the loop

        # the worktrees check asks `"$FOREMAN_BIN" config --json` for the folder: here the default one (no [paths]),
        # with two of the repo's worktrees and an unrelated folder in it
        toml.write_text(re.sub(r"(?m)^\[paths\]\nworktrees = .*\n", "", toml.read_text()))
        p = config.load_project(self.home, config_file=toml, strict=True)
        self.assertEqual(p.worktrees_dir, self.tmp / "wt")
        for name in ("web-ana", "web-bo", "other-x"):
            (self.tmp / "wt" / name).mkdir(parents=True)
        env = {**os.environ, "FOREMAN_HOME": str(self.home), "FOREMAN_BIN": str(BIN)}
        r = commands.run(
            "loop.checks", "WORKTREES_INFO=2 WORKTREES_ACT=3 checks/worktrees.py", p, cwd=self.home, env=env
        )
        self.assertEqual(
            (r.rc, json.loads(r.stdout)),
            (
                0,
                {
                    "kind": "worktrees",
                    "actionable": False,
                    "key": "worktrees:False",
                    "text": f"2 worktrees of quayside in {self.tmp / 'wt'}",
                },
            ),
        )
        r = commands.run(
            "loop.checks", "WORKTREES_INFO=2 WORKTREES_ACT=2 checks/worktrees.py", p, cwd=self.home, env=env
        )
        self.assertTrue(json.loads(r.stdout)["actionable"])
        r = commands.run("loop.checks", "checks/worktrees.py", p, cwd=self.home, env=env)
        self.assertEqual((r.rc, r.stdout), (0, ""))  # below its default of 4

        run = "test_a ... ok\n" + "-" * 20 + "\nRan 12 tests in 0.301s\n\nOK\n"
        cmd = p.repo("web")["ci_summary"]
        r = commands.run("repo.ci_summary", cmd, p, {"ci": "INTEGRATION=1", "repo": "web"}, self.home, run)
        self.assertEqual((r.rc, r.stdout), (0, "INTEGRATION=1: Ran 12 tests in 0.301s OK\n"))
        r = commands.run("repo.ci_summary", cmd, p, {"ci": "", "repo": "web"}, self.home, "== 3 passed in 0.2s ==\n")
        self.assertEqual(r.stdout, "default run: 3 passed in 0.2s\n")

    def test_config_json(self):
        """`foreman config --json`: every key resolved with its default, [paths] and repo paths absolute, and where
        each value comes from: foreman.toml, a default, the registry (a machine resource's keys) or the environment
        (the default state folder under FOREMAN_STATE_HOME). Without [paths] (a fake project's toml with only the
        project) the folders are the defaults'."""
        self.write_toml('[project]\nname = "fake"\n[repos.app]\npath = "app"\nlocal_ci = "make"\n')
        r = self.cli("config", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        d = json.loads(r.stdout)
        self.assertEqual(
            {k: d[k] for k in ("name", "home", "config_file", "mode", "registered", "api", "warnings")},
            {
                "name": "fake",
                "home": str(self.home),
                "config_file": str(self.home / "foreman.toml"),
                "mode": "active",
                "registered": True,
                "api": 1,
                "warnings": [],
            },
        )
        c, s = d["config"], d["sources"]
        self.assertEqual(
            c["paths"],
            {
                "notes": str(self.home / "notes"),
                "reviews": str(self.home / "reviews"),
                "state": str(self.tmp / "state/fake"),
                "worktrees": str(self.tmp / "wt"),
            },
        )
        self.assertEqual(s["paths"], {**dict.fromkeys(c["paths"], "default"), "state": "environment"})
        self.assertEqual(c["loop"], config.DEFAULTS["loop"])
        self.assertEqual(set(s["loop"].values()), {"default"})
        self.assertEqual((s["project"]["name"], s["project"]["owner"]), ("foreman.toml", "default"))
        self.assertEqual(c["project"]["config_schema"], config.CONFIG_SCHEMA)
        self.assertEqual(
            c["repos"]["app"], {**config.REPO_DEFAULTS, "path": str(self.home / "app"), "local_ci": "make"}
        )
        self.assertEqual({k for k, v in s["repos"]["app"].items() if v == "foreman.toml"}, {"path", "local_ci"})
        self.assertEqual((c["resources"], c["recurring"], s["recurring"]), ({}, [], "default"))
        for sec in config.DEFAULTS:  # the same shape: a source for every value
            with self.subTest(sec=sec):
                self.assertEqual(set(c[sec]), set(s[sec]))
                self.assertLessEqual(set(s[sec].values()), set(config.SOURCES))
        text = self.cli("config").stdout
        self.assertIn('repos.app.local_ci = "make"  (foreman.toml)\n', text)
        self.assertIn("loop.every_min = 60  (default)\n", text)

        # examples/tidepool.toml with the example registry: its gpu's machine keys are the registry's, its guard its own
        os.environ["FOREMAN_REGISTRY"] = str(ROOT / "examples/machine/projects.toml")
        p = config.load_project(self.tmp, config_file=ROOT / "examples/tidepool.toml", strict=True)
        d = p.as_dict()
        c, s = d["config"], d["sources"]
        self.assertEqual(
            (d["name"], d["registered"], d["config_file"]), ("tidepool", False, str(ROOT / "examples/tidepool.toml"))
        )
        self.assertEqual(c["paths"]["worktrees"], str(Path("~/Projects/tidepool/wt").expanduser()))
        self.assertEqual(s["paths"]["worktrees"], "foreman.toml")
        self.assertEqual(c["repos"]["app"]["path"], str(Path("~/Projects/tidepool/tidepool-app").expanduser()))
        gpu = s["resources"]["gpu"]
        self.assertEqual(
            (gpu["dir"], gpu["busy"], gpu["slots"], gpu["guard"]), ("registry", "registry", "registry", "foreman.toml")
        )
        self.assertEqual(c["resources"]["gpu"]["dir"], str(Path("~/.gpu-lock").expanduser()))
        own = [n for n in s["resources"] if n != "gpu"]
        self.assertTrue(own)
        for n in own:
            self.assertNotIn("registry", s["resources"][n].values())
        json.dumps(d)  # JSON as it is

    def test_check_run_checks(self):
        """`foreman check --run-checks` runs each [loop] checks command once and validates its lines: a kind of
        foreman's own (reserved or not), bad JSON or a non-boolean `actionable` is a warning (the check still passes);
        an exit code other than 0 or 1 fails it. Nothing is marked: the loop's state stays as it was."""
        checks = self.home / "checks.sh"
        checks.write_text(
            "#!/bin/bash\n"
            'echo \'{"kind": "inbox", "text": "spoofed"}\'\n'
            'echo \'{"kind": "lock_long", "actionable": "yes", "text": "old lock"}\'\n'
            'echo \'{"kind": "mine", "actionable": false, "text": "fine"}\'\n'
            "echo '{not json'\n"
            "echo plain words\n"
        )
        checks.chmod(0o755)
        self.write_toml(
            self.toml.replace('prompt = "Fake orchestrator loop"', 'prompt = "x"\nchecks = ["./checks.sh"]')
        )
        before = sorted(x.name for x in self.project.state.glob("*")) if self.project.state.exists() else []
        r = self.cli("check", "--run-checks")
        self.assertIn("loop check `./checks.sh`: rc 0, 5 notices", r.stdout)
        self.assertIn("1. check: spoofed\n       warning: kind `inbox` is one of foreman's own", r.stdout)
        self.assertIn("2. check: old lock\n       warning: kind `lock_long` is one of foreman's own", r.stdout)
        self.assertIn("warning: `actionable` is 'yes', not true or false", r.stdout)
        self.assertIn("3. mine (info): fine\n    4.", r.stdout)
        self.assertIn("4. check: {not json\n       warning: not valid JSON", r.stdout)
        self.assertIn("5. check: plain words\n", r.stdout)
        no_run = self.cli("check")
        self.assertNotIn("loop check", no_run.stdout)
        self.assertEqual(r.returncode, no_run.returncode)  # warnings never fail it
        self.assertEqual(
            sorted(x.name for x in self.project.state.glob("*")) if self.project.state.exists() else [], before
        )

        checks.write_text("#!/bin/bash\necho half; echo broken >&2; exit 3\n")
        r = self.cli("check", "--run-checks")
        self.assertEqual(r.returncode, 1)
        self.assertIn(
            "loop check `./checks.sh`: FAILED, exit code 3 (0 or 1 is normal), 1 notice\n    stderr: broken", r.stdout
        )

    def test_defaults_and_paths(self):
        p = self.project
        self.assertEqual(p.name, "fake")
        self.assertEqual(p.state, self.tmp / "state" / "fake")
        self.assertEqual(p.notes_dir, self.home / "notes")
        self.assertEqual(p.worktrees_dir, self.tmp / "wt")
        self.assertEqual(p.section("agents")["production_slots"], 1)
        self.assertNotIn("schema", p.section("backlog"))

    def test_bad_config(self):
        """A bad value does not load; an unknown key loads with a warning and fails a strict read (more cases in
        test_config_compat)."""
        bad = [
            '[project]\nname = "x"\n[repos.a]\nmerge = "octopus"\n',
            "[project]\n",
            '[project]\nname = "x"\n[deny]\npatterns = ["("]\n',
        ]
        for text in bad:
            with self.subTest(text=text):
                (self.home / "foreman.toml").write_text(text)
                with self.assertRaises(config.ConfigError):
                    config.load_project(self.home)
        for text in ('[project]\nname = "x"\n[nonsense]\n', '[project]\nname = "x"\n[agents]\nslots = 2\n'):
            with self.subTest(text=text):
                (self.home / "foreman.toml").write_text(text)
                self.assertEqual(len(config.load_project(self.home).warnings), 1)
                with self.assertRaises(config.ConfigError):
                    config.load_project(self.home, strict=True)

    def test_project_for_cwd(self):
        sub = self.home / "a/b"
        sub.mkdir(parents=True)
        self.assertEqual(config.project_for_cwd(sub).name, "fake")
        self.assertEqual(config.project_for_cwd(self.tmp / "wt").name, "fake")  # the worktrees folder
        self.assertIsNone(config.project_for_cwd(self.tmp))
        self.assertIsNone(config.project_for_cwd("/"))

    def shared_projects_folder(self, modes):
        """A registry shaped like a real machine: one folder of projects (P) where one product keeps its home, its
        repos and a workspace (P/tidepool: home ops/, repos, wt/), another uses P itself as its worktrees folder
        (quayside: home P/quayside, repo P/dockyard, worktrees P/dockyard-*), and a third keeps its home inside its repo
        and its worktrees beside it (foreman)."""
        P = self.tmp / "Projects"
        for d in (
            "tidepool/ops",
            "tidepool/tidepool",
            "tidepool/tidepool-api",
            "tidepool/wt/x",
            "quayside",
            "dockyard",
            "dockyard-foo",
            "homelab",
            "foreman/ops",
            "foreman/lib",
            "foreman-wt/foreman-x",
        ):
            (P / d).mkdir(parents=True)
        (P / "tidepool/ops/foreman.toml").write_text(f"""
[project]
name = "tidepool"
[paths]
worktrees = "{P}/tidepool/wt"
[agents]
production_worktree = "{P}/tidepool/wt/"
[repos.app]
path = "{P}/tidepool/tidepool"
[repos.api]
path = "{P}/tidepool/tidepool-api"
""")
        (self.tmp / "quayside.toml").write_text(f"""
[project]
name = "quayside"
[paths]
worktrees = "{P}"
[agents]
production_worktree = "{P}/dockyard-"
[repos.dockyard]
path = "{P}/dockyard"
worktree = "{P}/dockyard-{{name}}"
""")
        (P / "foreman/ops/foreman.toml").write_text(f"""
[project]
name = "foreman"
[paths]
worktrees = "{P}/foreman-wt"
[agents]
production_worktree = "{P}/foreman-wt/"
[repos.foreman]
path = "{P}/foreman"
worktree = "{{worktrees}}/{{repo}}-{{name}}"
""")
        (self.tmp / "projects.toml").write_text(
            f'[[project]]\nname = "tidepool"\nhome = "{P}/tidepool/ops"\nmode = "{modes[0]}"\n'
            f'[[project]]\nname = "quayside"\nhome = "{P}/quayside"\nmode = "{modes[1]}"\n'
            f'config = "{self.tmp}/quayside.toml"\n'
            f'[[project]]\nname = "foreman"\nhome = "{P}/foreman/ops"\nmode = "{modes[2]}"\n'
        )
        return P

    def test_shared_projects_folder(self):
        """A shared worktrees folder (P) is not all of one product: P/tidepool is tidepool's workspace, P/dockyard-* are
        quayside's worktrees, and P itself or P/homelab belong to nobody."""
        want = {
            "tidepool": "tidepool",
            "tidepool/ops": "tidepool",
            "tidepool/wt/x": "tidepool",
            "tidepool/tidepool-api": "tidepool",
            "dockyard-foo": "quayside",
            "dockyard": "quayside",
            "quayside": "quayside",
            "homelab": None,
            ".": None,
            "foreman": "foreman",
            "foreman/ops": "foreman",
            "foreman/lib": "foreman",
            "foreman-wt/foreman-x": "foreman",
            "foreman-wt": "foreman",
        }
        for modes in (("inactive", "inactive", "active"), ("active", "active", "active")):
            P = self.shared_projects_folder(modes)
            active = {n for n, m in zip(("tidepool", "quayside", "foreman"), modes) if m == "active"}
            for rel, name in want.items():
                for active_only in (False, True):
                    with self.subTest(modes=modes, cwd=rel, active_only=active_only):
                        p = config.project_for_cwd(P / rel, active_only=active_only)
                        expected = name if (not active_only or name in active) else None
                        self.assertEqual(p.name if p else None, expected)
            self.assertEqual(config.resolve(cwd=P / "tidepool").name, "tidepool")
            with self.assertRaises(config.ConfigError):
                config.resolve(cwd=P / "homelab")
            shutil.rmtree(P)

    def test_backlog_schema_key_is_unknown(self):
        """`[backlog] schema` is gone (foreman reads only schema 5): one left is the usual unknown-key warning."""
        for v in (4, 5):
            self.write_toml(self.toml.replace("[project]\n", f"[backlog]\nschema = {v}\n\n[project]\n", 1))
            p = config.load_project(self.home)
            self.assertEqual(p.warnings, ["foreman.toml: [backlog]: unknown keys schema (ignored)"])
            self.assertEqual(p.section("backlog")["schema"], v)  # kept as read, ignored

    def test_inactive_is_set_up_but_hooks_off(self):
        (self.tmp / "projects.toml").write_text(
            f'[[project]]\nname = "fake"\nhome = "{self.home}"\nmode = "inactive"\n'
        )
        self.assertIsNone(config.project_for_cwd(self.home))  # hooks do nothing
        p = config.resolve("fake")
        self.assertEqual((p.mode, p.active), ("inactive", False))  # the CLI still works
        ev = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "make up"}}
        self.assertEqual(self.hook("guard", ev).returncode, 0)
        self.assertEqual(self.cli("--project", "fake", "guard", "make up").returncode, 2)
        self.assertIn("fake\tinactive", self.cli("config", "--projects").stdout)

    def test_no_registry_means_no_project(self):
        os.environ["FOREMAN_REGISTRY"] = str(self.tmp / "missing.toml")
        self.assertIsNone(config.project_for_cwd(self.home))

    def test_cli_check_and_projects(self):
        r = self.cli("check")
        self.assertIn("fake", r.stdout)
        r = self.cli("config", "--projects")
        self.assertIn("fake\tactive", r.stdout)
        r = self.cli("check", cwd=self.tmp)
        self.assertEqual(r.returncode, 2)
        self.assertIn("no project here", r.stderr)

    def test_help_lists_every_command(self):
        """Every command main() dispatches (before or through argparse) is named in `foreman -h` with its own line."""
        src = (ROOT / "lib/foreman/cli.py").read_text()
        main_src = src[src.index("def main(") : src.index("ap = build_parser()")]
        before = set(re.findall(r'argv\[:\d\] == \["([a-z][a-z-]*)"', main_src))
        after = set(re.findall(r'^    if c == "([a-z-]+)"', src, re.M))
        self.assertTrue({"hook", "hung-shells", "time-limit", "lock", "wait"} <= before, before)
        out = self.cli("-h").stdout
        for name in sorted((before | after) - {"hook"}):
            self.assertRegex(out, rf"[{{,]{re.escape(name)}[,}}]", f"`foreman -h` has no choice {name}")
            self.assertRegex(out, rf"(?m)^{re.escape(name)}\b", f"`foreman -h` has no line for {name}")
        # `hook` is internal (Claude Code's hooks call it): left out of `foreman -h`, and still dispatched
        self.assertNotRegex(out, r"[{,]hook[,}]")
        self.assertNotRegex(out, r"(?m)^hook\b")
        self.assertEqual(self.cli("hook", "nope", inp="{}").returncode, 0)

    def test_replaced_commands(self):
        """A command folded into another (cli.REPLACED) says, for one version, which command replaces it, and exits
        2 without doing anything; the new form works, and `foreman -h` names neither the old one nor a line for it."""
        out = self.cli("-h").stdout
        for old, new in cli.REPLACED.items():
            r = self.cli(old, "x")
            self.assertEqual(r.returncode, 2, old)
            self.assertIn(f"replaced by `foreman {new}`", r.stderr)
            self.assertEqual(r.stdout, "")
            self.assertNotRegex(out, rf"[{{,]{old}[,}}]")
            self.assertNotRegex(out, rf"(?m)^{old}\b")
        self.assertEqual(self.cli("--project", "fake", "heartbeat", "loop").returncode, 2)
        self.assertFalse((self.project.state / "heartbeats.json").exists())
        r = self.cli("recurring", "done", "loop")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("loop", util.read_json(self.project.state / "heartbeats.json", {}))
        for bad in (
            ["recurring", "done"],
            ["recurring", "loop"],
            ["session", "S1", "--all"],
            ["session", "--json"],
            ["config", "--projects", "--json"],
            ["permissions", "grants", "--candidate", "x.toml"],
        ):
            self.assertEqual(self.cli(*bad).returncode, 2, bad)

    def test_owner_guide_is_cited_where_the_owner_is_written_to(self):
        """docs/owner.md exists and every procedure that writes to the owner cites it: cited, not copied."""
        self.assertTrue((ROOT / "docs/owner.md").is_file())
        self.assertEqual(self.cli("docs", "owner").returncode, 0)
        for f in (
            "docs/orchestrator.md",
            "docs/agent.md",
            "skills/orchestrator-pass/SKILL.md",
            "skills/review-plan/SKILL.md",
            "skills/setup/SKILL.md",
            "skills/owner-catchup/SKILL.md",
            "skills/owner-decisions/SKILL.md",
            "skills/owner-backlog/SKILL.md",
            "skills/owner-permissions/SKILL.md",
            "agents/dev.md",
            "agents/mechanic.md",
            "agents/planner.md",
            "agents/reviewer.md",
        ):
            text = (ROOT / f).read_text()
            self.assertRegex(text, r"docs/owner\.md|foreman docs owner", f"{f} does not cite the owner guide")
            self.assertNotIn("## Before and after", text, f"{f} copies the owner guide")

    def published_files(self, root=ROOT):
        """Every file the public repo carries, under `root` (the working tree, or an exported snapshot): all but what
        the snapshot leaves out (snapshot.EXCLUDED: ops/, the project's own home, and tests/live/), caches, hidden
        folders and a worktree's .git file (.claude-plugin: its manifests name the author), this file (it holds the
        patterns) and LICENSE."""
        for f in sorted(root.rglob("*")):
            rel = f.relative_to(root)
            if (
                not f.is_file()
                or snapshot.excluded(rel)
                or "__pycache__" in rel.parts
                or any(d.startswith(".") for d in rel.parts[:-1])
                or f.suffix == ".pyc"
            ):
                continue
            if str(rel) in ("LICENSE", ".git", "tests/test_config.py"):
                continue
            yield rel, f.read_text(errors="replace")

    def scan(self, pat, root=ROOT):
        hits = []
        for rel, text in self.published_files(root):
            for n, line in enumerate(text.splitlines(), 1):
                if pat.search(line.replace(self.PUBLIC_REPO, "")):
                    hits.append(f"{rel}:{n}: {line.strip()[:80]}")
        return hits

    # The public repository's own address (README's install line) is the one place the owner's account may appear.
    PUBLIC_REPO = "github.com/" + "jordi" + "sala1991/foreman"
    PROJECT_PATHS = re.compile(
        "|".join(("local" + "-llm", "ing" + "ot", "cop" + "ets", "Jor" + "di", "/Us" + "ers/")), re.I
    )
    ISSUE_IDS = re.compile(r"\bFO" + r"-[0-9]+")

    def test_no_project_paths_in_published_files(self):
        """No published file names a real product, the owner or a path of the owner's machine: examples and tests
        use invented names (tidepool, quayside, ...)."""
        self.assertEqual(self.scan(self.PROJECT_PATHS), [])

    def test_no_issue_ids_in_published_files(self):
        """The public repo has no ops/, so an issue id would point nowhere: files say what the code does."""
        self.assertEqual(self.scan(self.ISSUE_IDS), [])

    def test_public_snapshot_passes_the_publish_checks(self):
        """`foreman-dev snapshot` of HEAD into an empty folder: no ops/ nor tests/live/, the same two scans clean over
        it, and its short CHANGELOG satisfies the release check."""
        if (
            subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, check=False).returncode
            != 0
        ):
            self.skipTest("not a git checkout")
        dest = self.tmp / "snapshot"
        r = subprocess.run(
            [sys.executable, str(ROOT / "dev/foreman-dev"), "snapshot", str(dest)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r"^\d+ files, \d+ KB of [0-9a-f]{12} in ")
        self.assertFalse((dest / "ops").exists())
        self.assertFalse((dest / "tests/live").exists())
        self.assertTrue((dest / "lib/foreman/hooks.py").is_file())
        self.assertTrue((dest / "examples/extend/ops/foreman.toml").is_file(), "only the top-level ops/ is left out")
        self.assertEqual(self.scan(self.PROJECT_PATHS, dest), [])
        self.assertEqual(self.scan(self.ISSUE_IDS, dest), [])
        changelog = (dest / "CHANGELOG.md").read_text()
        self.assertIn("First public release", changelog)
        self.assertLess(len(changelog.splitlines()), 30)
        self.assertEqual([p for p in release.check(dest) if "CHANGELOG" in p], [])
        again = subprocess.run(
            [sys.executable, str(ROOT / "dev/foreman-dev"), "snapshot", str(dest)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(again.returncode, 2)
        self.assertIn("is not an empty folder", again.stderr)


if __name__ == "__main__":
    unittest.main()
