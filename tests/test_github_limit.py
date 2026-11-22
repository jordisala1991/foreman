"""One `gh` runner for the GitHub adapter: a provider call obeys and records the rate limit's stop file, and maps gh's
failures as the backlog's calls do. `proc.run` is replaced by a stand-in that answers for gh and logs each argv."""

import time

import pytest
from helpers import Env

from foreman import proc, util
from foreman.providers import Change, registry
from foreman.providers.github.api import STOP_FILE
from foreman.providers.github.provider import GitHub
from foreman.providers.github.types import PermanentError, RateLimited, TransientError

CHANGE = Change(id="TY-1", pr=1)
SPENT = "gh: API rate limit already exceeded for user ID 1.\n"


class Gh:
    """Stands in for `proc.run`: `gh api rate_limit` gives the reset; any other gh call fails with `err` or raises `boom`."""

    def __init__(self) -> None:
        self.reset = int(time.time()) + 3600
        self.err = SPENT
        self.boom: proc.ProcError | None = None
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], **_: object) -> proc.Result:
        if argv[:3] == ["gh", "api", "rate_limit"]:
            return proc.Result(0, f"{self.reset}\n", "")
        self.calls.append(argv)
        if self.boom:
            raise self.boom
        return proc.Result(1, "", self.err)


@pytest.fixture
def gh(monkeypatch: pytest.MonkeyPatch) -> Gh:
    fake = Gh()
    monkeypatch.setattr(proc, "run", fake)
    return fake


def test_a_recorded_reset_stops_provider_calls(gh: Gh) -> None:
    e = Env()
    util.write_json(e.p.state / STOP_FILE, {"reset": gh.reset})
    with pytest.raises(RateLimited, match="no call until"):
        GitHub(e.p, registry.repo(e.p)).view(CHANGE)
    assert gh.calls == []


def test_a_spent_limit_answered_to_a_provider_call_is_recorded(gh: Gh) -> None:
    e = Env()
    provider = GitHub(e.p, registry.repo(e.p))
    with pytest.raises(RateLimited):
        provider.ready(CHANGE)
    assert util.read_json(e.p.state / STOP_FILE, None) == {"reset": gh.reset}
    with pytest.raises(RateLimited):
        provider.view(CHANGE)
    assert len(gh.calls) == 1


def test_provider_gh_failures_are_mapped_by_the_one_runner(gh: Gh) -> None:
    e = Env()
    provider = GitHub(e.p, registry.repo(e.p))
    gh.err = "HTTP 502"
    with pytest.raises(TransientError) as t:
        provider.view(CHANGE)
    assert not isinstance(t.value, RateLimited)
    assert not (e.p.state / STOP_FILE).exists()
    gh.err = "pull request is closed"
    with pytest.raises(PermanentError):
        provider.view(CHANGE)
    gh.boom = proc.ProcError("t", timed_out=True)
    with pytest.raises(TransientError):
        provider.view(CHANGE)
    gh.boom = proc.ProcError("x")
    with pytest.raises(PermanentError):
        provider.view(CHANGE)
