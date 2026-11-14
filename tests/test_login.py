import json
import os
import plistlib
import unittest
from datetime import UTC, datetime, timedelta
from unittest import mock

from helpers import Env, fm

from fm import backlog, health, login, loop, proc


class Login(unittest.TestCase):
    def setUp(self):
        self.e = Env(slots=1)
        self.p = self.e.p
        env = {k: v for k, v in os.environ.items() if k != login.ENV}
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def launch(self):
        backlog.add(self.p, "x", status="ready", criteria="y")
        loop.tick(self.p)
        return self.e.calls()[0]["token"]

    def set_login(self, logged_in: bool, expires: datetime | None = None) -> None:
        data: dict[str, object] = {"loggedIn": logged_in}
        if expires:
            data["loginExpiresAt"] = expires.isoformat().replace("+00:00", "Z")
        (self.e.bin / "login.json").write_text(json.dumps(data))

    def test_agents_never_get_the_token(self):
        for working in (True, False):
            with self.subTest(login_works=working):
                self.setUp()
                os.environ[login.ENV] = "from-env"
                self.set_login(working, datetime.now(UTC) + timedelta(days=30))
                self.assertIsNone(self.launch())

    def test_child_env_drops_only_the_token(self):
        os.environ[login.ENV] = "x"
        os.environ["FM_PROBE"] = "1"
        env = login.child_env()
        self.assertEqual(env["FM_PROBE"], "1")
        self.assertNotIn(login.ENV, env)

    def test_fm_never_reads_the_keychain(self):
        self.set_login(False)
        with mock.patch("fm.proc.run", wraps=proc.run) as run:
            self.launch()
            health.login_check(self.p)
        self.assertFalse([c for c in run.call_args_list if c.args[0][0] == "security"])

    def test_health_is_quiet_while_the_login_works(self):
        self.assertEqual(health.login_check(self.p, check=lambda c: login.Login(True, datetime.now(UTC) + timedelta(days=30))), [])

    def test_health_warns_before_the_login_expires(self):
        f = health.login_check(self.p, check=lambda c: login.Login(True, datetime.now(UTC) + timedelta(days=2)))
        self.assertEqual([x["key"] for x in f], ["login-expiring"])
        self.assertIn("/login", f[0]["detail"])
        self.assertEqual(health.login_check(self.p, check=lambda c: login.Login(True, datetime.now(UTC) + timedelta(days=4))), [])

    def test_health_says_agents_cannot_start_when_the_login_fails(self):
        f = health.login_check(self.p, check=lambda c: login.Login(False))
        self.assertEqual([x["key"] for x in f], ["login-failed"])
        self.assertIn("Agents cannot start until the owner runs /login", f[0]["detail"])

    def test_fm_health_cli_with_the_login_down(self):
        self.set_login(False)
        out = fm(self.p.home, "health")
        self.assertIn("! The Claude login does not work", out)
        self.assertNotIn("Keychain", out)
        self.assertNotIn("setup-token", out)

    def test_init_prints_no_setup_for_a_token(self):
        out = fm(self.p.home, "init", "--prefix", "ZZ", "--repo", "r")
        self.assertNotIn("setup-token", out)
        self.assertNotIn("Keychain", out)

    def test_login_status_reads_claude_auth_status(self):
        self.set_login(True, datetime.now(UTC) + timedelta(days=1))
        claude = str(self.e.bin / "claude")
        self.assertTrue(login.login_status(claude).expiring())
        self.set_login(False)
        self.assertFalse(login.login_status(claude).ok)

    def test_launchd_makes_two_jobs_without_the_token(self):
        os.environ[login.ENV] = "sk-ant-oat01-SECRET"
        out = fm(self.p.home, "launchd")
        self.assertNotIn("sk-ant-oat01-SECRET", out)
        for job in ("loop", "panel"):
            plist = plistlib.loads(loop.launchd_plist("/x/fm", job).encode())
            self.assertEqual(plist["Label"], f"dev.fm.{job}")
            self.assertTrue(plist["RunAtLoad"] and plist["KeepAlive"])
            self.assertEqual(list(plist["EnvironmentVariables"]), ["PATH"])
            self.assertIn(job, plist["ProgramArguments"])
            self.assertIn(f"dev.fm.{job}.plist", out)

    def test_launchd_dir_writes_both_jobs_with_the_panel_args_and_escapes(self):
        d = self.e.tmp
        fm(self.p.home, "launchd", "--dir", str(d), "--panel-host", "0.0.0.0")
        panel = plistlib.loads((d / "dev.fm.panel.plist").read_bytes())
        self.assertEqual(panel["ProgramArguments"][-5:], ["panel", "--host", "0.0.0.0", "--port", "7788"])
        self.assertTrue((d / "dev.fm.loop.plist").exists())
        with mock.patch.dict(os.environ, {"PATH": "/a&b:/c"}):
            self.assertEqual(plistlib.loads(loop.launchd_plist("/x/fm").encode())["EnvironmentVariables"]["PATH"], "/a&b:/c")


if __name__ == "__main__":
    unittest.main()
