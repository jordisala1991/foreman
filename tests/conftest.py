"""Fixtures shared by the tests."""

import os
from collections.abc import Iterator
from unittest import mock

import pytest
from fakerepo import FakeRepo
from fakerunner import FakeRunner
from helpers import Env

from foreman.providers import registry


@pytest.fixture
def env() -> Env:
    """A temporary project (helpers.Env) with the defaults."""
    return Env()


@pytest.fixture
def fake_git(monkeypatch: pytest.MonkeyPatch) -> FakeRepo:
    """An in-memory repo (fakerepo.FakeRepo) that every `registry.repo(p)` hands out for the test: no git runs."""
    fake = FakeRepo()
    monkeypatch.setattr(registry, "repo", lambda p: fake)
    return fake


@pytest.fixture
def fake_runner(monkeypatch: pytest.MonkeyPatch) -> FakeRunner:
    """A fake Runner (fakerunner.FakeRunner) that every `registry.runner(p)` hands out for the test: no claude runs."""
    fake = FakeRunner()
    monkeypatch.setattr(registry, "runner", lambda p: fake)
    return fake


@pytest.fixture
def owner() -> Iterator[None]:
    """The owner's shell: no agent session (FOREMAN_TASK) leaks in, whatever the caller's environment."""
    with mock.patch.dict(os.environ):
        os.environ.pop("FOREMAN_TASK", None)
        yield
