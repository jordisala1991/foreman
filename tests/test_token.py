import json
import os
import plistlib
import threading
import time
import unittest
from datetime import UTC, datetime, timedelta
from unittest import mock

from helpers import Env, fm

from fm import backlog, health, loop, token, util

SECRET = "sk-ant-oat01-SECRET"


def LOGIN_OFF(claude: str) -> token.Login:
    return token.Login(False)


class Token(unittest.TestCase):
    def setUp(self):
        self.e = Env(slots=1)
        self.p = self.e.p
        env = {k: v for k, v in os.environ.items() if k != token.ENV}
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def launch(self):
        backlog.add(self.p, "x", status="ready", criterion="y")
        loop.tick(self.p)
        return self.e.calls()[0]["token"]

    def set_login(self, logged_in: bool, expires: datetime | None = None) -> None:
        data: dict[str, object] = {"loggedIn": logged_in}
        if expires:
            data["loginExpiresAt"] = expires.isoformat().replace("+00:00", "Z")
        (self.e.bin / "login.json").write_text(json.dumps(data))

    def test_login_works_so_the_agents_get_no_token(self):
        os.environ[token.ENV] = "from-env"
        self.set_login(True, datetime.now(UTC) + timedelta(days=30))
        with mock.patch.object(token, "keychain", lambda: SECRET):
            self.assertIsNone(self.launch())

    def test_login_expired_so_the_agents_get_the_keychain_token(self):
        self.set_login(True, datetime.now(UTC) - timedelta(hours=1))
        with mock.patch.object(token, "keychain", lambda: SECRET):
            self.assertEqual(self.launch(), SECRET)

    def test_login_not_working_and_env_token_wins_over_the_keychain(self):
        os.environ[token.ENV] = "from-env"
        self.set_login(False)
        with mock.patch.object(token, "keychain", lambda: SECRET):
            self.assertEqual(self.launch(), "from-env")

    def test_health_on_the_fallback_says_so(self):
        f = health.claude_token(self.p, read=lambda: SECRET, works=lambda c, t: True, login=LOGIN_OFF)
        self.assertEqual(f[0]["key"], "token-fallback")
        self.assertIn("do not show in the Claude app; run /login", f[0]["detail"])

    def test_health_is_quiet_while_the_login_works_and_warns_before_it_expires(self):
        def ok(claude: str) -> token.Login:
            return token.Login(True, datetime.now(UTC) + timedelta(days=30))

        def soon(claude: str) -> token.Login:
            return token.Login(True, datetime.now(UTC) + timedelta(days=2))

        self.assertEqual(health.claude_token(self.p, read=lambda: "", login=ok), [])
        f = health.claude_token(self.p, read=lambda: "", login=soon)
        self.assertEqual(f[0]["key"], "login-expiring")
        self.assertIn("/login", f[0]["detail"])

    def test_login_status_reads_claude_auth_status(self):
        self.set_login(True, datetime.now(UTC) + timedelta(days=1))
        claude = str(self.e.bin / "claude")
        self.assertTrue(token.login_status(claude).expiring())
        self.set_login(False)
        self.assertFalse(token.login_status(claude).ok)

    def test_keychain_is_read_with_the_documented_command(self):
        with mock.patch("fm.proc.run") as run:
            run.return_value = mock.Mock(code=0, out=SECRET + "\n")
            self.assertEqual(token.keychain(), SECRET)
        self.assertEqual(run.call_args[0][0][:2], ["security", "find-generic-password"])
        self.assertEqual(run.call_args[0][0][-3:], ["-s", "claude-oauth-token", "-w"])

    def test_health_says_no_token_with_the_renew_command(self):
        f = health.claude_token(self.p, read=lambda: "", login=LOGIN_OFF)
        self.assertEqual(f[0]["title"], "No token")
        self.assertIn("security add-generic-password -U", f[0]["detail"])

    def test_health_says_token_does_not_work_and_caches_the_probe(self):
        calls = []

        def bad(claude: str, tok: str) -> bool:
            calls.append(tok)
            return False

        for _ in range(2):
            f = health.claude_token(self.p, read=lambda: SECRET, works=bad, login=LOGIN_OFF)
        self.assertEqual(f[0]["title"], "Token does not work")
        self.assertIn("claude setup-token", f[0]["detail"])
        self.assertEqual(len(calls), 1)
        self.assertNotIn(SECRET, (self.p.state / "token.json").read_text())

    def test_launchd_makes_two_jobs_without_the_token(self):
        os.environ[token.ENV] = SECRET
        out = fm(self.p.home, "launchd")
        self.assertNotIn(SECRET, out)
        for job in ("loop", "panel"):
            plist = plistlib.loads(loop.launchd_plist("/x/fm", job).encode())
            self.assertEqual(plist["Label"], f"dev.fm.{job}")
            self.assertTrue(plist["RunAtLoad"] and plist["KeepAlive"])
            self.assertEqual(list(plist["EnvironmentVariables"]), ["PATH"])
            self.assertIn(job, plist["ProgramArguments"])
            self.assertIn(f"dev.fm.{job}.plist", out)

    def test_init_explains_the_setup(self):
        out = fm(self.p.home, "init", "--prefix", "ZZ", "--repo", "r")
        self.assertIn("claude setup-token", out)

    def test_fm_health_cli_shows_the_renew_command_for_no_token(self):
        self.set_login(False)
        with mock.patch.object(token, "keychain", lambda: ""):
            out = fm(self.p.home, "health")
        self.assertIn("security add-generic-password -U", out)
        self.assertIn("claude setup-token", out)

    def test_unreadable_keychain_is_not_no_token_and_files_nothing(self):
        def locked() -> str:
            raise token.Unreadable("locked")

        self.assertEqual(health.claude_token(self.p, read=locked, login=LOGIN_OFF), [])  # a transient failure shows nothing
        self.assertEqual(health.claude_token(self.p, read=locked, login=LOGIN_OFF), [])  # and a second check does not count
        stamp = (datetime.now() - timedelta(minutes=health.KEYCHAIN_MINUTES + 1)).strftime("%Y-%m-%d %H:%M:%S")
        util.write_json(self.p.state / "keychain.json", {"since": stamp})  # unreadable for longer than that
        f = health.claude_token(self.p, read=locked, login=LOGIN_OFF)
        self.assertEqual(f[0]["title"], "Keychain unreadable: locked")
        health.claude_token(self.p, read=lambda: SECRET, works=lambda c, t: True, login=LOGIN_OFF)  # a good read resets
        self.assertEqual(health.claude_token(self.p, read=locked, login=LOGIN_OFF), [])
        with mock.patch("fm.proc.run", return_value=mock.Mock(code=36, out="", err="")), self.assertRaises(token.Unreadable):
            token.keychain()
        with mock.patch("fm.proc.run", return_value=mock.Mock(code=44, out="", err="")):
            self.assertEqual(token.keychain(), "")

    def test_the_panel_path_does_not_wait_for_the_probe(self):
        gate = threading.Event()
        started = threading.Event()

        def slow(claude: str, tok: str) -> bool:
            started.set()
            gate.wait(10)
            return False

        t0 = time.time()
        first = health.claude_token(self.p, read=lambda: SECRET, works=slow, wait=False, login=LOGIN_OFF)
        self.assertEqual(first[0]["key"], "token-fallback")  # until the probe answers
        self.assertLess(time.time() - t0, 2)
        self.assertTrue(started.wait(5))
        gate.set()
        for _ in range(100):
            if (self.p.state / "token.json").exists():
                break
            time.sleep(0.05)
        self.assertEqual(health.claude_token(self.p, read=lambda: SECRET, works=slow, wait=False, login=LOGIN_OFF)[0]["key"], "token-bad")

    def fake_claude(self, code: int, err: str) -> str:
        path = self.e.tmp / f"claude{code}"
        path.write_text(f"#!/bin/sh\necho '{err}' >&2\nexit {code}\n")
        path.chmod(0o755)
        return str(path)

    def test_probe_only_blames_the_token_when_claude_says_so(self):
        self.assertTrue(token.works(self.fake_claude(0, ""), SECRET))
        self.assertTrue(token.works(self.fake_claude(1, "API Error: 529 overloaded, usage limit reached"), SECRET))
        self.assertTrue(token.works(self.fake_claude(1, "getaddrinfo ENOTFOUND api.anthropic.com"), SECRET))
        self.assertTrue(token.works(self.fake_claude(1, "API Error: 503 request_id req_401abc, session expired, oauth proxy down"), SECRET))
        self.assertFalse(token.works(self.fake_claude(1, "Failed to authenticate. API Error: 401 Invalid bearer token"), SECRET))
        self.assertFalse(token.works(self.fake_claude(1, "OAuth token has expired. Please run /login"), SECRET))

    def test_launchd_dir_writes_both_jobs_with_the_panel_args_and_escapes(self):
        d = self.e.tmp / "new" / "agents"
        fm(self.p.home, "launchd", "--dir", str(d), "--panel-host", "0.0.0.0", "--panel-port", "9000")
        panel = plistlib.loads((d / "dev.fm.panel.plist").read_bytes())
        self.assertEqual(panel["ProgramArguments"][-5:], ["panel", "--host", "0.0.0.0", "--port", "9000"])
        self.assertTrue((d / "dev.fm.loop.plist").exists())
        with mock.patch.dict(os.environ, {"PATH": "/a&b:/c"}):
            self.assertEqual(plistlib.loads(loop.launchd_plist("/x/fm").encode())["EnvironmentVariables"]["PATH"], "/a&b:/c")


if __name__ == "__main__":
    unittest.main()
