import json
import os
import shutil
import unittest

from helpers import BASE_TOML, ProjectCase

from foreman import backlog_check
from foreman.backlog import Backlog, BacklogError


class BacklogTest(ProjectCase):
    def setUp(self):
        super().setUp()
        self.b = Backlog(self.project)
        self.b.init()
        with self.b.mutate() as d:
            self.b.add_project(d, "E1", "First", "What E1 is for.")

    def show(self, *ref):
        r = self.cli("backlog", "show", *ref)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_init_schema5_and_no_pages(self):
        """init and the writes leave the backlog file alone: no text copy of it (no pages/, no other .md)."""
        d = self.b.load()
        self.assertEqual(d["schema_version"], 5)
        self.assertNotIn("pages", d)
        with self.assertRaises(BacklogError):
            self.b.init()
        (self.home / "reviews").mkdir(exist_ok=True)
        (self.home / "reviews/r.md").write_text("report\n")
        before = set(self.home.rglob("*.md"))
        steps = [
            ("add", "E1", "--id", "AB-1", "--title", "one", "--status", "ready"),
            ("sub", "AB-1", "T1", "--title", "first", "--report", "reviews/r.md"),
            ("status", "AB-1", "done", "--result", "ok"),
            ("decision", "add", "--id", "OPS J1", "--title", "Which", "--text", "t", "--option", "a", "--option", "b"),
            ("decision", "resolve", "OPS J1", "--option", "1"),
            ("archive", "AB-1"),
        ]
        for args in steps:
            r = self.cli("backlog", *args)
            self.assertEqual(r.returncode, 0, (args, r.stderr))
        self.assertFalse((self.home / "pages").exists())
        new = {f for f in self.home.rglob("*.md") if "notes" not in f.relative_to(self.home).parts} - before
        self.assertEqual(new, set())

    def test_render_and_page_are_gone(self):
        """`backlog render` and `backlog page` no longer exist: argparse's error, exit 2."""
        for args in (("render",), ("render", "--check"), ("page", "backlog", "--heading", "x")):
            r = self.cli("backlog", *args)
            self.assertEqual(r.returncode, 2, args)
            self.assertIn("invalid choice", r.stderr)

    def test_old_pages_block_is_dropped_on_write(self):
        """A schema 5 file with a `pages` block still loads; the next write drops the key."""
        raw = json.loads(self.b.data_path.read_text())
        raw["pages"] = {"backlog": {"intro": ["x"]}}
        self.b.data_path.write_text(json.dumps(raw))
        self.assertIn("pages", self.b.load())
        with self.b.mutate() as d:
            self.b.add_issue(d, "E1", "AB-1", "one", status="ready")
        self.assertNotIn("pages", json.loads(self.b.data_path.read_text()))

    def test_other_schema_is_refused_generically(self):
        """A schema 4 file is refused with the generic text, no per-version hint."""
        raw = json.loads(self.b.data_path.read_text())
        raw["schema_version"] = 4
        self.b.data_path.write_text(json.dumps(raw))
        with self.assertRaises(BacklogError) as c:
            self.b.load()
        self.assertIn("is schema 4: this foreman reads schema 5", str(c.exception))
        self.assertNotIn("migrate", str(c.exception))

    def test_idea_is_no_status(self):
        """`idea` is not an alias of triage any more."""
        with self.assertRaises(BacklogError):
            self.b.norm_status("idea")

    def test_issue_lifecycle(self):
        b = self.b
        with b.mutate() as d:
            b.add_issue(d, "E1", "AB-1", "Do a thing", status="ready")
            b.add_issue(d, "E1", "AB-2", "Then another", status="ready", blocked="waits for AB-1")
            b.set_sub(d, "AB-1", "T1", title="part one", agent="a1", branch="ab1")
            b.set_sub(d, "AB-1", "T2", title="part two")
            b.set_status(d, "AB-1", "in progress")
        (self.home / "reviews").mkdir()
        (self.home / "reviews/r1.md").write_text("report\n")
        with b.mutate() as d:
            did, _doubtful = b.integrated(d, "AB-1", None, "abc123", "ab1", "reviews/r1.md")
        self.assertIn("AB-1 T1 ✓ abc123, report reviews/r1.md", did)
        self.assertIn("AB-1 ready (left: T2)", did)
        d = b.load()
        self.assertEqual(b.issue(d, "AB-1")["status"], "ready")
        with b.mutate() as d:
            b.set_sub(d, "AB-1", "T2", branch="ab1b")
            did, _doubtful = b.integrated(d, "AB-1", "T2", "def456", result="it works: 3/3")
        self.assertIn("AB-1 done: it works: 3/3", did)
        self.assertIn("AB-2 unblocked (was: waits for AB-1)", did)
        d = b.load()
        self.assertEqual(b.issue(d, "AB-1")["closed"][:2], "20")
        self.assertEqual(b.issue(d, "AB-2")["blocked"], "")
        out = self.show("AB-1")
        self.assertIn("T1 part one ✓ abc123 agent a1, branch `ab1` report reviews/r1.md", out)
        self.assertNotIn("../", out)
        self.assertEqual(self.cli("backlog", "get", "AB-1", "subs").stdout.count("report reviews/r1.md"), 1)
        with b.mutate() as d:
            b.archive(d, "AB-1")
        self.assertIn("AB-1", self.cli("backlog", "list", "--archived").stdout)
        self.assertEqual([e for e in backlog_check.validate(b, b.load()) if not e.startswith("warning")], [])

    def test_relation_blocks_count_and_release_on_close(self):
        b = self.b
        with b.mutate() as d:
            b.add_issue(d, "E1", "AB-1", "blocker", status="in progress")
            b.add_issue(d, "E1", "AB-2", "waits by relation", status="ready")
            b.add_issue(d, "E1", "AB-3", "waits for two", status="ready")
            b.add_issue(d, "E1", "AB-4", "the other blocker", status="ready")
            b.add_issue(d, "E1", "AB-5", "relation and a reason", status="ready", blocked="waits for the store")
            b.set_sub(d, "AB-1", "T1", branch="ab1")
            b.relate(d, "AB-1", "blocks", "AB-2")
            b.relate(d, "AB-3", "blocked-by", "AB-1")
            b.relate(d, "AB-3", "blocked-by", "AB-4")
            b.relate(d, "AB-1", "blocks", "AB-5")
        d = b.load()
        self.assertEqual(b.relation_blockers(d), {"AB-2": ["AB-1"], "AB-3": ["AB-1", "AB-4"], "AB-5": ["AB-1"]})
        self.assertEqual(b.counts(d)["ready"], 1)  # only AB-4
        self.assertEqual(
            [(i["id"], how) for _, i, how in b.blocked_by(d, "AB-1")],
            [("AB-2", "relation"), ("AB-3", "relation"), ("AB-5", "relation")],
        )
        self.assertIn("blocked by relation: AB-1", self.cli("backlog", "show", "AB-2").stdout)
        self.assertIn("[blocked: by AB-1]", self.cli("backlog", "show").stdout)
        with b.mutate() as d:
            did, doubtful = b.integrated(d, "AB-1", None, "c1", "ab1", result="r")
        self.assertIn("AB-2 unblocked (relation: AB-1 done)", did)
        self.assertIn("AB-3 no longer blocked by AB-1; still blocked by relation AB-4", did)
        self.assertIn("AB-5 no longer blocked by AB-1; still blocked by its reason: waits for the store", did)
        self.assertEqual(doubtful, [])
        d = b.load()
        self.assertEqual(sorted(b.issue(d, "AB-1")["relations"]["blocks"]), ["AB-2", "AB-3", "AB-5"])  # history
        self.assertEqual(b.relation_blockers(d), {"AB-3": ["AB-4"]})
        self.assertEqual(b.counts(d)["ready"], 2)  # AB-2 and AB-4
        self.assertNotIn("blocked: by AB-1", self.show())
        r = self.cli("backlog", "status", "AB-4", "canceled", "--result", "not needed")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("AB-3 unblocked (relation: AB-4 canceled)", r.stdout)
        self.assertEqual(b.counts(b.load())["ready"], 2)  # AB-2 and AB-3
        self.assertEqual(b.blocked_by(b.load(), "AB-4"), [])

    def test_text_reason_and_relation_together(self):
        b = self.b
        with b.mutate() as d:
            b.add_issue(d, "E1", "AB-1", "blocker", status="in progress")
            b.add_issue(d, "E1", "AB-2", "other blocker", status="ready")
            b.add_issue(d, "E1", "AB-3", "both ways", status="ready", blocked="waits for AB-1")
            b.add_issue(d, "E1", "AB-4", "text and another relation", status="ready", blocked="waits for AB-1")
            b.set_sub(d, "AB-1", "T1", branch="ab1")
            b.relate(d, "AB-1", "blocks", "AB-3")
            b.relate(d, "AB-2", "blocks", "AB-4")
        with b.mutate() as d:
            did, doubtful = b.integrated(d, "AB-1", None, "c1", "ab1", result="r")
        self.assertIn("AB-3 unblocked (was: waits for AB-1)", did)
        self.assertEqual(sum(x.startswith("AB-3") for x in did), 1)  # said once
        self.assertIn("AB-4: reason cleared (was: waits for AB-1); still blocked by relation AB-2", did)
        self.assertEqual((doubtful, b.issue(b.load(), "AB-4")["blocked"]), ([], ""))

    def test_blocks_cycle_is_a_warning(self):
        b = self.b
        with b.mutate() as d:
            for n in (1, 2, 3):
                b.add_issue(d, "E1", f"AB-{n}", f"issue {n}", status="ready")
            b.relate(d, "AB-1", "blocks", "AB-2")
            b.relate(d, "AB-2", "blocks", "AB-3")
            b.relate(d, "AB-3", "blocks", "AB-1")
        warn = [e for e in backlog_check.validate(b, b.load()) if "cycle" in e]
        self.assertEqual(warn, ["warning: blocks cycle: AB-1 → AB-2 → AB-3 → AB-1 (none of them can be unblocked)"])
        r = self.cli("check")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("blocks cycle", r.stdout)
        with b.mutate() as d:
            b.set_status(d, "AB-3", "canceled", result="dropped")
        self.assertEqual(
            [e for e in backlog_check.validate(b, b.load()) if "cycle" in e], []
        )  # a closed issue breaks it

    def test_acceptance_criterion_required_by_prefix(self):
        b = self.b
        with self.assertRaises(BacklogError):
            with b.mutate() as d:
                b.add_issue(d, "E1", "PF-1", "Faster", status="ready")
        with b.mutate() as d:
            b.add_issue(d, "E1", "PF-1", "Faster", status="triage")
        with self.assertRaises(BacklogError):
            with b.mutate() as d:
                b.set_status(d, "PF-1", "ready")
        with b.mutate() as d:
            b.edit(d, "PF-1", claim="2x faster", criterion="p50 latency below 100 ms")
            b.set_status(d, "PF-1", "ready")
        d = b.load()
        self.assertEqual(b.issue(d, "PF-1")["acceptance"]["criterion"], "p50 latency below 100 ms")
        with self.assertRaises(BacklogError):  # closing needs the verdict too
            with b.mutate() as d:
                b.set_status(d, "PF-1", "done", result="98 ms")
        with b.mutate() as d:
            b.set_status(d, "PF-1", "done", result="98 ms", verdict="met")
        self.assertIn("Result (met): 98 ms", self.show("PF-1"))

    def test_show_is_plain_text(self):
        """`show ID` drops the bold written in a goal, criterion or result (an owner's, or an older foreman's)."""
        r = self.cli(
            "backlog", "add", "E1", "--title", "Bold", "--id", "PF-1", "--claim", "x **y**", "--criterion", "a **b** c"
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        iid = r.stdout.split()[-1]
        self.assertEqual(self.cli("backlog", "edit", iid, "--result", "r **s**").returncode, 0)
        shown = self.cli("backlog", "show", iid).stdout
        for text in ("x y", "a b c", "r s"):
            self.assertIn(text, shown)
        self.assertNotIn("**", shown)

    def test_add_short_and_dry_run(self):
        """`backlog add --short` stores the short name (and warns like `edit --short`); `--dry-run` shows the issue as
        `show ID` would and writes nothing; the errors are the same with and without it."""
        b = self.b

        def files():
            return {f: f.read_bytes() for f in self.home.rglob("*") if f.is_file() and f.name != ".backlog.lock"}

        crit = ["--claim", "2x faster", "--criterion", "p50 < 100 ms"]
        base = ["backlog", "add", "E1", "--title", "Faster export", *crit]
        # --short, --claim and --criterion are stored; the short name goes through foreman plain
        r = self.cli(*base, "--id", "PF-7", "--short", "Export")
        self.assertEqual((r.returncode, r.stdout.strip(), r.stderr), (0, "issue PF-7", ""), r.stderr)
        i = b.issue(b.load(), "PF-7")
        self.assertEqual(i["short"], "Export")
        self.assertEqual((i["acceptance"]["claim"], i["acceptance"]["criterion"]), ("2x faster", "p50 < 100 ms"))
        r = self.cli("backlog", "add", "E1", "--title", "Go on", "--short", "Go on with T5", "--status", "triage")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("short name", r.stderr)
        # --dry-run: the show block with the id it would get, and the same bytes afterwards
        before, bj = files(), (self.home / "backlog.json").read_bytes()
        r = self.cli(*base, "--short", "Export two", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        shown = r.stdout.splitlines()
        self.assertEqual(shown[0], "would add PF-9 (dry run)")
        self.assertEqual(shown[1], "PF-9 · Faster export")
        for text in ("project E1", "status backlog", "short «Export two»", "2x faster", "p50 < 100 ms"):
            self.assertIn(text, r.stdout)
        self.assertEqual((self.home / "backlog.json").read_bytes(), bj)
        self.assertEqual(files(), before)
        r = self.cli("backlog", "add", "E1", "--title", "Plain", *crit, "--before", "PF-7", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.home / "backlog.json").read_bytes(), bj)
        r = self.cli(*base, "--short", "Export two")  # what it showed is what is written
        self.assertEqual(r.stdout.strip(), "issue PF-9")
        self.assertIn("short «Export two»", self.cli("backlog", "show", "PF-9").stdout)
        # the errors come out the same with and without --dry-run
        bj = (self.home / "backlog.json").read_bytes()
        cases = (
            (["--title", "Again", "--id", "PF-7"], "already exists"),
            (["--title", "Half", "--id", "PF-30", "--claim", "c"], "criterion"),
            (["--title", "F", "--id", "PF-30", "--status", "ready"], "needs an acceptance criterion"),
        )
        for extra, msg in cases:
            real = self.cli("backlog", "add", "E1", *extra)
            dry = self.cli("backlog", "add", "E1", *extra, "--dry-run")
            self.assertEqual((real.returncode, dry.returncode), (2, 2), real.stderr + dry.stderr)
            self.assertEqual(real.stderr, dry.stderr)
            self.assertIn(msg, dry.stderr)
        self.assertEqual((self.home / "backlog.json").read_bytes(), bj)

    def test_decisions_and_moves(self):
        b = self.b
        with b.mutate() as d:
            b.add_decision(d, "OPS J1", "Which way", "Two roads.", ["a) left", "b) right"], "a) is shorter")
            b.add_issue(d, "E1", "AB-1", "one", status="ready")
            b.add_issue(d, "E1", "AB-2", "two", status="ready")
            b.move_issue(d, "AB-2", before="AB-1")
        page = self.show()
        self.assertIn("## Pending decisions (Owner)", page)
        self.assertIn("OPS J1", page)
        self.assertIn("**OPS J1, Which way**", self.show("OPS J1"))
        self.assertEqual([i["id"] for i in b.load()["projects"][0]["issues"]], ["AB-2", "AB-1"])
        with self.assertRaises(BacklogError):
            with b.mutate() as d:
                b.add_decision(d, "J1", "bad id", "", ["a"])
        with b.mutate() as d:
            b.resolve_decision(d, "OPS J1", "a")
        self.assertNotIn("Pending decisions", self.show())

    def test_decision_id_is_generated(self):
        b = self.b
        with b.mutate() as d:
            self.assertEqual(b.add_decision(d, None, "t", "", ["a"]), "DEC J1")
            self.assertEqual(b.add_decision(d, None, "t", "", ["a"], issue="AB-3"), "AB-3 J1")
            self.assertEqual(b.add_decision(d, "AB-3 J7", "t", "", ["a"]), "AB-3 J7")
            self.assertEqual(b.add_decision(d, None, "t", "", ["a"], issue="AB-3"), "AB-3 J8")
            b.resolve_decision(d, "DEC J1", "a")
        with b.mutate() as d:  # resolved ones still count: ids are never reused
            self.assertEqual(b.add_decision(d, "", "t", "", ["a"]), "DEC J2")
        with self.assertRaises(BacklogError) as cm:
            with b.mutate() as d:
                b.add_decision(d, "J1", "bad", "", ["a"], issue="AB-3")
        self.assertIn("e.g. «AB-3 J1»", str(cm.exception))
        self.assertIn("next free one is «AB-3 J9»", str(cm.exception))
        with self.assertRaises(BacklogError):
            with b.mutate() as d:
                b.add_decision(d, None, "bad", "", ["a"], issue="nope")
        r = self.cli("backlog", "decision", "add", "--issue", "AB-5", "--title", "t", "--option", "a")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "decision AB-5 J1"), r.stderr)
        r = self.cli("backlog", "decision", "add", "--title", "t", "--option", "a")
        self.assertEqual(r.stdout.strip(), "decision DEC J3", r.stderr)
        r = self.cli("backlog", "decision", "add", "--id", "oops", "--title", "t", "--option", "a")
        self.assertEqual(r.returncode, 2)
        self.assertIn("next free one is «DEC J4»", r.stderr)

    def test_decision_id_area_follows_the_pattern(self):
        b = self.b
        with b.mutate() as d:
            self.assertEqual(b.add_decision(d, None, "t", "", ["a"], issue="XX-3"), "XX-3 J1")
        self.write_toml(BASE_TOML.replace("[ids]\n", '[ids]\ndecision = "^[A-Z][A-Za-z]* J\\\\d+$"\n'))
        b = Backlog(self.project)
        with b.mutate() as d:
            b.add_decision(d, "RP J12", "t", "", ["a"])
            b.resolve_decision(d, "RP J12", "a")
        r = self.cli("backlog", "decision", "add", "--issue", "RP-11", "--title", "t", "--option", "a")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "decision RP J13"), r.stderr)
        r = self.cli("backlog", "decision", "add", "--issue", "RP-11", "--title", "t", "--option", "a")
        self.assertEqual(r.stdout.strip(), "decision RP J14", r.stderr)
        r = self.cli("backlog", "decision", "add", "--title", "t", "--option", "a")
        self.assertEqual(r.stdout.strip(), "decision DEC J1", r.stderr)
        r = self.cli("backlog", "decision", "add", "--id", "bad", "--issue", "RP-11", "--title", "t", "--option", "a")
        self.assertEqual(r.returncode, 2)
        self.assertIn("next free one is «RP J15»", r.stderr)

    def test_decision_id_no_default_fits(self):
        self.write_toml(BASE_TOML.replace("[ids]\n", '[ids]\ndecision = "^Z\\\\d+$"\n'))
        for args in ((), ("--issue", "RP-11"), ("--id", "bad", "--issue", "RP-11")):
            r = self.cli("backlog", "decision", "add", *args, "--title", "t", "--option", "a")
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("give --id matching that pattern", r.stderr)
            self.assertNotIn("next free one", r.stderr)
            self.assertNotIn("RP-11 J1", r.stderr)

    def held_world(self):
        """AB-1 ready with T2, T3 to do; AB-2 ready with T1 to do; AB-3 ready without sub-issues."""
        b = self.b
        with b.mutate() as d:
            for iid in ("AB-1", "AB-2", "AB-3"):
                b.add_issue(d, "E1", iid, f"issue {iid}", status="ready")
            b.set_sub(d, "AB-1", "T2", title="two")
            b.set_sub(d, "AB-1", "T3", title="three")
            b.set_sub(d, "AB-2", "T1", title="one")

    def test_decision_blocks_is_stored_only_when_given(self):
        """--blocks Tn / issue (with --issue) or a full reference; none by default, and then the
        decision is the one of before (no `blocks` key); `decision blocks` changes it later."""
        self.held_world()
        add = ("backlog", "decision", "add", "--title", "t", "--option", "a")
        r = self.cli(*add, "--issue", "AB-1", "--blocks", "T3")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "decision AB-1 J1"), r.stderr)
        r = self.cli(*add, "--issue", "AB-1", "--blocks", "issue")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.cli(*add, "--issue", "AB-2", "--blocks", "AB-1", "AB-2/T1", "--blocks", "AB-1")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.cli(*add, "--issue", "AB-2", "--date", "2020-01-01")
        self.assertEqual(r.returncode, 0, r.stderr)
        d = {x["id"]: x for x in self.b.load()["decisions"]}
        self.assertEqual(d["AB-1 J1"]["blocks"], ["AB-1 T3"])
        self.assertEqual(d["AB-1 J2"]["blocks"], ["AB-1"])
        self.assertEqual(d["AB-2 J1"]["blocks"], ["AB-1", "AB-2 T1"])
        self.assertEqual(
            list(d["AB-2 J2"]), ["id", "title", "date", "text", "options", "recommendation"], "same keys as before"
        )
        raw = (self.home / "backlog.json").read_text()
        self.assertIn(
            '      "id": "AB-2 J2",\n      "title": "t",\n      "date": "2020-01-01",\n      "text": "",\n'
            '      "options": [\n        "a"\n      ],\n      "recommendation": ""\n    }',
            raw,
        )
        self.assertIn('"blocks": [\n        "AB-1 T3"\n      ]', raw)
        # a sub-issue that does not exist yet is accepted with a warning; an issue that does not exist is refused
        r = self.cli(*add, "--issue", "AB-3", "--blocks", "T9")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("warning: AB-3 T9: AB-3 has no sub-issue T9 (yet)", r.stderr)
        for bad, msg in (
            (("--blocks", "T3"), "relative to the issue: give --issue"),
            (("--blocks", "AB-9"), "AB-9 is not an issue of the backlog"),
            (("--blocks", "nope"), "is not «ISSUE», «ISSUE Tn»"),
            (("--issue", "AB-1", "--blocks", "AB-1 nope"), "is not «ISSUE», «ISSUE Tn»"),
        ):
            r = self.cli(*add, *bad)
            self.assertEqual(r.returncode, 2, bad)
            self.assertIn(msg, r.stderr)
        self.assertEqual(len(self.b.load()["decisions"]), 5)
        # decision blocks ID REF… | --none
        r = self.cli("backlog", "decision", "blocks", "AB-2 J2", "AB-1 T2", "AB-3")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "AB-2 J2 holds: AB-1 T2, AB-3"), r.stderr)
        r = self.cli("backlog", "decision", "blocks", "AB-2 J2", "T1", "--issue", "AB-2")
        self.assertEqual(r.stdout.strip(), "AB-2 J2 holds: AB-2 T1", r.stderr)
        self.assertEqual(self.b.load()["decisions"][3]["blocks"], ["AB-2 T1"])
        r = self.cli("backlog", "decision", "blocks", "AB-2 J2", "--none")
        self.assertEqual(r.stdout.strip(), "AB-2 J2 holds: nothing", r.stderr)
        self.assertNotIn("blocks", self.b.load()["decisions"][3])
        for args in (("AB-2 J2",), ("AB-2 J2", "AB-1", "--none"), ("AB-9 J9", "AB-1")):
            r = self.cli("backlog", "decision", "blocks", *args)
            self.assertEqual(r.returncode, 2, args)
        self.assertIn("no pending decision AB-9 J9", r.stderr)
        r = self.cli("backlog", "decision", "resolve", "AB-2 J2", "--blocks", "AB-1")
        self.assertEqual(r.returncode, 2)
        self.assertIn("unrecognized arguments: --blocks AB-1", r.stderr)
        # what an older foreman's canon() would drop, this one keeps
        self.assertEqual(self.b.canon(self.b.load())["decisions"][0]["blocks"], ["AB-1 T3"])

    def test_decision_holds_and_launchable(self):
        """held_by / launchable / counts: a held sub-issue, a whole issue, and an issue with one sub free."""
        b = self.b
        self.held_world()
        d = b.load()
        rb = b.relation_blockers(d)
        before = b.counts(d)
        self.assertEqual(before["ready"], 3)
        b.add_decision(d, "AB-1 J1", "t", "", ["a"], blocks=["T3"], issue="AB-1")
        b.add_decision(d, "AB-1 J2", "t", "", ["a"], blocks=["AB-1 T2", "AB-1 T3"])
        b.add_decision(d, "AB-2 J1", "t", "", ["a"], blocks=["issue"], issue="AB-2")
        b.add_decision(d, "AB-3 J1", "t", "", ["a"], blocks=["AB-3 T1"])
        holds = b.decision_holds(d)
        self.assertEqual(holds["AB-1"], {"T3": ["AB-1 J1", "AB-1 J2"], "T2": ["AB-1 J2"]})
        self.assertEqual(b.held_by(holds, "AB-1", "T3"), ["AB-1 J1", "AB-1 J2"])
        self.assertEqual(b.held_by(holds, "AB-1"), [])
        self.assertEqual(b.held_by(holds, "AB-2", "T7"), ["AB-2 J1"])
        self.assertEqual(b.held_by(holds, "AB-9", "T1"), [])
        self.assertFalse(b.launchable(b.issue(d, "AB-1"), rb, holds), "every sub-issue to do is held")
        self.assertFalse(b.launchable(b.issue(d, "AB-2"), rb, holds), "held whole")
        self.assertTrue(b.launchable(b.issue(d, "AB-3"), rb, holds), "no sub-issue to do: a hold on T1 is moot")
        self.assertEqual(b.counts(d)["ready"], 1)
        self.assertEqual(b.counts(d)["refining"], before["refining"])
        answered = json.loads(json.dumps(d))  # answered in the panel is resolved: counts() sees it free at once
        b.answer_decision(answered, "AB-1 J2", "a", "Owner, 2026-11-02 10:00 (panel)")
        self.assertTrue(b.launchable(b.issue(answered, "AB-1"), rb, b.decision_holds(answered)), "T2 free")
        b.answer_decision(answered, "AB-2 J1", "a", "Owner, 2026-11-02 10:00 (panel)")
        self.assertEqual(b.counts(answered)["ready"], 3)
        b.set_sub(d, "AB-1", "T4", title="four")
        self.assertTrue(b.launchable(b.issue(d, "AB-1"), rb, holds), "T4 is free")
        b.set_sub(d, "AB-1", "T4", status="in progress")
        self.assertFalse(b.launchable(b.issue(d, "AB-1"), rb, holds), "a ▶ sub-issue is not to do")
        b.block(d, "AB-3", "waits for the vendor")
        self.assertFalse(b.launchable(b.issue(d, "AB-3"), rb, holds), "blocked")

    def test_decision_resolve_prints_what_it_frees(self):
        """`freed: REF`, or `REF: still held by ID` while another pending one holds
        it; a done sub-issue and an issue held whole with its subs held by another one."""
        self.held_world()
        b = self.b
        with b.mutate() as d:
            b.add_decision(d, "AB-1 J1", "t", "", ["a"], blocks=["AB-1 T3", "AB-1 T2"])
            b.add_decision(d, "AB-1 J2", "t", "", ["a"], blocks=["AB-1 T3"])
            b.add_decision(d, "AB-2 J1", "t", "", ["a"], blocks=["AB-2", "AB-3"])
            b.add_decision(d, "AB-2 J2", "t", "", ["a"], blocks=["AB-2 T1"])
            b.set_sub(d, "AB-1", "T2", status="done")
            b.set_status(d, "AB-3", "canceled", result="not needed")
        r = self.cli("backlog", "decision", "resolve", "AB-1 J1", "--answer", "a")
        self.assertEqual((r.returncode, r.stdout), (0, "AB-1 T3: still held by AB-1 J2\n"), r.stderr)
        r = self.cli("backlog", "decision", "resolve", "AB-1 J2")
        self.assertEqual(r.stdout, "freed: AB-1 T3\n")
        r = self.cli("backlog", "decision", "resolve", "AB-2 J1")
        self.assertEqual(r.stdout, "freed: AB-2\nAB-2 T1: still held by AB-2 J2\n")
        r = self.cli("backlog", "decision", "add", "--title", "t", "--option", "a")
        r = self.cli("backlog", "decision", "resolve", "DEC J1")
        self.assertEqual((r.returncode, r.stdout), (0, ""), "a decision that holds nothing prints nothing")
        resolved = {x["id"]: x for x in b.load()["decisions_resolved"]}
        self.assertEqual(resolved["AB-1 J1"]["blocks"], ["AB-1 T3", "AB-1 T2"], "kept as history")

    def test_decision_by_and_answer_from_the_panel(self):
        """`by` (who answered and when): optional, kept only when set, a string; `--by` on the CLI; the panel's
        answer_decision takes only a pending decision and one of its options."""
        b = self.b
        with b.mutate() as d:
            b.add_decision(d, "AB-1 J1", "t", "", ["a", "b"])
            b.add_decision(d, "AB-1 J2", "t", "", ["a", "b"])
            b.add_decision(d, "AB-1 J3", "t", "", ["a", "b"])
        r = self.cli("backlog", "decision", "resolve", "AB-1 J1", "--answer", "a", "--by", "Owner, 21:20 (chat)")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.cli("backlog", "decision", "resolve", "AB-1 J2", "--answer", "b")
        resolved = {x["id"]: x for x in b.load()["decisions_resolved"]}
        self.assertEqual(resolved["AB-1 J1"]["by"], "Owner, 21:20 (chat)")
        self.assertNotIn("by", resolved["AB-1 J2"])
        self.assertNotIn('"by": ""', b.data_path.read_text())
        with b.mutate() as d:
            for did, opt, said in (
                ("AB-1 J1", "a", "already answered: "),
                ("AB-1 J9", "a", "no pending decision AB-1 J9"),
                ("AB-1 J3", "c", "'c' is not one of its options"),
            ):
                with self.assertRaises(BacklogError) as e:
                    b.answer_decision(d, did, opt, "Owner, now (panel)")
                self.assertIn(said, str(e.exception))
            self.assertEqual(b.answer_decision(d, "AB-1 J3", "b", "Owner, now (panel)"), [])
        self.assertEqual(b.resolved_decision(b.load(), "AB-1 J3")["by"], "Owner, now (panel)")
        raw = json.loads(b.data_path.read_text())
        raw["decisions_resolved"][0]["by"] = 3
        found = backlog_check.validate(b, raw, files=False)
        self.assertIn("decision AB-1 J1: by is not a string", found)

    def test_decision_blocks_check_and_show(self):
        """The checks of the backlog and the hold where the blocking reason shows: show, the summary and the
        decision's text."""
        self.held_world()
        b = self.b
        with b.mutate() as d:
            b.add_decision(d, "AB-1 J1", "Which way", "", ["a"], blocks=["AB-1 T3"])
            b.add_decision(d, "AB-2 J1", "t", "", ["a"], blocks=["AB-2"])
        self.assertIn("[blocked: waits for decision AB-2 J1]", self.show())
        self.assertIn("**AB-1 J1, Which way** (", self.show("AB-1 J1"))
        self.assertIn("Waiting for the answer: AB-1 T3. Options: a", self.show("AB-1 J1"))
        r = self.cli("backlog", "show", "AB-1")
        self.assertIn("status ready · T3 waits for decision AB-1 J1", r.stdout)
        r = self.cli("backlog", "show")
        self.assertIn("[blocked: T3 waits for decision AB-1 J1]", r.stdout)
        self.assertIn("AB-2 J1", r.stdout)
        self.assertIn("[holds: AB-2]", r.stdout)
        r = self.cli("backlog", "show", "AB-1 J1")
        self.assertIn("Waiting for the answer: AB-1 T3.", r.stdout)
        self.assertEqual(json.loads(self.cli("backlog", "show", "AB-1 J1", "--json").stdout)["blocks"], ["AB-1 T3"])
        self.assertEqual(self.cli("check").returncode, 0)
        # check: an issue that does not exist is an error; a closed issue, a sub-issue missing or closed, a warning
        with b.mutate() as d:
            b.set_sub(d, "AB-1", "T2", status="done")
            b.set_status(d, "AB-3", "done", result="done: 1/1")
            b.add_decision(d, "AB-3 J1", "t", "", ["a"], blocks=["AB-1 T2", "AB-1 T8", "AB-3"])
        found = backlog_check.validate(b, b.load(), files=False)
        self.assertIn("warning: decision AB-3 J1 blocks AB-1 T2: T2 is done", found)
        self.assertIn("warning: decision AB-3 J1 blocks AB-1 T8: AB-1 has no sub-issue T8 (yet)", found)
        self.assertIn("warning: decision AB-3 J1 blocks AB-3: AB-3 is done", found)
        d = b.load()
        d["decisions"][-1]["blocks"].append("AB-77 T1")
        found = backlog_check.validate(b, d, files=False)
        self.assertIn("decision AB-3 J1 blocks AB-77 T1: there is no issue AB-77", found)
        self.assertTrue(backlog_check.hard(found))
        d["decisions"][-1]["blocks"] = "AB-1"
        self.assertIn("decision AB-3 J1: blocks is not a list", backlog_check.validate(b, d, files=False))
        # a closed issue shows no hold
        with b.mutate() as d:
            b.set_status(d, "AB-2", "done", result="done: 1/1")
        self.assertNotIn("waits for decision AB-2 J1", self.show())

    def test_issue_id_is_generated(self):
        b = self.b
        with self.assertRaises(BacklogError) as cm:  # nothing to learn the prefix from
            with b.mutate() as d:
                b.add_issue(d, "E1", None, "first")
        self.assertIn("--id", str(cm.exception))
        r = self.cli("backlog", "add", "--title", "first")
        self.assertEqual(r.returncode, 2)
        self.assertIn("give the first one with --id", r.stderr)
        with b.mutate() as d:
            self.assertEqual(b.add_issue(d, "E1", "AB-1", "given"), "AB-1")
            self.assertEqual(b.add_issue(d, "E1", None, "next"), "AB-2")
            self.assertEqual(b.add_issue(d, "", "AB-9", "loose"), "AB-9")
            self.assertEqual(b.add_issue(d, "-", "", "next after the loose one"), "AB-10")
            b.set_status(d, "AB-10", "canceled", result="not needed")
            b.archive(d, "AB-10")
        with b.mutate() as d:  # archived ones still count: ids are never reused
            self.assertEqual(b.add_issue(d, None, None, "after the archive"), "AB-11")
        r = self.cli("backlog", "add", "E1", "--title", "via cli")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "issue AB-12"), r.stderr)
        self.assertEqual(b.find(b.load(), "AB-12")[2], "E1")
        r = self.cli("backlog", "add", "--title", "no project")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "issue AB-13"), r.stderr)
        self.assertEqual(b.find(b.load(), "AB-13")[2], "")
        r = self.cli("backlog", "add", "", "--id", "AB-20", "--title", "empty project still works")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "issue AB-20"), r.stderr)
        self.assertEqual(b.find(b.load(), "AB-20")[2], "")
        r = self.cli("backlog", "add", "nope", "--title", "t")
        self.assertEqual(r.returncode, 2)
        self.assertIn("nope is not a project (projects: E1)", r.stderr)
        self.assertIn("leave the project out", r.stderr)
        with b.mutate() as d:  # two prefixes: no guess, the error names both next ids
            b.add_issue(d, "E1", "CD-4", "other prefix")
        with self.assertRaises(BacklogError) as cm:
            with b.mutate() as d:
                b.add_issue(d, "E1", None, "ambiguous")
        self.assertIn("AB-21, CD-5", str(cm.exception))

    def test_bad_writes_are_not_saved(self):
        b = self.b
        before = b.data_path.read_text()
        for bad in (
            lambda d: b.add_issue(d, "E1", "bad", "x"),
            lambda d: b.set_status(d, "NO-1", "ready"),
            lambda d: b.add_issue(d, "E9", "AB-9", "x"),
        ):
            with self.assertRaises(BacklogError):
                with b.mutate() as d:
                    bad(d)
        self.assertEqual(b.data_path.read_text(), before)

    def test_want_marks_a_triage_issue(self):
        """The owner's «I want it done» (the panel's /api/want): only on triage, once; the status stays; other writes
        keep it; «no longer» clears it whatever the status; show and the page say it; a bad value is refused."""
        b = self.b
        with b.mutate() as d:
            b.add_issue(d, "E1", "AB-1", "Wanted", status="triage")
            b.add_issue(d, "E1", "AB-2", "Ready", status="ready")
            b.add_issue(d, "E1", "AB-3", "Closed", status="triage")
            b.set_status(d, "AB-3", "canceled", result="not needed")
        with b.mutate() as d:
            b.want(d, "AB-1", True, "2026-11-02 10:14")
        i = b.issue(b.load(), "AB-1")
        self.assertEqual((i["status"], i["wanted"]), ("triage", "2026-11-02 10:14"))
        for iid, on, why in (
            ("AB-1", True, "already wanted"),
            ("AB-2", True, "not in triage"),
            ("AB-3", True, "not in triage"),
            ("AB-2", False, "not wanted"),
            ("AB-9", True, "not an issue"),
        ):
            with self.assertRaises(BacklogError) as cm, b.mutate() as d:
                b.want(d, iid, on, "2026-11-02 10:15")
            self.assertIn(why, str(cm.exception))
        r = self.cli("backlog", "edit", "AB-1", "--append", "more")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(b.issue(b.load(), "AB-1")["wanted"], "2026-11-02 10:14")  # canon() keeps it
        self.assertIn("wanted by the owner since 2026-11-02 10:14", self.cli("backlog", "show", "AB-1").stdout)
        with b.mutate() as d:
            b.set_status(d, "AB-1", "in progress")
            b.want(d, "AB-1", False, "")
        self.assertNotIn("wanted", b.issue(b.load(), "AB-1"))
        self.assertEqual(b.issue(b.load(), "AB-1")["status"], "in progress")
        with self.assertRaises(BacklogError) as cm, b.mutate() as d:
            b.issue(d, "AB-2")["wanted"] = "yesterday"
        self.assertIn("wanted 'yesterday' is not a YYYY-MM-DD HH:MM time", str(cm.exception))

    def test_sub_kind_measure(self):
        """A sub-issue's `kind`: "measure" is stored, `code` (or empty) clears it, anything else is refused; the
        check refuses a bad stored value, show marks a measurement; `backlog sub --kind` and `launch --kind`."""
        b = self.b
        with b.mutate() as d:
            b.add_issue(d, "E1", "AB-1", "Measure a thing", status="ready")
            b.set_sub(d, "AB-1", "T0", title="only measure", kind="measure", branch="ab1/m")
            b.set_sub(d, "AB-1", "T1", title="the code")
        d = b.load()
        subs = b.issue(d, "AB-1")["subs"]
        self.assertEqual([s.get("kind") for s in subs], ["measure", None])
        self.assertEqual(list(subs[0]), ["id", "title", "status", "branch", "kind"])  # schema order, kind last
        page = self.show("AB-1")
        self.assertIn("T0 only measure (measure)", page)
        self.assertNotIn("T1 the code (measure)", page)
        with self.assertRaises(BacklogError) as e, b.mutate() as d:
            b.set_sub(d, "AB-1", "T1", kind="study")
        self.assertIn("bad sub-issue kind 'study' (measure|code)", str(e.exception))
        with b.mutate() as d:
            b.set_sub(d, "AB-1", "T0", kind="code")
        self.assertNotIn("kind", b.issue(b.load(), "AB-1")["subs"][0])
        # the CLI
        r = self.cli("backlog", "sub", "AB-1", "T1", "--kind", "measure")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(b.issue(b.load(), "AB-1")["subs"][1]["kind"], "measure")
        self.assertIn("T1 the code (measure)", self.cli("backlog", "show", "AB-1").stdout)
        r = self.cli("backlog", "sub", "AB-1", "T1", "--kind", "study")
        self.assertEqual(r.returncode, 2)
        self.cli("backlog", "sub", "AB-1", "T1", "--kind", "code")
        self.assertNotIn("kind", b.issue(b.load(), "AB-1")["subs"][1])
        # foreman launch --kind marks the sub-issue with the rest of the launch
        r = self.cli("launch", "mz1", "--issue", "AB-1", "--kind", "measure")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--kind needs --issue and --sub", r.stderr)
        r = self.cli("launch", "mz1", "--issue", "AB-1", "--sub", "T1", "--kind", "measure")
        self.assertEqual(r.returncode, 0, r.stderr)
        t1 = b.issue(b.load(), "AB-1")["subs"][1]
        self.assertEqual((t1["status"], t1["agent"], t1["kind"]), ("in progress", "mz1", "measure"))
        # a bad stored value (by hand, or a newer foreman) is refused by the check
        d = b.load()
        b.issue(d, "AB-1")["subs"][1]["kind"] = "study"
        errs = backlog_check.validate(b, d)
        self.assertIn("AB-1 (E1) T1: bad sub-issue kind 'study' (measure, or none)", errs)

    def test_absolute_links_stay_absolute(self):
        # a backlog moved from another product links that product's reports where they are
        other = "/elsewhere/raw/reviews/2026-10-30-x1.md"
        with self.b.mutate() as data:
            self.b.add_issue(data, "E1", "AB-9", "moved", status="backlog", plan=[f"plan={other}"])
            self.b.set_sub(data, "AB-9", "T1", status="done", report=other)
        page = self.show("AB-9")
        self.assertIn(f"report {other}", page)
        self.assertNotIn(f"/{other}", page)

    def test_cli_commands(self):
        steps = [
            ("add", "E1", "--id", "AB-1", "--title", "via cli", "--status", "ready"),
            ("sub", "AB-1", "T1", "--title", "first", "--status", "▶", "--agent", "x"),
            ("block", "AB-1", "waiting"),
            ("unblock", "AB-1"),
            ("edit", "AB-1", "--append", "more detail", "--github", "acme/app#7"),
            ("decision", "add", "--id", "OPS J2", "--title", "t", "--text", "x", "--option", "a", "--option", "b"),
        ]
        for s in steps:
            r = self.cli("backlog", *s)
            self.assertEqual(r.returncode, 0, f"{s}: {r.stderr}")
        r = self.cli("backlog", "show", "AB-1")
        self.assertIn("github acme/app#7", r.stdout)
        self.assertIn("T1 first ▶ agent x", r.stdout)
        r = self.cli("backlog", "get", "AB-1", "detail")
        self.assertEqual(r.stdout.strip(), "more detail")
        r = self.cli("backlog", "show")
        self.assertIn("Pending decisions (Owner)", r.stdout)
        r = self.cli("backlog", "status", "AB-1", "done")
        self.assertEqual(r.returncode, 2)
        self.assertIn("needs its result", r.stderr)

    def test_import_gh(self):
        self.write_toml(self.toml + '\n[repos.app]\npath = "{tmp}/app"\ngithub = "acme/app"\n')
        fake = self.tmp / "gh"
        fake.write_text(
            '#!/bin/sh\necho \'[{"number": 12, "title": "Crash on start", "url": "u12"},'
            ' {"number": 5, "title": "Typo", "url": "u5"}]\'\n'
        )
        fake.chmod(0o755)
        b = Backlog(self.project)
        with b.mutate() as d:
            added = b.import_gh(d, "acme/app", "E1", "AP", gh=str(fake))
        self.assertEqual(added, ["AP-1", "AP-2"])
        d = b.load()
        self.assertEqual(b.issue(d, "AP-1")["github"], "acme/app#5")
        with b.mutate() as d:
            self.assertEqual(b.import_gh(d, "acme/app", "E1", "AP", gh=str(fake)), [])


class LinearModelTest(ProjectCase):
    """Schema 5: project fields, issue fields and relations, issues without project, the layer commands gone,
    schemas 2 and 3 refused, and the auto-close option."""

    toml = BASE_TOML + '\n[repos.app]\npath = "{tmp}/app"\ngithub = "acme/app"\n[repos.api]\npath = "{tmp}/api"\n'

    def setUp(self):
        super().setUp()
        self.b = Backlog(self.project)
        self.b.init()
        with self.b.mutate() as d:
            self.b.add_project(d, "E1", "First", "What E1 is for.")
            self.b.add_project(d, "E2", "Second", "What E2 is for.")
            self.b.add_issue(d, "E1", "AB-1", "one", status="ready")
            self.b.add_issue(d, "E1", "AB-2", "two", status="backlog")
            self.b.add_issue(d, "E2", "AB-3", "three", status="in progress")

    def test_schema3_is_refused(self):
        """A schema 3 file is no longer upgraded on read; like schema 2, every command refuses it and names
        the older foremen that move it to schema 4 and then 5."""
        raw = json.loads(self.b.data_path.read_text())
        raw["schema_version"] = 3
        del raw["issues"]
        self.b.data_path.write_text(json.dumps(raw))
        before = self.b.data_path.read_bytes()
        for call in (self.b.load, lambda: self.b.mutate().__enter__()):
            with self.assertRaises(BacklogError) as e:
                call()
            self.assertIn("is schema 3: this foreman reads schema 5", str(e.exception))
        for args in (("show",), ("list",), ("status", "AB-2", "triage")):
            r = self.cli("backlog", *args)
            self.assertNotEqual(r.returncode, 0, args)
        for args in (("show",), ("list",), ("status", "AB-2", "triage")):
            self.assertIn("reads schema 5", self.cli("backlog", *args).stderr, args)
        r = self.cli("check")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("reads schema 5", r.stdout)
        self.assertEqual(self.b.data_path.read_bytes(), before)
        self.b.data_path.write_text(json.dumps({**raw, "schema_version": 5, "issues": []}))
        with self.b.mutate() as d:
            self.b.set_status(d, "AB-2", "triage")
        self.assertEqual(self.b.issue(self.b.load(), "AB-2")["status"], "triage")

    def test_schema4_is_refused(self):
        """A schema 4 file (foreman 0.53 and older) is no longer read: every command refuses it naming foreman 0.54,
        whose `backlog migrate` moves it to 5, and `foreman check` reports it as a backlog problem (exit 1, no
        traceback). The commands that read and wrote schema 4 are gone."""
        raw = json.loads(self.b.data_path.read_text())
        raw = {**raw, "schema_version": 4, "initiatives": []}
        self.b.data_path.write_text(json.dumps(raw))
        before = self.b.data_path.read_bytes()
        with self.assertRaises(BacklogError) as e:
            self.b.load()
        self.assertIn("is schema 4: this foreman reads schema 5", str(e.exception))
        r = self.cli("backlog", "show")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("reads schema 5", r.stderr)
        r = self.cli("check")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("is schema 4: this foreman reads schema 5", r.stdout)
        self.assertNotIn("Traceback", r.stdout + r.stderr)
        self.assertEqual(self.b.data_path.read_bytes(), before)
        for args in (("migrate",), ("migrate", "--write"), ("schema",), ("blocked-by", "AB-1")):
            r = self.cli("backlog", *args)
            self.assertEqual(r.returncode, 2, args)
            self.assertIn("invalid choice", r.stderr, args)

    def test_schema5_read_as_is_and_incomplete_files_refused(self):
        """A schema 5 file is read exactly as written; one without a schema 5 list, or with another schema, is
        refused instead of being filled in."""
        text = self.b.data_path.read_text()
        self.assertEqual(json.loads(text)["schema_version"], 5)
        self.assertNotIn("initiatives", json.loads(text))
        self.assertEqual(self.b.load(), json.loads(text))
        self.assertEqual(self.b.dumps(self.b.load()), text)
        raw = json.loads(text)
        del raw["decisions_resolved"]
        self.b.data_path.write_text(json.dumps(raw))
        with self.assertRaises(BacklogError) as e:
            self.b.load()
        self.assertIn("not a valid schema 5 file: no list decisions_resolved", str(e.exception))
        for v in (6, None):
            raw = json.loads(text)
            raw["schema_version"] = v
            self.b.data_path.write_text(json.dumps(raw))
            with self.assertRaises(BacklogError) as e:
                self.b.load()
            self.assertIn(f"is schema {v!r}: this foreman reads schema 5", str(e.exception))
            self.assertEqual(self.b.load(raw=True), raw)

    def test_project_and_issue_fields(self):
        b = self.b
        with b.mutate() as d:
            b.edit_project(d, "E1", status="in progress", priority="high", repos=["app", "api"])
            b.edit(d, "AB-1", priority="urgent", assignee="dev1", repo="app")
            b.set_status(d, "AB-3", "done", result="ok")
        d = b.load()
        e1 = b.get_project(d, "E1")
        self.assertEqual(
            (e1["status"], e1["priority"], e1["repos"]),
            ("in progress", "high", ["app", "api"]),
        )
        self.assertIn("in progress · priority high · repo app, api", self.cli("backlog", "show", "E1").stdout)
        self.assertIn("priority urgent · assignee dev1 · repo app", self.cli("backlog", "show", "AB-1").stdout)
        self.assertIn("## E1 · First  (0/2 done)", self.cli("backlog", "show", "E1").stdout)
        before = b.data_path.read_text()
        for bad in (
            lambda d: b.edit_project(d, "E1", status="done"),
            lambda d: b.edit(d, "AB-1", repo="web"),
            lambda d: b.edit(d, "AB-1", priority="soon"),
        ):
            with self.assertRaises(BacklogError):
                with b.mutate() as d:
                    bad(d)
        self.assertEqual(b.data_path.read_text(), before)
        r = self.cli("backlog", "edit", "AB-1", "--priority", "", "--assignee", "")  # an empty value clears it
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(
            {k: b.issue(b.load(), "AB-1").get(k) for k in ("priority", "assignee", "repo")},
            {"priority": None, "assignee": None, "repo": "app"},
        )

    def test_removed_fields_and_statuses_still_load(self):
        """A file written by an older foreman with a field it no longer has (estimate, labels, a project's
        products or depends_on) or a status it no longer has: the commands still read it, `foreman check` warns of the
        fields (dropped at the next write) and fails on the status with the way to set one."""
        raw = json.loads(self.b.data_path.read_text())
        raw["projects"][0].update(products=["other"], depends_on=["E2"])
        raw["projects"][0]["issues"][0].update(estimate=3, labels=["ui"])
        raw["projects"][0]["issues"][1]["status"] = "in review"
        self.b.data_path.write_text(json.dumps(raw))
        self.assertEqual(self.cli("backlog", "show").returncode, 0)
        self.assertEqual(self.cli("backlog", "list").returncode, 0)
        errors = backlog_check.validate(self.b, self.b.load(), files=False)
        self.assertIn("warning: E1: unknown keys depends_on, products (dropped at the next write)", errors)
        self.assertIn("warning: AB-1 (E1): unknown keys estimate, labels (dropped at the next write)", errors)
        bad = [e for e in backlog_check.hard(errors) if "bad status" in e]
        self.assertEqual(len(bad), 1, errors)
        self.assertIn("'in review'", bad[0])
        self.assertIn("foreman backlog status AB-2 STATUS sets one", bad[0])
        r = self.cli("backlog", "status", "AB-2", "in progress")  # the way out: the write fixes it
        self.assertEqual(r.returncode, 0, r.stderr)
        d = self.b.load()
        self.assertNotIn("estimate", d["projects"][0]["issues"][0])
        self.assertNotIn("products", d["projects"][0])
        self.assertEqual(backlog_check.validate(self.b, d, files=False), [])

    def test_layer_commands_are_gone(self):
        """initiative, initiatives, milestone, update and fields are no longer commands (argparse's exit 2, not in the
        help), nor are the project fields health, lead, start, target, products and depends_on, the issue fields
        milestone, estimate and labels, the status «in review» or an abbreviated option."""
        for args in (
            ("initiative", "add", "I1", "--name", "Grow"),
            ("initiatives",),
            ("milestone", "E1", "M1", "--name", "Alpha"),
            ("update", "E1", "--health", "on track", "--text", "fine"),
            ("project", "edit", "E1", "--health", "on track"),
            ("project", "edit", "E1", "--lead", "Owner"),
            ("project", "edit", "E1", "--start", "2026-11-01"),
            ("project", "edit", "E1", "--target", "2026-12-01"),
            ("fields", "AB-1", "--priority", "low"),
            ("edit", "AB-1", "--milestone", "M1"),
            ("edit", "AB-1", "--estimate", "3"),
            ("edit", "AB-1", "--label", "ui"),
            ("project", "edit", "E1", "--product", "other"),
            ("project", "edit", "E1", "--depends-on", "E2"),
            ("list", "--all"),  # no abbreviated options: not --all-products
        ):
            r = self.cli("backlog", *args)
            self.assertEqual(r.returncode, 2, args)
            self.assertIn("error:", r.stderr, args)
        helptext = self.cli("backlog", "--help").stdout
        for word in ("\n  initiative", "\n  milestone", "\n  update", "\n  fields", "--milestone", "--health", "--lead",
                     "--target", "--estimate", "--label", "--product", "--depends-on", "in review"):  # fmt: skip
            self.assertNotIn(word, helptext, word)
        r = self.cli("backlog", "status", "AB-1", "in review")
        self.assertEqual((r.returncode, "bad status 'in review'" in r.stderr), (2, True), r.stderr)

    def test_relations(self):
        b = self.b
        with b.mutate() as d:
            b.relate(d, "AB-2", "blocked-by", "AB-1")
            b.relate(d, "AB-1", "related", "AB-3")
        d = b.load()
        self.assertEqual(b.issue(d, "AB-1")["relations"], {"blocks": ["AB-2"], "related": ["AB-3"]})
        self.assertEqual(b.relations_of(d, "AB-2")["blocked_by"], ["AB-1"])
        self.assertEqual(b.relations_of(d, "AB-3")["related"], ["AB-1"])
        self.assertIn("relations: blocked by AB-1", self.cli("backlog", "show", "AB-2").stdout)
        with b.mutate() as d:
            b.add_issue(d, "E1", "AB-4", "same as one", status="backlog")
            b.relate(d, "AB-4", "duplicate", "AB-1")
        d = b.load()
        self.assertEqual(
            (b.issue(d, "AB-4")["status"], b.relations_of(d, "AB-1")["duplicates"]), ("duplicate", ["AB-4"])
        )
        with b.mutate() as d:
            b.relate(d, "AB-2", "blocked-by", "AB-1", remove=True)
        self.assertNotIn("relations", b.issue(b.load(), "AB-2"))
        r = self.cli("backlog", "relate", "AB-1", "parent-of", "AB-2")
        self.assertEqual(r.returncode, 2)

    def test_issues_without_project_and_cli(self):
        steps = [
            ("add", "-", "--id", "AP-1", "--title", "loose", "--status", "triage"),
            ("edit", "AP-1", "--repo", "app", "--priority", "low"),
            ("move", "AB-2", "--project", "-"),
            ("project", "edit", "E1", "--status", "planned", "--priority", "low", "--repo", "app"),
            ("relate", "AP-1", "blocks", "AB-1"),
            ("status", "AP-1", "in progress"),
        ]
        for s in steps:
            r = self.cli("backlog", *s)
            self.assertEqual(r.returncode, 0, f"{s}: {r.stderr}")
        d = self.b.load()
        self.assertEqual([i["id"] for i in d["issues"]], ["AB-2", "AP-1"])  # active before triage
        self.assertEqual(self.b.get_project(d, "E1")["priority"], "low")
        self.assertIn("## No project", self.cli("backlog", "show").stdout)
        with self.b.mutate() as d:
            self.b.set_status(d, "AB-2", "canceled", result="not needed")
            self.b.archive(d, "AB-2")
        self.assertIn(
            {"project": "", "issues": ["AB-2"]},
            [{"project": g["project"], "issues": [i["id"] for i in g["issues"]]} for g in self.b.load()["archive"]],
        )
        r = self.cli("backlog", "project", "remove", "E2")
        self.assertEqual(r.returncode, 2)  # still has AB-3
        self.cli("backlog", "move", "AB-3", "--project", "-")
        r = self.cli("backlog", "project", "remove", "E2")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.cli("check").returncode, 0)

    def test_schema2_is_refused(self):
        """A schema 2 file is neither read nor written; every command points to an older foreman."""
        raw = json.loads(self.b.data_path.read_text())
        raw["schema_version"] = 2
        raw.pop("issues")
        self.b.data_path.write_text(json.dumps(raw))
        before = self.b.data_path.read_bytes()
        for call in (self.b.load, lambda: self.b.mutate().__enter__()):
            with self.assertRaises(BacklogError) as e:
                call()
            self.assertIn("is schema 2: this foreman reads schema 5", str(e.exception))
            self.assertIn("reads schema 5", str(e.exception))
        for args in (("show",), ("status", "AB-2", "triage")):
            r = self.cli("backlog", *args)
            self.assertNotEqual(r.returncode, 0, args)
            self.assertIn("reads schema 5", r.stderr, args)
        r = self.cli("check")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("reads schema 5", r.stdout)
        self.assertEqual(self.b.data_path.read_bytes(), before)


def issue2(iid, status, **kw):
    return {
        "id": iid,
        "title": f"title of {iid}",
        "status": status,
        "blocked": "",
        "plan": [],
        "subs": [],
        "detail": "",
        **kw,
    }


class RealBacklogsTest(ProjectCase):
    """Opt-in check on copies of real backlogs (schema 5)."""

    @unittest.skipUnless(os.environ.get("FOREMAN_REAL_BACKLOGS"), "set FOREMAN_REAL_BACKLOGS to run it")
    def test_copies_of_real_backlogs(self):
        """FOREMAN_REAL_BACKLOGS=PATH:PATH…: each file is copied into $TMPDIR first (never read in place again),
        is schema 5 and is written back byte for byte."""
        for k, src in enumerate(os.environ["FOREMAN_REAL_BACKLOGS"].split(os.pathsep)):
            copy = self.tmp / f"real-{k}.json"
            shutil.copyfile(src, copy)
            text = copy.read_text()
            d = json.loads(text)
            with self.subTest(src=src):
                self.assertEqual(d["schema_version"], 5)
                self.assertEqual(Backlog(self.project).dumps(d), text)


class LegacyIdsTest(ProjectCase):
    """`ids.legacy`: archived issues with an old id (H6) and old ids kept as aliases (H7) are valid in a schema 5
    file and resolve to their issue."""

    toml = BASE_TOML.replace("[ids]\n", '[ids]\nlegacy = "^H\\\\d+$"\n')

    def test_legacy_ids_and_aliases(self):
        b = Backlog(self.project)
        raw = {
            **b.empty(),
            "projects": [
                {"id": "E1", "title": "First", "intro": "What E1 is for.", "issues": [issue2("AB-1", "ready")]}
            ],
            "archive": [
                {
                    "project": "E1",
                    "issues": [
                        issue2(
                            "H6",
                            "done",
                            acceptance={"criterion": "k", "verdict": "confirmed"},
                            result="held",
                            closed="2026-09-01",
                        ),
                        issue2(
                            "AB-5",
                            "done",
                            acceptance={"criterion": "k", "verdict": "met", "aliases": ["H7", "H8"]},
                            result="done it",
                            closed="2026-09-02",
                        ),
                    ],
                }
            ],
        }
        b.data_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n")
        self.assertEqual(b.resolve(b.load(), "H7"), "AB-5")
        self.assertEqual(b.resolve(b.load(), "H6"), "H6")
        r = self.cli("check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
