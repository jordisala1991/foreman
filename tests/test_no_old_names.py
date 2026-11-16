"""The glossary's names, with no compatibility: an old flag, environment variable, config section or stored key is
not read."""

import argparse
import os
import re
import subprocess
from pathlib import Path
from unittest import mock

import pytest
from helpers import fm

from foreman import launcher, util
from foreman.core.oversight import costs, guard
from foreman.core.project import registry
from foreman.core.project.config import Project
from foreman.core.review import describe, evidence, inline
from foreman.core.tasks import backlog
from foreman.services import lock
from foreman.ui.cli import common
from foreman.ui.panel import server

# The old names, written from parts so a scan for them finds nothing outside GLOSSARY.md.
OLD_ENV = "FM_" + "IS" + "SUE"
OLD_FLAG = "--" + "cla" + "im"
OLD_SECTION = "inte" + "grate"
OLD_KEY = "is" + "sues"


class TestOldNames:
    @pytest.fixture(autouse=True)
    def set_up(self, request, env) -> None:
        self.e = env
        patch = mock.patch.dict(os.environ)
        patch.start()
        request.addfinalizer(patch.stop)
        for k in ("FOREMAN_TASK", "FM_TASK", OLD_ENV):
            os.environ.pop(k, None)

    def test_old_flag_fails(self):
        with pytest.raises(SystemExit) as r:
            fm(self.e.home, "add", "t", OLD_FLAG, "x")
        assert r.value.code == 2
        tid = fm(self.e.home, "add", "t", "--goal", "x").strip()
        assert self.e.task(tid)["acceptance"]["goal"] == "x"

    @pytest.mark.parametrize("old", [OLD_ENV, "FM_TASK"])
    def test_old_env_is_not_read(self, old):
        tid = backlog.add(self.e.p, "t", criteria="c")
        os.environ[old] = tid
        with pytest.raises(SystemExit) as r:
            fm(self.e.home, "block", "why")
        assert "FOREMAN_TASK" in str(r.value.code)
        os.environ["FOREMAN_TASK"] = tid
        fm(self.e.home, "block", "why")
        assert self.e.task(tid)["blocked"] == "why"

    def test_old_config_section_is_not_read(self):
        toml = self.e.home / "foreman.toml"
        toml.write_text(f'[{OLD_SECTION}]\nautonomy = "medium"\n')
        assert Project.load(self.e.home).get("merge", "autonomy") == "manual"
        toml.write_text('[merge]\nautonomy = "low"\n')
        assert Project.load(self.e.home).get("merge", "autonomy") == "low"

    def test_old_stored_key_is_not_read(self):
        util.write_json(self.e.p.backlog_file, {"schema_version": 5, OLD_KEY: [{"id": "TY-1", "title": "t", "status": "backlog"}]})
        assert backlog.tasks(backlog.load(self.e.p)) == []


OLD_FILE = "fm" + ".toml"
OLD_PLUGIN = "fm" + "@" + "fm"


class TestOldInstallNames:
    """What foreman installs and writes says foreman: the old variables, files, folders, jobs, plugin and cookie are not read."""

    @pytest.fixture(autouse=True)
    def set_up(self, request, env) -> None:
        self.e = env
        patch = mock.patch.dict(os.environ)
        patch.start()
        request.addfinalizer(patch.stop)
        for k in [k for k in os.environ if k.startswith(("FOREMAN_", "FM_"))]:
            del os.environ[k]
        self.cwd = Path.cwd()
        request.addfinalizer(lambda: os.chdir(self.cwd))

    def outside(self) -> Path:
        os.chdir(self.e.tmp)
        return self.e.tmp

    @pytest.mark.parametrize(
        ("name", "value", "read", "default"),
        [
            ("FM_REGISTRY", "/x/r.json", registry.registry_file, lambda: Path.home() / ".config/foreman/projects.json"),
            ("FM_LOCKS", "/x/l", lock.root, lambda: Path.home() / ".local/state/foreman/locks"),
            ("FM_CLAUDE_PROJECTS", "/x/c", costs.projects_dir, lambda: Path.home() / ".claude/projects"),
            ("FM_PINNED", "/x/v1", launcher.pinned, lambda: ""),
            ("FM_PINNED", "/x/v1", launcher.stale, lambda: ""),
        ],
    )
    def test_old_variables_are_not_read(self, name, value, read, default):
        os.environ[name] = value
        assert read() == default()

    @pytest.mark.parametrize("name", ["FM_HOME", "FM_PROJECT"])
    def test_old_home_and_project_variables_are_not_read(self, name):
        os.environ[name] = str(self.e.home) if name == "FM_HOME" else self.e.p.name
        self.outside()
        with pytest.raises(SystemExit) as r:
            common.home(argparse.Namespace(project=None, home=None))
        assert "no project here" in str(r.value.code)

    def test_old_project_file_is_not_read(self):
        h = self.e.tmp / "old-home"
        h.mkdir()
        (h / OLD_FILE).write_text('[merge]\nautonomy = "low"\n')
        assert Project.load(h).get("merge", "autonomy") == "manual"
        (h / "foreman.toml").write_text('[merge]\nautonomy = "low"\n')
        assert Project.load(h).get("merge", "autonomy") == "low"

    def test_old_project_file_does_not_make_a_home_here(self):
        h = self.e.tmp / "old-here"
        h.mkdir()
        (h / OLD_FILE).write_text("[project]\n")
        os.chdir(h)
        with pytest.raises(SystemExit) as r:
            common.here()
        assert "no project here" in str(r.value.code)
        (h / "foreman.toml").write_text("[project]\n")
        assert common.here().home == h.resolve()

    def test_old_registry_and_token_are_not_read(self):
        old = Path.home() / ".config/fm"
        old.mkdir(parents=True)
        (old / "projects.json").write_text(f'["{self.e.home}"]')
        (old / "panel.token").write_text("old")
        assert registry.registered() == []
        assert server.load_token() != "old"
        assert (Path.home() / ".config/foreman/panel.token").exists()

    def test_old_plugin_name_is_not_read(self, request):
        f = launcher.record()
        f.parent.mkdir(parents=True, exist_ok=True)
        folder = self.e.tmp / "v1"
        (folder / "bin").mkdir(parents=True)
        (folder / "bin/foreman").write_text("")
        entry = {"scope": "user", "installPath": str(folder), "version": "1"}
        util.write_json(f, {"version": 2, "plugins": {OLD_PLUGIN: [entry]}})
        request.addfinalizer(lambda: f.unlink(missing_ok=True))
        with pytest.raises(launcher.LauncherError, match="foreman@foreman"):
            launcher.installed()

    def test_no_old_install_names_remain(self):
        root = Path(__file__).resolve().parent.parent
        skip = ("GLOSSARY.md", "tests/test_no_old_names.py", "uv.lock", "panel/package-lock.json", "plugins/foreman/panel/dist/")  # prefixes
        files = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True).stdout.split("\n")
        old = re.compile(
            r"\bFM_|fm\.toml|\.config/fm\b|\.local/state/fm\b|dev\.fm\.|\bfm@fm|fm_key|fm-(loop|panel|\{job\})\.log"
            r"|<!-- /?fm:|fm-link|fm-evidence|fm-project|^name = \x22fm\x22$"
        )
        found = []
        for name in files:
            if name and not name.startswith(skip) and (root / name).is_file():
                text = (root / name).read_text(encoding="utf-8", errors="ignore")
                found += [f"{name}:{n}" for n, line in enumerate(text.splitlines(), 1) if old.search(line)]
        assert found == []


class TestOldGitHubNames:
    """What foreman posts to GitHub and pushes there says foreman: the old markers and links are not ours."""

    def test_old_description_block_is_not_ours(self):
        body = "<!-- fm:description -->x<!-- /fm:description -->"
        assert describe.merge_body(body, "new") == body + "\n\n" + describe.wrap("new")
        assert describe.wrap("new").startswith("<!-- foreman:description -->")

    def test_old_link_is_not_resolved(self):
        text = "[`a.py:3`](fm-link:a.py%3A3) x"
        assert inline.resolve_links(text, "https://github.com/o/r", "SHA", {}) == text

    def test_the_evidence_branch(self):
        assert evidence.BRANCH == "foreman-evidence"
        sc = guard.Scope("/w", [Path("/w")], "foreman/ty-1", Path("/h/foreman.toml"), [])
        assert guard.check_bash("git push origin HEAD:foreman-evidence", sc) == "push-evidence"


def test_the_command_is_called_foreman() -> None:
    """The docs, briefs and instructions name the command `foreman`: macOS's /usr/bin/fm is not it."""
    root = Path(__file__).resolve().parent.parent
    texts = root / "plugins/foreman/foreman"
    docs = [root / "README.md", root / "DESIGN.md", root / "GLOSSARY.md", *(texts / "briefs").glob("*.md"), *(texts / "shared").rglob("*.md")]
    subcommands = "list|show|add|edit|go|ask|done|ready|block|reject|accept|cancel|health|launchd|loop|panel|init|lock"
    subcommands += "|verdict|diagnose|allow|unblock|metrics|project|session-context|guard"
    old = re.compile(rf"`fm ({subcommands})\b|(?<!usr/)bin/fm\b")
    found = [f"{d.name}:{n}" for d in docs for n, line in enumerate(d.read_text().splitlines(), 1) if old.search(line)]
    assert [f for f in found if not f.startswith("GLOSSARY.md")] == []
