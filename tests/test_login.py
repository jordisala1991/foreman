import json
import os
import plistlib
from datetime import UTC, datetime, timedelta
from unittest import mock

import pytest
from helpers import Env, fm, install_fm, version_folder

from foreman import launcher, proc
from foreman.core.tasks import backlog
from foreman.loop import tick
from foreman.services import launchd
from foreman.services.claude import login, sessions
from foreman.steps.health import checks


class TestLogin:
    @pytest.fixture(autouse=True)
    def set_up(self, request):
        self.e = Env(slots=1)
        self.p = self.e.p
        env = {k: v for k, v in os.environ.items() if k != login.ENV}
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        request.addfinalizer(patcher.stop)

    def launch(self):
        backlog.add(self.p, "x", status="ready", criteria="y")
        tick.tick(self.p)
        return self.e.calls()[0]["token"]

    def set_login(self, logged_in: bool, expires: datetime | None = None) -> None:
        data: dict[str, object] = {"loggedIn": logged_in}
        if expires:
            data["loginExpiresAt"] = expires.isoformat().replace("+00:00", "Z")
        (self.e.bin / "login.json").write_text(json.dumps(data))

    @pytest.mark.parametrize("working", (True, False))
    def test_agents_never_get_the_token(self, working):
        os.environ[login.ENV] = "from-env"
        self.set_login(working, datetime.now(UTC) + timedelta(days=30))
        assert self.launch() is None

    def test_child_env_drops_only_the_token(self):
        os.environ[login.ENV] = "x"
        os.environ["FM_PROBE"] = "1"
        env = login.child_env()
        assert env["FM_PROBE"] == "1"
        assert login.ENV not in env

    def test_fm_never_reads_the_keychain(self):
        self.set_login(False)
        with mock.patch("foreman.proc.run", wraps=proc.run) as run:
            self.launch()
            checks.login_check(self.p)
        assert not [c for c in run.call_args_list if c.args[0][0] == "security"]

    def test_no_claude_command_gets_the_token(self):
        os.environ[login.ENV] = "from-env"
        with mock.patch("foreman.proc.run", wraps=proc.run) as run:
            sessions.listing(self.p)
            sessions.stop(self.p, "abc")
            login.login_status(self.p.get("agents", "claude"))
        assert len(run.call_args_list) == 3
        for c in run.call_args_list:
            assert login.ENV not in c.kwargs["env"]

    def test_health_is_quiet_while_the_login_works(self):
        assert checks.login_check(self.p, check=lambda c: login.Login(True, datetime.now(UTC) + timedelta(days=30))) == []

    def test_health_warns_before_the_login_expires(self):
        f = checks.login_check(self.p, check=lambda c: login.Login(True, datetime.now(UTC) + timedelta(days=2)))
        assert [x["key"] for x in f] == ["login-expiring"]
        assert "/login" in f[0]["detail"]
        assert checks.login_check(self.p, check=lambda c: login.Login(True, datetime.now(UTC) + timedelta(days=4))) == []

    def test_health_says_agents_cannot_start_when_the_login_fails(self):
        f = checks.login_check(self.p, check=lambda c: login.Login(False))
        assert [x["key"] for x in f] == ["login-failed"]
        assert "Agents cannot start until the owner runs /login" in f[0]["detail"]

    def test_fm_health_cli_with_the_login_down(self):
        self.set_login(False)
        out = fm(self.p.home, "health")
        assert "! The Claude login does not work" in out
        assert "Keychain" not in out
        assert "setup-token" not in out

    def test_init_prints_no_setup_for_a_token(self):
        out = fm(self.p.home, "init", "--prefix", "ZZ", "--repo", "r")
        assert "setup-token" not in out
        assert "Keychain" not in out

    def test_login_status_reads_claude_auth_status(self):
        self.set_login(True, datetime.now(UTC) + timedelta(days=1))
        claude = str(self.e.bin / "claude")
        assert login.login_status(claude).expiring()
        self.set_login(False)
        assert not login.login_status(claude).ok

    def test_launchd_makes_two_jobs_without_the_token(self):
        os.environ[login.ENV] = "sk-ant-oat01-SECRET"
        out = fm(self.p.home, "launchd")
        assert "sk-ant-oat01-SECRET" not in out
        for job in ("loop", "panel"):
            plist = plistlib.loads(launchd.plist(job).encode())
            assert plist["Label"] == f"dev.fm.{job}"
            assert plist["RunAtLoad"] and plist["KeepAlive"]
            assert list(plist["EnvironmentVariables"]) == ["PATH"]
            assert job in plist["ProgramArguments"]
            assert f"dev.fm.{job}.plist" in out

    def test_launchd_dir_writes_both_jobs_with_the_panel_args_and_escapes(self):
        d = self.e.tmp
        fm(self.p.home, "launchd", "--dir", str(d), "--panel-host", "0.0.0.0")
        panel = plistlib.loads((d / "dev.fm.panel.plist").read_bytes())
        assert panel["ProgramArguments"][-5:] == ["panel", "--host", "0.0.0.0", "--port", "7788"]
        assert (d / "dev.fm.loop.plist").exists()
        assert panel["ProgramArguments"][1] == str(launcher.ROOT / "bin/foreman")  # a checkout, no plugin: names it
        with mock.patch.dict(os.environ, {"PATH": "/a&b:/c"}):
            assert plistlib.loads(launchd.plist().encode())["EnvironmentVariables"]["PATH"] == "/a&b:/c"

    def test_launchd_dir_from_the_plugin_writes_jobs_that_name_no_version(self, request):
        cache = self.e.tmp / "cache/fm/fm"
        install_fm(request, version_folder(cache, "aaa"))
        version_folder(cache, "bbb")
        with mock.patch.object(launcher, "ROOT", cache / "bbb"):
            fm(self.p.home, "launchd", "--dir", str(self.e.tmp))
        for job in ("loop", "panel"):
            argv = plistlib.loads((self.e.tmp / f"dev.fm.{job}.plist").read_bytes())["ProgramArguments"]
            assert argv[1:3] == ["-c", launcher.source()]
            assert not [a for a in argv if "aaa" in a or "bbb" in a]
