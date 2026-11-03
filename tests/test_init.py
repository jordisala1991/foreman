"""`foreman init`: a new project's home and its registry entry, made in one go (lib/foreman/init.py).

Every test runs in its own temporary folder with FOREMAN_REGISTRY and FOREMAN_STATE_HOME there (helpers.ProjectCase):
the machine's real registry is never read or written."""

import json
import unittest

from helpers import ROOT, ProjectCase, git, make_repo

from foreman import backlog, backlog_check, config, i18n, toml_reader

# a registry as a person keeps it: comments, another project, no blank line at the end
REGISTRY = (
    "# this machine's products (hand-written)\n"
    "[[project]]\n"
    'name = "fake"   # the test project\n'
    'home = "{home}"\n'
    'mode = "active"\n'
    "\n"
    "[panel]\n"
    'who = "Owner"  # no newline after this'
)


class InitCase(ProjectCase):
    def setUp(self):
        super().setUp()
        self.registry = self.tmp / "projects.toml"
        self.registry.write_text(REGISTRY.format(home=self.home))
        self.before = self.registry.read_bytes()
        self.app = make_repo(self.tmp / "app")
        self.ops = self.app / "ops"

    def init(self, *args, cwd=None):
        return self.cli("init", *args, cwd=cwd or self.tmp)

    def demo(self, *extra):
        return self.init(
            str(self.ops), "--name", "demo", "--repo", f"app={self.app}", "--local-ci", "make test",
            "--language", "es", "--sample", *extra,
        )  # fmt: skip

    def snapshot(self):
        """Every file under the temporary folder but the state root, with its bytes."""
        return {
            str(f.relative_to(self.tmp)): f.read_bytes()
            for f in sorted(self.tmp.rglob("*"))
            if f.is_file() and "state" not in f.relative_to(self.tmp).parts[:1] and ".git" not in f.parts
        }


class TestInit(InitCase):
    def test_a_new_project_from_nothing(self):
        r = self.demo()
        self.assertEqual(r.returncode, 0, r.stderr)
        raw = toml_reader.loads((self.ops / "foreman.toml").read_text())
        self.assertEqual(raw["project"]["config_schema"], config.CONFIG_SCHEMA)
        p = config.load_project(self.ops, strict=True)  # strict: no warning at all
        self.assertEqual((p.name, p.language), ("demo", "es"))
        self.assertEqual(p.repo("app")["path"].resolve(), self.app)
        self.assertEqual(raw["repos"]["app"], {"path": "..", "remote": "none", "remote_checks": "none",
                                               "local_ci": "make test"})  # fmt: skip
        # the home is inside the repo: the agents' worktrees go beside it, never inside
        self.assertEqual(p.worktrees_dir, self.tmp / "app-wt")
        lines = (self.ops / "foreman.toml").read_text().splitlines()
        self.assertLessEqual(len(lines), 25)
        rules = (self.ops / "rules.md").read_text()
        self.assertTrue(rules.startswith("# Normas de demo\n"))
        self.assertIn("- app: `make test`", rules)
        self.assertTrue((self.ops / "notes").is_dir() and (self.ops / "reviews").is_dir())
        b = backlog.Backlog(p)
        data = b.load()
        self.assertEqual([(i["id"], i["status"]) for _, i, _ in b.iter_issues(data)], [("DE-1", "triage")])
        self.assertEqual([e for e in backlog_check.validate(b, data) if not e.startswith("warning: ")], [])
        self.assertTrue((self.ops / "pages/backlog.md").exists())
        # the registry: what was there, byte for byte, then the new entry, which loads
        text = self.registry.read_bytes()
        self.assertTrue(text.startswith(self.before))
        self.assertEqual(
            text[len(self.before) :].decode(),
            f'\n\n[[project]]\nname = "demo"\nhome = "{self.ops}"\nmode = "active"\n',
        )
        entries, broken, _ = config.registry_scan()
        self.assertEqual([(e["name"], e["mode"]) for e in entries], [("fake", "active"), ("demo", "active")])
        self.assertEqual(broken, [])
        self.assertIn("config schema", r.stdout)  # it ends with the new project's check
        self.assertIn("foreman start", r.stdout)
        c = self.cli("--project", "demo", "check", cwd=self.tmp)
        self.assertEqual(c.returncode, 0, c.stdout + c.stderr)

    def test_again_changes_nothing(self):
        self.assertEqual(self.demo().returncode, 0)
        before = self.snapshot()
        r = self.demo()
        self.assertEqual(r.returncode, 0, r.stderr)
        steps = r.stdout.split("\n\n")[0].splitlines()[1:]
        self.assertEqual(len(steps), 7)
        self.assertTrue(all("already there" in s for s in steps), steps)
        self.assertEqual(self.snapshot(), before)

    def test_a_clash_writes_nothing(self):
        self.assertEqual(self.demo().returncode, 0)
        other = self.tmp / "other"
        make_repo(other)
        before = self.snapshot()
        for args in (
            (str(other / "ops"), "--name", "demo"),  # another home with the same name
            (str(self.ops), "--name", "demo2", "--repo", f"app={self.app}"),  # the same home with another name
            (str(self.ops), "--name", "demo", "--repo", f"app={self.app}", "--language", "es", "--sample",
             "--local-ci", "make test", "--mode", "inactive"),  # registered with another mode
            (str(self.home), "--name", "fake2", "--no-register"),  # a foreman.toml of its own is never rewritten
        ):  # fmt: skip
            with self.subTest(args=args):
                r = self.init(*args)
                self.assertEqual(r.returncode, 2, r.stdout)
                self.assertIn("foreman init:", r.stderr)
                self.assertEqual(self.snapshot(), before)
                self.assertFalse((other / "ops").exists())

    def test_another_home_of_a_registered_name(self):
        r = self.init(str(self.tmp / "x"), "--name", "fake")
        self.assertEqual(r.returncode, 2)
        self.assertIn("fake is already registered", r.stderr)
        self.assertFalse((self.tmp / "x").exists())
        self.assertEqual(self.registry.read_bytes(), self.before)

    def test_dry_run_writes_nothing(self):
        before = self.snapshot()
        r = self.demo("--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.ops.exists())
        self.assertIn("foreman.toml: would be written", r.stdout)
        self.assertIn("sample issue DE-1: would be added, in triage", r.stdout)
        self.assertIn('name = "demo"\nlanguage = "es"\n', r.stdout)  # the files whole
        self.assertIn("# Normas de demo", r.stdout)
        self.assertIn(f'[[project]]\nname = "demo"\nhome = "{self.ops}"\nmode = "active"\n', r.stdout)
        self.assertNotIn("config schema", r.stdout)  # no check: nothing to check yet

    def test_no_registry_yet(self):
        reg = self.tmp / "conf" / "foreman" / "projects.toml"
        r = self.cli("init", str(self.tmp / "solo"), "--name", "solo", cwd=self.tmp, env={"FOREMAN_REGISTRY": str(reg)})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(
            reg.read_text(), f'[[project]]\nname = "solo"\nhome = "{self.tmp / "solo"}"\nmode = "active"\n'
        )
        raw = toml_reader.loads((self.tmp / "solo/foreman.toml").read_text())
        self.assertEqual(raw, {"project": {"name": "solo", "config_schema": config.CONFIG_SCHEMA}})  # all defaults
        self.assertEqual(backlog.Backlog(config.load_project(self.tmp / "solo")).load()["issues"], [])

    def test_no_register_and_inactive(self):
        r = self.init(str(self.tmp / "a"), "--name", "a", "--no-register")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.registry.read_bytes(), self.before)
        self.assertIn("registry: left alone (--no-register)", r.stdout)
        r = self.init(str(self.tmp / "b"), "--name", "b", "--mode", "inactive")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.registry.read_text().endswith('name = "b"\nhome = "' + str(self.tmp / "b") +
                                                          '"\nmode = "inactive"\n'))  # fmt: skip

    def test_a_github_remote_and_other_values(self):
        git(self.app, "config", "remote.origin.url", "git@github.com:acme/app.git")
        git(self.app, "branch", "-m", "trunk")
        home = self.tmp / "ops"  # beside the repo: the default worktrees folder is outside it
        r = self.init(
            str(home), "--name", "acme.app", "--owner", "Ana", "--repo", "app",
            "--local-ci", "app=npm test", "--slots", "2", "--every-min", "30", "--sample",
        )  # fmt: skip
        self.assertEqual(r.returncode, 0, r.stderr)
        raw = toml_reader.loads((home / "foreman.toml").read_text())
        self.assertNotIn("paths", raw)
        self.assertEqual(raw["project"]["owner"], "Ana")
        self.assertEqual(raw["agents"], {"production_slots": 2})
        self.assertEqual(raw["loop"], {"every_min": 30})
        self.assertEqual(
            raw["repos"]["app"], {"path": str(self.app), "main": "trunk", "github": "acme/app", "local_ci": "npm test"}
        )
        self.assertIn("a direct instruction from Ana.", (home / "rules.md").read_text())
        p = config.load_project(home, strict=True)
        self.assertEqual([i["id"] for _, i, _ in backlog.Backlog(p).iter_issues(backlog.Backlog(p).load())], ["AC-1"])

    def test_bad_arguments(self):
        for args, why in (
            (("x", "--name", "Demo"), "--name Demo"),
            (("x", "--name", "x", "--sample"), "--prefix"),
            (("x", "--name", "x", "--sample", "--prefix", "abc"), "two capital letters"),
            (("x", "--name", "x", "--prefix", "AB"), "--prefix goes with --sample"),
            (("x", "--name", "x", "--repo", f"a={self.app}", "--repo", f"b={self.app}", "--local-ci", "make"),
             "name its repo"),
            (("x", "--name", "x", "--repo", f"a={self.tmp / 'nowhere'}"), "is not a folder"),
            (("x", "--name", "x", "--slots", "0"), "1 or more"),
        ):  # fmt: skip
            with self.subTest(args=args):
                r = self.init(*args)
                self.assertEqual(r.returncode, 2, r.stdout)
                self.assertIn(why, r.stderr)
                self.assertFalse((self.tmp / "x").exists())

    def test_a_registry_that_does_not_read_is_left_alone(self):
        self.registry.write_text("[[project]\nname = ")
        r = self.init(str(self.tmp / "x"), "--name", "x")
        self.assertEqual(r.returncode, 2)
        self.assertIn("does not read", r.stderr)
        self.assertEqual(self.registry.read_text(), "[[project]\nname = ")
        self.assertFalse((self.tmp / "x").exists())

    def test_an_existing_backlog_gets_only_the_sample(self):
        r = self.init(str(self.tmp / "x"), "--name", "xy", "--no-register")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.init(str(self.tmp / "x"), "--name", "xy", "--no-register", "--sample")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("backlog.json: already there", r.stdout)
        self.assertIn("sample issue XY-1: added, in triage", r.stdout)

    def test_a_home_without_a_backlog_gets_one(self):
        """A home with its own foreman.toml keeps it as it is and gets only what it lacks (its backlog); an option
        that would shape that foreman.toml is still refused, nothing written."""
        b = backlog.Backlog(self.project)
        if b.exists():
            b.data_path.unlink()
        toml = (self.home / "foreman.toml").read_bytes()
        before = self.snapshot()
        r = self.init(str(self.home), "--name", "fake", "--slots", "2")
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("without --slots it is kept as it is", r.stderr)
        self.assertEqual(self.snapshot(), before)
        r = self.init(str(self.home), "--name", "fake")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("foreman.toml: already there", r.stdout)
        self.assertIn("backlog.json: made, with its pages", r.stdout)
        self.assertEqual((self.home / "foreman.toml").read_bytes(), toml)
        self.assertEqual(b.load()["schema_version"], backlog.SCHEMA_VERSION)
        self.assertEqual(self.registry.read_bytes(), self.before)

    def test_the_removed_backlog_commands_are_unknown(self):
        """The backlog's own init and check are gone (foreman init, foreman check): argparse refuses them."""
        for cmd in ("init", "check"):
            with self.subTest(cmd=cmd):
                r = self.cli("backlog", cmd)
                self.assertEqual(r.returncode, 2, r.stdout)
                self.assertIn(f"invalid choice: '{cmd}'", r.stderr)

    def test_the_texts_are_in_both_languages(self):
        self.assertEqual(set(i18n.strings("en")["init"]), set(i18n.strings("es")["init"]))
        en = json.loads((ROOT / "lib/foreman/i18n/en.json").read_text())["init"]
        self.assertIn("{checks}", en["rules"])


if __name__ == "__main__":
    unittest.main()
