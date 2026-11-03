"""Machine settings: the registry's [machine], shaped like a foreman.toml, inherited by every registered product
(config.machine_settings, config.merge_settings). Defaults < machine < product: a product's scalar wins, lists add up,
tables merge key by key; `[deny] drop` removes an inherited rule; a bad value in [machine] is left out alone, with a
warning in every product; `foreman permissions` says where each rule comes from. Two fake products share one fake
registry: "fake" (GrantCase's, with its own patterns and groups) and "other" (bare)."""

import json
import textwrap

from test_grants import BASE, GrantCase

from foreman import config, grants, guard, orchestrator, permissions

# built in pieces where they could look like a command to the installed guard reading this file
SHIP = "zz-" + "ship"
RELEASE = "zz-" + "release"
OWN = "zz-" + "own"
MACHINE = f"""
[machine.project]
owner = "Machine Owner"
language = "es"
owner_knows = ["CI"]

[machine.deny]
patterns = ["{SHIP}"]
message = "ask the machine's owner"

[machine.deny.grants]
release = ["{RELEASE}"]
"""


class MachineSettingsCase(GrantCase):
    """GrantCase's project ("fake", marked orchestrator S1) plus "other", both in a registry with `machine` text."""

    def setUp(self):
        super().setUp()
        self.other_home = self.tmp / "other"
        self.other_home.mkdir()
        self.other_toml("")

    def other_toml(self, body):
        (self.other_home / "foreman.toml").write_text('[project]\nname = "other"\n' + textwrap.dedent(body))

    def registry(self, machine=MACHINE):
        (self.tmp / "projects.toml").write_text(
            f'[[project]]\nname = "fake"\nhome = "{self.home}"\nmode = "active"\n\n'
            f'[[project]]\nname = "other"\nhome = "{self.other_home}"\nmode = "active"\n' + textwrap.dedent(machine)
        )

    def load(self, name):
        warnings = []
        projects = {p.name: p for p in config.registered_projects(warnings=warnings)}
        self.assertEqual(warnings, [])  # [machine] is a known section of the registry
        return projects[name]

    def both(self):
        return self.load("fake"), self.load("other")


class InheritTest(MachineSettingsCase):
    def test_machine_pattern_denies_an_agent_in_both(self):
        self.registry()
        for p in self.both():
            with self.subTest(p=p.name):
                v = guard.check(p, f"{SHIP} now")
                self.assertEqual(v[0], "deny", v)
                self.assertEqual(p.section("deny")["message"], "ask the machine's owner")
        fake = self.load("fake")
        self.assertEqual(guard.check(fake, "npm " + "publish")[0], "deny")  # its own patterns stay
        self.assertEqual(fake.deny_origin["patterns"][SHIP], "machine")
        self.assertEqual(fake.deny_origin["patterns"]["npm " + "publish"], "product")

    def test_machine_group_needs_each_products_own_yes(self):
        self.registry()
        fake, other = self.both()
        self.assertEqual(grants.groups(fake)["release"], [RELEASE])
        self.assertEqual(grants.groups(other), {"release": [RELEASE]})
        self.project = fake
        self.give("release")
        self.assertIsNone(guard.check(fake, RELEASE, self.ctx()))  # A's orchestrator, with its yes
        self.assertEqual(guard.check(fake, RELEASE, self.ctx(agent="a1"))[0], "orchestrator")  # never an agent
        orchestrator.mark(other, "S1", "other-1", own_session="S1")
        v = guard.check(other, RELEASE, self.ctx())  # B's orchestrator, without B's yes
        self.assertEqual((v[0], v[1].group), ("orchestrator", "release"))
        self.assertNotEqual(v[1].why, "")

    def test_product_adds_its_own_pattern(self):
        self.registry()
        self.other_toml(f'\n[deny]\npatterns = ["{OWN}", "{SHIP}"]\n')
        other = self.load("other")
        self.assertEqual(other.section("deny")["patterns"], [SHIP, OWN])  # the machine's first, no repeats
        for cmd in (SHIP, OWN):
            self.assertEqual(guard.check(other, cmd)[0], "deny", cmd)
        self.assertEqual(other.deny_origin["patterns"], {SHIP: "both", OWN: "product"})
        s = other.as_dict()["sources"]["deny"]
        self.assertEqual((s["patterns"], s["message"], s["grants"]), ("registry+foreman.toml", "registry", "registry"))

    def test_drop_removes_an_inherited_rule_in_that_product_only(self):
        self.registry()
        self.other_toml(f'\n[deny]\ndrop = ["{SHIP}", "group:release"]\n')
        fake, other = self.both()
        self.assertIsNone(guard.check(other, SHIP))
        self.assertEqual(grants.groups(other), {})
        self.assertEqual(other.deny_origin["dropped"], [SHIP, "group:release"])
        self.assertEqual(other.warnings, [])
        self.assertEqual(guard.check(fake, SHIP)[0], "deny")
        self.assertIn("release", grants.groups(fake))

    def test_drop_of_a_pattern_inside_a_group_and_of_nothing(self):
        self.registry(MACHINE.replace(f'release = ["{RELEASE}"]', f'release = ["{RELEASE}", "{OWN}"]'))
        self.other_toml(f'\n[deny]\ndrop = ["{OWN}", "nothing-here"]\n\n[deny.grants]\nrelease = ["{OWN}"]\n')
        other = self.load("other")
        self.assertEqual(grants.groups(other)["release"], [RELEASE, OWN])  # its own pattern stays
        self.assertEqual(other.deny_origin["grants"]["release"], {RELEASE: "machine", OWN: "product"})
        (w,) = other.warnings
        self.assertIn("[deny] drop 'nothing-here': nothing inherited", w)
        r = self.cli("--project", "other", "check")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("nothing inherited from the registry's [machine.deny]", r.stdout)

    def test_drop_must_be_a_list_of_texts(self):
        (self.home / "foreman.toml").write_text(BASE + 'drop = "x"\n')
        with self.assertRaises(config.ConfigError) as c:  # a bad value of a known key: it does not load
            config.load_project(self.home)
        self.assertIn("[deny] drop must be a list", str(c.exception))

    def test_owner_reaches_both_and_one_changes_it(self):
        self.registry()
        fake, other = self.both()
        self.assertEqual((fake.owner, fake.language), ("Owner", "es"))  # its foreman.toml writes the owner
        self.assertEqual((other.owner, other.language), ("Machine Owner", "es"))
        self.assertEqual(other.section("project")["owner_knows"], ["CI"])
        s = fake.as_dict()["sources"]["project"]
        self.assertEqual((s["owner"], s["language"], s["rules"]), ("foreman.toml", "registry", "default"))
        for p in (fake, other):
            d = p.as_dict()
            for sec in config.DEFAULTS:
                self.assertLessEqual(set(d["sources"][sec].values()), set(config.SOURCES))

    def test_lists_and_tables_merge(self):
        self.registry(MACHINE + '\n[machine.strings]\nnotice = "machine title"\n')
        self.other_toml('owner_knows = ["PR", "CI"]\n\n[deny.grants]\nrelease = ["zz-mine"]\nown = ["zz-two"]\n')
        other = self.load("other")
        self.assertEqual(grants.groups(other), {"release": [RELEASE, "zz-mine"], "own": ["zz-two"]})
        self.assertEqual(other.section("project")["owner_knows"], ["CI", "PR"])
        self.assertEqual(other.section("strings"), {"notice": "machine title"})
        self.assertEqual(other.as_dict()["sources"]["strings"], {"notice": "registry"})
        self.assertEqual(other.as_dict()["sources"]["project"]["owner_knows"], "registry+foreman.toml")

    def test_a_pattern_denied_at_one_level_and_granted_at_the_other_warns(self):
        self.registry()
        self.other_toml(f'\n[deny.grants]\nmine = ["{SHIP}"]\n')
        (w,) = self.load("other").warnings
        self.assertIn("[deny.grants] mine: in patterns too, one in the registry's [machine.deny]", w)

    def test_no_machine_section_changes_nothing(self):
        self.registry("")
        fake = self.load("fake")
        before = config.load_project(self.home)
        self.assertEqual(fake.raw, before.raw)
        self.assertEqual((fake.origin, fake.inherited()), ({}, (0, 0, 0)))
        self.assertNotIn("registry", json.dumps(fake.as_dict()["sources"]))
        r = self.cli("check")
        self.assertIn("inherited from the machine (registry [machine]): 0 settings, 0 deny rules\n", r.stdout)

    def test_check_says_what_is_inherited(self):
        self.registry()
        self.other_toml(f'\n[deny]\ndrop = ["{SHIP}"]\n')
        r = self.cli("--project", "other", "check")
        # owner, language, owner_knows, message; the group's one pattern; the pattern it drops
        self.assertIn(
            "inherited from the machine (registry [machine]): 4 settings, 1 deny rule; [deny] drop removes 1 here",
            r.stdout,
        )


class MistakesTest(MachineSettingsCase):
    def test_bad_values_are_left_out_alone_with_a_warning_in_every_product(self):
        self.registry(
            MACHINE.replace(f'patterns = ["{SHIP}"]', f'patterns = ["{SHIP}", "(unclosed"]')
            + 'Bad = ["x"]\n\n[machine.loop]\ncontext_rotate = -1\nevery_min = 30\nprompt = "mine"\n'
            + 'config_schema = 1\n\n[machine.paths]\nnotes = "n"\n\n[machine.futuro]\nx = 1\n'
        )
        fake, other = self.both()
        for p in (fake, other):
            with self.subTest(p=p.name):
                self.assertEqual(guard.check(p, SHIP)[0], "deny")  # the good pattern of the same list stays
                self.assertIn("release", grants.groups(p))  # and the good group
                self.assertNotIn("Bad", grants.groups(p))
                self.assertEqual(p.section("loop")["every_min"], 30)
                self.assertEqual(p.section("loop")["context_rotate"], config.DEFAULTS["loop"]["context_rotate"])
                text = "\n".join(p.warnings)
                for words in (
                    "registry: [machine.deny] bad pattern '(unclosed'",
                    "registry: [machine.deny.grants] 'Bad': a group name",
                    "registry: [machine.loop] context_rotate must be",
                    "registry: [machine.loop]: prompt is each product's own",
                    "registry: [machine.paths] is each product's own",
                    "registry: [machine]: unknown section [futuro]",
                    "registry: [machine.loop]: unknown keys config_schema (ignored)",
                ):
                    self.assertIn(words, text)
        self.assertEqual(self.cli("check").returncode, 1)
        with self.assertRaises(config.ConfigError):
            config.load_entry(config.registry_scan()[0][0], strict=True)

    def test_newer_schema_and_bad_shapes(self):
        self.registry('\n[machine.project]\nconfig_schema = 99\n\n[machine.deny]\npatterns = "x"\ngrants = []\n')
        w = "\n".join(self.load("other").warnings)
        self.assertIn("registry: [machine.project]: written for a newer foreman (config schema 99", w)
        self.assertIn("registry: [machine.deny] patterns must be a list (left out)", w)
        self.assertIn("registry: [machine.deny] grants must be a table (left out)", w)
        self.assertEqual(self.load("other").section("project")["config_schema"], config.CONFIG_SCHEMA)
        self.registry("")
        reg = (self.tmp / "projects.toml").read_text()
        (self.tmp / "projects.toml").write_text("machine = 1\n" + reg)
        self.assertIn("registry: [machine] must be a table", self.load("other").warnings[0])

    def test_unreadable_registry_loads_the_product_alone(self):
        (self.tmp / "projects.toml").write_text("not toml [")
        self.other_toml("")
        p = config.load_project(self.other_home)
        self.assertEqual((p.warnings, p.origin), ([], {}))


class ClassificationTest(MachineSettingsCase):
    def test_every_key_is_the_machines_or_the_products(self):
        """A new key of DEFAULTS has to say whether the registry's [machine] may set it (MACHINE_SETTINGS) or it is
        each product's own (PRODUCT_SETTINGS): never both, never neither."""

        def has(table, sec, key):
            return sec in table and (table[sec] is None or key in table[sec])

        for sec, keys in config.DEFAULTS.items():
            self.assertTrue(sec in config.MACHINE_SETTINGS or sec in config.PRODUCT_SETTINGS, sec)
            for key in keys:
                with self.subTest(key=f"{sec}.{key}"):
                    self.assertNotEqual(
                        has(config.MACHINE_SETTINGS, sec, key), has(config.PRODUCT_SETTINGS, sec, key), "classify it"
                    )
        for table in (config.MACHINE_SETTINGS, config.PRODUCT_SETTINGS):
            for sec, keys in table.items():
                self.assertIn(sec, config.KNOWN)
                for key in keys or ():
                    self.assertTrue(key in config.DEFAULTS[sec] or (sec, key) == ("project", "name"), (sec, key))
        for sec in ("repos", "resources", "recurring"):
            self.assertIn(sec, config.PRODUCT_SETTINGS)


class PermissionsTest(MachineSettingsCase):
    """`foreman permissions` says where each rule comes from (text and `--json` `from`) and what `[deny] drop`
    removed of the inherited ones."""

    def view(self, name):
        r = self.cli("--project", name, "permissions", "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        d = json.loads(r.stdout)
        return d, {(x["band"], x.get("group"), x["pattern"]): x["from"] for x in d["rules"]}

    def test_each_rule_says_where_it_comes_from(self):
        self.registry()
        self.other_toml(f'\n[deny]\npatterns = ["{OWN}", "{SHIP}"]\n')
        d, by = self.view("other")
        self.assertEqual(by[("never", None, SHIP)], "both")
        self.assertEqual(by[("never", None, OWN)], "product")
        self.assertEqual(by[("orchestrator", "release", RELEASE)], "machine")
        self.assertEqual({x["from"] for x in d["rules"] if x["band"] == "builtin"}, {"foreman"})
        self.assertEqual(d["inherited"], {"rules": 2, "dropped": []})
        d, by = self.view("fake")
        self.assertEqual(by[("never", None, "npm " + "publish")], "product")
        self.assertEqual(by[("never", None, SHIP)], "machine")
        self.assertEqual(by[("turn", None, "make up")], "product")  # a resource's guard is each product's own
        self.assertEqual(by[("orchestrator", "deploy", "make deploy")], "product")
        text = self.cli("--project", "other", "permissions").stdout  # the machine's language: es
        self.assertIn("Reglas de la máquina (el [machine.deny] del registro): 2\n", text)
        self.assertIn(f"  - «{SHIP}» (de la máquina y del producto): ", text)
        self.assertIn(f"    - «{RELEASE}» (de la máquina): ", text)
        self.assertIn(f"  - «{OWN}»: ", text)
        self.assertNotIn("[deny] drop", text)

    def test_drop_is_listed_and_no_machine_says_zero(self):
        self.registry()
        self.other_toml(f'\n[deny]\ndrop = ["{SHIP}", "group:release"]\n')
        d, by = self.view("other")
        self.assertEqual(d["inherited"], {"rules": 0, "dropped": [SHIP, "group:release"]})
        self.assertNotIn(("never", None, SHIP), by)
        text = self.cli("--project", "other", "permissions").stdout
        self.assertIn(f"Reglas heredadas que este producto quita con [deny] drop (2): «{SHIP}», grupo release\n", text)
        self.registry("")
        d, by = self.view("fake")
        self.assertEqual(d["inherited"], {"rules": 0, "dropped": []})
        self.assertEqual({v for k, v in by.items() if k[0] != "builtin"}, {"product"})
        text = self.cli("permissions").stdout
        self.assertIn("Rules from the machine (the registry's [machine.deny]): 0\n", text)
        self.assertNotIn("(from the machine)", text)

    def test_a_new_drop_in_a_candidate_opens(self):
        self.registry()
        other = self.load("other")
        cand = self.tmp / "candidate.toml"
        cand.write_text(f'[project]\nname = "other"\n\n[deny]\ndrop = ["{SHIP}"]\n')
        c = config.load_project(self.other_home, config_file=cand, strict=True)
        d = permissions.diff(other, c)
        self.assertEqual(d["rules"], [{"pattern": SHIP, "before": ["never"], "after": []}])
        self.assertEqual(len(permissions.opens(other, c, d)), 1)
        same = config.load_project(self.other_home, config_file=self.other_home / "foreman.toml", strict=True)
        self.assertEqual(permissions.diff(other, same)["rules"], [])  # the machine's rules are on both sides


ENV_READ = "cat ." + "env"  # in pieces: the installed guard reads this file's commands
OTHER_TABLE = f"""# expect<TAB>command
orchestrator\t{ENV_READ}
allow\t{SHIP} now
allow\tls
"""
SECRETS = """
[machine.project]
owner = "Machine Owner"

[machine.deny.grants]
secrets = ["(^|[ /])[.]env"]
"""


class MachineCandidateTest(MachineSettingsCase):
    """`foreman permissions --machine-candidate FILE`: the effect of a new [machine] on every registered product,
    read only."""

    def setUp(self):
        super().setUp()
        self.other_toml('\n[check]\nguard_table = "guard.tsv"\n\n[deny.grants]\nenv-app = ["[.]env"]\n')
        (self.other_home / "guard.tsv").write_text(OTHER_TABLE)
        self.registry("")

    def candidate(self, machine):
        f = self.tmp / "registry-candidate.toml"
        f.write_text((self.tmp / "projects.toml").read_text() + textwrap.dedent(machine))
        return f

    def run_it(self, machine, *extra):
        r = self.cli("--project", "other", "permissions", "--machine-candidate", str(self.candidate(machine)), *extra)
        return r, (json.loads(r.stdout) if "--json" in extra and r.stdout else None)

    def test_a_machine_group_over_a_products_group_needs_two_yeses(self):
        reg = self.tmp / "projects.toml"
        before = (reg.read_text(), reg.stat().st_mtime_ns)
        r, m = self.run_it(SECRETS, "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(before, (reg.read_text(), reg.stat().st_mtime_ns))  # only reads
        self.assertFalse(m["outside"])
        by = {d["project"]: d for d in m["products"]}
        self.assertEqual(set(by), {"fake", "other"})
        other = by["other"]
        self.assertEqual(other["rows"], [])  # the verdict stays «orchestrator»: the diff alone would not see it
        (row,) = other["more_groups"]
        self.assertEqual((row["line"], row["command"], row["before"]), (2, ENV_READ, ["env-app"]))
        self.assertEqual(set(row["after"]), {"env-app", "secrets"})
        self.assertEqual(other["rules"], [{"pattern": "(^|[ /])[.]env", "before": [], "after": ["group:secrets"]}])
        self.assertEqual(other["inherited"], {"before": 0, "after": 1})
        self.assertIn({"key": "project.owner", "before": "owner", "after": "Machine Owner"}, other["settings"])
        self.assertEqual((other["opens"], other["warnings"], other["table_failures"]), ([], [], []))
        self.assertEqual(by["fake"]["settings"], [])  # its foreman.toml writes its owner
        text = self.cli("--project", "other", "permissions", "--machine-candidate", str(self.candidate(SECRETS)))
        self.assertEqual(text.returncode, 0, text.stdout)
        self.assertIn("Machine candidate ", text.stdout)
        self.assertIn("  Rules from the machine: 0 → 1\n", text.stdout)
        self.assertIn("    project.owner: «owner» → «Machine Owner»\n", text.stdout)
        self.assertIn("  1 rows would need the yes of several groups (one each), not as before\n", text.stdout)
        self.assertIn(f"    row 2 `{ENV_READ}`: groups env-app → ", text.stdout)
        self.assertTrue(text.stdout.endswith("No product loses a row of its table or a lifeline command\n"))

    def test_a_row_or_a_lifeline_command_lost_is_exit_1(self):
        r, m = self.run_it(f'\n[machine.deny]\npatterns = ["{SHIP}"]\n', "--json")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        other = {d["project"]: d for d in m["products"]}["other"]
        self.assertEqual(len(other["table_failures"]), 1)
        self.assertEqual(other["rows"][0]["after"], "deny")
        r, m = self.run_it('\n[machine.deny]\npatterns = ["^git -C "]\n', "--json")
        self.assertEqual(r.returncode, 1)
        self.assertTrue(all(d["lifeline"]["failures"] for d in m["products"]))
        self.other_toml(f'\n[check]\nguard_table = "guard.tsv"\n\n[deny]\npatterns = ["{OWN}"]\n')
        r, _ = self.run_it(f'\n[machine.deny.grants]\nmine = ["{OWN}"]\n')  # denied here, granted there
        self.assertEqual(r.returncode, 1)
        self.assertIn("New warnings (`foreman check` would fail)", r.stdout)
        self.assertIn("A product loses a row", r.stdout)

    def test_bad_files_and_the_registry_outside_machine(self):
        r, _ = self.run_it('\n[machine.deny]\npatterns = ["("]\n')
        self.assertEqual((r.returncode, r.stdout), (2, ""))
        self.assertIn("[machine.deny]", r.stderr)
        bad = self.tmp / "bad.toml"
        bad.write_text("[machine\n")
        r = self.cli("permissions", "--machine-candidate", str(bad))
        self.assertEqual(r.returncode, 2)
        r = self.cli("permissions", "--machine-candidate", str(bad), "--table", "x.tsv")
        self.assertIn("goes alone", r.stderr)
        only = self.tmp / "only-machine.toml"
        only.write_text(SECRETS)  # just a [machine]: nothing else to compare
        r = self.cli("--project", "other", "permissions", "--machine-candidate", str(only), "--json")
        self.assertFalse(json.loads(r.stdout)["outside"])
        moved = self.tmp / "moved.toml"
        moved.write_text('[[project]]\nname = "x"\nhome = "/nowhere"\n' + SECRETS)
        r = self.cli("--project", "other", "permissions", "--machine-candidate", str(moved))
        self.assertIn("only its [machine] is tested", r.stdout)
        self.assertIn("  2 products", r.stdout.replace(":", " "))
