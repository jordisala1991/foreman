"""`make size` fails on a foreman/ module over 300 lines, also in a directory without `__init__.py`: the real target, run in a
temporary tree. Given a package, pylint walks only its subpackages, so the tree needs the `__init__.py` chain to reproduce."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from helpers import ROOT

PACKAGE = ["plugins/foreman/foreman/__init__.py", "plugins/foreman/foreman/services/__init__.py", "plugins/foreman/foreman/services/agents/__init__.py"]
BIG = "plugins/foreman/foreman/services/agents/nopkg/big.py"


def size(tmp_path: Path, lines: int) -> subprocess.CompletedProcess[str]:
    for name in [*PACKAGE, "tests/t.py", BIG]:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("X = 1\n" * (lines if name == BIG else 1))
    shutil.copy(ROOT / "pyproject.toml", tmp_path)
    shutil.copy(ROOT / "uv.lock", tmp_path)
    env = {k: v for k, v in os.environ.items() if k not in ("MAKEFLAGS", "MAKELEVEL", "MFLAGS")}
    env["UV_PROJECT_ENVIRONMENT"] = sys.prefix
    return subprocess.run(["make", "-f", str(ROOT / "Makefile"), "size"], cwd=tmp_path, capture_output=True, text=True, env=env)


def test_a_module_over_the_limit_fails_in_a_directory_without_init(tmp_path: Path) -> None:
    result = size(tmp_path, 402)
    assert result.returncode != 0
    assert "too-many-lines" in result.stdout


def test_a_module_under_the_limit_passes(tmp_path: Path) -> None:
    assert size(tmp_path, 200).returncode == 0
