"""Fixtures shared by the tests."""

import os
from collections.abc import Iterator
from typing import Any
from unittest import mock

import pytest
from fakerepo import FakeRepo
from fakerunner import FakeRunner
from helpers import Env

from foreman.core.project.config import Project
from foreman.loop import queue
from foreman.providers import registry


@pytest.fixture
def env() -> Env:
    """A temporary project (helpers.Env) with the defaults."""
    return Env()


@pytest.fixture
def fake_git(monkeypatch: pytest.MonkeyPatch) -> FakeRepo:
    """An in-memory repo (fakerepo.FakeRepo) that every `registry.repo(p)` hands out for the test: no git runs."""
    fake = FakeRepo()

    def repo(p: Project) -> FakeRepo:
        return fake

    monkeypatch.setattr(registry, "repo", repo)
    return fake


@pytest.fixture
def fake_runner(monkeypatch: pytest.MonkeyPatch) -> FakeRunner:
    """A fake Runner (fakerunner.FakeRunner) that every `registry.runner(p)` hands out for the test: no claude runs."""
    fake = FakeRunner()

    def runner(p: Project) -> FakeRunner:
        return fake

    monkeypatch.setattr(registry, "runner", runner)
    return fake


@pytest.fixture
def owner() -> Iterator[None]:
    """The owner's shell: no agent session (FOREMAN_TASK) leaks in, whatever the caller's environment."""
    with mock.patch.dict(os.environ):
        os.environ.pop("FOREMAN_TASK", None)
        yield


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "real_session_check: the loop checks the session of a delivered change or bug task")


def not_returned(p: Project, i: dict[str, Any]) -> bool:
    return False


@pytest.fixture(autouse=True)
def no_session_check(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """The deliveries of the other tests have no session: the loop sends none back, unless the test is marked `real_session_check`."""
    if request.node.get_closest_marker("real_session_check") is None:
        monkeypatch.setattr(queue, "session_returned", not_returned)
