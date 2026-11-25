"""`make suppressions` fails on any pyright, pylint, fmt, isort or yapf directive comment, under foreman/ and tests/: the real target,
run in a temporary git repo."""

import os
import subprocess
from pathlib import Path

import pytest
from helpers import ROOT

# Split so this file holds no directive the target would flag.
PYRIGHT = "pyr" + "ight"
PYLINT = "py" + "lint"
FMT = "f" + "mt"
ISORT = "is" + "ort"
YAPF = "ya" + "pf"
PATHS = ["plugins/foreman/foreman/services/p.py", "tests/t.py"]
DIRECTIVES = [
    (PYLINT, "skip-file"),
    (PYLINT, "disable-next=C0302"),
    (PYLINT, "enable=too-many-lines"),
    (PYRIGHT, "standard"),
    (PYRIGHT, "reportMissingParameterType=false"),
    (PYRIGHT, "ignore"),
    (PYRIGHT, "basic"),
    (PYRIGHT, "off"),
    (FMT, "off"),
    (FMT, "skip"),
    (ISORT, "skip_file"),
    (ISORT, "off"),
    (YAPF, "disable"),
    ("ruff: " + ISORT, "skip_file"),
]


def suppressions(tmp_path: Path, relpath: str, content: str) -> subprocess.CompletedProcess[str]:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    file = tmp_path / relpath
    file.parent.mkdir(parents=True)
    file.write_text(content)
    subprocess.run(["git", "add", relpath], cwd=tmp_path, check=True)
    env = {k: v for k, v in os.environ.items() if k not in ("MAKEFLAGS", "MAKELEVEL", "MFLAGS")}
    return subprocess.run(["make", "-f", str(ROOT / "Makefile"), "suppressions"], cwd=tmp_path, capture_output=True, text=True, env=env)


@pytest.mark.parametrize("relpath", PATHS)
@pytest.mark.parametrize(("tool", "rest"), DIRECTIVES)
def test_a_directive_comment_fails(tmp_path: Path, relpath: str, tool: str, rest: str) -> None:
    result = suppressions(tmp_path, relpath, f"# {tool}: {rest}\nX = 1\n")
    assert result.returncode != 0
    assert "suppressions: fix the cause" in result.stdout + result.stderr


@pytest.mark.parametrize("relpath", PATHS)
def test_a_file_without_directives_passes(tmp_path: Path, relpath: str) -> None:
    assert suppressions(tmp_path, relpath, "X = 1\n").returncode == 0
