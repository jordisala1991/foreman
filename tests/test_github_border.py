"""The typed border of what gh answers, and the provider choice."""

import unittest

from helpers import Env, git
from test_github import GH

from fm import providers
from fm.providers import github, registry


class Provider(unittest.TestCase):
    def test_provider_is_detected_from_origin_and_can_be_overridden(self):
        e = Env()
        self.assertEqual(registry.get(e.p).name, "local")
        git(e.repo, "remote", "add", "origin", "git@github.com:o/r.git")
        self.assertEqual(registry.get(e.p).name, "github")
        git(e.repo, "remote", "set-url", "origin", "https://gitlab.com/o/r.git")
        self.assertEqual(registry.get(e.p).name, "local")
        git(e.repo, "remote", "set-url", "origin", "https://github.com/o/r.git")
        e.p.cfg["merge"]["provider"] = "local"
        self.assertEqual(registry.get(e.p).name, "local")

    def test_local_has_no_rebase_github_has(self):
        e = Env()
        e.p.cfg["merge"]["strategy"] = "rebase"
        with self.assertRaises(providers.ConfigError):
            registry.get(e.p)
        e.p.cfg["merge"]["provider"] = "github"
        self.assertEqual(registry.get(e.p).name, "github")


class Border(unittest.TestCase):
    """What gh says is turned into fm's own types at the border; a shape that does not fit is a clear ProviderError."""

    GOOD = {"number": 7, "url": "https://github.com/o/r/pull/7", "isDraft": True, "state": "OPEN", "mergeCommit": None}

    def test_a_pull_request_becomes_a_typed_value(self):
        pr = github.pull_request(self.GOOD)
        self.assertEqual((pr.number, pr.url, pr.draft, pr.state, pr.merge_commit), (7, "https://github.com/o/r/pull/7", True, "OPEN", ""))
        merged = github.pull_request({**self.GOOD, "state": "MERGED", "mergeCommit": {"oid": "abc"}})
        self.assertEqual((merged.state, merged.merge_commit), ("MERGED", "abc"))

    def test_a_pull_request_of_the_wrong_shape_is_a_provider_error(self):
        for bad in (
            [],
            "x",
            {**self.GOOD, "state": "SIDEWAYS"},
            {**self.GOOD, "number": "7"},
            {**self.GOOD, "number": True},
            {**self.GOOD, "isDraft": "yes"},
            {**self.GOOD, "mergeCommit": "abc"},
            {**self.GOOD, "mergeCommit": {"oid": 1}},
            {k: v for k, v in self.GOOD.items() if k != "url"},
        ):
            with self.assertRaises(providers.ProviderError, msg=repr(bad)):
                github.pull_request(bad)

    def test_the_open_prs_answer_becomes_head_and_ci_or_a_provider_error(self):
        def node(state):
            return {"number": 1, "headRefOid": "abc", "commits": {"nodes": [{"commit": {"statusCheckRollup": state}}]}}

        def answer(*nodes):
            return {"data": {"repository": {"pullRequests": {"nodes": list(nodes)}}}}

        self.assertEqual(github.open_prs(answer(node({"state": "SUCCESS"}))), {"1": {"head": "abc", "ci": "pass"}})
        self.assertEqual(github.open_prs(answer(node(None)))["1"]["ci"], "none")
        for bad in (answer({"number": "1"}), answer("x")):
            with self.assertRaises(providers.ProviderError, msg=repr(bad)):
                github.open_prs(bad)

    def test_gh_answering_a_bad_shape_reaches_the_caller_as_a_provider_error(self):
        g = GH()
        self.addCleanup(g.close)
        g.set(prs={"1": {"draft": False, "state": "OPEN", "comments": []}}, bad_view=True)
        with self.assertRaises(providers.ProviderError):
            registry.get(g.e.p).state({"pr": 1})
