"""One `gh` runner for the GitHub adapter: a provider call obeys and records the rate limit's stop file, and maps gh's
failures as the backlog's calls do. `proc.run` is replaced by a stand-in that answers for gh and logs each argv."""

import json
import time

import pytest
from helpers import Env

from foreman import proc, util
from foreman.providers import Change, registry
from foreman.providers.github.api import STOP_FILE, Api
from foreman.providers.github.provider import GitHub
from foreman.providers.github.types import PermanentError, RateLimited, TransientError

CHANGE = Change(id="TY-1", pr=1)
SPENT = "gh: API rate limit already exceeded for user ID 1.\n"


class Gh:
    """Stands in for `proc.run`: `gh api rate_limit` gives `resources` as GitHub's JSON; any other gh call fails with `err` or raises `boom`."""

    def __init__(self) -> None:
        self.reset = int(time.time()) + 1800  # not a short wait, so a wrong stop shows
        self.resources = {"core": (0, self.reset), "graphql": (5000, self.reset + 1800)}  # name: (remaining, reset)
        self.rate_limit_calls = 0
        self.err = SPENT
        self.boom: proc.ProcError | None = None
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], **_: object) -> proc.Result:
        if argv[:3] == ["gh", "api", "rate_limit"]:
            self.rate_limit_calls += 1
            return proc.Result(0, json.dumps({"resources": {k: {"remaining": n, "reset": at} for k, (n, at) in self.resources.items()}}), "")
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


SECONDARY = "You have exceeded a secondary rate limit. (HTTP 403)\n"


def stop(e: Env) -> dict[str, int]:
    return util.json_object(util.read_json(e.p.state / STOP_FILE, None)) or {}


def assert_stops_for(e: Env, before: int, seconds: int) -> None:
    reset = stop(e)["reset"]
    assert before + seconds <= reset <= int(time.time()) + seconds


def limited(e: Env) -> None:
    with pytest.raises(RateLimited):
        GitHub(e.p, registry.repo(e.p)).ready(CHANGE)


def test_a_core_limit_records_the_core_reset(gh: Gh) -> None:
    e = Env()
    with pytest.raises(RateLimited):
        GitHub(e.p, registry.repo(e.p)).ready(CHANGE)
    assert util.read_json(e.p.state / STOP_FILE, None) == {"reset": gh.reset}


def test_a_graphql_limit_records_the_graphql_reset(gh: Gh) -> None:
    e = Env()
    gh.resources = {"core": (5000, gh.reset), "graphql": (0, gh.reset + 900)}
    with pytest.raises(RateLimited):
        GitHub(e.p, registry.repo(e.p)).ready(CHANGE)
    assert util.read_json(e.p.state / STOP_FILE, None) == {"reset": gh.reset + 900}


def test_with_two_spent_resources_the_earliest_reset_is_kept(gh: Gh) -> None:
    e = Env()
    gh.resources = {"core": (0, gh.reset + 600), "graphql": (0, gh.reset + 1800)}
    with pytest.raises(RateLimited):
        GitHub(e.p, registry.repo(e.p)).ready(CHANGE)
    assert util.read_json(e.p.state / STOP_FILE, None) == {"reset": gh.reset + 600}


def test_a_secondary_limit_without_a_wait_stops_for_a_minute(gh: Gh) -> None:
    gh.err = SECONDARY
    e, before = Env(), int(time.time())
    limited(e)
    assert_stops_for(e, before, 60)
    assert gh.rate_limit_calls == 0


@pytest.mark.parametrize(
    ("err", "seconds"),
    [
        ("secondary rate limit. Please wait 120 seconds", 120),
        ("secondary rate limit\nRetry-After: 45", 45),
        ("secondary rate limit. Please wait 2 minutes", 120),
        ("secondary rate limit. Please wait 1 minute", 60),
        ("secondary rate limit\nRetry-After: 99999999", 900),
        ("secondary rate limit. Please wait 99 minutes", 900),
    ],
)
def test_a_secondary_limit_stops_for_the_wait_github_gives(gh: Gh, err: str, seconds: int) -> None:
    gh.err = err
    e, before = Env(), int(time.time())
    limited(e)
    assert_stops_for(e, before, seconds)


@pytest.mark.parametrize(("wait", "ended", "seconds"), [(60, 10, 120), (600, 10, 900), (900, 10, 900), (60, 1000, 60)])
def test_a_new_secondary_limit_doubles_the_wait_up_to_fifteen_minutes(gh: Gh, wait: int, ended: int, seconds: int) -> None:
    gh.err = SECONDARY
    e = Env()
    util.write_json(e.p.state / STOP_FILE, {"reset": int(time.time()) - ended, "wait": wait})
    before = int(time.time())
    limited(e)
    assert_stops_for(e, before, seconds)
    assert stop(e)["wait"] == seconds


def test_no_spent_resource_stops_for_a_minute(gh: Gh) -> None:
    gh.resources = {"core": (5000, gh.reset), "graphql": (5000, gh.reset + 1800)}
    e, before = Env(), int(time.time())
    limited(e)
    assert_stops_for(e, before, 60)
    assert gh.rate_limit_calls == 1


def test_the_error_that_stopped_is_logged_once(gh: Gh) -> None:
    gh.err = SECONDARY
    e = Env()
    provider = GitHub(e.p, registry.repo(e.p))
    with pytest.raises(RateLimited):
        provider.ready(CHANGE)
    with pytest.raises(RateLimited):
        provider.view(CHANGE)
    lines = [json.loads(line) for line in (e.p.state / "errors.jsonl").read_text().splitlines()]
    assert len(lines) == 1
    assert lines[0]["step"].startswith("gh")
    assert "secondary rate limit" in lines[0]["error"]


def test_query_and_run_share_the_stop_length(gh: Gh) -> None:
    gh.err = SECONDARY
    e, before = Env(), int(time.time())
    with pytest.raises(RateLimited):
        Api(e.p.state / STOP_FILE).query("query { x }")
    assert_stops_for(e, before, 60)
    assert len((e.p.state / "errors.jsonl").read_text().splitlines()) == 1
    util.write_json(e.p.state / STOP_FILE, {"reset": int(time.time()) - 10, "wait": 60})
    before = int(time.time())
    with pytest.raises(RateLimited):
        Api(e.p.state / STOP_FILE).query("query { x }")
    assert_stops_for(e, before, 120)


def test_a_permanent_failure_that_mentions_the_limit_is_not_a_stop(gh: Gh) -> None:
    e = Env()
    gh.err = "pull request is closed: title says rate limit"
    with pytest.raises(PermanentError):
        GitHub(e.p, registry.repo(e.p)).view(CHANGE)
    assert not (e.p.state / STOP_FILE).exists()
    assert gh.rate_limit_calls == 0
