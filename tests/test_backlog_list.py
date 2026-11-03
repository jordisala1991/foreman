"""`foreman backlog list`: filters, counts and --json over one product or every product of a fake registry (in
$TMPDIR), each backlog read where its foreman.toml puts it."""

import json
import textwrap

from helpers import BASE_TOML, ProjectCase, config

from foreman.backlog import Backlog


class BacklogListTest(ProjectCase):
    def setUp(self):
        super().setUp()
        self.b = Backlog(self.project)
        self.b.init()
        b = self.b
        with b.mutate() as d:
            b.add_project(d, "E1", "First", "What E1 is for.")
            b.add_project(d, "E2", "Second", "What E2 is for.")
            b.add_issue(d, "E1", "AB-1", "one **bold**", status="ready")
            b.add_issue(d, "E1", "AB-2", "two", status="triage")
            b.add_issue(d, "E2", "AB-3", "three", status="in progress")
            b.add_issue(d, "", "AB-4", "four")
            b.add_issue(d, "E2", "AB-5", "five")
            b.set_status(d, "AB-4", "done", result="ok")
            b.set_status(d, "AB-5", "canceled", result="not needed")
            b.edit(d, "AB-1", priority="high", repo="app")
            b.edit(d, "AB-3", assignee="dev1")
            b.set_sub(d, "AB-3", "T1", title="part", status="in progress")
            b.set_sub(d, "AB-1", "T1", title="part")
            b.add_issue(d, "E1", "AB-6", "six")
            b.set_status(d, "AB-6", "done", result="ok")
            b.archive(d, "AB-6")

    def ids(self, *args):
        r = self.cli("backlog", "list", "--json", *args)
        self.assertEqual(r.returncode, 0, r.stderr)
        return [x["id"] for x in json.loads(r.stdout)]

    def counts(self, *args, cli=("backlog", "list")):
        r = self.cli(*cli, "--json", *args)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_lines_and_filters(self):
        r = self.cli("backlog", "list")
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.splitlines()
        self.assertEqual(lines[-1], "5 issues")  # the archive only with --archived
        self.assertRegex(lines[0], r"^AB-1 +ready +E1 +one bold$")
        self.assertRegex(lines[-2], r"^AB-4 +done +- +four$")  # no project: -, after the projects (file order)
        self.assertEqual(self.ids("--open"), ["AB-1", "AB-2", "AB-3"])
        self.assertEqual(self.ids("--status", "todo", "in-progress"), ["AB-1", "AB-3"])  # the aliases
        self.assertEqual(self.ids("--group", "E2"), ["AB-3", "AB-5"])
        self.assertEqual(self.ids("--group", "-", "E2", "--open"), ["AB-3"])
        self.assertEqual(self.ids("--has", "assignee"), ["AB-3"])
        self.assertEqual(self.ids("--has", "priority=high"), ["AB-1"])
        self.assertEqual(self.ids("--has", "repo=app", "--has", "priority=high"), ["AB-1"])
        self.assertEqual(self.ids("--has", "repo=nope"), [])
        self.assertEqual(self.ids("--sub-status", "▶"), ["AB-3"])
        self.assertEqual(self.ids("--sub-status", "todo"), ["AB-1"])
        self.assertEqual(self.ids("--archived", "--status", "done"), ["AB-4", "AB-6"])
        rows = json.loads(self.cli("backlog", "list", "--json", "--archived", "--status", "done").stdout)
        self.assertEqual(
            [(x["product"], x["group"], x["archived"]) for x in rows], [("fake", "", False), ("fake", "E1", True)]
        )
        self.assertIn("*", self.cli("backlog", "list", "--archived", "--group", "E1").stdout.splitlines()[-2])
        self.assertEqual(self.cli("backlog", "list", "--status", "duplicate").stdout.strip(), "no issues match")

    def test_counts(self):
        self.assertEqual(self.cli("backlog", "list", "--count", "--open").stdout.strip(), "3 issues")
        self.assertEqual(self.cli("backlog", "list", "--count", "--group", "-").stdout.strip(), "1 issue")
        self.assertEqual(self.counts("--count"), {"total": 5})
        c = self.counts("--by", "status")  # --by implies --count
        self.assertEqual(
            list(c["counts"].items()), [("triage", 1), ("ready", 1), ("in progress", 1), ("done", 1), ("canceled", 1)]
        )
        self.assertEqual(self.counts("--by", "group")["counts"], {"E1": 2, "E2": 2, "-": 1})
        self.assertEqual(self.counts("--by", "product"), {"total": 5, "by": "product", "counts": {"fake": 5}})
        text = self.cli("backlog", "list", "--by", "group", "--open").stdout.splitlines()
        self.assertEqual([x.split() for x in text], [["E1", "2"], ["E2", "1"], ["3", "issues"]])

    def test_bad_filters_exit_2(self):
        for args, said in (
            (("--status", "nope"), "bad status"),
            (("--has", "nope"), "unknown field 'nope'"),
            (("--sub-status", "nope"), "bad sub-issue status"),
            (("--by", "milestone"), "invalid choice: 'milestone'"),
            (("--has", "milestone"), "unknown field 'milestone'"),
        ):
            r = self.cli("backlog", "list", *args)
            self.assertEqual(r.returncode, 2, args)
            self.assertIn(said, r.stderr, args)

    def test_all_products(self):
        """Every registered product with a backlog, each read where its foreman.toml says (here wiki/backlog.json);
        one without a backlog is skipped, one that does not load is said on stderr; keys carry their product."""
        other = self.tmp / "other"
        (other / "wiki").mkdir(parents=True)
        (other / "foreman.toml").write_text(
            textwrap.dedent(
                BASE_TOML.replace('name = "fake"', 'name = "other"').replace("{tmp}", str(self.tmp))
                + '\n[backlog]\nfile = "wiki/backlog.json"\npage = "wiki/backlog.md"\n'
                'archive_page = "wiki/archive.md"\ndecisions_page = "wiki/decisions.md"\n'
            )
        )
        ob = Backlog(config.load_project(other))
        ob.init()
        with ob.mutate() as d:
            ob.add_project(d, "E1", "Theirs", "Their E1.")
            ob.add_issue(d, "E1", "AB-1", "their one", status="triage")
            ob.add_issue(d, "E1", "AB-2", "their two")
            ob.set_status(d, "AB-2", "done", result="ok")
        empty = self.tmp / "empty"
        empty.mkdir()
        (empty / "foreman.toml").write_text(BASE_TOML.replace('name = "fake"', 'name = "empty"'))
        broken = self.tmp / "broken"
        broken.mkdir()
        (broken / "foreman.toml").write_text(BASE_TOML.replace('name = "fake"', 'name = "broken"'))
        (broken / "backlog.json").write_text('{"schema_version": 3}')
        reg = self.tmp / "projects.toml"
        reg.write_text(
            reg.read_text()
            + "".join(f'[[project]]\nname = "{n}"\nhome = "{self.tmp / n}"\n' for n in ("other", "empty", "broken"))
        )
        self.assertTrue((other / "wiki/backlog.json").exists())
        c = self.counts("--all-products", "--by", "product")
        self.assertEqual(c["counts"], {"fake": 5, "other": 2})
        r = self.cli("backlog", "list", "--all-products", "--open", "--by", "group", cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("left out: broken", r.stderr)
        self.assertEqual(
            [x.split() for x in r.stdout.splitlines()],
            [["fake/E1", "2"], ["fake/E2", "1"], ["other/E1", "1"]] + [["4", "issues"]],
        )
        lines = self.cli("backlog", "list", "--all-products", "--status", "triage", cwd=self.tmp).stdout.splitlines()
        self.assertRegex(lines[0], r"^fake +AB-2 +triage +E1 +two$")
        self.assertRegex(lines[1], r"^other +AB-1 +triage +E1 +their one$")
        rows = json.loads(self.cli("backlog", "list", "--all-products", "--json", "--group", "E1", cwd=self.tmp).stdout)
        got = [(x["product"], x["id"]) for x in rows]
        self.assertEqual(sorted(got), [("fake", "AB-1"), ("fake", "AB-2"), ("other", "AB-1"), ("other", "AB-2")])
        self.assertEqual([p for p, _ in got], ["fake", "fake", "other", "other"])  # the registry's order
        r = self.cli("--project", "fake", "backlog", "list", "--all-products")
        self.assertEqual(r.returncode, 2)
        self.assertIn("leave out --project", r.stderr)
        r = self.cli("--project", "other", "backlog", "list", "--count")  # --project reads that product's own file
        self.assertEqual(r.stdout.strip(), "2 issues")

    def test_spanish_texts(self):
        self.write_toml(BASE_TOML.replace('language = "en"', 'language = "es"'))
        self.assertEqual(self.cli("backlog", "list", "--count").stdout.strip(), "5 trabajos")
        self.assertEqual(self.cli("backlog", "list", "--status", "duplicate").stdout.strip(), "ningún trabajo coincide")

    def test_progress_leaves_out_triage_and_canceled(self):
        """`show EN`: done over the issues neither canceled nor in triage (E1: AB-1 ready, AB-2 triage, AB-6
        archived: 0/1), as Linear counts a project's progress; the open ones in triage do not count."""
        self.assertIn("## E1 · First  (0/1 done)", self.cli("backlog", "show", "E1").stdout)
        self.assertIn("## E2 · Second  (0/1 done)", self.cli("backlog", "show", "E2").stdout)  # AB-5 canceled out

    def test_list_is_a_read(self):
        """`backlog list` only reads: it is not an acting command (no «older foreman» line or acting checks)."""
        from foreman import cli

        self.assertFalse(cli.acts(["backlog", "list", "--open"]))
        self.assertTrue(cli.acts(["backlog", "status", "AB-1", "done"]))
