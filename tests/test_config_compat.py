"""Config compatibility (tolerant reading): a foreman.toml or registry entry this foreman does not fully understand
keeps its product, with visible warnings, everywhere but in the strict places (`foreman check`, the selftest,
`foreman integrate` for [repos.*]); a bad value keeps the product out, visibly; `config_schema` tells a newer
foreman's file from a typo. Everything on fake projects in the temporary directory.
"""

import json
import unittest

from helpers import ROOT, ProjectCase, config

from foreman import i18n, loop, toml_reader
from foreman.backlog import Backlog

RECURRING = '[project]\nname = "x"\n[[recurring]]\nname = "r"\nsources = [{ %s }]\n'


class ConfigCompatTest(ProjectCase):
    def write_future(self, extra_repo="futuro = 1", schema=99, load=True):
        """The fake project as written by a newer foreman: a schema above ours and a key we do not know."""
        text = self.toml.replace("[project]\n", f"[project]\nconfig_schema = {schema}\n", 1)
        text += f'\n[repos.app]\npath = "{self.tmp}/app"\n{extra_repo}\n'
        if load:
            self.write_toml(text)
        else:
            (self.home / "foreman.toml").write_text(text)

    def add_to_registry(self, text):
        f = self.tmp / "projects.toml"
        f.write_text(f.read_text() + text)

    # ---------- foreman.toml ----------
    def test_bad_value_is_an_error(self):
        bad = [
            '[project]\nname = "x"\n[repos.a]\nmerge = "octopus"\n',
            "[project]\n",
            '[project]\nname = "x"\n[deny]\npatterns = ["("]\n',
            '[project]\nname = "x"\nconfig_schema = "two"\n',
            '[project]\nname = "x"\nconfig_schema = 0\n',
            'paths = 3\n[project]\nname = "x"\n',
            '[project]\nname = "x"\n[repos]\na = 1\n',
        ]
        bad += [
            RECURRING % src
            for src in (  # a daily time is HH:MM, and a source reads one thing
                'heartbeat = "a", at = "9:30"',
                'heartbeat = "a", at = "24:00"',
                'heartbeat = "a", at = "09:60"',
                'heartbeat = "a", at = 930',
                'heartbeat = "a", at = "09:30:00"',
                'heartbeat = "a", at = "09:30", max_h = 24',
                'at = "09:30"',
                'heartbeat = "a", glob = "b"',
                'heartbeat = "a", max_h = 0',
                'models = "reviews/*.md", at = "09:30"',
                'models = ""',
                "models = 3",
                'models = "reviews/*.md", glob = "b"',
            )
        ]
        for text in bad:
            with self.subTest(text=text):
                (self.home / "foreman.toml").write_text(text)
                with self.assertRaises(config.ConfigError):
                    config.load_project(self.home)

    def test_recurring_sources(self):
        """Good sources load; an unknown source key is a warning."""
        for src in (
            'heartbeat = "a", at = "00:00"',
            'glob = "r/*.md", at = "23:59", label = "ran"',
            'log = "lint", file = "log.md", at = "13:05"',
            'heartbeat = "a", max_h = 1.5',
            'heartbeat = "a"',
            'models = "reviews/*-review-harness*.md"',
            'models = "reviews/*-review-harness*.md", max_h = 720, label = "models"',
        ):
            with self.subTest(src=src):
                self.assertEqual(config.problems(toml_reader.loads(RECURRING % src)), ([], []))
        err, warn = config.problems(toml_reader.loads(RECURRING % 'heartbeat = "a", every = "daily"'))
        self.assertEqual(err, [])
        self.assertIn("unknown source key every", warn[0])

    def test_unknown_key_is_a_warning_and_strict_an_error(self):
        for text, word in (
            ('[project]\nname = "x"\n[nonsense]\n', "[nonsense]"),
            ('[project]\nname = "x"\n[agents]\nslots = 2\n', "slots"),
            ('[project]\nname = "x"\n[resources.gpu]\nfuturo = 1\n', "futuro"),
        ):
            with self.subTest(text=text):
                (self.home / "foreman.toml").write_text(text)
                p = config.load_project(self.home)
                self.assertEqual(p.name, "x")
                self.assertEqual(len(p.warnings), 1)
                self.assertIn(word, p.warnings[0])
                self.assertIn("(ignored)", p.warnings[0])
                with self.assertRaises(config.ConfigError) as cm:
                    config.load_project(self.home, strict=True)
                self.assertIn(word, str(cm.exception))
                self.assertNotEqual(config.validate(toml_reader.loads(text)), [])

    def test_strings_are_checked(self):
        """[strings]: an unknown key is a warning (strict: an error), a value of the wrong kind an error."""
        base = '[project]\nname = "x"\n'
        good = base + '[strings]\nnotes_heading = "# Log {date}"\n[strings.start]\ntitle = "T"\n'
        self.assertEqual(config.problems(toml_reader.loads(good)), ([], []))
        for text, word in (
            (base + '[strings]\nnotes_heding = "x"\n', "[strings]: unknown keys notes_heding"),
            (base + '[strings.start]\ntittle = "x"\n', "[strings] start: unknown keys tittle"),
        ):
            with self.subTest(text=text):
                (self.home / "foreman.toml").write_text(text)
                p = config.load_project(self.home)
                self.assertEqual(len(p.warnings), 1, p.warnings)
                self.assertIn(word, p.warnings[0])
                with self.assertRaises(config.ConfigError):
                    config.load_project(self.home, strict=True)
        for text, word in (
            (base + "[strings]\nnotes_heading = 3\n", "[strings] notes_heading must be a text"),
            (base + '[strings]\nstart = "x"\n', "[strings] start must be a table of texts"),
        ):
            with self.subTest(text=text):
                (self.home / "foreman.toml").write_text(text)
                with self.assertRaises(config.ConfigError) as cm:
                    config.load_project(self.home)
                self.assertIn(word, str(cm.exception))

    def test_removed_keys_are_a_warning(self):
        """[agents] name_phrase, [strings.panel] and [ids] initiative and milestone (the backlog has neither since
        schema 5) are no longer read: one left in a foreman.toml loads, ignored,
        with a warning that says to delete it (strict: `foreman check` fails naming it), never an error."""
        base = '[project]\nname = "x"\n'
        for text, word in (
            (base + '[agents]\nname_phrase = "te llamas"\n', "[agents] name_phrase is no longer used"),
            (base + '[strings.panel]\ntab_now = "Today"\n[strings.panel.lanes]\ndone = "x"\n', "[strings.panel] is"),
            (base + '[strings]\npanel = "x"\n', "[strings] panel is no longer used"),
            (base + '[ids]\ninitiative = "^I\\\\d+$"\n', "[ids] initiative is no longer used"),
            (base + '[ids]\nmilestone = "^M\\\\d+$"\n', "[ids] milestone is no longer used"),
            # config schema 18: keys no product set; a bad old value is no longer an error either
            (base + '[paths]\nboard = "board.md"\n', "[paths] board is no longer used"),
            (base + "[backlog]\nauto_close_parent = false\n", "[backlog] auto_close_parent is no longer used"),
            (base + '[loop]\nskip_idle = "no"\n', "[loop] skip_idle is no longer used"),
            (base + '[panel]\nlanguage = "es"\n', "[panel] language is no longer used"),
            (base + '[agents]\nplanner_types = "lab-planner"\n', "[agents] planner_types is no longer used"),
            (base + "owner_plain = false\n", "[project] owner_plain is no longer used"),
        ):
            with self.subTest(text=text):
                (self.home / "foreman.toml").write_text(text)
                p = config.load_project(self.home)
                self.assertEqual(len(p.warnings), 1, p.warnings)
                self.assertIn(word, p.warnings[0])
                self.assertIn("delete it", p.warnings[0])
                self.assertNotIn("panel", i18n.project_overrides(p))
                with self.assertRaises(config.ConfigError):
                    config.load_project(self.home, strict=True)
        self.assertIn("[agents]\n", self.toml)
        self.write_toml(self.toml.replace("[agents]\n", '[agents]\nname_phrase = "te llamas"\n', 1))
        self.assertEqual(len(self.project.warnings), 1, self.project.warnings)
        r = self.cli("check")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("name_phrase is no longer used", r.stdout + r.stderr)

    def test_removed_page_keys_are_a_warning(self):
        """Config schema 22: [backlog] page, archive_page, decisions_page and decisions_section (foreman no longer
        writes text copies of the backlog). Each one loads with a warning that says to delete it and the file it
        named; `foreman check` exits 1 until it is gone, 0 without it; the backlog still works and no page is
        written where the key pointed."""
        Backlog(self.project).init()
        for key, value in (
            ("page", '"pages/backlog.md"'),
            ("archive_page", '"pages/backlog-archive.md"'),
            ("decisions_page", '"pages/decisions.md"'),
            ("decisions_section", '"## Open decisions"'),
        ):
            with self.subTest(key=key):
                self.write_toml(self.toml + f'\n[backlog]\nfile = "backlog.json"\n{key} = {value}\n')
                (w,) = self.project.warnings
                self.assertIn(f"[backlog] {key} is no longer used", w)
                self.assertIn("delete the file it names too", w)
                r = self.cli("check")
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                self.assertIn(f"{key} is no longer used", r.stdout + r.stderr)
                r = self.cli("backlog", "add", "--id", f"AB-{len(key)}", "--title", f"after {key}")
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertFalse((self.home / "pages").exists())
        self.write_toml(self.toml)
        self.assertEqual(self.project.warnings, [])
        r = self.cli("check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_removed_repo_keys_are_unknown(self):
        """[repos.*] close_keyword and delivery_check (config schema 18): an unknown-key warning, ignored."""
        text = '[project]\nname = "x"\n[repos.app]\npath = "app"\nclose_keyword = "Fixes"\ndelivery_check = "true"\n'
        (self.home / "foreman.toml").write_text(text)
        p = config.load_project(self.home)
        want = "foreman.toml: [repos.app]: unknown keys close_keyword, delivery_check (ignored)"
        self.assertEqual(p.warnings, [want])

    def test_production_types_and_lock_long_min_values(self):
        base = '[project]\nname = "x"\n'
        good = base + '[agents]\nproduction_types = ["lab-dev"]\n[loop]\nlock_long_min = 180\n'
        self.assertEqual(config.problems(toml_reader.loads(good)), ([], []))
        for text, word in (
            (base + "[agents]\nproduction_types = [1]\n", "[agents] production_types must be a list"),
            (base + "[loop]\nlock_long_min = 0\n", "[loop] lock_long_min must be a whole number"),
            (base + "[loop]\nlock_long_min = 1.5\n", "[loop] lock_long_min must be a whole number"),
            (base + "[loop]\nlock_long_min = true\n", "[loop] lock_long_min must be a whole number"),
        ):
            with self.subTest(text=text):
                (self.home / "foreman.toml").write_text(text)
                with self.assertRaises(config.ConfigError) as cm:
                    config.load_project(self.home)
                self.assertIn(word, str(cm.exception))

    def test_newer_schema(self):
        self.write_future()
        w = self.project.warnings
        self.assertIn(
            f"written for a newer foreman (config schema 99, this foreman knows {config.CONFIG_SCHEMA})", w[0]
        )
        self.assertIn("futuro", w[1])
        self.assertEqual(self.project.repo("app")["futuro"], 1)  # kept, ignored
        self.write_toml(self.toml.replace("[project]\n", f"[project]\nconfig_schema = {config.CONFIG_SCHEMA}\n", 1))
        self.assertEqual(self.project.warnings, [])
        self.assertEqual(self.project.section("project")["config_schema"], config.CONFIG_SCHEMA)

    def test_schema_keys_recorded(self):
        """O2: CONFIG_SCHEMA goes up with every new (or removed) key. tests/config_schema.json keeps the keys of each
        schema; a key
        added without bumping the schema (or a schema without its list) fails here."""
        rec = json.loads((ROOT / "tests/config_schema.json").read_text())
        schemas = sorted(int(k) for k in rec if k.isdigit())
        self.assertEqual(
            schemas[-1],
            config.CONFIG_SCHEMA,
            "CONFIG_SCHEMA and the newest list of tests/config_schema.json must agree: "
            "if the newest schema is not released yet (CHANGELOG Unreleased), add your keys to its list; "
            "else bump CONFIG_SCHEMA and add a new list",
        )
        keys = sorted(
            [f"{s}.{k}" for s, d in config.DEFAULTS.items() for k in d]
            + ["project.name"]
            + [f"repos.*.{k}" for k in config.REPO_DEFAULTS]
            + [f"resources.*.{k}" for k in config.RESOURCE_DEFAULTS]
            + ["repos", "resources", "recurring"]
            + list(config.DEFAULTS)
        )
        self.assertEqual(
            keys,
            rec[str(config.CONFIG_SCHEMA)],
            "the keys of foreman.toml changed: "
            "if the newest schema is not released yet (CHANGELOG Unreleased), add your keys to its list; "
            "else bump CONFIG_SCHEMA and add a new list (in tests/config_schema.json, and a line in docs/config.md)",
        )
        for a, b in zip(schemas, schemas[1:]):
            self.assertEqual(b, a + 1)

    def test_our_home_is_strict(self):
        """The home that ships with the repo (if any) has nothing a strict read rejects."""
        f = ROOT / "ops/foreman.toml"
        if not f.exists():
            self.skipTest("no ops/ home in this checkout")
        self.assertEqual(config.validate(toml_reader.loads(f.read_text())), [])

    # ---------- the CLI ----------
    def test_check_is_strict(self):
        """(c) `foreman check` exits 1 and names the key and the schema."""
        self.assertEqual(self.cli("check").returncode, 0, self.cli("check").stdout)
        self.write_future()
        r = self.cli("check")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("futuro", r.stdout)
        self.assertIn("config schema 99", r.stdout)
        self.assertIn("newer foreman", r.stdout)
        # a misspelt [strings] key too: it did nothing before, silently
        self.write_toml(self.toml + '\n[strings]\nnotes_heding = "# Log {date}"\n')
        r = self.cli("check")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("[strings]: unknown keys notes_heding (ignored)", r.stdout)
        self.write_future()
        # every other command still works on it
        self.assertEqual(self.cli("loop").returncode, 0)
        self.assertIn("Config warnings", self.cli("loop").stdout)
        # a bad value: the command cannot even resolve the project, and says why
        self.write_future('merge = "octopus"', schema=1, load=False)
        r = self.cli("check")
        self.assertEqual(r.returncode, 2)
        self.assertIn("merge must be one of", r.stderr)

    def test_integrate_refuses_unknown_repo_keys(self):
        """O7: integrate is strict on [repos.*]: a misspelt key there could merge something unguarded."""
        self.write_future("require_aproval = true", schema=1)
        r = self.cli("integrate", "app", "some-branch", "PF-1", "--check")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("[repos.app] require_aproval", r.stderr)
        self.assertIn("nothing done", r.stderr)

    # ---------- the registry ----------
    def test_registry_unknown_mode_keeps_the_others(self):
        """(d) an entry with a mode this foreman does not know is broken; the other products load."""
        other = self.tmp / "other"
        other.mkdir()
        (other / "foreman.toml").write_text('[project]\nname = "other"\n')
        self.add_to_registry(
            f'[[project]]\nname = "other"\nhome = "{other}"\nmode = "paused"\n[[project]]\nhome = "{other}"\n'
        )
        errors, broken = [], []
        projects = config.registered_projects(errors, broken)
        self.assertEqual([p.name for p in projects], ["fake"])
        self.assertEqual([b["name"] for b in broken], ["other", "#3"])
        self.assertIn("unknown mode 'paused'", broken[0]["error"])
        self.assertEqual(len(errors), 2)
        self.assertEqual(config.project_for_cwd(self.home).name, "fake")
        with self.assertRaises(config.ConfigError) as cm:
            config.resolve("other")
        self.assertIn("unknown mode", str(cm.exception))
        out = self.cli("config", "--projects").stdout
        self.assertIn("fake\tactive", out)
        self.assertIn("other\tpaused\t", out)
        self.assertIn("does not load: registry: unknown mode", out)
        # check of another product: the broken one is mentioned, but does not fail it
        r = self.cli("check")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("other does not load", r.stdout)

    def test_registry_unknown_keys_are_warnings(self):
        f = self.tmp / "projects.toml"
        f.write_text(
            f.read_text().replace('mode = "active"', 'mode = "active"\ncolour = "red"')
            + '[panel]\nlanguage = "en"\ntheme = "dark"\n[future]\nx = 1\n'
        )
        warnings = []
        (p,) = config.registered_projects(warnings=warnings)
        self.assertEqual(len(p.warnings), 1)
        self.assertIn("colour", p.warnings[0])
        self.assertEqual(len(warnings), 2)
        self.assertEqual(config.panel_settings()["language"], "en")
        self.assertEqual(self.cli("check").returncode, 1)  # its own entry: strict

    # ---------- the hooks ----------
    def test_guard_still_guards_with_warnings(self):
        """(b) a product with unknown keys keeps its guard."""
        self.write_future()
        ev = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "make up"}}
        r = self.hook("guard", ev)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("widget", r.stderr)

    def who(self, session="S2", cwd=None):
        r = self.hook("who", {"hook_event_name": "SessionStart", "session_id": session, "source": "startup"}, cwd=cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"] if r.stdout.strip() else ""

    def test_who_and_loop_name_the_warnings(self):
        self.write_future()
        ctx = self.who()
        self.assertIn("no orchestrator now", ctx)
        self.assertIn("configuration has warnings", ctx)
        self.assertIn("futuro", ctx)
        self.cli("session", "S1")
        self.assertIn("futuro", self.who("S1"))  # the orchestrator too
        lines = loop.summary(self.project)
        self.assertTrue(lines[0].startswith("Config warnings"), lines)
        self.assertIn("config schema 99", lines[0])

    def test_broken_config_fails_open_but_visibly(self):
        """J6: a product whose config does not load has no guard (fails open); `who` says so, the other hooks keep
        quiet and never fail, and no loop pass comes (the module's tick finds no active project)."""
        self.cli("session", "S1")
        (self.home / "foreman.toml").write_text(
            self.toml.replace("[resources.widget]", '[repos.app]\nmerge = "octopus"\n[resources.widget]')
        )
        ev = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "make up"}}
        r = self.hook("guard", ev)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
        ctx = self.who()
        self.assertIn("does not load", ctx)
        self.assertIn("the guard lets every command through", ctx)
        self.assertIn("merge must be one of", ctx)
        r = self.cli("hook", "mod", "tick", inp=json.dumps({"cwd": str(self.home), "session": "S1"}))
        self.assertEqual((r.returncode, json.loads(r.stdout)), (0, {"marked": False}))
        # the loop prompt, another session's prompt, an ordinary prompt, or outside the project: nothing
        for sid, prompt, cwd in (
            ("S1", "Fake orchestrator loop", None),
            ("S9", "Fake orchestrator loop", None),
            ("S1", "hello", None),
            ("S1", "Fake orchestrator loop", self.tmp),
        ):
            r = self.hook("loop", {"hook_event_name": "UserPromptSubmit", "session_id": sid, "prompt": prompt}, cwd=cwd)
            self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertEqual(self.who(cwd=self.tmp), "")
        # even an unreadable foreman.toml: still said, never a failure
        (self.home / "foreman.toml").write_text("[project\n")
        self.assertIn("does not load", self.who())
        # an inactive product's broken config: its hooks would do nothing anyway
        f = self.tmp / "projects.toml"
        f.write_text(f.read_text().replace('mode = "active"', 'mode = "inactive"'))
        self.assertEqual(self.who(), "")

    def test_unknown_mode_is_said_in_its_sessions(self):
        f = self.tmp / "projects.toml"
        f.write_text(f.read_text().replace('mode = "active"', 'mode = "paused"'))
        ctx = self.who()
        self.assertIn("does not load", ctx)
        self.assertIn("unknown mode 'paused'", ctx)


if __name__ == "__main__":
    unittest.main()
