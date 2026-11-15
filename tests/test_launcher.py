"""Which fm runs: Claude Code's record of the installed fm@fm, the launcher the launchd plists carry, and a job started
by it exiting when another version is installed."""

import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import install_fm, version_folder

from fm import launchd, launcher, util


class Launcher(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cache = Path(tmp.name).resolve() / "cache/fm/fm"
        self.a = version_folder(self.cache, "aaa")
        self.b = version_folder(self.cache, "bbb")
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(launcher.PINNED, None)

    def fails(self, needle: str) -> None:
        with self.assertRaises(launcher.LauncherError) as e:
            launcher.installed()
        self.assertIn(needle, str(e.exception))
        self.assertIn("fm@fm" if "format version" not in needle else "installed_plugins.json", str(e.exception))
        self.assertNotIn("\n", str(e.exception))

    def test_the_user_install_is_read_by_its_folder(self) -> None:
        install_fm(self, {**self.a, "scope": "project", "installPath": "/elsewhere"}, self.b)
        self.assertEqual(launcher.installed(), launcher.Installed(self.cache / "bbb", "bbb"))

    def test_what_does_not_fit_says_so_in_one_line(self) -> None:
        self.fails("is not installed in Claude Code")  # no record at all
        install_fm(self, self.a, version=3)
        self.fails("has format version 3, fm reads 2")
        install_fm(self)
        self.fails("run claude plugin install fm@fm")
        install_fm(self, self.a, self.b)
        self.fails("2 user installs")
        install_fm(self, {"scope": "user", "version": "aaa"})
        self.fails("no installPath or version")
        install_fm(self, {**self.a, "installPath": str(self.cache / "gone")})
        self.fails(f"its folder {self.cache / 'gone'} is missing")
        for odd in (5, self.a, ["aaa"], [{"version": "aaa"}]):  # not a list of installs, each with its scope
            util.write_json(launcher.record(), {"version": 2, "plugins": {"fm@fm": odd}})
            self.fails("unexpected format for fm@fm")

    def test_only_a_pinned_job_goes_stale(self) -> None:
        install_fm(self, self.b)
        self.assertEqual(launcher.stale(), "")  # not started by the launcher
        os.environ[launcher.PINNED] = self.b["installPath"]
        self.assertEqual(launcher.stale(), "")
        install_fm(self, self.a)
        self.assertEqual(launcher.stale(), "fm@fm aaa is installed now; this runs bbb")
        launcher.record().unlink()
        self.assertIn("fm@fm is not installed", launcher.stale())

    def test_from_plugin_when_this_is_a_sibling_of_the_installed_folder(self) -> None:
        with mock.patch.object(launcher, "ROOT", self.cache / "bbb"):
            self.assertFalse(launcher.from_plugin())  # nothing installed
            install_fm(self, self.a)
            self.assertTrue(launcher.from_plugin())
        self.assertFalse(launcher.from_plugin())  # this checkout

    def test_the_plugin_plist_names_no_version_folder(self) -> None:
        install_fm(self, self.a)
        with mock.patch.object(launcher, "ROOT", self.cache / "bbb"):
            argv = plistlib.loads(launchd.plist("loop").encode())["ProgramArguments"]
        self.assertEqual(argv[1:2], ["-c"])
        self.assertEqual(argv[-1], "loop")
        self.assertFalse([a for a in argv if "aaa" in a or "bbb" in a])

    def test_a_checkout_plist_names_the_checkout(self) -> None:
        install_fm(self, self.a)
        argv = plistlib.loads(launchd.plist("panel", ("--port", "1")).encode())["ProgramArguments"]
        self.assertEqual(argv[1:], [str(launcher.ROOT / "bin/fm"), "panel", "--port", "1"])

    def run_plist(self, *args: str) -> subprocess.CompletedProcess[str]:
        """What launchd runs for the plugin plist: python3 -c <launcher> JOB ARGS."""
        (self.cache / "aaa/bin/fm").write_text("import os, sys\nprint(os.path.realpath(__file__), sys.argv[1:], os.environ['FM_PINNED'])\n")
        return subprocess.run([sys.executable, "-c", launcher.source(), *args], capture_output=True, text=True, timeout=30, check=False)

    def test_the_launcher_runs_the_installed_folder_pinned(self) -> None:
        install_fm(self, self.a)
        r = self.run_plist("panel", "--port", "7788")
        bin_fm = self.cache / "aaa/bin/fm"
        self.assertEqual(r.stdout.strip(), f"{bin_fm} ['panel', '--port', '7788'] {self.cache / 'aaa'}")

    def test_without_an_install_the_job_exits_with_one_line(self) -> None:
        for entry in (None, {**self.a, "installPath": str(self.cache / "gone")}):
            if entry:
                install_fm(self, entry)
            r = self.run_plist("loop")
            self.assertNotEqual(r.returncode, 0)
            self.assertEqual(len(r.stderr.strip().splitlines()), 1, r.stderr)
            self.assertIn("fm@fm", r.stderr)
            self.assertEqual(r.stdout, "")


if __name__ == "__main__":
    unittest.main()
