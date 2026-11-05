"""`foreman permissions` and `--candidate` (lib/foreman/permissions.py) on a fake product with [deny]
patterns, two [deny.grants] groups, a resource with a guard and a guard table: the bands and their rows, a rule with no
row, the titles in the project's language, the candidate's diff (rules, rows, failing rows), the lifeline, the exit
codes, that neither writes anything in the home or in <state>, and that guard.table_verdicts says what run_table says
on the golden table."""

import json
import os
import shutil
import subprocess
import textwrap
from unittest import mock

from helpers import BASE_TOML, ROOT, ProjectCase

from foreman import config, grants, guard, permissions

TOML = BASE_TOML + textwrap.dedent(
    """
    [deny]
    patterns = ["make prod", "rclone"]

    [deny.grants]
    deploy = ["make deploy"]
    release = ["git tag v"]

    [check]
    guard_table = "guard.tsv"
    """
)

TABLE = """# the fake product's guard table
deny\tmake prod
orchestrator\tmake deploy
orchestrator\tgit tag v1.0
lock:widget\tmake up
lock:widget\tsimctl boot x
allow\tgit status
allow\tforeman --project fake lock widget run "x" make up
"""


class PermissionsCase(ProjectCase):
    toml = TOML

    def setUp(self):
        super().setUp()
        (self.home / "guard.tsv").write_text(TABLE)

    def run_cli(self, *args, rc=0):
        r = self.cli("permissions", *args)
        self.assertEqual(r.returncode, rc, r.stdout + r.stderr)
        return r

    def candidate(self, toml=None, name="candidate.toml"):
        """A candidate foreman.toml in the temporary folder: the live one, or `toml` (dedented, {tmp} filled)."""
        f = self.tmp / name
        text = (self.home / "foreman.toml").read_text() if toml is None else textwrap.dedent(toml)
        f.write_text(text.replace("{tmp}", str(self.tmp)))
        return f

    def snapshot(self):
        """Names and mtimes of every file under the home and the state root."""
        out = {}
        for root in (self.home, self.tmp / "state"):
            for p in sorted(root.rglob("*")):
                out[str(p)] = p.stat().st_mtime_ns
        return out


class ViewTest(PermissionsCase):
    def test_every_band_with_its_rows(self):
        d = json.loads(self.run_cli("--json").stdout)
        by = {r["pattern"]: r for r in d["rules"]}
        self.assertEqual([r["band"] for r in d["rules"][:2]], ["builtin", "builtin"])
        self.assertEqual(by["make prod"]["band"], "never")
        self.assertEqual((by["make prod"]["rows"], by["make prod"]["examples"]), (1, ["make prod"]))
        self.assertEqual((by["make deploy"]["band"], by["make deploy"]["group"]), ("orchestrator", "deploy"))
        self.assertEqual(by["git tag v"]["group"], "release")
        self.assertEqual((by["make up"]["band"], by["make up"]["resource"]), ("turn", "widget"))
        self.assertEqual(
            by["make up"]["rows"], 1
        )  # not the row that goes through the lock (a prefix, as check() reads)
        self.assertEqual(by["simctl boot"]["rows"], 1)
        self.assertEqual(d["groups"], {"deploy": "closed", "release": "closed"})
        self.assertEqual(d["grant_minutes"], 30)
        self.assertEqual(d["table_rows"], 7)
        self.assertEqual(d["lifeline"], {"commands": len(permissions.LIFELINE), "failures": []})
        apply_rule = [r for r in d["rules"] if r.get("only")]
        self.assertEqual(
            [(r["band"], r["pattern"], r["only"]) for r in apply_rule],
            [("builtin", p, "orchestrator") for p in (guard.PERMISSIONS_APPLY, *guard.AGENT_ONLY)],
        )

    def test_a_rule_with_no_row_is_a_warning(self):
        d = json.loads(self.run_cli("--json").stdout)
        self.assertEqual(d["no_row"], ["rclone"])  # a builtin rule without a row is not the product's
        out = self.run_cli().stdout
        self.assertIn("«rclone»: no row of the guard table checks it", out)
        self.assertIn("Warning: 1 rules with no row in the guard table", out)

    def test_text_in_bands(self):
        out = self.run_cli().stdout
        for line in (
            "Permissions of fake (",
            "Guard table: ",
            "Never, for anyone (foreman's own rules)",
            "Never without Owner",
            "  - «make prod»: 1 row (`make prod`)",
            "Only the orchestrator, after Owner's yes (each yes lasts 30 min)",
            "  group deploy: closed",
            "    - «make deploy»: 1 row (`make deploy`)",
            "  foreman (its own rule): changing the permissions with `foreman permissions apply`; when it allows "
            "something new (--opens), after Owner's yes to the yes/no question",
            "Waits its turn",
            "  resource widget",
            "    - «make up»: 1 row (`make up`)",
            "What the orchestrator must keep (17 commands, without any group): all pass",
        ):
            self.assertIn(line, out)

    def test_titles_in_the_projects_language(self):
        self.write_toml(TOML.replace('language = "en"', 'language = "es"'))
        out = self.run_cli().stdout
        for line in ("Permisos de fake", "Nunca sin Owner", "Solo el orquestador, tras el sí de Owner", "Espera turno"):
            self.assertIn(line, out)
        self.assertNotIn("Never without", out)

    def test_another_table_and_none(self):
        other = self.tmp / "other.tsv"
        other.write_text("deny\trclone sync a b\n")
        d = json.loads(self.run_cli("--json", "--table", str(other)).stdout)
        self.assertEqual(d["table_rows"], 1)
        self.assertIn("make prod", d["no_row"])
        self.assertNotIn("rclone", d["no_row"])
        self.write_toml(TOML.replace('[check]\nguard_table = "guard.tsv"\n', ""))
        out = self.run_cli().stdout
        self.assertIn("No guard table", out)

    def test_only_reads(self):
        st = self.project.state
        st.mkdir(parents=True, exist_ok=True)
        (st / "session").write_text("11111111-aaaa-4aaa-8aaa-000000000001\n")
        (st / "grants.json").write_text('{"grants": {}, "cooldowns": {}, "pending": {}}')
        before = self.snapshot()
        self.run_cli()
        self.run_cli("--json")
        self.run_cli("--candidate", str(self.candidate()))
        self.run_cli("--candidate", str(self.candidate(TOML.replace('"rclone"', '"git status"'), "c2.toml")), rc=1)
        after = self.snapshot()
        for f in ("candidate.toml", "c2.toml"):
            after.pop(str(self.tmp / f), None)
        self.assertEqual(before, after)


class CandidateTest(PermissionsCase):
    def test_nothing_changes(self):
        r = self.run_cli("--candidate", str(self.candidate()))
        self.assertIn("Nothing changes", r.stdout)
        self.assertIn("all pass", r.stdout)
        d = json.loads(self.run_cli("--candidate", str(self.candidate()), "--json").stdout)
        self.assertEqual((d["rules"], d["rows"], d["table_failures"]), ([], [], []))

    def test_a_broken_candidate_is_exit_2_and_nothing_else(self):
        r = self.run_cli("--candidate", str(self.candidate("[project\nname = 1\n")), rc=2)
        self.assertEqual(r.stdout, "")
        self.assertIn("candidate.toml", r.stderr)
        r = self.run_cli("--candidate", str(self.tmp / "missing.toml"), rc=2)
        self.assertIn("cannot read", r.stderr)

    def test_a_rule_that_refuses_the_lifeline(self):
        cand = self.candidate(TOML.replace('"rclone"', '"rclone", "--project fake check"'))
        r = self.run_cli("--candidate", str(cand), rc=1)
        self.assertIn("+ «--project fake check» (never)", r.stdout)
        self.assertIn("`foreman --project fake check`: blocked by «--project fake check»", r.stdout)
        d = json.loads(self.run_cli("--candidate", str(cand), "--json", rc=1).stdout)
        self.assertEqual(
            d["lifeline"]["failures"],
            [{"command": "foreman --project fake check", "kind": "deny", "rule": "--project fake check"}],
        )

    def test_a_group_that_takes_a_lifeline_command(self):
        group = 'release = ["git tag v", "git -C \\\\S+ commit"]'
        cand = self.candidate(TOML.replace('release = ["git tag v"]', group))
        d = json.loads(self.run_cli("--candidate", str(cand), "--json", rc=1).stdout)
        [f] = d["lifeline"]["failures"]
        self.assertEqual((f["kind"], f["rule"]), ("group", "release"))
        self.assertTrue(f["command"].startswith("git -C ") and f["command"].endswith(" commit -m x"))
        self.assertEqual(d["rules"], [{"pattern": "git -C \\S+ commit", "before": [], "after": ["group:release"]}])
        out = self.run_cli("--candidate", str(cand), rc=1).stdout
        self.assertIn("needs the owner's yes (group release)", out)

    def test_a_change_that_opens_a_row(self):
        cand = self.candidate(TOML.replace('patterns = ["make prod", "rclone"]', 'patterns = ["rclone"]'))
        d = json.loads(self.run_cli("--candidate", str(cand), "--json", rc=1).stdout)
        self.assertEqual(d["rules"], [{"pattern": "make prod", "before": ["never"], "after": []}])
        self.assertEqual([(r["command"], r["before"], r["after"]) for r in d["rows"]], [("make prod", "deny", "allow")])
        self.assertEqual(len(d["table_failures"]), 1)  # the live table still wants it denied
        self.assertIn("guard.tsv:2: want deny, got allow", d["table_failures"][0])
        out = self.run_cli("--candidate", str(cand), rc=1).stdout
        self.assertIn("- «make prod» (never)", out)
        self.assertIn("row 2 `make prod`: blocked → allowed", out)
        # with a table that says so, it is only listed
        table = self.tmp / "new.tsv"
        table.write_text(TABLE.replace("deny\tmake prod", "allow\tmake prod"))
        d = json.loads(self.run_cli("--candidate", str(cand), "--table", str(table), "--json").stdout)
        self.assertEqual(d["table_failures"], [])
        self.assertEqual([(r["before"], r["after"]) for r in d["rows"]], [("deny", "allow")])

    def test_a_rule_moved_to_a_group(self):
        cand = self.candidate(
            TOML.replace('patterns = ["make prod", "rclone"]', 'patterns = ["rclone"]').replace(
                'deploy = ["make deploy"]', 'deploy = ["make deploy", "make prod"]'
            )
        )
        d = json.loads(self.run_cli("--candidate", str(cand), "--json", rc=1).stdout)
        self.assertEqual(d["rules"], [{"pattern": "make prod", "before": ["never"], "after": ["group:deploy"]}])
        self.assertEqual([(r["before"], r["after"]) for r in d["rows"]], [("deny", "orchestrator")])
        out = self.run_cli("--candidate", str(cand), rc=1).stdout
        self.assertIn("~ «make prod»: never → group deploy", out)
        self.assertIn("blocked → only the orchestrator", out)

    def test_a_new_table_row_that_fails(self):
        table = self.tmp / "new.tsv"
        table.write_text(TABLE + "deny\trclone sync a b\ndeny\tcurl https://example.com\n")
        r = self.run_cli("--candidate", str(self.candidate()), "--table", str(table), rc=1)
        self.assertIn("1 rows of the table fail with the candidate", r.stdout)
        self.assertIn("new.tsv:10: want deny, got allow", r.stdout)

    def test_rows_added_and_removed_are_counted(self):
        """A candidate table that only adds or removes rows changes no verdict, but the diff says how many."""
        table = self.tmp / "rows.tsv"
        table.write_text(TABLE.replace("allow\tgit status\n", "") + "deny\trclone sync a b\nallow\techo hi\n")
        r = self.run_cli("--candidate", str(self.candidate()), "--table", str(table))
        self.assertIn("No rule or verdict changes", r.stdout)
        self.assertNotIn("Nothing changes", r.stdout)
        self.assertIn("Guard table: 2 rows added, 1 removed", r.stdout)
        d = json.loads(self.run_cli("--candidate", str(self.candidate()), "--table", str(table), "--json").stdout)
        self.assertEqual((d["table_added"], d["table_removed"]), (2, 1))
        # the live table itself, or no --table: nothing to count
        d = json.loads(
            self.run_cli("--candidate", str(self.candidate()), "--table", str(self.home / "guard.tsv"), "--json").stdout
        )
        self.assertEqual((d["table_added"], d["table_removed"]), (0, 0))
        r = self.run_cli("--candidate", str(self.candidate()))
        self.assertNotIn("Guard table:", r.stdout.split("\n", 1)[1])

    def test_a_table_that_does_not_read(self):
        table = self.tmp / "bad.tsv"
        table.write_text("maybe\tx\n")
        r = self.run_cli("--candidate", str(self.candidate()), "--table", str(table), rc=2)
        self.assertIn("The guard table does not read", r.stdout)


class UnitsTest(PermissionsCase):
    def test_label(self):
        self.assertEqual(permissions.label("deny", "deny"), "deny")
        self.assertEqual(permissions.label("deny", "allow"), "orchestrator")
        self.assertEqual(permissions.label("allow", "lock:widget"), "allow/lock:widget")

    def test_lifeline_is_quoted(self):
        cmds = permissions.lifeline(self.project)
        self.assertIn(f"git -C {self.home} status", cmds)
        self.assertIn("foreman --project fake permissions apply candidate.toml", cmds)

    def test_table_verdicts_says_what_run_table_says_on_the_golden_table(self):
        from test_golden import FARO_DENY

        repos = ""  # the golden world's code repos and global git config, which its git-class rows name
        for key, repo in (("app", "faro-app"), ("api", "harbor-api")):
            subprocess.run(["git", "init", "-q", str(self.tmp / repo)], check=True)
            repos += f'\n[repos.{key}]\npath = "{self.tmp / repo}"\n'
        self.write_toml(BASE_TOML + FARO_DENY + repos)
        table = self.tmp / "golden_guard.tsv"
        shutil.copy(ROOT / "tests/golden_guard.tsv", table)
        p = config.load_project(self.home)
        with mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(self.tmp / "gitconfig")}):
            rows = guard.table_verdicts(p, table)
            counts, failures = guard.run_table(p, table)
        self.assertEqual(sum(counts.values()), len(rows))
        self.assertEqual(failures, [f for f in (guard.row_failure(table.name, r) for r in rows) if f])
        self.assertEqual(failures, [])
        self.assertEqual({r[4] for r in rows if r[1] == "deny"}, {"deny"})


class ApplyTest(PermissionsCase):
    """`foreman permissions apply` (T1): the copy, the atomic replacement, the note, the base, and the J2 rule (the
    owner's yes/no question, --opens, only for a change that allows something that was not allowed before)."""

    def base(self):
        return json.loads(self.run_cli("--json").stdout)["base"]

    def based(self, toml, name="candidate.toml", base=None):
        """A candidate with its base line (the live hash, or `base`) at the top."""
        f = self.candidate(toml, name)
        f.write_text(f"# foreman permissions base: {base or self.base()}\n" + f.read_text())
        return f

    def apply(self, *args, rc=0):
        return self.run_cli("apply", *args, rc=rc)

    def backups(self):
        folder = self.project.state / "permissions"
        return sorted(folder.iterdir()) if folder.exists() else []

    def test_backup_replace_and_note(self):
        before_toml = (self.home / "foreman.toml").read_bytes()
        before_table = (self.home / "guard.tsv").read_bytes()
        cand = self.based(TOML.replace('"rclone"]', '"rclone", "curl"]'))
        table = self.tmp / "new.tsv"
        table.write_text(TABLE + "deny\tcurl https://example.com\n")
        r = self.apply(str(cand), "--table", str(table))  # only restricts: no --opens, no question
        self.assertIn("+ «curl» (never)", r.stdout)
        self.assertIn("Applied: ", r.stdout)
        live = (self.home / "foreman.toml").read_text()
        self.assertEqual(live, cand.read_text().split("\n", 1)[1])  # the base line is not kept
        self.assertEqual((self.home / "guard.tsv").read_text(), table.read_text())
        copies = self.backups()
        self.assertEqual([p.suffix for p in copies], [".toml", ".tsv"])
        self.assertRegex(copies[0].name, r"^\d{8}-\d{6}\.toml$")
        self.assertEqual((copies[0].read_bytes(), copies[1].read_bytes()), (before_toml, before_table))
        note = "".join(p.read_text() for p in self.project.notes_dir.glob("*.md"))
        self.assertIn("Permissions changed", note)
        self.assertIn("- + «curl» (never)", note)
        self.assertIn(f"guard table replaced ({self.home / 'guard.tsv'})", note)
        self.assertNotIn("--opens", note)
        # no temporary file is left next to the live ones
        self.assertEqual(sorted(p.name for p in self.home.iterdir() if p.name.startswith(".")), [])
        undo = r.stdout.rsplit("`", 2)[1].split()
        self.assertEqual(undo[:5], ["foreman", "--project", "fake", "permissions", "apply"])
        self.assertEqual(undo[5:9], [str(copies[0]), "--table", str(copies[1]), "--base"])

    def test_undo_restores_the_bytes(self):
        before_toml = (self.home / "foreman.toml").read_bytes()
        before_table = (self.home / "guard.tsv").read_bytes()
        cand = self.based(TOML.replace('"rclone"]', '"rclone", "curl"]'))
        table = self.tmp / "new.tsv"
        table.write_text(TABLE + "deny\tcurl https://example.com\n")
        r = self.apply(str(cand), "--table", str(table))
        undo = r.stdout.rsplit("`", 2)[1].split()[5:]
        r = self.apply(*undo, rc=2)  # it removes a rule: it opens
        self.assertIn("allows something that was not allowed before", r.stderr)
        self.assertIn("- «curl» (never)", r.stdout)
        self.apply(*undo, "--opens")
        self.assertEqual((self.home / "foreman.toml").read_bytes(), before_toml)
        self.assertEqual((self.home / "guard.tsv").read_bytes(), before_table)

    def test_a_changed_live_file_is_exit_2_and_nothing_written(self):
        cand = self.based(TOML.replace('"rclone"]', '"rclone", "curl"]'))
        (self.home / "foreman.toml").write_text((self.home / "foreman.toml").read_text() + "\n# a later edit\n")
        before = self.snapshot()
        r = self.apply(str(cand), rc=2)
        self.assertIn("foreman.toml changed after the candidate was copied", r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertEqual(self.snapshot(), before)
        # --base wins over the line, and the same check holds
        r = self.apply(str(cand), "--base", "0123456789abcdef", rc=2)
        self.assertIn("base 0123456789abcdef", r.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_no_base_is_exit_2(self):
        before = self.snapshot()
        r = self.apply(str(self.candidate(TOML.replace('"rclone"]', '"rclone", "curl"]'))), rc=2)
        self.assertIn("does not say which foreman.toml", r.stderr)
        r = self.apply(str(self.candidate()), "--base", "abc", rc=2)  # too short to compare
        self.assertIn("does not say which foreman.toml", r.stderr)
        self.assertEqual(before, self.snapshot())

    def test_a_change_that_opens_needs_opens(self):
        """J2: the yes/no question only when the change allows something new; here a rule removed (and its row)."""
        cand = self.based(TOML.replace('patterns = ["make prod", "rclone"]', 'patterns = ["rclone"]'))
        table = self.tmp / "new.tsv"
        table.write_text(TABLE.replace("deny\tmake prod", "allow\tmake prod"))
        before = self.snapshot()
        r = self.apply(str(cand), "--table", str(table), rc=2)
        self.assertIn("What it allows that was not allowed before", r.stdout)
        self.assertIn("  - «make prod» (never)", r.stdout)
        self.assertIn("row 2 `make prod`: blocked → allowed", r.stdout)
        self.assertIn("run it again with --opens", r.stderr)
        self.assertEqual(self.snapshot(), before)
        d = json.loads(self.apply(str(cand), "--table", str(table), "--json", rc=2).stdout)
        self.assertEqual(len(d["opens"]), 2)
        r = self.apply(str(cand), "--table", str(table), "--opens")
        self.assertIn("Applied: ", r.stdout)
        note = "".join(p.read_text() for p in self.project.notes_dir.glob("*.md"))
        self.assertIn("- - «make prod» (never)", note)
        self.assertIn("allows something new, with Owner's yes to the question (--opens)", note)

    def test_a_change_that_allows_nothing_new_needs_no_opens(self):
        """Only a row added to the table: nothing allowed changes, so no question; the table is replaced."""
        cand = self.based(None)
        table = self.tmp / "new.tsv"
        table.write_text(TABLE + "deny\trclone sync a b\n")
        r = self.apply(str(cand), "--table", str(table))
        self.assertIn("No rule or verdict changes", r.stdout)
        self.assertIn("Guard table: 1 rows added, 0 removed", r.stdout)
        self.assertEqual((self.home / "guard.tsv").read_text(), table.read_text())
        note = "".join(p.read_text() for p in self.project.notes_dir.glob("*.md"))
        self.assertIn("Guard table: 1 rows added, 0 removed", note)
        # and the very same files: nothing to write, no copy
        n = len(self.backups())
        r = self.apply(str(self.based(None, "again.toml")), "--table", str(table))
        self.assertIn("nothing written", r.stdout)
        self.assertEqual(len(self.backups()), n)

    def test_apply_asks_for_a_changed_command(self):
        cand = self.based(TOML.replace('prompt = "Fake orchestrator loop"', 'prompt = "x"\nchecks = ["curl x"]'))
        r = self.apply(str(cand), rc=2)
        self.assertIn("[loop] checks, a command foreman runs on its own", r.stdout)
        self.assertIn("run it again with --opens", r.stderr)
        self.assertIn("Applied: ", self.apply(str(cand), "--opens").stdout)

    def test_failures_write_nothing(self):
        before = self.snapshot()
        cand = self.based(TOML.replace('"rclone"]', '"rclone", "--project fake check"]'))
        r = self.apply(str(cand), rc=1)
        self.assertIn("Nothing written", r.stderr)
        self.apply(str(self.based("[project\nname = 1\n", "broken.toml")), rc=2)
        bad = self.tmp / "bad.tsv"
        bad.write_text("maybe\tx\n")
        self.apply(str(cand), "--table", str(bad), rc=2)
        after = self.snapshot()
        for f in ("broken.toml", "bad.tsv"):
            after.pop(str(self.tmp / f), None)
        self.assertEqual(before, after)

    def test_a_table_with_nowhere_to_go(self):
        no_table = TOML.replace('[check]\nguard_table = "guard.tsv"\n', "")
        self.write_toml(no_table)
        cand = self.based(no_table)
        r = self.apply(str(cand), "--table", str(self.home / "guard.tsv"), rc=2)
        self.assertIn("has nowhere to go", r.stderr)

    def test_usage(self):
        self.assertIn("usage", self.run_cli("apply", rc=2).stderr)
        self.assertIn("go with apply", self.run_cli("--opens", rc=2).stderr)

    def test_the_view_shows_the_base(self):
        out = self.run_cli().stdout
        self.assertIn(f"Base for a candidate: {self.base()}", out)
        self.assertRegex(self.base(), r"^[0-9a-f]{16}$")


class OpensTest(PermissionsCase):
    """permissions.opens(): what allows something that was not allowed before, and what does not."""

    def opened(self, toml):
        cand = config.load_project(self.home, config_file=self.candidate(toml), strict=True)
        return permissions.opens(self.project, cand, permissions.diff(self.project, cand))

    def test_restrictions_open_nothing(self):
        self.assertEqual(self.opened(TOML.replace('"rclone"]', '"rclone", "curl"]')), [])
        moved = TOML.replace('deploy = ["make deploy"]\n', "").replace('"rclone"]', '"rclone", "make deploy"]')
        self.assertEqual(self.opened(moved), [])  # a group's rule to «never»
        self.assertEqual(self.opened(TOML.replace("[deny]\n", "[deny]\ngrant_minutes = 10\n")), [])

    def test_what_opens(self):
        to_group = TOML.replace('patterns = ["make prod", "rclone"]', 'patterns = ["rclone"]').replace(
            'deploy = ["make deploy"]', 'deploy = ["make deploy", "make prod"]'
        )
        self.assertIn("~ «make prod»: never → group deploy", self.opened(to_group))
        self.assertEqual(
            self.opened(TOML.replace("[deny]\n", "[deny]\ngrant_minutes = 60\n")),
            ["each yes of a group lasts 60 min instead of 30"],
        )
        out = self.opened(TOML.replace('release = ["git tag v"]\n', ""))
        self.assertIn("- «git tag v» (group release)", out)
        self.assertIn("row 4 `git tag v1.0`: only the orchestrator → allowed", out)

    def test_a_lock_allow_opens(self):
        text = (self.home / "foreman.toml").read_text()
        self.assertIn("[resources.widget]", text)
        cand = text.replace("[resources.widget]\n", '[resources.widget]\nallow = ["ci\\\\.sh"]\n')
        self.assertEqual(self.opened(cand), ["«ci\\.sh» skips the turn of widget"])

    def test_judge_says_the_verdict_apply_and_integrate_share(self):
        """permissions.judge: ok, opens, fails (a row), broken (the table does not read), in that order."""

        def judged(toml, table=None, base=None):
            cand = config.load_project(self.home, config_file=self.candidate(toml), strict=True)
            return permissions.judge(self.project, cand, table, base)

        self.assertEqual(judged(TOML.replace('"rclone"]', '"rclone", "curl"]'))[1], "ok")
        d, verdict = judged(TOML.replace('"make prod", "rclone"]', '"make prod"]'))
        self.assertEqual((verdict, d["opens"]), ("opens", ["- «rclone» (never)"]))
        bad = self.tmp / "t.tsv"
        bad.write_text(TABLE + "allow\tmake prod\n")
        self.assertEqual(judged(None, str(bad))[1], "fails")
        self.assertEqual(judged(None, str(self.tmp / "none.tsv"))[1], "broken")
        # a copy of the table elsewhere: a relative @DIR is joined to `base`, as for the live one
        rel = self.tmp / "copy" / "t.tsv"
        rel.parent.mkdir()
        rel.write_text("allow@sub\tgit status\n")
        d, _ = judged(None, str(rel), self.home)
        self.assertEqual(d["table_failures"], [])
        self.assertEqual(guard.table_verdicts(self.project, rel, self.home)[0][3], str(self.home / "sub"))

    def test_every_command_point_opens(self):
        """Each command of commands.POINTS, added or changed, opens; also on a repo the candidate adds."""
        from foreman import commands

        repo = '\n[repos.web]\npath = "."\nlocal_ci = "make test"\n'
        self.write_toml(TOML + repo)
        self.project = config.load_project(self.home)
        live = (self.home / "foreman.toml").read_text()
        values = {
            "loop.checks": live.replace('prompt = "Fake orchestrator loop"', 'prompt = "x"\nchecks = ["true"]'),
            "resource.busy": live.replace("[resources.widget]\n", '[resources.widget]\nbusy = "pgrep x"\n'),
        }
        for key in permissions.COMMAND_KEYS:
            value = '["a", "b"]' if key in ("ci_runs", "post_create") else '"echo x"'
            values[f"repo.{key}"] = live + f"{key} = {value}\n"
        values["repo.local_ci"] = live.replace('"make test"', '"echo x"')  # the repo sets it: changed
        # every point of foreman.toml is a case (and ci_runs); a hidden check's commands are in its own holdout.toml
        points = {k for k in commands.POINTS if not k.startswith("holdout.")}
        self.assertLessEqual(points, set(values))
        for point, text in values.items():
            with self.subTest(point=point):
                out = self.opened(text)
                self.assertEqual(len(out), 1, out)
                self.assertIn("a command foreman runs on its own", out[0])
        self.assertIn(
            "[repos.web] local_ci, a command foreman runs on its own with the orchestrator's power (the guard does not "
            "see it): «make test» → «make test && true»",
            self.opened(live.replace('"make test"', '"make test && true"')),
        )
        self.assertIn("«make test» → —", self.opened(live.replace('local_ci = "make test"', 'local_ci = ""'))[0])
        self.assertEqual(self.opened(live.replace(repo, "\n")), [])  # a repo removed: nothing of it runs
        out = self.opened(live + '\n[repos.api]\npath = "."\npost_merge = "make docs"\n')
        self.assertEqual(out, [out[0]])
        self.assertIn("[repos.api] post_merge", out[0])

    def test_brakes_that_come_off_open(self):
        repo = '\n[repos.web]\npath = "."\nrequire_approval = true\npublish_on_merge = true\npublic = true\n'
        repo += 'risky_paths = ["deploy/", "infra/"]\n'
        self.write_toml(TOML + repo)
        self.project = config.load_project(self.home)
        live = (self.home / "foreman.toml").read_text()
        for key in ("require_approval", "publish_on_merge", "public"):
            with self.subTest(key=key):
                out = self.opened(live.replace(f"{key} = true", f"{key} = false"))
                self.assertEqual(out, [f"[repos.web] {key}, a brake of the integration comes off: true → false"])
        out = self.opened(live.replace('"deploy/", ', ""))
        self.assertEqual(out, ["[repos.web] risky_paths, a brake of the integration comes off: «deploy/» → —"])
        out = self.opened(live.replace("public = true", 'public = true\nremote_checks = "none"'))
        self.assertEqual(out, ["[repos.web] remote_checks, a brake of the integration comes off: «required» → «none»"])
        # stricter, or a repo removed: shown at integration, never asked
        stricter = live.replace('"infra/"]', '"infra/", "ops/"]')
        self.assertEqual(self.opened(stricter), [])
        cand = config.load_project(self.home, config_file=self.candidate(stricter), strict=True)
        self.assertEqual(permissions.also_changes(self.project, cand), ["[repos.web] risky_paths"])
        gone = config.load_project(self.home, config_file=self.candidate(TOML), strict=True)
        self.assertEqual(self.opened(TOML), [])
        self.assertEqual(len(permissions.also_changes(self.project, gone)), 4)
        self.assertEqual(permissions.also_changes(self.project, self.project), [])

    def test_review_brakes(self):
        repo = '\n[repos.web]\npath = "."\nreview = "auto"\n'
        repo += 'review_paths = ["lib/guard.py", "hooks/"]\nreview_lines = 400\n'
        self.write_toml(TOML + repo)
        self.project = config.load_project(self.home)
        live = (self.home / "foreman.toml").read_text()
        brake = "a brake of the integration comes off"
        self.assertEqual(self.opened(live.replace('"auto"', '"off"')), [f"[repos.web] review, {brake}: «auto» → «off»"])
        self.assertEqual(
            self.opened(live.replace('"lib/guard.py", ', "")),
            [f"[repos.web] review_paths, {brake}: «lib/guard.py» → —"],
        )
        out = self.opened(live.replace("= 400", "= 900"))
        self.assertEqual(out, [f"[repos.web] review_lines, {brake}: 400 → 900"])
        self.assertEqual(self.opened(live.replace("= 400", "= 0")), [f"[repos.web] review_lines, {brake}: 400 → 0"])
        for stricter in (live.replace("= 400", "= 200"), live.replace('"hooks/"]', '"hooks/", "x/"]')):
            with self.subTest(stricter=stricter):
                self.assertEqual(self.opened(stricter), [])
        # turning it on from the default (off) only restricts
        self.write_toml(TOML + '\n[repos.web]\npath = "."\n')
        self.project = config.load_project(self.home)
        self.assertEqual(self.opened(TOML + repo), [])

    def test_looser(self):
        self.assertTrue(permissions._looser("deny", "allow"))
        self.assertTrue(permissions._looser("deny", "lock:a"))
        self.assertTrue(permissions._looser("lock:a", "lock:b"))
        self.assertFalse(permissions._looser("allow", "lock:a"))
        self.assertFalse(permissions._looser("lock:a", "deny"))
        self.assertEqual(permissions._roles("orchestrator"), ("deny", "allow"))
        self.assertEqual(permissions._roles("allow/lock:a"), ("allow", "lock:a"))


class ApplyGuardTest(PermissionsCase):
    """The guard's rule for `permissions apply`: only the marked orchestrator's main thread (an agent: deny); with
    --opens, also the internal group `permissions` (the owner's yes); the lifeline keeps it without any group."""

    APPLY = "foreman --project fake permissions apply cand.toml --table t.tsv"

    def test_agent_deny_orchestrator_allow(self):
        v = guard.check(self.project, self.APPLY, guard.as_role("agent", str(self.home)))
        self.assertEqual((v[0], v[1], v[1].why), ("orchestrator", guard.PERMISSIONS_APPLY, "agent"))
        msg = guard.message(self.project, v)
        self.assertIn("`foreman permissions apply` changes the permissions of fake", msg)
        self.assertIn("never an agent or a subagent", msg)
        self.assertIsNone(guard.check(self.project, self.APPLY, guard.as_role("orchestrator", str(self.home))))
        # another session: refused, as for a group
        other = {"session": "other", "agent": None, "marked": guard.TABLE_SESSION}
        self.assertEqual(guard.check(self.project, self.APPLY, other)[1].why, "session")
        # a subagent of the marked session
        sub = {"session": guard.TABLE_SESSION, "agent": "a1", "marked": guard.TABLE_SESSION}
        self.assertEqual(guard.check(self.project, self.APPLY, sub)[1].why, "agent")

    def test_opens_is_the_internal_group(self):
        cmd = self.APPLY + " --opens"
        ctx = guard.as_role("orchestrator", str(self.home))
        self.assertIsNone(guard.check(self.project, cmd, ctx))  # a table run takes every group as granted
        self.assertEqual(ctx["passed"], grants.INTERNAL["permissions"][0])
        self.assertEqual(grants.groups_of(self.project, cmd), ["permissions"])
        self.assertEqual(grants.groups_of(self.project, self.APPLY), [])
        self.assertNotIn("permissions", grants.groups(self.project))  # the product's groups only
        self.assertIn("permissions", grants.groups(self.project, True))
        self.assertEqual(guard.check(self.project, cmd, guard.as_role("agent"))[1].why, "agent")

    def test_forms(self):
        for cmd in (
            "bin/foreman permissions --table t apply c.toml",
            "foreman 'permissions' 'apply' c.toml",
            "bash -c 'foreman permissions apply x'",
            "cd x && foreman permissions apply y && git status",
        ):
            with self.subTest(cmd):
                self.assertEqual(guard.check(self.project, cmd, guard.as_role("agent"))[0], "orchestrator")
        for cmd in (
            "foreman permissions --candidate apply.toml",
            "foreman permissions",
            'foreman note "permissions apply x --opens"',
        ):
            with self.subTest(cmd):
                self.assertIsNone(guard.check(self.project, cmd, guard.as_role("agent")))
        self.assertTrue(guard.names_apply("foreman perm\\issions ap'p'ly"))
        self.assertFalse(guard.names_apply("git status"))

    def test_integrate_with_opens_is_the_internal_group(self):
        """`foreman integrate … --opens` is in the group `permissions` (the same yes as apply --opens); its
        texts are data, and without --opens it is no group's."""
        ctx = guard.as_role("orchestrator", str(self.home))
        for cmd in (
            'foreman integrate app ab1 "AB-1 T0" --opens',
            "bin/foreman --project fake integrate app ab1 AB-1 --opens --result ok",
            "foreman 'integrate' app ab1 AB-1 '--opens'",  # a quoted option is one too
        ):
            with self.subTest(cmd):
                self.assertTrue(guard.names_internal(cmd))
                self.assertEqual(grants.groups_of(self.project, cmd), ["permissions"])
                self.assertEqual(guard.check(self.project, cmd, guard.as_role("agent"))[1].why, "agent")
                self.assertIsNone(guard.check(self.project, cmd, ctx))
        for cmd in ('foreman integrate app ab1 AB-1 --note "--opens"', "foreman integrate app ab1 AB-1 --check"):
            with self.subTest(cmd):
                self.assertEqual(grants.groups_of(self.project, cmd), [])
        self.assertFalse(guard.names_internal("foreman integrate app ab1 AB-1"))
        self.assertTrue(guard.names_internal("foreman permissions apply c.toml"))

    def test_table_rows_without_groups(self):
        """A product with no group still tells the agent from the orchestrator on a row that runs apply."""
        self.write_toml(TOML.replace('[deny.grants]\ndeploy = ["make deploy"]\nrelease = ["git tag v"]\n', ""))
        table = self.tmp / "t.tsv"
        table.write_text(f"orchestrator\t{self.APPLY}\norchestrator\t{self.APPLY} --opens\nallow\tgit status\n")
        self.assertEqual(guard.run_table(self.project, table)[1], [])

    def test_lines_show_the_internal_group_only_with_a_state(self):
        self.assertNotIn("permissions", "\n".join(grants.lines(self.project)))


class RefusalLogTest(PermissionsCase):
    """T2: the guard hook writes one line per refusal in <state>/guard.jsonl, never the command's text; a log that
    cannot be written leaves the verdict as it is; `--refused [--since]` counts by rule and the view adds 7 days."""

    SESSION = "11111111-aaaa-4aaa-8aaa-000000000001"

    def guard_hook(self, cmd, **ev):
        import contextlib
        import io

        from foreman import hooks

        err = io.StringIO()
        event = {"cwd": str(self.home), "tool_name": "Bash", "tool_input": {"command": cmd}, **ev}
        with contextlib.redirect_stderr(err):
            rc = hooks.hook_guard(self.project, event)
        return rc, err.getvalue()

    def lines(self):
        f = permissions.log_file(self.project)
        return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []

    def mark(self):
        self.project.state.mkdir(parents=True, exist_ok=True)
        (self.project.state / "session").write_text(self.SESSION + "\n")

    def test_one_line_per_refusal_without_the_command(self):
        self.mark()
        cases = [
            ("make prod PASSWORD=s3cret-one", {"agent_id": "a1", "session_id": self.SESSION}),
            ("foreman hook mod s3cret-two", {"session_id": "other"}),
            ("make deploy --token s3cret-three", {"agent_id": "a1", "session_id": self.SESSION}),
            ("make up E2E_PASSWORD=s3cret-four", {"session_id": self.SESSION}),
            ("foreman --project fake permissions apply s3cret-five.toml", {"agent_id": "a2"}),
        ]
        for cmd, ev in cases:
            self.assertEqual(self.guard_hook(cmd, **ev)[0], 2, cmd)
        self.assertEqual(self.guard_hook("git status", session_id=self.SESSION), (0, ""))  # allowed: no line
        recs = self.lines()
        self.assertNotIn("s3cret", permissions.log_file(self.project).read_text())
        got = [(r["kind"], r["rule"], r.get("group"), r.get("resource"), r["who"]) for r in recs]
        self.assertEqual(
            got,
            [
                ("deny", "make prod", None, None, "agent"),
                ("builtin", guard.HOOK_MOD, None, None, "other"),
                ("orchestrator", "make deploy", "deploy", None, "agent"),
                ("lock", "make up", None, "widget", "orchestrator"),
                ("builtin", guard.PERMISSIONS_APPLY, None, None, "agent"),
            ],
        )
        self.assertEqual(recs[2]["why"], "agent")
        self.assertEqual([r["where"] for r in recs], ["command"] * 5)
        self.assertEqual(set(recs[0]), {"at", "kind", "rule", "where", "who"})

    def test_a_log_that_cannot_be_written_keeps_the_verdict(self):
        st = self.project.state
        st.mkdir(parents=True, exist_ok=True)
        rc, msg = self.guard_hook("make prod", agent_id="a1")
        permissions.log_file(self.project).unlink()
        st.chmod(0o500)
        try:
            self.assertEqual(self.guard_hook("make prod", agent_id="a1"), (rc, msg))
        finally:
            st.chmod(0o700)
        self.assertFalse(permissions.log_file(self.project).exists())
        self.assertEqual(rc, 2)

    def test_the_guards_own_failure_and_a_lock_without_its_prefix(self):
        rec = permissions.refusal(self.project, {}, ("deny", "(the guard could not check this command: KeyError)"))
        self.assertEqual((rec["kind"], rec["where"], rec["who"]), ("error", None, "other"))
        rec = permissions.refusal(self.project, {"agent_id": "a"}, ("lock", ("widget", "other words")))
        self.assertEqual((rec["kind"], rec["rule"], rec["resource"]), ("lock", "widget", "widget"))

    def test_the_log_is_cut_to_its_last_lines(self):
        from unittest import mock

        with mock.patch.object(permissions, "LOG_BYTES", 400), mock.patch.object(permissions, "LOG_LINES", 3):
            for _ in range(8):
                self.guard_hook("make prod", agent_id="a1")
        self.assertLessEqual(len(self.lines()), 4)
        self.assertGreaterEqual(len(self.lines()), 1)

    def write_log(self, recs):
        f = permissions.log_file(self.project)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("".join(json.dumps(r) + "\n" for r in recs) + "not json\n[1]\n")

    def test_refused_counts_by_rule_since(self):
        from foreman import util

        now = util.now()
        recent = (now - permissions.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S")
        old = (now - permissions.timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%S")
        self.write_log(
            [
                {"at": old, "kind": "deny", "rule": "make prod", "where": "command", "who": "agent"},
                {"at": recent, "kind": "deny", "rule": "make prod", "where": "command", "who": "agent"},
                {"at": recent, "kind": "deny", "rule": "make prod", "where": "command", "who": "orchestrator"},
                {"at": recent, "kind": "orchestrator", "rule": "make deploy", "group": "deploy", "who": "agent"},
                {"at": recent, "kind": "lock", "rule": "make up", "resource": "widget", "who": "weird"},
                {"at": recent, "kind": "builtin", "rule": guard.HOOK_MOD, "who": "other"},
                {"at": recent, "kind": "builtin", "rule": guard.PERMISSIONS_APPLY, "who": "agent"},
                {"at": recent, "kind": "error", "rule": "(the guard could not check)", "who": "other"},
            ]
        )
        d = json.loads(self.run_cli("--refused", "--json").stdout)
        self.assertEqual(d["total"], 7)
        first = d["rules"][0]
        self.assertEqual((first["rule"], first["n"], first["who"]), ("make prod", 2, {"agent": 1, "orchestrator": 1}))
        self.assertEqual(json.loads(self.run_cli("--refused", "--since", "30d", "--json").stdout)["total"], 8)
        out = self.run_cli("--refused").stdout
        self.assertIn("Refusals of the guard since", out)
        self.assertIn("2 × «make prod» (never): agents 1, orchestrator 1", out)
        self.assertIn("«make deploy» (group deploy): agents 1", out)
        self.assertIn("«make up» (waits for widget): other sessions 1", out)
        self.assertIn("(foreman's own rule)", out)
        self.assertIn("(the guard failed)", out)
        # the view: each rule's count of the last 7 days
        v = json.loads(self.run_cli("--json").stdout)
        by = {(r["band"], r["pattern"]): r["refused"] for r in v["rules"]}
        self.assertEqual(by[("never", "make prod")], 2)
        self.assertEqual(by[("orchestrator", "make deploy")], 1)
        self.assertEqual(by[("turn", "make up")], 1)
        self.assertEqual(by[("builtin", guard.HOOK_MOD)], 1)
        self.assertEqual(by[("builtin", guard.PERMISSIONS_APPLY)], 1)
        self.assertEqual(by[("never", "rclone")], 0)
        self.assertEqual(v["refused"], 7)  # every refusal, also the guard's own failure (no rule of the view)
        text = self.run_cli().stdout
        self.assertIn("«make prod»: 1 row (`make prod`); refusals in 7 days: 2", text)
        self.assertIn("Refused in the last 7 days: 7", text)

    def test_refused_with_nothing_and_its_usage(self):
        self.assertIn("(none)", self.run_cli("--refused").stdout)
        self.assertIn("Refused in the last 7 days: 0", self.run_cli().stdout)
        self.assertIn("use 7d", self.run_cli("--refused", "--since", "soon", rc=2).stderr)
        self.assertIn("goes with --refused", self.run_cli("--since", "7d", rc=2).stderr)
        self.assertIn("goes alone", self.run_cli("--refused", "--candidate", "x.toml", rc=2).stderr)
        self.assertIn("usage", self.run_cli("apply", "x.toml", "--refused", rc=2).stderr)


class ReservedBandTest(PermissionsCase):
    """The reserved-files band of the view: guard.reserved()'s classes for this product and the machine, each path one
    the guard refuses for that class, who may write it, its refusals of the last 7 days and the other products."""

    def setUp(self):
        super().setUp()
        import os

        self.saved_home = {k: os.environ.get(k) for k in ("HOME", "CLAUDE_CONFIG_DIR")}
        os.environ.pop("CLAUDE_CONFIG_DIR", None)
        self.user = self.tmp / "user"
        (self.user / ".claude").mkdir(parents=True)
        os.environ["HOME"] = str(self.user)
        other = self.tmp / "other"
        other.mkdir()
        (other / "foreman.toml").write_text('[project]\nname = "other"\nowner = "Owner"\n')
        with open(self.tmp / "projects.toml", "a") as f:
            f.write(f'\n[[project]]\nname = "other"\nhome = "{other}"\nmode = "active"\n')

    def tearDown(self):
        import os

        for k, v in self.saved_home.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        super().tearDown()

    def test_each_class_with_its_paths(self):
        band = json.loads(self.run_cli("--json").stdout)["reserved"]
        by = {c["kind"]: c["paths"] for c in band["classes"]}
        self.assertEqual(list(by), list(guard.FILE_KINDS))
        self.assertEqual(by["config"], [str(self.home / "foreman.toml")])
        self.assertEqual(by["table"], [str(self.home / "guard.tsv")])
        self.assertEqual(by["state"], [str(self.tmp / "state") + "/"])  # this product's state lies under the root
        self.assertEqual(by["registry"], [str(self.tmp / "projects.toml")])  # FOREMAN_REGISTRY: the file alone
        self.assertEqual(by["claude"], [str(self.user / ".claude") + "/", str(self.user / ".claude.json")])
        self.assertEqual(band["others"], 1)
        for kind, paths in by.items():  # each path is one the guard refuses, for that class
            for p in paths:
                v = guard.check_file(self.project, p + ("x" if p.endswith("/") else ""))
                self.assertEqual(v[1].kind if v else None, kind, p)

    def test_text_and_refusals_by_class(self):
        from foreman import util

        recent = (util.now() - permissions.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
        f = permissions.log_file(self.project)
        f.parent.mkdir(parents=True, exist_ok=True)
        rec = {"at": recent, "kind": "file", "where": "file", "who": "agent"}
        f.write_text(
            "".join(
                json.dumps({**rec, "rule": r, "file": "x"}) + "\n"
                for r in ("file:config", "file:config", "file:claude")
            )
        )
        band = json.loads(self.run_cli("--json").stdout)["reserved"]
        self.assertEqual({c["kind"]: c["refused"] for c in band["classes"]}["config"], 2)
        out = self.run_cli().stdout
        self.assertIn(
            "Reserved files (no session writes them with its tools, the orchestrator's neither, but as each line says)",
            out,
        )
        self.assertIn(
            f"  - configuration: {self.home / 'foreman.toml'}. Changes only with `foreman permissions apply` (the "
            "orchestrator, after Owner's yes); refusals in 7 days: 2",
            out,
        )
        self.assertIn(f"foreman's state (not scratch): {self.tmp / 'state'}/. Written by foreman's own commands", out)
        self.assertIn(f"the orchestrator writes its board ({self.project.board})", out)
        self.assertIn(
            f"Claude Code's settings: {self.user / '.claude'}/ (but its projects/, plans/), "
            f"{self.user / '.claude.json'}, "
            "and settings.json, settings.local.json in any .claude folder. Owner by hand, or a chat with Owner's yes, "
            "for that one change (never an agent)",
            out,
        )
        self.assertIn("; refusals in 7 days: 1", out.split("Claude Code's settings")[1].splitlines()[0])
        self.assertIn("other registered products", out)
        self.assertIn("Refused in the last 7 days: 3", out)
        self.write_toml(TOML.replace('language = "en"', 'language = "es"'))
        out = self.run_cli().stdout
        self.assertIn("Ficheros reservados", out)
        self.assertIn("estado de foreman (no es scratch)", out)

    def test_a_state_outside_the_root_and_the_default_registry(self):
        import os

        self.write_toml(TOML.replace('worktrees = "{tmp}/wt"', 'worktrees = "{tmp}/wt"\nstate = "{tmp}/own-state"'))
        reg = self.user / ".config/foreman/projects.toml"
        reg.parent.mkdir(parents=True)
        shutil.copy(self.tmp / "projects.toml", reg)
        os.environ.pop("FOREMAN_REGISTRY")
        band = permissions.reserved_files(self.project)
        by = {c["kind"]: c["paths"] for c in band["classes"]}
        self.assertEqual(by["state"], [str(self.tmp / "own-state") + "/", str(self.tmp / "state") + "/"])
        self.assertEqual(by["registry"], [str(reg.parent) + "/"])  # the default registry: its whole folder
