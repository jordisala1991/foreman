import os
import plistlib
import unittest
from unittest import mock

from helpers import Env, fm

from fm import backlog, health, loop, token

SECRET = "sk-ant-oat01-SECRET"


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

    def test_loop_passes_the_keychain_token_to_the_agents(self):
        with mock.patch.object(token, "keychain", lambda: SECRET):
            self.assertEqual(self.launch(), SECRET)

    def test_env_token_wins_over_the_keychain(self):
        os.environ[token.ENV] = "from-env"
        with mock.patch.object(token, "keychain", lambda: SECRET):
            self.assertEqual(self.launch(), "from-env")

    def test_keychain_is_read_with_the_documented_command(self):
        with mock.patch("fm.proc.run") as run:
            run.return_value = mock.Mock(code=0, out=SECRET + "\n")
            self.assertEqual(token.keychain(), SECRET)
        self.assertEqual(run.call_args[0][0][:2], ["security", "find-generic-password"])
        self.assertEqual(run.call_args[0][0][-3:], ["-s", "claude-oauth-token", "-w"])

    def test_health_says_no_token_with_the_renew_command(self):
        f = health.claude_token(self.p, read=lambda: "")
        self.assertEqual(f[0]["title"], "No token")
        self.assertIn("security add-generic-password -U", f[0]["detail"])

    def test_health_says_token_does_not_work_and_caches_the_probe(self):
        calls = []

        def bad(claude: str, tok: str) -> bool:
            calls.append(tok)
            return False

        for _ in range(2):
            f = health.claude_token(self.p, read=lambda: SECRET, works=bad)
        self.assertEqual(f[0]["title"], "Token does not work")
        self.assertIn("claude setup-token", f[0]["detail"])
        self.assertEqual(len(calls), 1)
        self.assertNotIn(SECRET, (self.p.state / "token.json").read_text())

    def test_health_is_quiet_with_a_working_token(self):
        self.assertEqual(health.claude_token(self.p, read=lambda: SECRET, works=lambda c, t: True), [])

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


if __name__ == "__main__":
    unittest.main()
