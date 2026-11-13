"""A test run never writes the owner's real ~/.config/fm: HOME and FM_REGISTRY point at a temp dir (see helpers)."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import REAL_REGISTRY, SANDBOX, fm

from fm import config, lock, panel


def leaked(registry: Path) -> list[str]:
    """Entries of `registry` that point into this run's sandbox: what a leaking test would have registered."""
    if not registry.exists():
        return []
    return [e for e in json.loads(registry.read_text()) if Path(e).resolve().is_relative_to(SANDBOX)]


class Sandbox(unittest.TestCase):
    def unset(self, *names: str) -> None:
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in names:
            os.environ.pop(name, None)

    def test_home_files_live_in_the_sandbox(self) -> None:
        self.unset("FM_REGISTRY", "FM_LOCKS")
        for path in (Path.home(), config.registry_file(), panel.token_file(), lock.root()):
            self.assertTrue(path.is_relative_to(SANDBOX), path)

    def test_init_registers_in_the_sandbox(self) -> None:
        self.unset("FM_REGISTRY")
        d = Path(tempfile.mkdtemp(prefix="fmtest-")).resolve()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        fm(d, "init", "--prefix", "ZZ", "--repo", "r")
        self.assertIn(d, config.registered())
        self.assertTrue(config.registry_file().is_relative_to(SANDBOX))

    def test_the_real_registry_has_nothing_of_this_run(self) -> None:
        self.assertEqual(leaked(REAL_REGISTRY), [])

    def test_the_guard_sees_only_this_runs_entries(self) -> None:
        registry = SANDBOX / "guard.json"
        registry.write_text(json.dumps(["/somewhere/else/home"]))  # another run, or the owner's own project
        self.assertEqual(leaked(registry), [])
        mine = str(SANDBOX / "tmp" / "fmtest-x" / "home")
        registry.write_text(json.dumps(["/somewhere/else/home", mine]))
        self.assertEqual(leaked(registry), [mine])
