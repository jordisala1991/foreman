"""A GitHub rate limit pauses the loop's gh calls until the reset; it never blocks a task."""

import time
import unittest
from unittest import mock

from test_github import GH

from fm import loop, util
from fm.providers import github


class Quota(unittest.TestCase):
    def test_the_reset_time_comes_from_the_exhausted_resource(self):
        def res(**r: dict) -> dict:
            return {"resources": r}

        core, gql = {"remaining": 4000, "reset": 9000}, {"remaining": 0, "reset": 5000}
        self.assertEqual(github.reset_at(res(core=core, graphql=gql), 1000), 5000)
        self.assertEqual(github.reset_at(res(a=gql, b={"remaining": 0, "reset": 7000}), 1000), 7000)
        for raw in (res(core=core), res(g={"remaining": 0, "reset": 900}), {}, [], "x", res(g={"remaining": 0, "reset": "soon"})):
            self.assertEqual(github.reset_at(raw, 1000), 1060, raw)


class Limited(unittest.TestCase):
    def make(self, extra: str = "", **kw) -> GH:
        g = GH(extra, **kw)
        self.addCleanup(g.close)
        return g

    def clock(self) -> list[float]:
        """Moves the provider's clock forward by offset[0] seconds."""
        real = time.time
        offset = [0.0]
        patch = mock.patch("fm.providers.github.time.time", lambda: real() + offset[0])
        patch.start()
        self.addCleanup(patch.stop)
        return offset

    def clean(self, g: GH, *ids: str) -> None:
        for iid in ids:
            i = g.e.issue(iid)
            self.assertFalse(i.get("blocked"), iid)
            self.assertEqual(i.get("retry_count") or 0, 0, iid)

    def test_a_rate_limit_pauses_gh_until_the_reset(self):
        offset = self.clock()
        g = self.make(test="true")
        first, second = g.work(), g.work()
        g.deliver(first)
        g.deliver(second)
        reset = int(time.time()) + 600
        g.set(reset=reset, limited=True)
        loop.tick(g.e.p)
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
        self.assertEqual(g.e.issue(first)["check"]["ci"], "pass")

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
        self.assertEqual(g.e.issue(iid)["check"]["ci"], "pass")

    def test_a_rate_limit_on_merge_pauses_without_blocking(self):
        g = self.make(test="true", autonomy="medium")
        iid = g.work()
        g.deliver(iid)
        g.set(refuse_merge="API rate limit exceeded for user ID 1")
        loop.tick(g.e.p)
        merges = len(g.calls("pr", "merge"))
        loop.tick(g.e.p)
        self.assertEqual(len(g.calls("pr", "merge")), merges)
        self.assertNotEqual(g.e.issue(iid)["status"], "done")
        self.clean(g, iid)
        self.assertTrue((g.e.p.state / "github_quota.json").exists())
