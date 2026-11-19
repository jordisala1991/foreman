"""What gh says is turned into fm's own types at the border (see test_github for the fake gh)."""

import inspect
import typing
from typing import Any

import pytest
from test_github import GH

from foreman import providers
from foreman.providers import local, registry
from foreman.providers.github import checks, provider


class TestBorder:
    """What gh says is turned into fm's own types at the border; a shape that does not fit is a clear ProviderError."""

    GOOD = {"number": 7, "url": "https://github.com/o/r/pull/7", "isDraft": True, "state": "OPEN", "mergeCommit": None}

    def test_a_pull_request_becomes_a_typed_value(self):
        pr = provider.pull_request(self.GOOD)
        assert (pr.number, pr.url, pr.draft, pr.state, pr.merge_commit) == (7, "https://github.com/o/r/pull/7", True, "OPEN", "")
        merged = provider.pull_request({**self.GOOD, "state": "MERGED", "mergeCommit": {"oid": "abc"}})
        assert (merged.state, merged.merge_commit) == ("MERGED", "abc")

    def test_a_pull_request_of_the_wrong_shape_is_a_provider_error(self):
        bad_pulls: list[Any] = [
            [],
            "x",
            {**self.GOOD, "state": "SIDEWAYS"},
            {**self.GOOD, "number": "7"},
            {**self.GOOD, "number": True},
            {**self.GOOD, "isDraft": "yes"},
            {**self.GOOD, "mergeCommit": "abc"},
            {**self.GOOD, "mergeCommit": {"oid": 1}},
            {k: v for k, v in self.GOOD.items() if k != "url"},
        ]
        for bad in bad_pulls:
            with pytest.raises(providers.ProviderError):
                provider.pull_request(bad)

    def test_the_open_prs_answer_becomes_head_and_ci_or_a_provider_error(self):
        def node(state: Any) -> dict[str, Any]:
            return {"number": 1, "headRefOid": "abc", "commits": {"nodes": [{"commit": {"statusCheckRollup": state}}]}}

        def answer(*nodes: Any) -> dict[str, Any]:
            return {"data": {"repository": {"pullRequests": {"nodes": list(nodes)}}}}

        assert checks.open_prs(answer(node({"state": "SUCCESS"}))) == {"1": {"head": "abc", "ci": "pass", "failed": []}}
        runs = [
            {"__typename": "CheckRun", "name": "lint", "conclusion": "FAILURE", "detailsUrl": "https://ci/lint"},
            {"__typename": "StatusContext", "context": "deploy", "state": "ERROR", "targetUrl": "https://ci/deploy"},
            {"__typename": "CheckRun", "name": "build", "conclusion": "SUCCESS", "detailsUrl": "https://ci/build"},
        ]
        failed = checks.open_prs(answer(node({"state": "FAILURE", "contexts": {"nodes": runs}})))["1"]["failed"]
        assert failed == [{"name": "lint", "url": "https://ci/lint"}, {"name": "deploy", "url": "https://ci/deploy"}]
        assert checks.open_prs(answer(node(None)))["1"]["ci"] == "none"
        for bad in (answer({"number": "1"}), answer("x")):
            with pytest.raises(providers.ProviderError):
                checks.open_prs(bad)

    def test_gh_answering_a_bad_shape_reaches_the_caller_as_a_provider_error(self, request: pytest.FixtureRequest):
        g = GH()
        request.addfinalizer(g.close)
        g.set(prs={"1": {"draft": False, "state": "OPEN", "comments": []}}, bad_view=True)
        with pytest.raises(providers.ProviderError):
            registry.get(g.e.p).state(providers.Change(id="FN-1", pr=1))

    def test_no_operation_of_the_port_takes_or_returns_a_dict_of_any(self):
        bad = dict[str, Any]
        for cls in (providers.Provider, provider.GitHub, local.Local):
            for name, fn in inspect.getmembers(cls, callable):
                if name.startswith("_"):
                    continue
                hints = typing.get_type_hints(fn)
                assert bad not in hints.values(), f"{cls.__name__}.{name}"
