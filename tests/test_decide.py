"""The loop's decisions (fm/core/rules/decide.py): one test per row of DESIGN.md's table "What the loop does with a task in
review", then the queue, the retry, the provider's config and main's tests; none launches a program. `Pure` keeps the module free of programs and files."""

import ast
from typing import Any

from helpers import ROOT

from fm.core.rules import decide
from fm.core.rules.decide import (
    MergeConfig,
    Retry,
    Seen,
    Step,
    after_check,
    checks_again,
    closes_review,
    conflict_note,
    failing_note,
    main_tests_due,
    merge_gate,
    provider_error,
    queue_full,
    queue_order,
    queue_turn,
    ran_on,
    retry_step,
    review_step,
    waits_for_ci,
)


def seen(**kw: Any) -> Seen:
    base: dict[str, Any] = {"head": "t2", "verdict_tip": "", "hunter_tip": "", "reviewer_tip": "", "free": True}
    flags = {"hunter_file": False, "hunter_working": False, "hunter_ended": False, "reviewer_ended": False}
    return Seen(**{**base, **flags, **kw})


def check(**kw: Any) -> dict[str, Any]:
    return {"risk": "low", "review": "clean", "ci": "", "conflict": False, "head": "h", "main": "m", **kw}


def step(**kw: Any) -> Step:
    return review_step(seen(**kw), {}, 2)


class TestReview:
    def test_no_hunter_launches_it(self):
        assert step() == Step("hunt", "round")
        assert step(free=False) == Step("wait", "round")
        assert step(hunter_tip="t1", reviewer_tip="t1") == Step("hunt", "round")

    def test_hunter_file_and_ended_launches_reviewer(self):
        assert step(hunter_tip="t2", hunter_file=True) == Step("review", "round")
        assert step(hunter_tip="t2", hunter_file=True, hunter_working=True) == Step("wait", "round")
        assert step(hunter_tip="t2", hunter_file=True, free=False) == Step("wait", "round")

    def test_hunter_without_file_blocks(self):
        s = step(hunter_tip="t2", hunter_ended=True)
        assert s.do == "block"
        assert "hunter ended without its findings file" in s.text
        assert step(hunter_tip="t2") == Step("wait", "round")

    def test_reviewer_without_verdict_blocks(self):
        s = step(hunter_tip="t2", reviewer_tip="t2", reviewer_ended=True)
        assert s.do == "block"
        assert "reviewer ended without a verdict" in s.text
        assert step(hunter_tip="t2", reviewer_tip="t2") == Step("wait", "round")

    def test_open_findings_go_back_once(self):
        s = review_step(seen(verdict_tip="t2"), {"open": 1, "round": 1, "file": "r.md"}, 2)
        assert s.do == "back"
        assert s.text.startswith("The review of your branch (round 1) leaves 1 open: read r.md."), s.text
        for v in ({"open": 0, "round": 1}, {"open": 1, "round": 2}, {"open": 1, "round": 1, "returned": True}):
            assert review_step(seen(verdict_tip="t2"), v, 2) == Step("go"), v

    def test_a_clean_merge_or_a_closed_review_holds(self):
        v = {"open": 0, "round": 1}
        assert review_step(seen(verdict_tip="t1", merge="clean"), v, 2) == Step("hold")
        assert review_step(seen(verdict_tip="t1", review_for="none"), v, 2) == Step("hold")
        assert review_step(seen(verdict_tip="t1", review_for="none", merge="resolved", auto="T"), v, 2) == Step("hold")
        assert step(merge="clean") == Step("hunt", "round")

    def test_a_review_closes_after_the_last_round(self):
        for v in ({"open": 1, "round": 2}, {"open": 0, "round": 2}):
            assert closes_review(v, 2), v
        for v in ({"open": 0, "round": 1}, {"open": 1, "round": 1}):
            assert not closes_review(v, 2), v

    def test_reject_and_conflict_launch_with_their_base(self):
        assert step(verdict_tip="t1", review_for="reject") == Step("hunt", "reject", "t1")
        assert step(verdict_tip="t1", review_for="conflict", merge="resolved", auto="T") == Step("hunt", "conflict", "T")
        assert step(verdict_tip="t1", review_for="conflict") == Step("hunt", "conflict", "")
        assert step(verdict_tip="t1", review_for="conflict", merge="clean") == Step("hold")
        assert step(verdict_tip="t1", review_for="reject", hunter_tip="t2", hunter_file=True) == Step("review", "reject", "t1")


class TestCheck:
    def test_pending_ci_waits(self):
        assert waits_for_ci("pending")
        for ci in ("pass", "fail", ""):
            assert not waits_for_ci(ci), ci

    def test_moved_branch_is_checked_again(self):
        c = check(ci="fail")
        assert checks_again(c, "h2", "m", None)
        assert checks_again(c, "h", "m2", None)
        assert checks_again(c, "h", "m", "pass")
        assert not checks_again(c, "h", "m", "fail")
        assert not checks_again(check(ci="pass"), "h", "m", None)

    def test_conflict_goes_back(self):
        assert after_check(check(conflict=True), "medium", False) == "conflict"
        assert conflict_note("main").count("main") == 2

    def test_conflict_waits_unless_someone_wants_the_merge(self):
        assert after_check(check(conflict=True), "manual", False) == "owner"
        assert after_check(check(conflict=True, risk="high"), "medium", False) == "owner"
        assert after_check(check(conflict=True), "low", False) == "conflict"
        assert after_check(check(conflict=True, risk="high"), "manual", False, "owner") == "conflict"
        assert after_check(check(risk="high"), "manual", False, "owner") == "merge"
        assert after_check(check(risk="high"), "manual", False, "owner (panel)") == "merge"  # the panel's accept
        assert after_check(check(review="open after round 2: 1"), "manual", False, "owner") == "owner"
        assert after_check(check(tests="fail"), "manual", False, "owner", failed=2) == "owner"
        assert after_check(check(ci="fail"), "manual", True, "owner", failed=2) == "owner"
        assert after_check(check(risk="high"), "low", False, "autonomy low") == "owner"  # high never merges alone
        assert after_check(check(), "low", False, "autonomy low") == "merge"

    def test_failing_tests_or_ci_go_back_up_to_the_cap(self):
        rows = [(check(tests="fail"), "manual", False, ""), (check(ci="fail"), "low", True, ""), (check(tests="fail", conflict=True), "low", False, "owner")]
        for c, autonomy, has_ci, resolving in rows:
            assert after_check(c, autonomy, has_ci, resolving) == "failing", c
            assert after_check(c, autonomy, has_ci, resolving, failed=2) == "owner", c
        assert after_check(check(tests="pass", ci="pass"), "low", True, failed=2) == "merge"

    def test_the_fix_of_a_failure_gets_a_review_of_only_that_fix(self):
        assert step(verdict_tip="t1", review_for="failing") == Step("hunt", "failing", "t1")

    def test_failing_note_names_the_output_and_the_checks(self):
        note = failing_note(check(tests="fail", tests_output="L9\nL10"), "https://pr/1")
        assert "L9\nL10" in note
        note = failing_note(check(tests="pass", ci="fail", failed_checks=[{"name": "lint", "url": "https://ci/lint"}]), "https://pr/1")
        assert "lint: https://ci/lint" in note
        assert "test command" not in note
        assert "https://pr/1" in failing_note(check(tests="pass", ci="fail", failed_checks=[]), "https://pr/1")
        for ci in ("", "fail"):
            assert "The tests fail and print nothing" in failing_note(check(tests="fail", tests_output="", ci=ci), ""), ci

    def test_autonomy_merges_alone(self):
        assert after_check(check(), "low", False) == "merge"
        assert after_check(check(risk="medium"), "medium", False) == "merge"
        assert after_check(check(ci="pass"), "low", True) == "merge"

    def test_everything_else_waits_for_the_owner(self):
        rows = [
            (check(), "manual", False),
            (check(risk="high"), "medium", False),
            (check(risk="medium"), "low", False),
            (check(review="open after round 2: 1"), "medium", False),
            (check(ci=""), "low", True),
        ]
        for c, autonomy, has_ci in rows:
            assert after_check(c, autonomy, has_ci) == "owner", (c, autonomy, has_ci)
        assert after_check(check(ci="fail"), "low", True, failed=2) == "owner"  # failing, past its returns


class TestMergeGate:
    """At the merge step (FN-113): merge, check it merged with main, back to its agent, or the owner decides."""

    def test_the_table(self):
        merged = check(merged_with="m", tests="pass")
        rows = [
            (check(), "h", "m", 0, True, "merge"),  # no test command or a conflict: as today
            (merged, "h2", "m", 0, False, "merge"),  # the branch moved: the merge step refuses it itself
            (merged, "h", "m", 0, False, "merge"),  # nothing moved since a passing merged check
            (check(merged_with="m", tests="fail"), "h", "m", 0, False, "failing"),
            (check(merged_with="m", tests="fail"), "h", "m", 1, False, "failing"),
            (check(merged_with="m", tests="fail"), "h", "m", 2, False, "owner"),
            (merged, "h", "m2", 0, False, "check"),  # main moved after a passing merged check
            (check(tests="pass"), "h", "m", 0, False, "check"),  # only checked on the branch alone
        ]
        for c, head, main, failed, as_today, want in rows:
            assert merge_gate(c, head, main, failed, as_today) == want, (c, head, main, failed, as_today)

    def test_what_the_check_ran_on(self):
        assert ran_on(check()) == "branch alone"
        assert ran_on(check(merged_with="abcdef1234567")) == "merged with main abcdef123"

    def test_a_merged_failure_says_so(self):
        note = failing_note(check(tests="fail", tests_output="L9", merged_with="abcdef1234567"), "")
        for s in ("abcdef123", "not on its branch alone", "L9"):
            assert s in note
        assert "not on its branch alone" not in failing_note(check(tests="fail", tests_output="L9"), "")


class TestQueue:
    def test_accepted_merge_first_then_checks_in_order(self):
        tasks = [
            {"id": "FN-1", "status": "in review"},
            {"id": "FN-2", "status": "in review", "accepted": {"by": "o"}},
            {"id": "FN-3", "status": "in review", "blocked": "why"},
            {"id": "FN-4", "status": "in progress"},
            {"id": "FN-5", "status": "in review"},
        ]
        assert queue_order(tasks) == [("FN-2", "merge"), ("FN-1", "check"), ("FN-5", "check")]

    def test_blocked_or_not_in_review_has_no_turn(self):
        assert queue_turn({"id": "FN-1", "status": "in review", "accepted": {"by": "o"}, "blocked": "why"}) == ""
        assert queue_turn({"id": "FN-1", "status": "done"}) == ""

    def test_one_check_per_tick(self):
        assert (queue_full(0), queue_full(1)) == (False, True)


class TestRetry:
    def test_retries_twice_saying_each_new_error_once(self):
        assert retry_step(0, "", "e") == Retry(False, 1, True)
        assert retry_step(1, "e", "e") == Retry(False, 2, False)
        assert retry_step(1, "e", "f") == Retry(False, 2, True)

    def test_the_third_in_a_row_blocks(self):
        assert retry_step(2, "e", "e") == Retry(True, 0, False)
        assert retry_step(2, "", "e").block


class TestTestsOrCi:
    def test_ci_stands_in_only_without_a_test_command_and_with_a_result(self):
        rows = [
            (("none", "pass"), "pass"),
            (("none", "fail"), "fail"),
            (("none", "none"), "none"),
            (("none", "pending"), "none"),
            (("pass", "fail"), "pass"),
            (("fail", "pass"), "fail"),
        ]
        for args, out in rows:
            assert decide.tests_or_ci(*args) == out, args


class TestAsToday:
    def test_without_tests_or_with_a_conflict(self):
        assert decide.as_today(False, False) and decide.as_today(False, True) and decide.as_today(True, True)
        assert not decide.as_today(True, False)


class TestProviderError:
    MERGES = {"local": ("squash", "merge"), "github": ("squash", "merge", "rebase")}

    def error(self, provider: str, strategy: str, pr: str) -> str:
        return provider_error(MergeConfig(provider, strategy, pr), self.MERGES, ("squash", "merge", "rebase"), ("draft", "done"))

    def test_valid(self):
        assert self.error("local", "squash", "draft") == ""
        assert self.error("github", "rebase", "draft") == ""

    def test_each_bad_value_is_named(self):
        rows = [
            (("gitlab", "squash", "draft"), "provider = 'gitlab'"),
            (("local", "squash", "never"), "pr = 'never'"),
            (("local", "bogus", "draft"), "strategy = 'bogus'"),
            (("local", "rebase", "draft"), "strategy = 'rebase'"),
        ]
        for args, named in rows:
            assert named in self.error(*args), args


class TestMainTestsDue:
    def test_due_when_main_moved_or_the_last_run_is_old(self):
        assert main_tests_due(None, "h", None, 30)
        assert not main_tests_due("h", "h", 600, 30)
        assert main_tests_due("h", "h", 1801, 30)
        assert main_tests_due("h", "h", None, 30)
        assert main_tests_due("h2", "h", 1, 30)


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


class TestPure:
    def test_programs_and_files_are_caught(self):
        for source in ("import subprocess", "from pathlib import Path", "from . import proc", "import os", 'x = open("f")'):
            assert impurities(source), source

    def test_allowed_imports_pass(self):
        source = "from collections.abc import Mapping\nfrom dataclasses import dataclass\nfrom typing import Any\nfrom .risk import allows\n"
        assert impurities(source) == []

    def test_decide_is_pure(self):
        assert impurities((ROOT / "fm" / "core" / "rules" / "decide.py").read_text()) == []
