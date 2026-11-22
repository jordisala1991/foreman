"""A way to start a program outside `foreman/proc.py` fails `make lint`: the real target, run in a temporary tree."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from helpers import ROOT

MODULE = "plugins/foreman/foreman/services/agents/p.py"


def lint(tmp_path: Path, source: str) -> subprocess.CompletedProcess[str]:
    (tmp_path / MODULE).parent.mkdir(parents=True)
    (tmp_path / MODULE).write_text(source)
    shutil.copy(ROOT / "pyproject.toml", tmp_path)
    shutil.copy(ROOT / "uv.lock", tmp_path)
    env = {k: v for k, v in os.environ.items() if k not in ("MAKEFLAGS", "MAKELEVEL", "MFLAGS")}
    env["UV_PROJECT_ENVIRONMENT"] = sys.prefix
    return subprocess.run(["make", "-f", str(ROOT / "Makefile"), "lint"], cwd=tmp_path, capture_output=True, text=True, env=env)


@pytest.mark.parametrize(
    "source",
    [
        'import webbrowser\n\nwebbrowser.open("file:///tmp/x")\n',
        "import pty\n\npty.fork()\n",
        "import os\n\nos.fork()\n",
        "import os\n\nos.forkpty()\n",
        'import multiprocessing\n\nmultiprocessing.get_context("spawn").Process(target=print).start()\n',
        'import ctypes\n\nctypes.CDLL(None).system(b"ls")\n',
        "from multiprocessing import Process\n\nprint(Process)\n",
        "from concurrent.futures import ProcessPoolExecutor\n\nprint(ProcessPoolExecutor)\n",
        "import concurrent.futures\n\nprint(concurrent.futures.ProcessPoolExecutor)\n",
        "from concurrent.futures import process\n\nprint(process)\n",
        "import _posixsubprocess\n\nprint(_posixsubprocess)\n",
        "import _multiprocessing\n\nprint(_multiprocessing)\n",
        "import _ctypes\n\nprint(_ctypes)\n",
        "import socketserver\n\nprint(socketserver.ForkingMixIn)\n",
    ],
)
def test_a_way_to_start_a_program_fails_lint(tmp_path: Path, source: str) -> None:
    result = lint(tmp_path, source)
    assert result.returncode != 0
    assert "TID251" in result.stdout


def test_a_module_that_runs_nothing_passes_lint(tmp_path: Path) -> None:
    assert lint(tmp_path, "import os\n\nprint(os.getpid())\n").returncode == 0


def test_threads_do_not_start_a_program(tmp_path: Path) -> None:
    source = "from concurrent.futures import ThreadPoolExecutor\nfrom socketserver import ThreadingMixIn\n\nprint(ThreadPoolExecutor, ThreadingMixIn)\n"
    assert lint(tmp_path, source).returncode == 0
