"""A task has its repo: filed and edited with --repo, refused without one when Go needs it, shown by show, list and the panel."""

import json
import textwrap

import pytest
from helpers import Env, fm

from foreman.core.project.config import Project
from foreman.core.tasks import backlog
from foreman.ui.panel import actions, views


def use(env: Env, *keys: str) -> None:
    """The project becomes one with a `[repos.K]` table per key."""
    tables = ""
    for k in keys:
        (env.tmp / k).mkdir(exist_ok=True)
        tables += f'[repos.{k}]\npath = "{env.tmp / k}"\n'
    (env.home / "foreman.toml").write_text(f'[project]\nname = "toy"\nprefix = "TY"\nworktrees = "{env.tmp / "wt"}"\n' + textwrap.dedent(tables))
    env.p = Project.load(env.home)


def refused(env: Env, *argv: str) -> str:
    before = backlog.load(env.p)
    with pytest.raises(SystemExit) as e:
        fm(env.home, *argv)
    assert backlog.load(env.p) == before
    return str(e.value.code)


class TestTaskRepo:
    @pytest.fixture(autouse=True)
    def set_up(self, owner, env):
        self.e = env

    def shown(self, tid: str) -> dict:
        return json.loads(fm(self.e.home, "show", tid))

    def line(self, tid: str) -> str:
        return next(ln for ln in fm(self.e.home, "list").splitlines() if ln.startswith(tid))

    def add(self, *argv: str) -> str:
        return fm(self.e.home, "add", "n", *argv).strip()

    def test_add_and_edit_store_a_known_key_and_refuse_another_listing_the_keys(self):
        use(self.e, "app", "api")
        tid = self.add("--criteria", "a", "--repo", "api")
        assert self.e.task(tid)["repo"] == "api"
        fm(self.e.home, "edit", tid, "--repo", "app")
        assert self.e.task(tid)["repo"] == "app"
        for argv in (["add", "n", "--repo", "web"], ["edit", tid, "--repo", "web"]):
            msg = refused(self.e, *argv)
            assert "web" in msg and "app" in msg and "api" in msg

    def test_several_repos_add_with_criteria_and_go_refuse_a_task_without_a_repo(self):
        use(self.e, "app", "api")
        msg = refused(self.e, "add", "n", "--criteria", "a")
        assert "app|api" in msg
        tid = self.add("--criteria", "a", "--triage")
        assert self.e.task(tid)["status"] == "triage" and "repo" not in self.e.task(tid)
        assert "choose its repo first" in refused(self.e, "go", tid)
        assert self.e.task(tid)["status"] == "triage"
        fm(self.e.home, "edit", tid, "--repo", "api")
        fm(self.e.home, "go", tid)
        assert self.e.task(tid)["status"] == "backlog"
        assert self.add("--triage")

    def test_several_repos_a_chain_step_needs_its_repo_and_is_not_told_to_triage(self):
        use(self.e, "app", "api")
        P = backlog.add(self.e.p, "chain", criteria="a", chain={"lens": "rules", "max_steps": 5, "max_lines": 150})
        msg = refused(self.e, "add", "s", "--parent", P)
        assert "app|api" in msg and "--triage" not in msg
        tid = self.add("--parent", P, "--repo", "api")
        assert (self.e.task(tid)["status"], self.e.task(tid)["repo"]) == ("backlog", "api")

    def test_go_lets_in_a_task_that_has_subtasks_without_a_repo(self):
        use(self.e, "app", "api")
        parent = self.add("--criteria", "a", "--triage")
        fm(self.e.home, "add", "s", "--criteria", "a", "--repo", "api", "--parent", parent)
        fm(self.e.home, "go", parent)
        assert self.e.task(parent)["status"] == "backlog" and "repo" not in self.e.task(parent)

    def test_edit_repo_returns_a_planned_task_to_backlog_without_its_plan(self):
        use(self.e, "app", "api")
        tid = self.add("--criteria", "a", "--repo", "app")
        for status in ("ready", "backlog"):
            backlog.update(self.e.p, tid, status=status, plan_file="/x/p.md")
            fm(self.e.home, "edit", tid, "--repo", "api")
            t = self.e.task(tid)
            assert (t["status"], t["repo"], "plan_file" in t) == ("backlog", "api", False)
            fm(self.e.home, "edit", tid, "--repo", "app")
        backlog.update(self.e.p, tid, status="triage")
        fm(self.e.home, "edit", tid, "--repo", "api")
        assert self.e.task(tid)["status"] == "triage"
        backlog.update(self.e.p, tid, status="in progress")
        refused(self.e, "edit", tid, "--repo", "app")

    def test_show_list_and_the_panel_data_show_each_tasks_repo(self):
        use(self.e, "app", "api")
        one = self.add("--criteria", "a", "--repo", "api")
        parent = self.add("--triage")
        fm(self.e.home, "add", "s", "--criteria", "a", "--repo", "app", "--parent", parent)
        loose = self.add("--triage")
        assert [self.shown(t)["repo"] for t in (one, parent, loose)] == ["api", "-", "-"]
        assert self.line(one).split()[2] == "api"
        assert self.line(parent).split()[2] == "-"
        assert views.task(self.e.p, one)["task"]["repo"] == "api"
        assert {i["id"]: i["repo"] for i in views.state(self.e.p)["tasks"]}[one] == "api"
        assert views.state(self.e.p)["repos"] == ["app", "api"]

    def test_the_panel_files_in_triage_when_the_project_has_several_repos(self):
        use(self.e, "app", "api")
        tid = actions.add(self.e.p, json.dumps({"title": "t", "criteria": "a"}))["id"]
        assert self.e.task(tid)["status"] == "triage"

    def test_one_repo_project_shows_and_asks_nothing_and_refuses_every_key(self):
        tid = self.add("--criteria", "a")
        assert self.e.task(tid)["status"] == "backlog"
        fm(self.e.home, "go", tid)
        for argv in (["add", "n", "--repo", "x"], ["edit", tid, "--repo", "x"]):
            assert "this project has one repo" in refused(self.e, *argv)
        assert "repo" not in self.shown(tid)
        assert self.line(tid).split()[:3] == [tid, "backlog", "change"]
        s = views.state(self.e.p)
        assert "repos" not in s and not any("repo" in i for i in s["tasks"])
        assert views.state(self.e.p)["tasks"][0]["status"] == "backlog"
        assert actions.add(self.e.p, json.dumps({"title": "t", "criteria": "a"}))["id"]

    def test_one_repos_table_accepts_its_key_without_storing_it_and_refuses_another(self):
        use(self.e, "app")
        tid = self.add("--criteria", "a", "--repo", "app")
        assert "repo" not in self.e.task(tid) and self.e.task(tid)["status"] == "backlog"
        assert "app" in refused(self.e, "add", "n", "--repo", "web")
        assert "app" in refused(self.e, "edit", tid, "--repo", "web")
        backlog.update(self.e.p, tid, status="ready", plan_file="/x/p.md")
        assert "nothing to change" in fm(self.e.home, "edit", tid, "--repo", "app")
        assert (self.e.task(tid)["status"], self.e.task(tid)["plan_file"]) == ("ready", "/x/p.md")
        assert "no task" in refused(self.e, "edit", "TY-99", "--repo", "app")
        backlog.update(self.e.p, tid, status="in progress")
        assert "in progress" in refused(self.e, "edit", tid, "--repo", "app")
        backlog.update(self.e.p, tid, status="ready")
        assert "repo" not in self.shown(tid) and "repo" not in self.line(tid).split()
        assert "repos" not in views.state(self.e.p)
