"""`foreman projects --json` lists a project's `[merge] strategy` only when it is valid; an invalid one is a line on stderr (FN-359's rule)."""

import argparse
import json
from pathlib import Path
from typing import Any

import pytest
from helpers import Env

from foreman.core.project import registry as homes
from foreman.ui.cli import plugin


@pytest.fixture(autouse=True)
def own_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A registry of this test's own: a home left with a bad strategy must not reach another test's `projects --json`."""
    monkeypatch.setenv("FOREMAN_REGISTRY", str(tmp_path / "projects.json"))


def with_strategy(value: str) -> Env:
    e = Env()
    toml = e.home / "foreman.toml"
    toml.write_text(toml.read_text().replace("[merge]", f'[merge]\nstrategy = "{value}"'), encoding="utf-8")
    homes.register(e.home)
    return e


def listed(capsys: pytest.CaptureFixture[str]) -> tuple[list[dict[str, Any]], str]:
    plugin.cmd_projects(argparse.Namespace())
    out, err = capsys.readouterr()
    return json.loads(out), err


def test_a_strategy_that_is_not_valid_is_not_listed(capsys: pytest.CaptureFixture[str]):
    bad = with_strategy("sqash")
    good = Env()
    homes.register(good.home)
    rows, err = listed(capsys)
    assert [r for r in rows if r["home"] == str(bad.home)] == []
    assert next(r for r in rows if r["home"] == str(good.home))["strategy"] == "squash"
    assert str(bad.home) in err and "sqash" in err


def test_every_valid_strategy_is_listed(capsys: pytest.CaptureFixture[str]):
    for value in ("squash", "merge", "rebase"):
        e = with_strategy(value)
        rows, _ = listed(capsys)
        assert next(r for r in rows if r["home"] == str(e.home))["strategy"] == value
