"""What gh says is turned into fm's own types at the border (see test_github for the fake gh)."""

import inspect
import typing
import unittest
from typing import Any

from test_github import GH

from fm import providers
from fm.providers import local, registry
from fm.providers.github import checks, provider


class Border(unittest.TestCase):
    """What gh says is turned into fm's own types at the border; a shape that does not fit is a clear ProviderError."""

    GOOD = {"number": 7, "url": "https://github.com/o/r/pull/7", "isDraft": True, "state": "OPEN", "mergeCommit": None}

    def test_a_pull_request_becomes_a_typed_value(self):
        pr = provider.pull_request(self.GOOD)
        self.assertEqual((pr.number, pr.url, pr.draft, pr.state, pr.merge_commit), (7, "https://github.com/o/r/pull/7", True, "OPEN", ""))
        merged = provider.pull_request({**self.GOOD, "state": "MERGED", "mergeCommit": {"oid": "abc"}})
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
                provider.pull_request(bad)

    def test_the_open_prs_answer_becomes_head_and_ci_or_a_provider_error(self):
        def node(state):
            return {"number": 1, "headRefOid": "abc", "commits": {"nodes": [{"commit": {"statusCheckRollup": state}}]}}

        def answer(*nodes):
            return {"data": {"repository": {"pullRequests": {"nodes": list(nodes)}}}}

        self.assertEqual(checks.open_prs(answer(node({"state": "SUCCESS"}))), {"1": {"head": "abc", "ci": "pass", "failed": []}})
        runs = [
            {"__typename": "CheckRun", "name": "lint", "conclusion": "FAILURE", "detailsUrl": "https://ci/lint"},
            {"__typename": "StatusContext", "context": "deploy", "state": "ERROR", "targetUrl": "https://ci/deploy"},
            {"__typename": "CheckRun", "name": "build", "conclusion": "SUCCESS", "detailsUrl": "https://ci/build"},
        ]
        failed = checks.open_prs(answer(node({"state": "FAILURE", "contexts": {"nodes": runs}})))["1"]["failed"]
        self.assertEqual(failed, [{"name": "lint", "url": "https://ci/lint"}, {"name": "deploy", "url": "https://ci/deploy"}])
        self.assertEqual(checks.open_prs(answer(node(None)))["1"]["ci"], "none")
        for bad in (answer({"number": "1"}), answer("x")):
            with self.assertRaises(providers.ProviderError, msg=repr(bad)):
                checks.open_prs(bad)

    def test_gh_answering_a_bad_shape_reaches_the_caller_as_a_provider_error(self):
        g = GH()
        self.addCleanup(g.close)
        g.set(prs={"1": {"draft": False, "state": "OPEN", "comments": []}}, bad_view=True)
        with self.assertRaises(providers.ProviderError):
            registry.get(g.e.p).state(providers.Change(id="FN-1", pr=1))

    def test_no_operation_of_the_port_takes_or_returns_a_dict_of_any(self):
        bad = dict[str, Any]
        for cls in (providers.Provider, provider.GitHub, local.Local):
            for name, fn in inspect.getmembers(cls, callable):
                if name.startswith("_"):
                    continue
                hints = typing.get_type_hints(fn)
                self.assertNotIn(bad, hints.values(), f"{cls.__name__}.{name}")


if __name__ == "__main__":
    unittest.main()
