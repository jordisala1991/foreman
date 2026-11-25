"""A way to start a program outside `foreman/proc.py` fails `make lint`: the real target, run in a temporary tree; a
`loop.subprocess_exec` or `subprocess_shell` call, which ruff cannot see, fails
`test_no_module_uses_a_loop_method_that_runs_a_program`; a call of the builtin `__import__` fails
`test_no_module_calls_the_builtin_import`."""

import ast
from pathlib import Path

import pytest
from helpers import PLUGIN, lint

LOOP_METHODS = {"subprocess_exec", "subprocess_shell"}


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
        "from socketserver import ForkingTCPServer\n\nprint(ForkingTCPServer)\n",
        "from socketserver import ForkingUDPServer\n\nprint(ForkingUDPServer)\n",
        "from socketserver import ForkingUnixStreamServer\n\nprint(ForkingUnixStreamServer)\n",
        "from socketserver import ForkingUnixDatagramServer\n\nprint(ForkingUnixDatagramServer)\n",
        "import posix\n\nposix.fork()\n",
        "import posix\n\nposix.forkpty()\n",
        'import posix\n\nposix.system("ls")\n',
        "import posix\n\nprint(posix.posix_spawn)\n",
        "import posix\n\nprint(posix.execve)\n",
        "from posix import fork\n\nprint(fork)\n",
        'import importlib\n\nimportlib.import_module("subprocess").run(["ls"], check=False)\n',
        "from importlib import import_module\n\nprint(import_module)\n",
        'import runpy\n\nrunpy.run_module("subprocess")\n',
    ],
)
def test_a_way_to_start_a_program_fails_lint(tmp_path: Path, source: str) -> None:
    result = lint(tmp_path, source)
    assert result.returncode != 0
    assert "TID251" in result.stdout


def test_a_module_that_runs_nothing_passes_lint(tmp_path: Path) -> None:
    assert lint(tmp_path, "import os\n\nprint(os.getpid())\n").returncode == 0


def test_reading_the_package_version_passes_lint(tmp_path: Path) -> None:
    assert lint(tmp_path, 'import importlib.metadata\n\nprint(importlib.metadata.version("foreman"))\n').returncode == 0


def test_threads_do_not_start_a_program(tmp_path: Path) -> None:
    source = "from concurrent.futures import ThreadPoolExecutor\nfrom socketserver import ThreadingMixIn\n\nprint(ThreadPoolExecutor, ThreadingMixIn)\n"
    assert lint(tmp_path, source).returncode == 0


def modules_using_loop_methods(root: Path) -> set[str]:
    """The modules under `root` naming an attribute `subprocess_exec` or `subprocess_shell`: ruff cannot resolve the loop
    they are called on, so the name is the check."""
    return {
        f.relative_to(root).as_posix()
        for f in root.rglob("*.py")
        if any(isinstance(n, ast.Attribute) and n.attr in LOOP_METHODS for n in ast.walk(ast.parse(f.read_text())))
    }


def test_no_module_uses_a_loop_method_that_runs_a_program() -> None:
    assert modules_using_loop_methods(PLUGIN / "foreman") == set()


@pytest.mark.parametrize("method", sorted(LOOP_METHODS))
def test_a_module_that_calls_a_loop_method_is_named(tmp_path: Path, method: str) -> None:
    (tmp_path / "services/agents").mkdir(parents=True)
    (tmp_path / "services/agents/p2.py").write_text(
        f'import asyncio\n\n\nasync def go() -> None:\n    loop = asyncio.get_running_loop()\n    await loop.{method}(asyncio.SubprocessProtocol, "ls")\n'
    )
    (tmp_path / "services/ok.py").write_text("import asyncio\n\nprint(asyncio.run)\n")
    assert modules_using_loop_methods(tmp_path) == {"services/agents/p2.py"}


def modules_calling_dunder_import(root: Path) -> set[str]:
    """The modules under `root` calling `__import__` or `builtins.__import__`: ruff cannot ban a builtin by name, and
    `__import__("subprocess")` names the module in a string."""
    return {
        f.relative_to(root).as_posix()
        for f in root.rglob("*.py")
        if any(
            isinstance(n, ast.Call)
            and ((isinstance(n.func, ast.Name) and n.func.id == "__import__") or (isinstance(n.func, ast.Attribute) and n.func.attr == "__import__"))
            for n in ast.walk(ast.parse(f.read_text()))
        )
    }


def test_no_module_calls_the_builtin_import() -> None:
    assert modules_calling_dunder_import(PLUGIN / "foreman") == set()


@pytest.mark.parametrize(
    "source",
    ['__import__("subprocess").run(["ls"], check=False)\n', 'import builtins\n\nbuiltins.__import__("subprocess")\n'],
)
def test_a_module_that_calls_the_builtin_import_is_named(tmp_path: Path, source: str) -> None:
    (tmp_path / "services/agents").mkdir(parents=True)
    (tmp_path / "services/agents/q7.py").write_text(source)
    (tmp_path / "services/ok.py").write_text("import os\n\nprint(os.getpid())\n")
    assert modules_calling_dunder_import(tmp_path) == {"services/agents/q7.py"}
