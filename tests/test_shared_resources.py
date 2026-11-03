"""A resource is one lock shared by name across the registry's projects; `foreman check` and the loop pass
warn (never fail) when two projects declare it with different dir/queue/next/jumped/log/busy/busy_wait/short_max
(and slots), comparing values after defaults, and suggest declaring it once in the registry."""

import textwrap

from helpers import ProjectCase

from foreman import config, lock, loop

OTHER = """
[project]
name = "{name}"

[resources.widget]
{body}
"""


class SharedResourceCase(ProjectCase):
    """The fake project declares [resources.widget] (guard only: every shared key at its default)."""

    def other(self, name, body=""):
        """Register one more project `name` whose [resources.widget] has `body`."""
        home = self.tmp / name
        home.mkdir(exist_ok=True)
        (home / "foreman.toml").write_text(textwrap.dedent(OTHER).format(name=name, body=body))
        with (self.tmp / "projects.toml").open("a") as f:
            f.write(f'\n[[project]]\nname = "{name}"\nhome = "{home}"\nmode = "active"\n')

    def warnings(self):
        return lock.shared_warnings(self.project)

    def test_same_values_nothing(self):
        self.other("second", 'busy = ""\nshort_max = 300')
        self.assertEqual(self.warnings(), [])
        r = self.cli("check")
        self.assertNotIn("declared differently", r.stdout)

    def test_omitted_equals_default_nothing(self):
        default_dir = config.state_root() / "locks/widget/held"
        self.other("second", f'busy_wait = 600\nshort_max = 300\ndir = "{default_dir}"\nbusy = ""')
        self.assertEqual(self.warnings(), [])

    def test_different_busy_names_both(self):
        self.other("second", 'busy = "pgrep -l server"')
        w = self.warnings()
        self.assertEqual(len(w), 1)
        for part in (
            "[resources.widget]",
            "fake",
            "second",
            "busy",
            "fake=''",
            "second='pgrep -l server'",
            "declare it once in the registry ([resources.widget]",
        ):
            self.assertIn(part, w[0])
        self.assertNotIn("short_max", w[0])
        r = self.cli("check")
        self.assertIn("warning: [resources.widget]", r.stdout)
        self.assertIn("second='pgrep -l server'", r.stdout)
        self.assertEqual(r.returncode, 0, r.stdout)  # a warning: the check still passes

    def test_three_projects(self):
        self.other("second", "busy_wait = 900")
        self.other("third", 'short_max = 60\ndir = "/tmp/elsewhere"')
        w = self.warnings()
        self.assertEqual(len(w), 1)
        self.assertIn("fake, second, third", w[0])
        self.assertIn("busy_wait fake=600, second=900, third=600", w[0])
        self.assertIn("short_max fake=300, second=300, third=60", w[0])
        self.assertIn("third='/tmp/elsewhere'", w[0])
        for key in ("busy ", "queue", "next", "jumped", "log", "slots"):
            self.assertNotIn(key, w[0].split(":", 1)[1].split("; declare it once")[0])

    def test_slots_compared(self):
        """Slots name the lock's folders, so two declarations with different slots are a conflict."""
        self.other("second", 'slots = ["A", "B"]')
        w = self.warnings()
        self.assertEqual(len(w), 1)
        self.assertIn("slots fake='', second='A B'", w[0])

    def test_only_own_resources(self):
        """Two other projects disagreeing on a resource this one does not declare: not this project's warning."""
        self.other("second", "busy_wait = 900")
        self.write_toml('[project]\nname = "fake"\n')
        self.other("third", "busy_wait = 60")
        self.assertEqual(self.warnings(), [])
        self.assertEqual(len(lock.shared_conflicts(config.registered_projects())), 1)

    def test_loop_pass_notice(self):
        self.assertNotIn("config_warnings", [n["kind"] for n in loop.notices_for_pass(self.project)])
        self.other("second", "busy_wait = 900")
        first = [n for n in loop.notices_for_pass(self.project) if n["kind"] == "config_warnings"]
        self.assertEqual([n["actionable"] for n in first], [True])
        self.assertIn("second=900", first[0]["text"])
        again = [n for n in loop.notices_for_pass(self.project) if n["kind"] == "config_warnings"]
        self.assertEqual([n["actionable"] for n in again], [False])  # actionable when it appears or changes

    def test_broken_registry_no_crash(self):
        (self.tmp / "projects.toml").write_text("this is not toml [")
        self.assertEqual(self.warnings(), [])
