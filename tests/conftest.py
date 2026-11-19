"""Fixtures shared by the tests."""

import os
from collections.abc import Iterator
from unittest import mock

import pytest
from fakerepo import FakeRepo
from fakerunner import FakeRunner
from helpers import Env

from foreman.core.project.config import Project
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
