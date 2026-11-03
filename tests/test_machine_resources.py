"""A machine resource is declared once in the registry ([resources.NAME]: paths, busy, busy_wait,
short_max, slots); a product uses it by declaring [resources.NAME] with only its guard and allow (or empty). The
machine wins; machine keys a product repeats are ignored with a warning (strict in `foreman check`). `foreman lock`,
`foreman wait`, the guard and the loop pass all read the table merged at load."""

import os
import textwrap

from helpers import ProjectCase

from foreman import config, lock, loop, waits

MACHINE = """
[resources.gpu]
dir = "locks/gpu-held"
queue = "locks/gpu-queue"
busy = "{busy}"
busy_wait = 1  # the always-busy check below waits this long on every acquire: keep it short
"""

PRODUCT = """
[project]
name = "fake"

[resources.gpu]
{body}
"""


class MachineCase(ProjectCase):
    """The fake project is registered; `machine()` appends a [resources.gpu] table to the registry."""

    def setUp(self):
        super().setUp()
        self.busy = self.tmp / "busy.sh"
        self.busy.write_text("#!/bin/bash\necho 'pid 1 outside the lock'\n")
        self.busy.chmod(0o755)

    def machine(self, text=None):
        text = MACHINE.format(busy=self.busy) if text is None else text
        with (self.tmp / "projects.toml").open("a") as f:
            f.write("\n" + textwrap.dedent(text))

    def product(self, body=""):
        self.write_toml(PRODUCT.format(body=body))

    def other(self, name, body=""):
        home = self.tmp / name
        home.mkdir(exist_ok=True)
        (home / "foreman.toml").write_text(f'[project]\nname = "{name}"\n\n[resources.gpu]\n{body}\n')
        # [[project]] entries must come before any [resources.*] table: rewrite the registry with it first
        reg = (self.tmp / "projects.toml").read_text()
        (self.tmp / "projects.toml").write_text(
            f'[[project]]\nname = "{name}"\nhome = "{home}"\nmode = "active"\n\n' + reg
        )
        return home

    # criterion 1
    def test_product_with_guard_only_gets_machine_keys(self):
        self.machine()
        self.product('guard = ["x"]')
        res = self.project.resources["gpu"]
        self.assertEqual(res["busy"], str(self.busy))
        self.assertEqual(res["dir"], str(self.tmp / "locks/gpu-held"))  # relative: against the registry's folder
        self.assertEqual(res["queue"], str(self.tmp / "locks/gpu-queue"))
        self.assertEqual(res["busy_wait"], 1)
        self.assertEqual(res["guard"], ["x"])
        self.assertNotIn("impl", res)
        self.assertEqual(self.project.resource_origin, {"gpu": "machine"})
        self.assertEqual(self.project.warnings, [])
        env = lock.environment("gpu", res, mkdirs=False)
        self.assertEqual(env["FOREMAN_LOCK_BUSY"], str(self.busy))
        self.assertEqual(env["FOREMAN_LOCK_DIR"], str(self.tmp / "locks/gpu-held"))
        self.assertEqual(env["FOREMAN_LOCK_BUSY_WAIT"], "1")
        r = self.cli("check")
        self.assertIn("resource gpu: machine (registry), foreman lock, busy ", r.stdout)
        self.assertIn("guard 1", r.stdout)
        self.assertNotIn("[resources.gpu]:", r.stdout)

    def test_empty_table_and_registered_projects(self):
        self.machine()
        self.product("")
        self.assertEqual(self.project.resources["gpu"]["guard"], [])
        p = next(x for x in config.registered_projects() if x.name == "fake")
        self.assertEqual(p.resources["gpu"]["busy"], str(self.busy))
        self.assertEqual(p.resource_origin["gpu"], "machine")

    def test_home_tilde_expanded(self):
        self.machine('[resources.gpu]\ndir = "~/somewhere-lock"\n')
        self.product("")
        self.assertEqual(self.project.resources["gpu"]["dir"], os.path.expanduser("~/somewhere-lock"))

    # criterion 2
    def test_machine_wins_with_warning_and_check_fails(self):
        self.machine()
        self.product('guard = ["x"]\nbusy = "echo mine"\ndir = "/tmp/elsewhere"\nslots = ["A"]')
        res = self.project.resources["gpu"]
        self.assertEqual(res["busy"], str(self.busy))
        self.assertEqual(res["dir"], str(self.tmp / "locks/gpu-held"))
        self.assertEqual(res["slots"], [])
        self.assertEqual(res["guard"], ["x"])
        self.assertEqual(len(self.project.warnings), 1, self.project.warnings)
        w = self.project.warnings[0]
        for part in ("[resources.gpu]", "busy, dir, slots", "declared by the machine", "remove them"):
            self.assertIn(part, w)
        r = self.cli("check")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("busy, dir, slots are declared by the machine", r.stdout)
        with self.assertRaises(config.ConfigError):
            config.load_project(self.home, strict=True)

    def test_warning_reaches_loop_pass_once(self):
        self.machine()
        self.product('busy = "echo mine"')
        first = [n for n in loop.notices_for_pass(self.project) if n["kind"] == "config_warnings"]
        self.assertEqual([n["actionable"] for n in first], [True])
        self.assertIn("busy is declared by the machine", first[0]["text"])
        again = [n for n in loop.notices_for_pass(self.project) if n["kind"] == "config_warnings"]
        self.assertEqual([n["actionable"] for n in again], [False])

    # criterion 3
    def test_registry_product_keys_ignored_with_warning(self):
        self.machine('[resources.gpu]\nbusy = "echo b"\nguard = ["x"]\nallow = ["y"]\ncolour = 1\n')
        res, errors, warnings = config.machine_resources()
        self.assertEqual(errors, {})
        self.assertEqual(res["gpu"]["guard"], [])
        self.assertNotIn("colour", res["gpu"])
        self.assertEqual(len(warnings), 2, warnings)
        self.assertIn("allow, guard are each product's own", warnings[0])
        self.assertIn("unknown keys colour", warnings[1])
        _, _, reg_warnings = config.registry_scan()
        self.assertEqual(reg_warnings, warnings)
        self.product("")
        r = self.cli("check")
        self.assertIn("(registry) registry: [resources.gpu]: allow, guard are", r.stdout)
        self.assertIn("(registry) registry: [resources.gpu]: unknown keys colour", r.stdout)
        self.assertEqual(r.returncode, 0, r.stdout)  # the registry's warning is not this product's problem

    def test_bad_machine_value_leaves_resource_out(self):
        for table, part in (
            ('busy_wait = "x"', "busy_wait must be a whole number"),
            ('slots = "A"', "slots must be a list of strings"),
            ("busy = 3", "busy must be a string"),
        ):
            with self.subTest(table=table):
                (self.tmp / "projects.toml").write_text(
                    f'[[project]]\nname = "fake"\nhome = "{self.home}"\nmode = "active"\n'
                )
                self.machine(f"[resources.gpu]\n{table}\n")
                res, errors, _ = config.machine_resources()
                self.assertNotIn("gpu", res)
                self.assertIn(part, errors["gpu"][0])
                self.product('busy = "echo mine"')  # the product's own keys are used, with a warning
                self.assertEqual(self.project.resources["gpu"]["busy"], "echo mine")
                self.assertEqual(self.project.resource_origin["gpu"], "project")
                self.assertEqual(len(self.project.warnings), 1)
                self.assertIn("the registry's machine resource does not load", self.project.warnings[0])
                r = self.cli("check")
                self.assertEqual(r.returncode, 1, r.stdout)
                self.assertIn(part, r.stdout)
                self.assertIn("(resource left out)", r.stdout)

    def test_bad_machine_name(self):
        self.machine('[resources.GPU]\nbusy = "echo b"\n')
        res, errors, _ = config.machine_resources()
        self.assertEqual(res, {})
        self.assertIn("lowercase letters", errors["GPU"][0])

    def test_unused_bad_resource_does_not_fail_other_products(self):
        self.machine("[resources.sim]\nbusy_wait = -1\n")
        self.product("")
        self.assertEqual(self.project.warnings, [])
        r = self.cli("check")
        self.assertIn("(registry) registry: [resources.sim]", r.stdout)
        self.assertEqual(r.returncode, 0, r.stdout)

    # criterion 4
    def test_lock_check_from_project_without_it_and_outside(self):
        self.machine()
        self.product("")
        self.write_toml('[project]\nname = "fake"\n')  # the product does not declare gpu
        self.assertEqual(lock.resource_config(self.project, "gpu")["busy"], str(self.busy))
        self.assertEqual(lock.resource_config(None, "gpu")["dir"], str(self.tmp / "locks/gpu-held"))
        r = self.cli("lock", "gpu", "check")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)  # lock.sh check: 1 = free, 0 = held
        self.assertIn("free", r.stdout)
        outside = self.tmp / "nowhere"
        outside.mkdir()
        r = self.cli("lock", "gpu", "check", cwd=outside)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("free", r.stdout)
        r = self.cli("lock", "gpu", "acquire", "test", cwd=outside, env={"FOREMAN_WHO": "tester"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("pid 1 outside the lock", r.stderr)  # the machine's busy ran (and waited busy_wait)
        r = self.cli("lock", "gpu", "check", cwd=outside)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("tester: test", r.stdout)
        self.assertTrue((self.tmp / "locks/gpu-held").is_dir())  # the machine's folder, not the default one
        self.assertFalse((self.tmp / "state/locks/gpu/held").exists())
        r = self.cli("lock", "gpu", "release", cwd=outside, env={"FOREMAN_WHO": "tester"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_unknown_resource_names_registry(self):
        self.machine()
        with self.assertRaises(lock.LockError) as e:
            lock.resource_config(self.project, "nothing")
        self.assertIn("unknown resource 'nothing'", str(e.exception))
        self.assertIn("gpu", str(e.exception))
        self.assertIn("in the registry", str(e.exception))
        r = self.cli("lock", "nothing", "check", cwd=self.tmp)
        self.assertEqual(r.returncode, 2)
        self.assertIn("in the registry", r.stderr)
        self.assertEqual(lock.resource_config(None, "nothing", tolerant=True), {})

    # criterion 5
    def test_no_conflict_warning_for_machine_resource(self):
        self.machine()
        self.product('busy = "echo mine"')
        self.other("second", "busy_wait = 900")
        self.assertEqual(lock.shared_warnings(self.project), [])
        self.assertEqual(lock.shared_conflicts(config.registered_projects()), [])

    def test_conflict_without_machine_suggests_registry(self):
        self.product('busy = "echo mine"')
        self.other("second", 'slots = ["A"]')
        w = lock.shared_warnings(self.project)
        self.assertEqual(len(w), 1)
        self.assertIn("slots second='A', fake=''", w[0])
        self.assertIn("declare it once in the registry ([resources.gpu]", w[0])

    # criterion 6
    def test_wait_lock_uses_machine_paths(self):
        self.machine()
        self.write_toml('[project]\nname = "fake"\n')
        self.assertTrue(waits.done_yet(self.project, "lock", ["gpu", "tester"]))
        r = self.cli("lock", "gpu", "acquire", "test", env={"FOREMAN_WHO": "tester"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(waits.done_yet(self.project, "lock", ["gpu", "tester"]))
        self.assertFalse(waits.done_yet(None, "lock", ["gpu", "tester"]))
        self.cli("lock", "gpu", "release", env={"FOREMAN_WHO": "tester"})
        self.assertTrue(waits.done_yet(self.project, "lock", ["gpu", "tester"]))

    def test_guard_uses_product_guard_with_machine_resource(self):
        self.machine()
        self.product('guard = ["make up"]')
        from foreman import guard

        self.assertEqual(guard.check(self.project, "make up now"), ("lock", ("gpu", "make up now")))
        self.assertIsNone(guard.check(self.project, "foreman lock gpu run 'up' make up now"))

    # criterion 8 (and the registry that does not read)
    def test_no_machine_section_unchanged(self):
        self.product('busy = "echo mine"')
        self.assertEqual(self.project.resources["gpu"]["busy"], "echo mine")
        self.assertEqual(self.project.resource_origin, {"gpu": "project"})
        self.assertEqual(config.machine_resources(), ({}, {}, []))
        self.assertEqual(self.project.warnings, [])

    def test_unreadable_registry_warns_only_with_resources(self):
        (self.tmp / "projects.toml").write_text("not toml [")
        self.product('busy = "echo mine"')
        self.assertEqual(self.project.resources["gpu"]["busy"], "echo mine")
        self.assertIn("machine resources not applied", self.project.warnings[0])
        self.write_toml('[project]\nname = "fake"\n')
        self.assertEqual(self.project.warnings, [])
