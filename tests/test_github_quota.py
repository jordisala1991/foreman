"""A GitHub rate limit pauses the loop's gh calls until the reset; it never blocks a task."""

import textwrap
import time
import unittest
from unittest import mock

from test_github import GH as BaseGH

from fm import backlog, loop, util
from fm.providers import gh_quota

# In front of the fake gh of test_github: it answers `gh api rate_limit` and, while `limited` is set, rate-limits the CI
# read (`gh api graphql`); every other call goes on to the fake.
SHIM = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json, os, sys
    d = os.path.dirname(os.path.abspath(__file__))
    a = sys.argv[1:]
    f = os.path.join(d, "gh.json")
    s = json.load(open(f)) if os.path.exists(f) else {}
    def say(text, code):
        open(os.path.join(d, "gh.log"), "a").write(json.dumps(a) + "\\n")
        print(text, file=sys.stderr if code else sys.stdout)
        sys.exit(code)
    if a[:2] == ["api", "rate_limit"]:
        r = {"limit": 5000, "reset": s.get("reset")}
        resources = {"core": {**r, "remaining": 4000}, "graphql": {**r, "remaining": 0}} if "reset" in s else {}
        say("HTTP 500" if s.get("quota_fail") else json.dumps({"resources": resources}), 1 if s.get("quota_fail") else 0)
    if s.get("limited") and a[:2] == ["api", "graphql"]:
        say("GraphQL: API rate limit already exceeded for user ID 1.", 1)
    if s.get("limited_body") and a[:2] == ["pr", "view"] and "body" in a:
        say("API rate limit exceeded for user ID 1.", 1)
    os.execv(os.path.join(d, "gh_fake"), ["gh_fake", *a])
""")


class GH(BaseGH):
    def __init__(self, extra: str = "", **kw) -> None:
        super().__init__(extra, **kw)
        gh = self.e.bin / "gh"
        gh.rename(self.e.bin / "gh_fake")
        gh.write_text(SHIM)
        gh.chmod(0o755)


class Quota(unittest.TestCase):
    def test_the_reset_time_comes_from_the_exhausted_resource(self):
        def res(**r: dict) -> dict:
            return {"resources": r}

        core, gql = {"remaining": 4000, "reset": 9000}, {"remaining": 0, "reset": 5000}
        self.assertEqual(gh_quota.reset_at(res(core=core, graphql=gql), 1000), 5000)
        self.assertEqual(gh_quota.reset_at(res(a=gql, b={"remaining": 0, "reset": 7000}), 1000), 7000)
        for raw in (res(core=core), res(g={"remaining": 0, "reset": 900}), {}, [], "x", res(g={"remaining": 0, "reset": "soon"})):
            self.assertEqual(gh_quota.reset_at(raw, 1000), 1060, raw)


class Limited(unittest.TestCase):
    def make(self, extra: str = "", **kw) -> GH:
        g = GH(extra, **kw)
        self.addCleanup(g.close)
        return g

    def clock(self) -> list[float]:
        """Moves the provider's clock forward by offset[0] seconds."""
        real = time.time
        offset = [0.0]
        patch = mock.patch("fm.providers.gh_quota.time.time", lambda: real() + offset[0])
        patch.start()
        self.addCleanup(patch.stop)
        return offset

    def clean(self, g: GH, *ids: str) -> None:
        for iid in ids:
            i = g.e.task(iid)
            self.assertFalse(i.get("blocked"), iid)
            self.assertEqual(i.get("retry_count") or 0, 0, iid)

    def test_a_rate_limit_pauses_gh_until_the_reset(self):
        offset = self.clock()
        g = self.make(test="true")
        first, second = g.work(), g.work()
        for iid in (first, second):
            backlog.set_status(g.e.p, iid, "in review", report={"summary": "s", "confused": ""})
        for _ in range(4):  # a tick launches at most one hunter or reviewer
            loop.tick(g.e.p)
            for iid in (first, second):
                if g.e.task(iid).get("hunter") and not g.e.task(iid).get("review"):
                    g.e.hunter_done(iid)
        for iid in (first, second):
            g.e.verdict(iid)
        reset = int(time.time()) + 600
        g.set(reset=reset, limited=True)
        before = len(g.calls("api", "graphql"))
        loop.tick(g.e.p)
        self.assertEqual(len(g.calls("api", "graphql")), before + 1)
        n = len(g.calls())
        for _ in range(9):
            loop.tick(g.e.p)
            self.assertEqual(len(g.calls()), n)
            self.clean(g, first, second)
        self.assertEqual(len(g.calls("api", "rate_limit")), 1)
        quota = util.read_json(g.e.p.state / "github_quota.json", {})
        self.assertEqual(quota["until"], reset)
        self.assertEqual((quota["resources"]["graphql"]["remaining"], quota["resources"]["graphql"]["limit"]), (0, 5000))
        g.set(limited=False)
        offset[0] = 540
        loop.tick(g.e.p)
        self.assertEqual(len(g.calls()), n)
        offset[0] = 660
        loop.tick(g.e.p)
        self.assertGreater(len(g.calls()), n)
        self.assertEqual(g.e.task(first)["check"]["ci"], "pass")

    def test_a_rate_limit_whose_reset_cannot_be_read_pauses_for_a_minute(self):
        offset = self.clock()
        g = self.make(test="true")
        iid = g.work()
        g.deliver(iid)
        g.set(quota_fail=True, limited=True)
        loop.tick(g.e.p)
        n = len(g.calls())
        offset[0] = 30
        loop.tick(g.e.p)
        self.assertEqual(len(g.calls()), n)
        offset[0] = 90
        g.set(limited=False)
        loop.tick(g.e.p)
        self.assertGreater(len(g.calls()), n)
        self.clean(g, iid)

    def test_a_broken_quota_file_means_no_pause(self):
        g = self.make(test="true")
        iid = g.work()
        g.deliver(iid)
        (g.e.p.state / "github_quota.json").write_text("not json")
        loop.tick(g.e.p)
        self.assertEqual(g.e.task(iid)["check"]["ci"], "pass")

    def test_a_rate_limit_on_merge_pauses_without_blocking(self):
        g = self.make(test="true", autonomy="medium")
        iid = g.work()
        g.deliver(iid)
        g.set(refuse_merge="API rate limit exceeded for user ID 1")
        loop.tick(g.e.p)
        merges = len(g.calls("pr", "merge"))
        loop.tick(g.e.p)
        self.assertEqual(len(g.calls("pr", "merge")), merges)
        self.assertNotEqual(g.e.task(iid)["status"], "done")
        self.clean(g, iid)
        self.assertTrue((g.e.p.state / "github_quota.json").exists())

    def test_a_rate_limit_on_the_pr_description_is_not_a_failure_of_the_task(self):
        g = self.make(test="true")
        iid = g.work()
        g.set(limited_body=True)
        g.deliver(iid)
        loop.tick(g.e.p)
        loop.tick(g.e.p)
        self.assertTrue((g.e.p.state / "github_quota.json").exists())
        self.assertFalse(g.e.task(iid).get("pr_error"))
        events = [e["text"] for e in util.read_jsonl(g.e.p.state / "events.jsonl") if "PR description" in e["text"]]
        self.assertEqual(events, [])
        self.clean(g, iid)

    def test_a_far_away_pause_is_capped(self):
        g = self.make(test="true")
        iid = g.work()
        g.deliver(iid)
        (g.e.p.state / "github_quota.json").write_text('{"until": 4102444800}')
        self.assertLessEqual(gh_quota.paused_until(g.e.p.state), time.time() + gh_quota.MAX_PAUSE)
