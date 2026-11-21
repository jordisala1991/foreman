"""Which fm runs: Claude Code's record of the installed foreman@foreman, the launcher the launchd plists carry, and a job started
by it exiting when another version is installed."""

import os
import plistlib
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest
from helpers import install_fm, version_folder

from foreman import launcher, util
from foreman.services import launchd


class TestLauncher:
    @pytest.fixture(autouse=True)
    def set_up(self, request: pytest.FixtureRequest) -> None:
        tmp = tempfile.TemporaryDirectory()
        request.addfinalizer(tmp.cleanup)
        self.cache = Path(tmp.name).resolve() / "cache/foreman/foreman"
        self.a = version_folder(self.cache, "aaa")
        self.b = version_folder(self.cache, "bbb")
        env = mock.patch.dict(os.environ)
        env.start()
        request.addfinalizer(env.stop)
        os.environ.pop(launcher.PINNED, None)

    def fails(self, needle: str) -> None:
        with pytest.raises(launcher.LauncherError) as e:
            launcher.installed()
        assert needle in str(e.value)
        assert ("foreman@foreman" if "format version" not in needle else "installed_plugins.json") in str(e.value)
        assert "\n" not in str(e.value)

    def test_the_user_install_is_read_by_its_folder(self, request: pytest.FixtureRequest) -> None:
        install_fm(request, {**self.a, "scope": "project", "installPath": "/elsewhere"}, self.b)
        assert launcher.installed() == launcher.Installed(self.cache / "bbb", "bbb")

    def test_what_does_not_fit_says_so_in_one_line(self, request: pytest.FixtureRequest) -> None:
        self.fails("is not installed in Claude Code")  # no record at all
        install_fm(request, self.a, version=3)
        self.fails("has format version 3, foreman reads 2")
        install_fm(request)
        self.fails("run claude plugin install foreman@foreman")
        install_fm(request, self.a, self.b)
        self.fails("2 user installs")
        install_fm(request, {"scope": "user", "version": "aaa"})
        self.fails("no installPath or version")
        install_fm(request, {**self.a, "installPath": str(self.cache / "gone")})
        self.fails(f"its folder {self.cache / 'gone'} is missing")
        for odd in (5, self.a, ["aaa"], [{"version": "aaa"}]):  # not a list of installs, each with its scope
            util.write_json(launcher.record(), {"version": 2, "plugins": {"foreman@foreman": odd}})
            self.fails("unexpected format for foreman@foreman")

    def test_only_a_pinned_job_goes_stale(self, request: pytest.FixtureRequest) -> None:
        install_fm(request, self.b)
        assert launcher.stale() == ""  # not started by the launcher
        os.environ[launcher.PINNED] = self.b["installPath"]
        assert launcher.stale() == ""
        install_fm(request, self.a)
        assert launcher.stale() == "foreman@foreman aaa is installed now; this runs bbb"
        launcher.record().unlink()
        assert "foreman@foreman is not installed" in launcher.stale()

    def test_from_plugin_when_this_is_a_sibling_of_the_installed_folder(self, request: pytest.FixtureRequest) -> None:
        with mock.patch.object(launcher, "ROOT", self.cache / "bbb"):
            assert not launcher.from_plugin()  # nothing installed
            install_fm(request, self.a)
            assert launcher.from_plugin()
        assert not launcher.from_plugin()  # this checkout

    def test_the_plugin_plist_names_no_version_folder(self, request: pytest.FixtureRequest) -> None:
        install_fm(request, self.a)
        with mock.patch.object(launcher, "ROOT", self.cache / "bbb"):
            argv = plistlib.loads(launchd.plist("loop").encode())["ProgramArguments"]
        assert argv[1:2] == ["-c"]
        assert argv[-1] == "loop"
        assert not [a for a in argv if "aaa" in a or "bbb" in a]

    def test_a_checkout_plist_names_the_checkout(self, request: pytest.FixtureRequest) -> None:
        install_fm(request, self.a)
        argv = plistlib.loads(launchd.plist("panel", ("--port", "1")).encode())["ProgramArguments"]
        assert argv[1:] == [str(launcher.ROOT / "bin/foreman"), "panel", "--port", "1"]

    def run_plist(self, *args: str) -> subprocess.CompletedProcess[str]:
        """What launchd runs for the plugin plist: python3 -c <launcher> JOB ARGS."""
        (self.cache / "aaa/bin/foreman").write_text("import os, sys\nprint(os.path.realpath(__file__), sys.argv[1:], os.environ['FOREMAN_PINNED'])\n")
        return subprocess.run([sys.executable, "-c", launcher.source(), *args], capture_output=True, text=True, timeout=30, check=False)

    def test_the_launcher_runs_the_installed_folder_pinned(self, request: pytest.FixtureRequest) -> None:
        install_fm(request, self.a)
        r = self.run_plist("panel", "--port", "7788")
        bin_fm = self.cache / "aaa/bin/foreman"
        assert r.stdout.strip() == f"{bin_fm} ['panel', '--port', '7788'] {self.cache / 'aaa'}"

    def test_without_an_install_the_job_exits_with_one_line(self, request: pytest.FixtureRequest) -> None:
        for entry in (None, {**self.a, "installPath": str(self.cache / "gone")}):
            if entry:
                install_fm(request, entry)
            r = self.run_plist("loop")
            assert r.returncode != 0
            assert len(r.stderr.strip().splitlines()) == 1, r.stderr
            assert "foreman@foreman" in r.stderr
            assert r.stdout == ""
