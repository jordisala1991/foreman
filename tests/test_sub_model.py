"""A sub-issue keeps its agent type and model (`type`, TYPE[/MODEL]); `foreman launch` uses it and refuses a dearer
one, and the Agent call of a launch with a planned type is denied when it would cost more. One test per row of the
table of the plan."""

import json

from helpers import ProjectCase, make_repo
from test_identity import LINE, TOML

from foreman import identity
from foreman.backlog import Backlog, BacklogError


class SubModelTest(ProjectCase):
    toml = TOML

    def setUp(self):
        super().setUp()
        make_repo(self.tmp / "app")
        self.b = Backlog(self.project)
        self.b.init()
        with self.b.mutate() as d:
            self.b.add_project(d, "E1", "One", "intro")
            self.b.add_issue(d, "E1", "AB-1", "feature", status="ready")
            for sid, kind in (
                ("T0", "mechanic"),
                ("T1", "mechanic/haiku"),
                ("T2", "dev/sonnet"),
                ("T3", "dev"),
                ("T4", ""),
            ):
                self.b.set_sub(d, "AB-1", sid, title="x", type=kind)
        self.cli("session", "S1")

    def start(self, name, sub, *more):
        r = self.launch(name, "--issue", "AB-1", "--sub", sub, "--repo", "app", "--branch", f"{name}/x", *more)
        return LINE.search(r.stdout).group(1)

    def call(self, token, kind, model=None):
        ti = {"description": "x", "prompt": f"[foreman launch {token}] n", "subagent_type": kind}
        if model:
            ti["model"] = model
        ev = {"hook_event_name": "PreToolUse", "tool_name": "Agent", "session_id": "S1", "tool_input": ti}
        r = self.hook("pause-agents", ev)
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        return json.loads(r.stdout)["hookSpecificOutput"] if r.stdout.strip() else None

    def test_type_is_stored_validated_and_shown(self):
        d = self.b.load()
        self.assertEqual(self.b.issue(d, "AB-1")["subs"][2]["type"], "dev/sonnet")
        self.assertIn("(dev/sonnet)", self.cli("backlog", "show", "AB-1").stdout)
        with self.b.mutate() as d, self.assertRaises(BacklogError):
            self.b.set_sub(d, "AB-1", "T0", type="mechanic/gpt")
        with self.b.mutate() as d:
            self.b.set_sub(d, "AB-1", "T2", type="")  # empty clears it
        self.assertNotIn("type", self.b.issue(self.b.load(), "AB-1")["subs"][2])

    def denied(self, out, *words):
        self.assertEqual((out["hookEventName"], out["permissionDecision"]), ("PreToolUse", "deny"))
        for w in words:
            self.assertIn(w, out["permissionDecisionReason"])

    # rows 1-5: the call of a mechanic, mechanic/haiku and dev/sonnet launch
    def test_mechanic_call_passes(self):
        self.assertIsNone(self.call(self.start("a1", "T0"), "mechanic"))

    def test_dev_without_model_for_a_mechanic_is_denied(self):
        self.denied(self.call(self.start("a1", "T0"), "dev"), 'subagent_type "mechanic"', 'model "sonnet"')

    def test_dev_sonnet_for_a_mechanic_passes(self):
        self.assertIsNone(self.call(self.start("a1", "T0"), "dev", "sonnet"))

    def test_mechanic_opus_is_denied(self):
        self.denied(self.call(self.start("a1", "T0"), "mechanic", "opus"))

    def test_mechanic_for_a_haiku_plan_is_denied(self):
        self.denied(self.call(self.start("a1", "T1"), "mechanic"), 'model "haiku"')
        self.assertIsNone(self.call(self.start("a2", "T1"), "mechanic", "haiku"))

    # row 6: no --type: the launch takes the sub-issue's type and model, and its line says so
    def test_launch_takes_the_plan(self):
        r = self.launch("a1", "--issue", "AB-1", "--sub", "T2", "--repo", "app", "--branch", "a1/x")
        self.assertIn("plan dev/sonnet", r.stdout)
        (rec,) = self.launches().values()
        self.assertEqual((rec["type"], rec["model"], rec["planned"]), ("dev", "sonnet", "dev/sonnet"))
        self.assertIsNone(self.call(LINE.search(r.stdout).group(1), "dev", "sonnet"))

    # row 7: --model dearer than the plan is refused
    def test_launch_dearer_is_refused(self):
        r = self.launch(
            "a1", "--issue", "AB-1", "--sub", "T2", "--model", "opus", "--repo", "app", "--branch", "b", rc=2
        )
        self.assertIn("foreman backlog sub AB-1 T2 --type dev/opus", r.stderr)
        self.assertEqual(self.launches(), {})
        self.launch("a1", "--issue", "AB-1", "--sub", "T0", "--type", "dev", "--repo", "app", "--branch", "b", rc=2)

    # row 8: --type cheaper than the plan passes
    def test_launch_cheaper_passes(self):
        token = self.start("a1", "T3", "--type", "mechanic")
        self.assertIsNone(self.call(token, "mechanic"))
        self.denied(self.call(token, "dev"))  # the launch is the mechanic: the call may not be a dev

    # row 9: a sub-issue without type: as today
    def test_no_type_is_not_checked(self):
        r = self.launch("a1", "--issue", "AB-1", "--sub", "T4", "--type", "mechanic", "--repo", "app", "--branch", "b")
        self.assertNotIn("plan ", r.stdout)
        self.assertIsNone(self.call(LINE.search(r.stdout).group(1), "dev"))

    # row 12: no launch line in the brief
    def test_no_launch_line_is_not_checked(self):
        self.assertIsNone(self.call("zzzzzzzzzz", "dev"))
        self.assertIsNone(self.call("", "dev"))

    def test_price_rank(self):
        rank = identity.price_rank
        self.assertEqual([rank("mechanic"), rank("dev"), rank("foreman:dev"), rank("x")], [1, 2, 2, 2])
        self.assertEqual([rank("dev", "claude-haiku-4"), rank("dev", "fable"), rank("dev", "weird")], [0, 3, 2])
