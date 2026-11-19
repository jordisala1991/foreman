"""foreman.toml with [repos.<key>] tables: strict keys, repo settings over the project's, post_create placeholders, layout."""

import tempfile
from pathlib import Path

import pytest

from foreman.core.project import repos as rules
from foreman.core.project.config import Project, ProjectError


class TestReposTest:
    @pytest.fixture(autouse=True)
    def set_up(self, request: pytest.FixtureRequest) -> None:
        tmp = tempfile.TemporaryDirectory()
        request.addfinalizer(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        for d in ("api", "web", "home"):
            (self.root / d).mkdir()

    def write(self, text: str, home: Path | None = None, project: str = "") -> Path:
        home = home or self.root / "home"
        home.mkdir(parents=True, exist_ok=True)
        (home / "foreman.toml").write_text(f'[project]\nname = "shop"\nprefix = "SH"\n{project}\n{text}', encoding="utf-8")
        return home

    def load(self, text: str, home: Path | None = None, project: str = "") -> Project:
        return Project.load(self.write(text, home, project))

    def refused(self, text: str, *needles: str, home: Path | None = None, project: str = "") -> None:
        with pytest.raises(ProjectError) as cm:
            self.load(text, home, project)
        for n in needles:
            assert n in str(cm.value)

    def tables(self, extra_api: str = "", extra_web: str = "") -> str:
        return f'[repos.api]\npath = "{self.root / "api"}"\n{extra_api}\n[repos.web]\npath = "{self.root / "web"}"\n{extra_web}\n'

    def test_unknown_key_refused(self) -> None:
        self.refused(self.tables("bogus = 1"), "[repos.api]", "'bogus'")
        self.refused(self.tables("slots = 3"), "[repos.api]", "'slots'")
        self.refused(self.tables("[repos.api.merge]\nfoo = 1"), "[repos.api.merge]", "'foo'")
        self.refused(self.tables("[repos.api.risk]\nfoo = 1"), "[repos.api.risk]", "'foo'")
        self.refused(self.tables("merge = 1"), "[repos.api.merge] must be a table")

    def test_repo_setting_wins_key_by_key(self) -> None:
        text = '[merge]\nautonomy = "low"\nstrategy = "squash"\ntest = "make a"\n\n' + self.tables(
            '[repos.api.merge]\nstrategy = "merge"\ntest = "make b"\n[repos.api.risk]\nlarge_lines = 10\n'
        )
        repos = self.load(text).cfg["repos"]
        api, web = repos["api"], repos["web"]
        assert (api["merge"]["strategy"], api["merge"]["test"]) == ("merge", "make b")
        assert (api["merge"]["autonomy"], api["merge"]["pr"]) == ("low", "draft")
        assert (web["merge"]["strategy"], web["merge"]["test"]) == ("squash", "make a")
        assert (api["risk"]["large_lines"], web["risk"]["large_lines"]) == (10, 400)

    def test_repo_risk_table_replaces_whole(self) -> None:
        text = '[risk.high]\na = ["x/"]\nb = ["y/"]\n\n' + self.tables('[repos.api.risk.high]\nc = ["z/"]\n')
        p = self.load(text)
        api, web = p.cfg["repos"]["api"]["risk"], p.cfg["repos"]["web"]["risk"]
        assert api["high"] == {"c": ["z/"]}
        assert web["high"] == {"a": ["x/"], "b": ["y/"]}
        assert api["medium"] == p.cfg["risk"]["medium"]

    def test_post_create_placeholders(self) -> None:
        p = self.load(self.tables('post_create = ["cp {main}/a {wt}/"]'))
        assert p.cfg["repos"]["api"]["post_create"] == ["cp {main}/a {wt}/"]
        self.refused(self.tables('post_create = ["x {repo}"]'), "[repos.api]", "{repo}")
        self.refused(self.tables('post_create = ["x {}"]'), "[repos.api]", "{}")
        self.refused(self.tables("post_create = 3"), "[repos.api]", "post_create")
        self.refused(self.tables('post_create = ["echo {"]'), "[repos.api]", "post_create")
        self.refused(self.tables('post_create = ["echo }"]'), "[repos.api]", "post_create")

    def test_worktrees_must_be_a_path(self) -> None:
        self.refused(self.tables("worktrees = 3"), "[repos.api]", "worktrees")
        self.refused(self.tables(), "[project]", "worktrees", project="worktrees = 3")

    def test_layout_inside_repo_refused(self) -> None:
        api, web = self.root / "api", self.root / "web"
        home = api / "home"
        self.refused(self.tables(), str(home), "api", home=home)
        self.refused(self.tables(), "worktrees", "web", project=f'worktrees = "{web / "wt"}"')
        self.refused(self.tables(extra_web=f'worktrees = "{api / "wt"}"'), "worktrees", "api")

    def test_layout_inside_umbrella_accepted(self) -> None:
        shop = self.root / "shop"
        for d in ("api", "web", "home", "worktrees"):
            (shop / d).mkdir(parents=True)
        text = f'[repos.api]\npath = "{shop / "api"}"\n[repos.web]\npath = "{shop / "web"}"\n'
        project = f'umbrella = "{shop}"\nworktrees = "{shop / "worktrees"}"'
        p = self.load(text, home=shop / "home", project=project)
        assert list(p.cfg["repos"]) == ["api", "web"]
        self.refused(text, "api", home=shop / "api" / "home", project=project)

    def test_one_repo_as_today(self) -> None:
        api = self.root / "api"
        home = api / "home"
        p = Project.load(self.write("", home=home, project=f'repo = "{api}"'))
        assert p.repo == api
        text = f'[repos.api]\npath = "{api}"\n[repos.api.merge]\nstrategy = "merge"\n'
        p = self.load(text, home=home)
        assert p.repo == api
        assert p.get("merge", "strategy") == "squash"
        p = self.load(f'[repos.api]\npath = "{api}"\nmain = 3\n', home=home)
        assert p.get("project", "main") == "3"

    def test_for_task_is_the_tasks_repo(self) -> None:
        text = self.tables(extra_web=f'worktrees = "{self.root / "webwts"}"\n[repos.web.merge]\ntest = "make web"')
        p = self.load(text, project=f'worktrees = "{self.root / "wts"}"')
        web = p.for_task({"repo": "web"})
        assert web.repo == self.root / "web"
        assert web.get("merge", "test") == "make web"
        assert web.path("worktrees") == self.root / "webwts"
        assert (web.home, web.get("agents", "slots")) == (p.home, p.get("agents", "slots"))
        assert p.get("merge", "test") == ""  # the project itself is unchanged

    def test_for_task_without_repo_is_the_first(self) -> None:
        p = self.load(self.tables(), project=f'worktrees = "{self.root / "wts"}"')
        assert p.for_task({}).repo == self.root / "api"

    def test_for_task_unknown_repo_refused(self) -> None:
        p = self.load(self.tables(), project=f'worktrees = "{self.root / "wts"}"')
        with pytest.raises(ProjectError, match="gone"):
            p.for_task({"repo": "gone"})

    def test_for_task_one_repo_form_is_the_project(self) -> None:
        p = self.load("", project=f'repo = "{self.root / "api"}"')
        assert p.for_task({"repo": "x"}).repo == self.root / "api"

    def test_remote_joins_the_view(self) -> None:
        p = self.load(self.tables(extra_web='remote = "none"'), project=f'worktrees = "{self.root / "wts"}"')
        assert p.for_task({"repo": "web"}).remote == "none"
        assert p.for_task({"repo": "api"}).remote == "origin"
        assert self.load("", project=f'repo = "{self.root / "api"}"').remote == "origin"
        self.refused(self.tables(extra_web="remote = 3"), "[repos.web] remote is a remote name")


def test_stray_cuts_at_the_kept_out_component() -> None:
    paths = [".claude/settings.json", ".claude/x", "docs/CLAUDE.md", "node_modules/", "src/a.py", "a/foreman.toml/b", "NOTCLAUDE.md", ".claude/"]
    assert rules.stray(paths) == [".claude", "docs/CLAUDE.md", "a/foreman.toml"]
