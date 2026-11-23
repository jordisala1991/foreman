"""FN-115: the diff is checked against the plan's declared risk. The plan's three lines (Declared risk, Areas, Expected
size) reach `score` as a `Plan` on `Facts`: a declared level is raised, never lowered; an unannounced path or a diff over
3 times the expected size raises to at least medium; a plan without the lines scores as before."""

import json
import tempfile
from pathlib import Path
from typing import Any

from helpers import Env, fm
from test_review_rules import commit

from foreman.core.project.config import Project
from foreman.core.rules import risk
from foreman.core.rules.risk import Plan
from foreman.loop import tick
from foreman.providers import registry


def project() -> Project:
    return Project.load(Path(tempfile.mkdtemp()))


def facts(files: dict[str, int], plan: Plan | None = None) -> risk.Facts:
    """`files`: path → added lines."""
    base: dict[str, Any] = dict(
        tests="pass",
        review="clean",
        files=[{"path": x, "added": n, "deleted": 0} for x, n in files.items()],
        deleted=[],
        conflict=False,
        commits=1,
        ci="",
        publishes="",
    )
    return risk.Facts(**base, plan=plan or Plan())


def plan_reasons(why: list[str]) -> list[str]:
    return [x for x in why if "[plan" in x]


class TestDeclared:
    def test_declared_high_raises(self):
        assert risk.score(project(), facts({"app.py": 1}, Plan(risk="high", why="moves money"))) == (
            "high",
            ["declared high by the plan: moves money [plan]"],
        )

    def test_declared_medium_raises(self):
        r, why = risk.score(project(), facts({"app.py": 1}, Plan(risk="medium", why="x")))
        assert (r, why) == ("medium", ["declared medium by the plan: x [plan]"])

    def test_declared_low_never_lowers(self):
        assert risk.score(project(), facts({".github/x.yml": 1}, Plan(risk="low")))[0] == "high"

    def test_declared_medium_never_lowers(self):
        assert risk.score(project(), facts({".github/x.yml": 1}, Plan(risk="medium", why="x")))[0] == "high"

    def test_declared_low_adds_nothing(self):
        r, why = risk.score(project(), facts({"app.py": 1}, Plan(risk="low", why="x")))
        assert (r, plan_reasons(why)) == ("low", [])


class TestAreas:
    def test_unannounced_paths_raise_to_medium(self):
        f = facts({"app/a.py": 1, "lib/b.py": 1, "lib/c.py": 1, "lib/d.py": 1, "lib/e.py": 1}, Plan(areas=["app/"]))
        r, why = risk.score(project(), f)
        assert (r, plan_reasons(why)) == ("medium", ["touches lib/b.py, lib/c.py, lib/d.py the plan did not announce [plan+diff]"])

    def test_tests_need_not_be_announced(self):
        assert risk.score(project(), facts({"app/a.py": 1, "tests/test_a.py": 1}, Plan(areas=["app/"])))[0] == "low"

    def test_glob_announces(self):
        assert risk.score(project(), facts({"lib/b.py": 1}, Plan(areas=["lib/*.py"])))[0] == "low"


class TestSize:
    def test_over_three_times_raises_to_medium(self):
        r, why = risk.score(project(), facts({"app.py": 31}, Plan(size=10)))
        assert (r, plan_reasons(why)) == ("medium", ["31 lines, the plan expected 10 [plan+diff]"])

    def test_three_times_is_within(self):
        assert risk.score(project(), facts({"app.py": 30}, Plan(size=10)))[0] == "low"

    def test_tests_are_not_counted(self):
        assert risk.score(project(), facts({"app.py": 6, "tests/test_app.py": 25}, Plan(size=10)))[0] == "low"


class TestWithoutLines:
    def test_scored_as_today(self):
        for files in ({"app.py": 1}, {".github/x.yml": 1}):
            without = risk.Facts("pass", "clean", facts(files).files, [], False, 1, "", "")
            got = risk.score(project(), facts(files, Plan()))
            assert got == risk.score(project(), without)
            assert plan_reasons(got[1]) == []


class TestParse:
    def test_three_lines(self):
        text = "# Plan\n\n## Declared risk\nDeclared risk: Medium — why\nAreas: `a.py`, `b/`, \nExpected size: 40 lines (counted)\n"
        assert risk.parse_plan(text) == Plan("medium", "why", ["a.py", "b/"], 40)

    def test_heading_form_is_not_a_line(self):
        assert risk.parse_plan("## Declared risk\nlow: x\n") == Plan()


class TestCheck:
    """The flow of `tests/test_merged_check.py`, with a plan set on the task before its branch is checked."""

    def deliver(self, plan: str) -> tuple[Env, str]:
        """A task at autonomy low with this plan, committed, delivered and reviewed; the next tick checks it."""
        e = Env(test="true", autonomy="low", slots=4)
        tid = registry.backlog(e.p).add("x", status="ready", criteria="y")
        f = Path(tempfile.mkdtemp()) / "plan.md"
        f.write_text(plan)
        registry.backlog(e.p).update(tid, plan_file=str(f))
        tick.tick(e.p)
        commit(e.task(tid)["worktree"], "a.py", "a\n")
        registry.backlog(e.p).set_status(tid, "in review", report={"summary": "s", "confused": ""})
        e.review(tid)
        return e, tid

    def test_declared_high_stops_a_low_autonomy_merge(self):
        e, tid = self.deliver("# Plan\n\nDeclared risk: high — x\nAreas: a.py\nExpected size: 1 lines\n")
        tick.tick(e.p)
        tick.tick(e.p)
        c = json.loads(fm(e.home, "show", tid))["check"]
        assert (e.task(tid)["status"], c["risk"]) == ("in review", "high")
        assert c["reasons"][0] == "declared high by the plan: x [plan]"

    def test_plan_without_the_lines_merges(self):
        e, tid = self.deliver("# Plan\n\n## Declared risk\nhigh: x\n")
        tick.tick(e.p)
        tick.tick(e.p)
        assert e.task(tid)["status"] == "done"
