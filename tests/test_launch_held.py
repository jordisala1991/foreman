"""`foreman launch` and the pending decisions that hold an issue or a sub-issue: a production launch of
what a pending decision holds (`blocks`) is exit 2 with nothing made, unless `--despite DID --why TEXT`, which is
recorded in the launch and the notes and gives a line for the owner; one answered in the panel holds nothing."""

import json
import re

from helpers import ProjectCase, make_repo
from test_identity import TOML

from foreman import notes
from foreman.backlog import Backlog

LINE = re.compile(r"^\[foreman launch ([a-z2-7]{10})\] (.*)$", re.M)
TITLE = "Which name do we publish it under?"


class LaunchHeldTest(ProjectCase):
    toml = TOML

    def setUp(self):
        super().setUp()
        make_repo(self.tmp / "app")
        self.b = Backlog(self.project)
        self.b.init()
        with self.b.mutate() as d:
            self.b.add_project(d, "E1", "One", "intro")
            self.b.add_issue(d, "E1", "AB-1", "feature", status="ready")
            for t in ("T1", "T2", "T3"):
                self.b.set_sub(d, "AB-1", t, title=f"part {t}")
            self.b.add_issue(d, "E1", "AB-2", "other", status="ready")

    def decide(self, blocks, title=TITLE, issue="AB-1"):
        with self.b.mutate() as d:
            return self.b.add_decision(d, None, title, "why", ["a", "b"], issue=issue, blocks=blocks)

    def prod(self, name, *extra, sub="T3", rc=0):
        args = [name, "--issue", "AB-1", *(["--sub", sub] if sub else []), "--repo", "app", "--branch", f"b/{name}"]
        return self.launch(*args, *extra, rc=rc)

    # criterion 2: refused, nothing made, both ways out named
    def test_a_held_sub_issue_is_refused_and_nothing_is_made(self):
        self.prod("z6")  # base: nothing holds it, exit 0
        did = self.decide(["T3"])
        before = self.b.data_path.read_bytes()
        launches = self.launches()
        r = self.prod("z7", rc=2)
        self.assertFalse((self.tmp / "wt" / "app-z7").exists())
        self.assertEqual(self.launches(), launches)
        self.assertEqual(self.b.data_path.read_bytes(), before)
        self.assertNotIn("[foreman launch", r.stdout)
        err = r.stderr
        self.assertIn(f"AB-1 T3 waits for the owner's decision {did} «{TITLE}»", err)
        self.assertIn(f'foreman backlog decision resolve "{did}"', err)
        self.assertIn(f'--despite "{did}" --why', err)
        # --no-backlog leaves the backlog unwritten, it does not skip the hold
        self.prod("z8", "--no-backlog", rc=2)
        self.assertFalse((self.tmp / "wt" / "app-z8").exists())

    # criterion 3: another sub-issue passes; a hold of the whole issue refuses every launch of it
    def test_only_what_is_held(self):
        self.decide(["T3"])
        self.prod("z1", sub="T2")
        self.prod("z2", sub=None)  # a hold of a sub-issue does not hold a launch without --sub
        self.launch("z3", "--issue", "AB-2", "--repo", "app", "--branch", "b/z3")
        self.decide(["issue"], title="Do we build it at all?")
        for name, sub in (("z4", "T1"), ("z5", None)):
            r = self.prod(name, sub=sub, rc=2)
            self.assertIn("Do we build it at all?", r.stderr)
        self.launch("z6", "--issue", "AB-2", "--repo", "app", "--branch", "b/z6")

    # criterion 4: a launch that is not production passes
    def test_not_production_passes(self):
        self.decide(["issue"])
        self.launch("plan1", "--issue", "AB-1", "--type", "foreman:planner")
        self.launch("plan2", "--issue", "AB-1", "--sub", "T3", "--no-backlog")  # no type, no worktree: undecided
        self.prod("np1", "--no-production")
        self.assertTrue((self.tmp / "wt" / "app-np1").exists())
        # a production type without a worktree is production
        self.launch("dev1", "--issue", "AB-1", "--type", "foreman:dev", rc=2)
        self.launch("dev2", "--issue", "AB-1", "--production", rc=2)

    # criterion 5: --despite with --why goes through, recorded, noted and told to the owner
    def test_despite_with_why(self):
        did = self.decide(["T3"])
        r = self.prod("z7", "--despite", did.lower(), "--why", "  the answer only changes the  docs ")
        token = LINE.search(r.stdout).group(1)
        rec = self.launches()[token]
        self.assertEqual(rec["despite"], [{"decision": did, "why": "the answer only changes the docs"}])
        self.assertTrue(rec["production"])
        self.assertTrue((self.tmp / "wt" / "app-z7").exists())
        tell = next(ln for ln in r.stdout.splitlines() if ln.startswith("tell the owner in the chat: "))
        self.assertIn(f"z7 was launched for AB-1 T3 before the owner answered «{TITLE}» ({did})", tell)
        self.assertIn("the answer only changes the docs", tell)
        text = self.notes_text()
        self.assertIn("launch despite", text)
        self.assertIn(f"z7 was launched for AB-1 T3 before the owner answered «{TITLE}» ({did})", text)
        sub = self.b.issue(self.b.load(), "AB-1")["subs"][2]
        self.assertEqual((sub["status"], sub["agent"]), ("in progress", "z7"))
        # --json carries the record and the line for the owner
        out = json.loads(self.prod("z9", "--despite", did, "--why", "same", "--json").stdout)
        self.assertEqual(out["despite"], [{"decision": did, "why": "same"}])
        self.assertIn("z9 was launched for AB-1 T3", out["tell_owner"])
        # a launch nothing holds records no `despite`
        r = self.prod("z10", sub="T2")
        self.assertNotIn("despite", self.launches()[LINE.search(r.stdout).group(1)])
        self.assertNotIn("tell the owner", r.stdout)

    def test_despite_misused_is_exit_2(self):
        did = self.decide(["T3"])
        did2 = self.decide(["issue"], title="Do we build it at all?")
        before = self.launches()
        for extra, said in (
            (["--despite", did], "--despite needs --why"),
            (["--despite", did, "--why", "  "], "--despite needs --why"),
            (["--why", "x"], "--why goes with --despite"),
            (["--despite", "AB-1 J9", "--why", "x"], "--despite AB-1 J9: no pending decision"),
            (["--despite", did, "--why", "x"], f"waits for the owner's decision {did2}"),  # each one must be named
        ):
            r = self.prod("zz", *extra, rc=2)
            self.assertIn(said, r.stderr)
        # a decision that holds something else is not one that holds this launch
        r = self.prod("zz", "--despite", did, "--why", "x", sub="T2", rc=2)
        self.assertIn(
            f"--despite {did}: no pending decision of that id holds this launch; it waits for {did2}", r.stderr
        )
        self.assertEqual(self.launches(), before)
        self.assertFalse((self.tmp / "wt").exists())
        self.prod("zz", "--despite", did, "--despite", did2, "--why", "x")

    # criterion 6: resolving it frees the launch
    def test_resolve_frees_the_launch(self):
        did = self.decide(["T3"])
        self.prod("z7", rc=2)
        r = self.cli("backlog", "decision", "resolve", did, "--answer", "a")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("freed: AB-1 T3", r.stdout)
        self.prod("z7")

    # answered in the panel is resolved: it holds nothing any more; an unread message alone frees nothing
    def test_answered_in_the_panel_holds_nothing(self):
        did = self.decide(["T3"])
        other = self.decide(["T3"], title="Another question")
        notes.inbox_add(self.project, "a", sender="owner", decision=TITLE)  # a message: nothing resolved
        r = self.prod("z7", rc=2)
        self.assertIn(TITLE, r.stderr)
        with self.b.mutate() as d:
            self.b.answer_decision(d, did, "a", "Owner, 2026-11-02 10:00 (panel)")
        r = self.prod("z7", rc=2)  # the other one still holds it
        self.assertIn(f"waits for the owner's decision {other} «Another question»", r.stderr)
        self.assertNotIn(TITLE, r.stderr)
        notes.inbox_read(self.project)  # read or not, resolved holds nothing
        with self.b.mutate() as d:
            self.b.answer_decision(d, other, "b", "Owner, 2026-11-02 10:01 (panel)")
        self.prod("z7")
