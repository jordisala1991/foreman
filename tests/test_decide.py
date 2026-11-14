"""The loop's review and merge decisions (fm/decide.py): one test per row of DESIGN.md's table "What the loop does with a
task in review", none launches a program. `Pure` keeps the module free of programs and files."""

import ast
import unittest
from typing import Any

from helpers import ROOT

from fm.decide import Seen, Step, after_check, checks_again, closes_review, conflict_note, review_step, waits_for_ci


def seen(**kw: Any) -> Seen:
    base: dict[str, Any] = {"head": "t2", "verdict_tip": "", "hunter_tip": "", "reviewer_tip": "", "free": True}
    flags = {"hunter_file": False, "hunter_working": False, "hunter_ended": False, "reviewer_ended": False}
    return Seen(**{**base, **flags, **kw})


def check(**kw: Any) -> dict[str, Any]:
    return {"risk": "low", "review": "clean", "ci": "", "conflict": False, "head": "h", "main": "m", **kw}


def step(**kw: Any) -> Step:
    return review_step(seen(**kw), {}, 2)


class Review(unittest.TestCase):
    def test_no_hunter_launches_it(self):
        self.assertEqual(step(), Step("hunt", "round"))
        self.assertEqual(step(free=False), Step("wait", "round"))
        self.assertEqual(step(hunter_tip="t1", reviewer_tip="t1"), Step("hunt", "round"))

    def test_hunter_file_and_ended_launches_reviewer(self):
        self.assertEqual(step(hunter_tip="t2", hunter_file=True), Step("review", "round"))
        self.assertEqual(step(hunter_tip="t2", hunter_file=True, hunter_working=True), Step("wait", "round"))
        self.assertEqual(step(hunter_tip="t2", hunter_file=True, free=False), Step("wait", "round"))

    def test_hunter_without_file_blocks(self):
        s = step(hunter_tip="t2", hunter_ended=True)
        self.assertEqual(s.do, "block")
        self.assertIn("hunter ended without its findings file", s.text)
        self.assertEqual(step(hunter_tip="t2"), Step("wait", "round"))

    def test_reviewer_without_verdict_blocks(self):
        s = step(hunter_tip="t2", reviewer_tip="t2", reviewer_ended=True)
        self.assertEqual(s.do, "block")
        self.assertIn("reviewer ended without a verdict", s.text)
        self.assertEqual(step(hunter_tip="t2", reviewer_tip="t2"), Step("wait", "round"))

    def test_open_findings_go_back_once(self):
        s = review_step(seen(verdict_tip="t2"), {"open": 1, "round": 1, "file": "r.md"}, 2)
        self.assertEqual(s.do, "back")
        self.assertTrue(s.text.startswith("The review of your branch (round 1) leaves 1 open: read r.md."), s.text)
        for v in ({"open": 0, "round": 1}, {"open": 1, "round": 2}, {"open": 1, "round": 1, "returned": True}):
            self.assertEqual(review_step(seen(verdict_tip="t2"), v, 2), Step("go"), v)

    def test_a_clean_merge_or_a_closed_review_holds(self):
        v = {"open": 0, "round": 1}
        self.assertEqual(review_step(seen(verdict_tip="t1", merge="clean"), v, 2), Step("hold"))
        self.assertEqual(review_step(seen(verdict_tip="t1", review_for="none"), v, 2), Step("hold"))
        self.assertEqual(review_step(seen(verdict_tip="t1", review_for="none", merge="resolved", auto="T"), v, 2), Step("hold"))
        self.assertEqual(step(merge="clean"), Step("hunt", "round"))

    def test_a_review_closes_after_the_last_round(self):
        for v in ({"open": 1, "round": 2}, {"open": 0, "round": 2}):
            self.assertTrue(closes_review(v, 2), v)
        for v in ({"open": 0, "round": 1}, {"open": 1, "round": 1}):
            self.assertFalse(closes_review(v, 2), v)

    def test_reject_and_conflict_launch_with_their_base(self):
        self.assertEqual(step(verdict_tip="t1", review_for="reject"), Step("hunt", "reject", "t1"))
        self.assertEqual(step(verdict_tip="t1", review_for="conflict", merge="resolved", auto="T"), Step("hunt", "conflict", "T"))
        self.assertEqual(step(verdict_tip="t1", review_for="conflict"), Step("hunt", "conflict", ""))
        self.assertEqual(step(verdict_tip="t1", review_for="conflict", merge="clean"), Step("hold"))
        self.assertEqual(step(verdict_tip="t1", review_for="reject", hunter_tip="t2", hunter_file=True), Step("review", "reject", "t1"))


class Check(unittest.TestCase):
    def test_pending_ci_waits(self):
        self.assertTrue(waits_for_ci("pending"))
        for ci in ("pass", "fail", ""):
            self.assertFalse(waits_for_ci(ci), ci)

    def test_moved_branch_is_checked_again(self):
        c = check(ci="fail")
        self.assertTrue(checks_again(c, "h2", "m", None))
        self.assertTrue(checks_again(c, "h", "m2", None))
        self.assertTrue(checks_again(c, "h", "m", "pass"))
        self.assertFalse(checks_again(c, "h", "m", "fail"))
        self.assertFalse(checks_again(check(ci="pass"), "h", "m", None))

    def test_conflict_goes_back(self):
        self.assertEqual(after_check(check(conflict=True), "medium", False), "conflict")
        self.assertEqual(conflict_note("main").count("main"), 2)

    def test_conflict_waits_unless_someone_wants_the_merge(self):
        self.assertEqual(after_check(check(conflict=True), "manual", False), "owner")
        self.assertEqual(after_check(check(conflict=True, risk="high"), "medium", False), "owner")
        self.assertEqual(after_check(check(conflict=True), "low", False), "conflict")
        self.assertEqual(after_check(check(conflict=True, risk="high"), "manual", False, "owner"), "conflict")
        self.assertEqual(after_check(check(risk="high"), "manual", False, "owner"), "merge")
        self.assertEqual(after_check(check(risk="high"), "manual", False, "owner (panel)"), "merge")  # the panel's accept
        self.assertEqual(after_check(check(review="open after round 2: 1"), "manual", False, "owner"), "owner")
        self.assertEqual(after_check(check(tests="fail"), "manual", False, "owner"), "owner")
        self.assertEqual(after_check(check(ci="fail"), "manual", True, "owner"), "owner")
        self.assertEqual(after_check(check(risk="high"), "low", False, "autonomy low"), "owner")  # high never merges alone
        self.assertEqual(after_check(check(), "low", False, "autonomy low"), "merge")

    def test_autonomy_merges_alone(self):
        self.assertEqual(after_check(check(), "low", False), "merge")
        self.assertEqual(after_check(check(risk="medium"), "medium", False), "merge")
        self.assertEqual(after_check(check(ci="pass"), "low", True), "merge")

    def test_everything_else_waits_for_the_owner(self):
        rows = [
            (check(), "manual", False),
            (check(risk="high"), "medium", False),
            (check(risk="medium"), "low", False),
            (check(review="open after round 2: 1"), "medium", False),
            (check(ci="fail"), "low", True),
            (check(ci=""), "low", True),
        ]
        for c, autonomy, has_ci in rows:
            self.assertEqual(after_check(c, autonomy, has_ci), "owner", (c, autonomy, has_ci))


ALLOWED = {"collections.abc", "dataclasses", "typing", ".risk"}
FORBIDDEN_CALLS = {"open", "exec", "eval", "compile", "__import__"}


def impurities(source: str) -> list[str]:
    """What in this source could run a program or touch a file: an import outside ALLOWED, or a call to open/exec/eval."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [f"import {a.name}" for a in node.names if a.name not in ALLOWED]
        elif isinstance(node, ast.ImportFrom):
            name = "." * node.level + (node.module or "")
            if name not in ALLOWED:
                found.append(f"from {name} import ...")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_CALLS:
            found.append(f"{node.func.id}()")
    return found


class Pure(unittest.TestCase):
    def test_programs_and_files_are_caught(self):
        for source in ("import subprocess", "from pathlib import Path", "from . import proc", "import os", 'x = open("f")'):
            self.assertTrue(impurities(source), source)

    def test_allowed_imports_pass(self):
        source = "from collections.abc import Mapping\nfrom dataclasses import dataclass\nfrom typing import Any\nfrom .risk import allows\n"
        self.assertEqual(impurities(source), [])

    def test_decide_is_pure(self):
        self.assertEqual(impurities((ROOT / "fm" / "decide.py").read_text()), [])


if __name__ == "__main__":
    unittest.main()
