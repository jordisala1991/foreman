"""A test run never writes the owner's real ~/.config/foreman: HOME and FOREMAN_REGISTRY point at a temp dir (see helpers), and nothing of an
agent's session (FOREMAN_TASK…) reaches a test."""

import functools
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest
from helpers import PLUGIN, REAL_REGISTRY, SANDBOX, fm

from foreman.core.project import registry
from foreman.services import lock
from foreman.ui.panel import server


def leaked(registry: Path) -> list[str]:
    """Entries of `registry` that point into this run's sandbox: what a leaking test would have registered."""
    if not registry.exists():
        return []
    return [e for e in json.loads(registry.read_text()) if Path(e).resolve().is_relative_to(SANDBOX)]


class TestSandbox:
    @pytest.fixture(autouse=True)
    def keep_request(self, request: pytest.FixtureRequest) -> None:
        self.request = request  # for the cleanups of helpers

    def unset(self, *names: str) -> None:
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.request.addfinalizer(patcher.stop)
        for name in names:
            os.environ.pop(name, None)

    def test_an_agents_session_variables_do_not_reach_the_tests(self) -> None:
        session = {"FOREMAN_TASK": "AL-1", "FOREMAN_AGENT": "al-1", "FOREMAN_HOME": "/x/home", "FOREMAN_PROJECT": "al", "FOREMAN_PINNED": "/x/v1"}
        code = f"import os, helpers; print(sorted(k for k in {sorted(session)!r} if k in os.environ))"
        path = os.pathsep.join([str(Path(__file__).parent), str(PLUGIN)])
        env = {**os.environ, **session, "PYTHONPATH": path}
        out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True).stdout
        assert out.strip() == "[]"

    def test_home_files_live_in_the_sandbox(self) -> None:
        self.unset("FOREMAN_REGISTRY", "FOREMAN_LOCKS")
        for path in (Path.home(), registry.registry_file(), server.token_file(), lock.root()):
            assert path.is_relative_to(SANDBOX), path
        assert registry.registry_file() == Path.home() / ".config/foreman/projects.json"
        assert server.token_file() == Path.home() / ".config/foreman/panel.token"
        assert lock.root() == Path.home() / ".local/state/foreman/locks"

    def test_init_registers_in_the_sandbox(self, request: pytest.FixtureRequest) -> None:
        self.unset("FOREMAN_REGISTRY")
        d = Path(tempfile.mkdtemp(prefix="fmtest-")).resolve()
        request.addfinalizer(functools.partial(shutil.rmtree, d, ignore_errors=True))
        fm(d, "init", "--prefix", "ZZ", "--repo", "r")
        assert d in registry.registered()
        assert registry.registry_file().is_relative_to(SANDBOX)

    def test_the_real_registry_has_nothing_of_this_run(self) -> None:
        assert leaked(REAL_REGISTRY) == []

    def test_the_guard_sees_only_this_runs_entries(self) -> None:
        registry = SANDBOX / "guard.json"
        registry.write_text(json.dumps(["/somewhere/else/home"]))  # another run, or the owner's own project
        assert leaked(registry) == []
        mine = str(SANDBOX / "tmp" / "fmtest-x" / "home")
        registry.write_text(json.dumps(["/somewhere/else/home", mine]))
        assert leaked(registry) == [mine]
