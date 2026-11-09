import tempfile
import unittest
from pathlib import Path

from fm import risk
from fm.config import Project


def project(toml: str = "") -> Project:
    d = Path(tempfile.mkdtemp())
    if toml:
        (d / "fm.toml").write_text(toml)
    return Project.load(d)


def facts(*paths: str, **kw) -> risk.Facts:
    base = dict(tests="pass", review="clean", files=[{"path": x, "added": 1, "deleted": 0} for x in paths], deleted=[], conflict=False, commits=1, ci="")
    return risk.Facts(**{**base, **kw})


class Score(unittest.TestCase):
    def test_custom_high_path_names_label_and_path(self):
        p = project('[risk.high]\n"the merge path" = ["fm/integrate.py"]\n')
        r, why = risk.score(p, facts("fm/integrate.py"))
        self.assertEqual(r, "high")
        self.assertTrue(any("the merge path" in x and "fm/integrate.py" in x and x.endswith("[diff]") for x in why))

    def test_default_ci_file_is_high(self):
        self.assertEqual(risk.score(project(), facts(".github/workflows/ci.yml"))[0], "high")

    def test_file_count_is_not_a_reason(self):
        f = facts(*[f"m{n}.py" for n in range(12)])
        for x in f.files:
            x["added"] = 30
        self.assertEqual(risk.score(project(), f)[0], "low")

    def test_open_review_is_a_reason_not_a_level(self):
        r, why = risk.score(project(), facts("app.py", review="open after round 2: H 1, M 0"))
        self.assertEqual(r, "low")
        self.assertTrue(any("[review]" in x for x in why))
        self.assertEqual(risk.score(project(), facts("app.py", review=""))[0], "low")

    def test_review_does_not_change_high_impact(self):
        for rev in ("clean", "open after round 2: H 1, M 0"):
            self.assertEqual(risk.score(project(), facts(".github/x.yml", review=rev))[0], "high")

    def test_dependencies_and_deletions_are_medium(self):
        self.assertEqual(risk.score(project(), facts("pyproject.toml"))[0], "medium")
        self.assertEqual(risk.score(project(), facts("app.py", deleted=["old.py"]))[0], "medium")

    def test_evidence_is_high_with_its_tag(self):
        rows = [
            (facts("a.py", tests="fail"), "[tests]"),
            (facts("a.py", ci="fail"), "[ci]"),
            (facts("a.py", conflict=True), "[git]"),
            (facts(commits=0), "[diff]"),
        ]
        for f, tag in rows:
            r, why = risk.score(project(), f)
            self.assertEqual(r, "high")
            self.assertTrue(why[0].endswith(tag), why)

    def test_many_hits_give_three_reasons_high_first(self):
        f = facts(".github/x.yml", tests="fail", ci="fail", conflict=True, deleted=["o.py"])
        r, why = risk.score(project(), f)
        self.assertEqual((r, len(why)), ("high", 3))
        self.assertFalse(any("deletes" in x for x in why))

    def test_high_and_medium_path_hits_capped_at_three(self):
        r, why = risk.score(project(), facts(".github/x.yml", "uv.lock", "pyproject.toml", tests="fail"))
        self.assertEqual(r, "high")
        self.assertLessEqual(len(why), 3)

    def test_empty_high_table_means_no_path_impact(self):
        self.assertEqual(risk.score(project("[risk.high]\n"), facts(".github/x.yml"))[0], "low")

    def test_path_matches(self):
        pats = project().get("risk", "high")["config, secrets or CI"]
        self.assertTrue(risk.path_matches(".github/workflows/ci.yml", pats))
        self.assertTrue(risk.path_matches("conf/fm.toml", pats))
        self.assertFalse(risk.path_matches("src/app.py", pats))


def sized(*pairs: tuple[str, int]) -> risk.Facts:
    return facts(*[p for p, _ in pairs]).__class__(**{**facts().__dict__, "files": [{"path": p, "added": n, "deleted": 0} for p, n in pairs]})


class LargeChange(unittest.TestCase):
    def test_450_counted_lines_on_an_ordinary_file_is_medium_with_the_reason(self):
        r, why = risk.score(project(), sized(("app.py", 450)))
        self.assertEqual(r, "medium")
        self.assertIn("large change, reviews miss more past ~400 lines (450 lines) [diff]", why)

    def test_lines_under_tests_do_not_count(self):
        self.assertEqual(risk.score(project(), sized(("app.py", 150), ("tests/test_a.py", 300)))[0], "low")

    def test_lock_and_generated_files_do_not_count(self):
        self.assertEqual(risk.score(project(), sized(("generated/x.py", 900)))[0], "low")
        self.assertEqual(risk.score(project("[risk.high]\n"), sized(("uv.lock", 900)))[0], "low")

    def test_large_change_on_a_high_path_is_high_with_the_path_reason_first(self):
        r, why = risk.score(project(), sized((".github/x.yml", 450)))
        self.assertEqual(r, "high")
        self.assertTrue(why[0].startswith("touches config, secrets or CI"))
        self.assertTrue(why[1].startswith("large change"))

    def test_large_lines_is_configurable(self):
        self.assertEqual(risk.score(project("[risk]\nlarge_lines = 100\n"), sized(("app.py", 150)))[0], "medium")
