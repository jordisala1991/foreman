import tempfile
from pathlib import Path

from foreman.core.project.config import Project
from foreman.core.rules import risk


def project(toml: str = "") -> Project:
    d = Path(tempfile.mkdtemp())
    if toml:
        (d / "foreman.toml").write_text(toml)
    return Project.load(d)


def facts(*paths: str, **kw) -> risk.Facts:
    base = dict(tests="pass", review="clean", files=[{"path": x, "added": 1, "deleted": 0} for x in paths], deleted=[], conflict=False, commits=1, ci="")
    return risk.Facts(**{**base, **kw})


class TestScore:
    def test_custom_high_path_names_label_and_path(self):
        p = project('[risk.high]\n"the merge path" = ["foreman/delivery.py"]\n')
        r, why = risk.score(p, facts("foreman/delivery.py"))
        assert r == "high"
        assert any("the merge path" in x and "foreman/delivery.py" in x and x.endswith("[diff]") for x in why)

    def test_default_ci_file_is_high(self):
        assert risk.score(project(), facts(".github/workflows/ci.yml"))[0] == "high"

    def test_file_count_is_not_a_reason(self):
        f = facts(*[f"m{n}.py" for n in range(12)])
        for x in f.files:
            x["added"] = 30
        assert risk.score(project(), f)[0] == "low"

    def test_open_review_is_a_reason_not_a_level(self):
        r, why = risk.score(project(), facts("app.py", review="open after round 2: H 1, M 0"))
        assert r == "low"
        assert any("[review]" in x for x in why)
        assert risk.score(project(), facts("app.py", review=""))[0] == "low"

    def test_review_does_not_change_high_impact(self):
        for rev in ("clean", "open after round 2: H 1, M 0"):
            assert risk.score(project(), facts(".github/x.yml", review=rev))[0] == "high"

    def test_dependencies_and_deletions_are_medium(self):
        assert risk.score(project(), facts("pyproject.toml"))[0] == "medium"
        assert risk.score(project(), facts("app.py", deleted=["old.py"]))[0] == "medium"

    def test_a_lock_file_alone_is_medium_dependencies(self):
        r, why = risk.score(project(), facts("uv.lock"))
        assert r == "medium"
        assert "dependencies" in why[0]

    def test_evidence_is_high_with_its_tag(self):
        rows = [
            (facts("a.py", tests="fail"), "[tests]"),
            (facts("a.py", ci="fail"), "[ci]"),
            (facts(commits=0), "[diff]"),
        ]
        for f, tag in rows:
            r, why = risk.score(project(), f)
            assert r == "high"
            assert why[0].endswith(tag), why

    def test_a_conflict_alone_is_low_and_comes_first(self):
        r, why = risk.score(project(), facts("a.py", conflict=True, review="open after round 2: 1"))
        assert r == "low"
        assert why[0].startswith("conflicts with main"), why

    def test_many_hits_give_three_reasons_high_first(self):
        f = facts(".github/x.yml", tests="fail", ci="fail", conflict=True, deleted=["o.py"])
        r, why = risk.score(project(), f)
        assert (r, len(why)) == ("high", 3)
        assert not any("deletes" in x for x in why)

    def test_high_and_medium_path_hits_capped_at_three(self):
        r, why = risk.score(project(), facts(".github/x.yml", "uv.lock", "pyproject.toml", tests="fail"))
        assert r == "high"
        assert len(why) <= 3

    def test_empty_high_table_means_no_path_impact(self):
        assert risk.score(project("[risk.high]\n"), facts(".github/x.yml"))[0] == "low"

    def test_path_matches(self):
        pats = project().get("risk", "high")["config, secrets or CI"]
        assert risk.path_matches(".github/workflows/ci.yml", pats)
        assert risk.path_matches("conf/foreman.toml", pats)
        assert not risk.path_matches("src/app.py", pats)


def sized(*pairs: tuple[str, int]) -> risk.Facts:
    return facts(*[p for p, _ in pairs]).__class__(**{**facts().__dict__, "files": [{"path": p, "added": n, "deleted": 0} for p, n in pairs]})


class TestLargeChange:
    def test_450_counted_lines_on_an_ordinary_file_is_medium_with_the_reason(self):
        r, why = risk.score(project(), sized(("app.py", 450)))
        assert r == "medium"
        assert "large change, reviews miss more past ~400 lines (450 lines) [diff]" in why

    def test_lines_under_tests_do_not_count(self):
        assert risk.score(project(), sized(("app.py", 150), ("tests/test_a.py", 300)))[0] == "low"

    def test_lock_and_generated_files_do_not_count(self):
        assert risk.score(project(), sized(("generated/x.py", 900)))[0] == "low"
        why = risk.score(project(), sized(("uv.lock", 900)))[1]  # medium as a dependency change, not as a large one
        assert not any("large change" in x for x in why), why

    def test_large_change_on_a_high_path_is_high_with_the_path_reason_first(self):
        r, why = risk.score(project(), sized((".github/x.yml", 450)))
        assert r == "high"
        assert why[0].startswith("touches config, secrets or CI")
        assert why[1].startswith("large change")

    def test_large_lines_is_configurable(self):
        assert risk.score(project("[risk]\nlarge_lines = 100\n"), sized(("app.py", 150)))[0] == "medium"


def repos_project(tables: str) -> Project:
    """A project of `[repos.*]` tables; `$T` stands for a temp folder holding the repos, the home is `$T/home`."""
    t = Path(tempfile.mkdtemp())
    (t / "home").mkdir()
    (t / "home" / "foreman.toml").write_text(tables.replace("$T", str(t)))
    return Project.load(t / "home")


TWO = '[repos.api]\npath = "$T/api"\n[repos.web]\npath = "$T/web"\n'


class TestPerRepo:
    def test_repo_table_gives_impact_and_label(self):
        p = repos_project(TWO + '[repos.api.risk.high]\nschema = ["migrations/"]\n')
        r, why = risk.score(p.for_task({"repo": "api"}), facts("migrations/1.sql"))
        assert r == "high"
        assert why[0].startswith("touches schema (migrations/1.sql)") and why[0].endswith("[diff, repo api]")
        assert risk.score(p.for_task({"repo": "web"}), facts("migrations/1.sql"))[0] == "low"

    def test_large_change_names_the_repo(self):
        p = repos_project(TWO + "[repos.web.risk]\nlarge_lines = 100\n").for_task({"repo": "web"})
        r, why = risk.score(p, sized(("app.py", 150)))
        assert r == "medium"
        assert why[0].startswith("large change") and why[0].endswith("[diff, repo web]")

    def test_publish_on_merge_is_high_first(self):
        p = repos_project('[repos.site]\npath = "$T/site"\npublish_on_merge = true\n' + TWO)
        site = p.for_task({"repo": "site"})
        r, why = risk.score(site, facts("app.py", publishes=site.publishes))
        assert (r, why[0]) == ("high", "merging publishes this repo (site)")
        api = p.for_task({"repo": "api"})
        r, why = risk.score(api, facts("app.py", publishes=api.publishes))
        assert r == "low" and not any("publishes" in x for x in why)
        why = risk.score(site, facts(".github/x.yml", tests="fail", ci="fail", publishes=site.publishes))[1]
        assert why[0] == "merging publishes this repo (site)"

    def test_one_repo_reasons_as_today(self):
        one = project()
        assert risk.score(one, facts(".github/x.yml"))[1][0].endswith("CI (.github/x.yml) [diff]")
        assert one.publishes == ""
        api = repos_project('[repos.api]\npath = "$T/api"\n').for_task({})
        assert risk.score(api, facts(".github/x.yml"))[1][0].endswith("CI (.github/x.yml) [diff]")
        assert repos_project('[repos.api]\npath = "$T/api"\npublish_on_merge = true\n').for_task({}).publishes == "api"
