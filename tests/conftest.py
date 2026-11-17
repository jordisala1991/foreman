"""Fixtures shared by the tests."""

import os
from collections.abc import Iterator
from unittest import mock

import pytest
from helpers import Env


@pytest.fixture
def env() -> Env:
    """A temporary project (helpers.Env) with the defaults."""
    return Env()


@pytest.fixture
def owner() -> Iterator[None]:
    """The owner's shell: no agent session (FOREMAN_TASK) leaks in, whatever the caller's environment."""
    with mock.patch.dict(os.environ):
        os.environ.pop("FOREMAN_TASK", None)
        yield
