"""fm as a Claude Code plugin: a session in a folder of a registered project gets its instructions (the hook), the
skill gets the project's values from `fm project`, and nothing reaches a session elsewhere or an agent's."""

import contextlib
import functools
import io
import json
import os
import re
import tempfile
from pathlib import Path
from unittest import mock

import pytest
from helpers import ROOT, fm

from fm import launcher, util
from fm.core.project import plugin, registry
from fm.core.project.config import Project
from fm.core.tasks import backlog
from fm.services.claude import sessions
from fm.ui.cli import main
from fm.ui.cli import plugin as cli_plugin


class TestPluginTest:
    @pytest.fixture(autouse=True)
    def set_up(self, request) -> None:
        tmp = tempfile.TemporaryDirectory()
        request.addfinalizer(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        env = mock.patch.dict(os.environ, {"FM_REGISTRY": str(self.root / "projects.json")})
        env.start()
        request.addfinalizer(env.stop)
        for k in ("FM_HOME", "FM_PROJECT", "FM_TASK"):
            os.environ.pop(k, None)
        request.addfinalizer(functools.partial(os.chdir, Path.cwd()))
        self.home = self.root / "umbrella/alpha"
        self.repo = self.root / "umbrella/code"

    def alpha(self, project_lines: str = "", tail: str = "") -> Project:
        self.home.mkdir(parents=True, exist_ok=True)
        (self.repo / "src").mkdir(parents=True, exist_ok=True)
        (self.home / "fm.toml").write_text(
            f'[project]\nname = "alpha"\nprefix = "AL"\nmain = "trunk"\numbrella = "{self.root / "umbrella"}"\n{project_lines}\n[agents]\nslots = 3\n{tail}',
            encoding="utf-8",
        )
        util.write_json(self.home / "backlog.json", backlog.empty())
        registry.register(self.home)
        return Project.load(self.home)

    def context(self, cwd: Path, env: dict[str, str] | None = None) -> str:
        projects, broken = registry.load_all()
        return plugin.session_context(json.dumps({"cwd": str(cwd), "source": "startup"}), env or {}, [*projects, *broken])

    def test_a_session_in_any_folder_of_a_project_gets_its_instructions(self) -> None:
        self.alpha(f'repo = "{self.repo}"')
        for cwd in (self.home, self.repo, self.repo / "src", self.home / "worktrees", self.root / "umbrella"):
            out = json.loads(self.context(cwd))["hookSpecificOutput"]
            assert out["hookEventName"] == "SessionStart", cwd
            text = out["additionalContext"]
            for want in ("**alpha**", "AL-3", str(self.repo), "`trunk`", "(3 at once)", str(self.home)):
                assert want in text, cwd
            assert not re.search(r"\{(project|prefix|home|repos|main|slots|repo|fm)\}", text)

    def test_the_owners_rules_follow_the_instructions(self) -> None:
        self.alpha(f'repo = "{self.repo}"')
        without = json.loads(self.context(self.repo))["hookSpecificOutput"]["additionalContext"]
        assert "# fm: the project alpha" in without
        (self.home / "instructions.md").write_text("Habla en español.\n", encoding="utf-8")
        text = json.loads(self.context(self.repo))["hookSpecificOutput"]["additionalContext"]
        assert text.startswith(without)
        assert text.rstrip().endswith("Habla en español.")

    def test_a_folder_of_no_project_gets_nothing(self) -> None:
        self.alpha(f'repo = "{self.repo}"')
        other = self.root / "other"
        other.mkdir()
        assert self.context(other) == ""
        (other / "fm.toml").write_text('[project]\nname = "beta"\n', encoding="utf-8")  # not registered
        assert self.context(other) == ""

    def test_an_agent_session_gets_nothing(self) -> None:
        self.alpha(f'repo = "{self.repo}"')
        assert self.context(self.home, {"FM_TASK": "AL-1"}) == ""

    def test_a_folder_two_projects_claim_gets_nothing(self) -> None:
        self.alpha(f'repo = "{self.repo}"')
        beta = self.root / "beta"
        beta.mkdir()
        (beta / "fm.toml").write_text(f'[project]\nname = "beta"\nrepo = "{self.repo}"\n', encoding="utf-8")
        registry.register(beta)
        assert self.context(self.repo) == ""

    def test_bad_input_gets_nothing(self) -> None:
        p = self.alpha(f'repo = "{self.repo}"')
        for stdin in ("", "not json", "{}", '{"cwd": 3}', "[]"):
            assert plugin.session_context(stdin, {}, [p]) == "", stdin

    def test_a_failing_hook_prints_nothing_and_exits_zero(self) -> None:
        out = io.StringIO()
        with mock.patch.object(registry, "load_all", side_effect=RuntimeError("boom")), contextlib.redirect_stdout(out):
            main.main(["session-context"])
        assert out.getvalue() == ""

    def test_a_session_start_on_a_good_python_clears_the_python_note(self):
        note = registry.python_note_file()
        note.write_text("fm: needs Python 3.12 or newer\n")
        self.hook(self.root, self.root / "env")
        assert not note.exists()

    def hook(self, cwd: Path, env_file: Path) -> str:
        out = io.StringIO()
        stdin = io.StringIO(json.dumps({"cwd": str(cwd)}))
        with mock.patch("sys.stdin", stdin), mock.patch.dict(os.environ, {"CLAUDE_ENV_FILE": str(env_file)}), contextlib.redirect_stdout(out):
            cli_plugin.cmd_session_context(mock.Mock())
        return out.getvalue()

    def test_the_hook_prints_the_context_and_puts_fm_first_on_the_path(self) -> None:
        """macOS has its own /usr/bin/fm, ahead of the plugin's bin/ that Claude Code appends to PATH."""
        self.alpha(f'repo = "{self.repo}"')
        env_file = self.root / "env.sh"
        assert json.loads(self.hook(self.repo, env_file))["hookSpecificOutput"]["hookEventName"] == "SessionStart"
        assert env_file.read_text() == f'export PATH="{ROOT / "bin"}:$PATH"\n'
        env_file.unlink()
        assert self.hook(self.root, env_file) == ""
        assert not env_file.exists()

    def test_a_failing_path_write_still_gives_the_instructions(self) -> None:
        self.alpha(f'repo = "{self.repo}"')
        out = self.hook(self.repo, self.root / "no/such/dir/env.sh")
        assert "# fm: the project alpha" in json.loads(out)["hookSpecificOutput"]["additionalContext"]

    def test_a_new_home_adds_no_rules_until_the_owner_writes_them(self) -> None:
        self.home.mkdir(parents=True)
        fm(self.home, "init", "--prefix", "AL", "--repo", str(self.repo))
        assert (self.home / "instructions.md").is_file()
        assert "Rules of this project" not in plugin.instructions(Project.load(self.home))

    def test_project_prints_the_values(self) -> None:
        a, b = self.root / "a", self.root / "b"
        (a / "src").mkdir(parents=True)
        b.mkdir()
        self.alpha(tail=f'\n[repos.a]\npath = "{a}"\nmain = "trunk"\n[repos.b]\npath = "{b}"\n')
        os.chdir(a / "src")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main.main(["project"])
        assert out.getvalue() == f"project: alpha\nprefix: AL\nhome: {self.home}\nrepos: {a}, {b}\nmain: trunk\nslots: 3\n"

    def test_agents_launch_with_the_plugin_disabled(self) -> None:
        p = self.alpha(f'repo = "{self.repo}"')
        assert sessions.settings(p, "AL-1", "al-1")["enabledPlugins"]["fm@fm"] is False
        p = self.alpha(f'repo = "{self.repo}"', '[agents.settings]\nenabledPlugins = {"foreman@foreman" = false}\n')
        assert sessions.settings(p, "AL-1", "al-1")["enabledPlugins"] == {"fm@fm": False, "foreman@foreman": False}

    def test_agents_run_the_loops_fm(self) -> None:
        """Their PATH and their guard hook name the folder the loop runs from."""
        p = self.alpha(f'repo = "{self.repo}"')
        with mock.patch.object(launcher, "ROOT", self.root / "v1"):
            s = sessions.settings(p, "AL-1", "al-1")
        assert s["env"]["PATH"].startswith(f"{self.root / 'v1/bin'}{os.pathsep}")
        assert f"{self.root / 'v1/bin/fm'} guard" in s["hooks"]["PreToolUse"][0]["hooks"][0]["command"]

    def test_the_manifests_name_fm_and_declare_no_version(self) -> None:
        market = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text())
        manifest = json.loads((ROOT / ".claude-plugin/plugin.json").read_text())
        assert (market["name"], manifest["name"]) == ("fm", "fm")
        assert [(x["name"], x["source"]) for x in market["plugins"]] == [("fm", "./")]
        for data in (market, manifest):
            assert '"version"' not in json.dumps(data)
        assert (ROOT / manifest["skills"] / "define-task/SKILL.md").is_file()
        hook = json.loads((ROOT / "hooks/hooks.json").read_text())["hooks"]["SessionStart"][0]["hooks"][0]
        assert hook["command"] == '"${CLAUDE_PLUGIN_ROOT}/bin/fm" session-context'

    def test_the_skill_reads_the_values_from_fm(self) -> None:
        text = (plugin.SHARED / "skills/define-task/SKILL.md").read_text()
        assert '!`"${CLAUDE_PLUGIN_ROOT}/bin/fm" project`' in text
        assert "fm.toml" not in text
