"""`selftest --coverage`: the whole test suite under coverage.py, subprocesses included, and a table of what ran.

Development tool, not a run-time dependency (foreman needs only the standard library). The version is the `coverage`
pin of pyproject.toml's `[dependency-groups] dev`; `[tool.coverage.run]` there says what is measured (`lib/` without the
vendored _tomli, `patch = ["subprocess"]` so that the workers, the launcher and the hooks the tests start are measured
too). Finding coverage is like finding the quality tools (quality.py): a Python that has it at the pinned version
(`uv run --offline --no-project --with coverage==V python`, from uv's cache), else the same once with the network,
saying «coverage: fetching coverage V (first run)», else this Python if it has that version. The tests run in that
Python (so do the processes they start: the launcher uses the tests' Python). Without it, `quality=auto` prints a SKIP
line and exits 0, `quality=require` fails.

The data files go to a folder under $TMPDIR (COVERAGE_FILE), removed at the end: a coverage run writes nothing into the
repository. Output: the selftest's own, then a table per module from the least covered and the total. No threshold.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import quality

if TYPE_CHECKING:
    from .util import PathLike

PROBE = "import coverage; print(coverage.__version__)"
# `uv run` puts its temporary environment first on the PATH: the suite must see the PATH it has without --coverage
# (the golden corpus records it), so the PATH travels in this variable and the measured Python puts it back.
RUN = (
    "import os, sys; os.environ['PATH'] = os.environ.pop('FOREMAN_COVERAGE_PATH'); "
    "from coverage.cmdline import main; sys.exit(main())"
)
TIMEOUT = 3600


def probe(cmd: list[str], version: str, env: Mapping[str, str], timeout: float = quality.PROBE_TIMEOUT) -> bool:
    """Whether `cmd` (a Python) can import coverage at `version`."""
    try:
        r = subprocess.run(
            cmd + ["-c", PROBE],
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and r.stdout.strip() == version


def find(version: str, env: Mapping[str, str], say: Callable[[str], Any]) -> list[str] | None:
    """The command (a list) of a Python with coverage `version`, or None."""
    uv = shutil.which("uv", path=env.get("PATH", ""))
    if uv:
        # the Python that runs foreman, so the suite is measured on the same one as without --coverage
        pre = ["--no-project", "--python", sys.executable, "--with", f"coverage=={version}", "python"]
        base = [uv, "run"] + pre
        offline = [uv, "run", "--offline"] + pre
        if probe(offline, version, env):
            return offline
        say(f"coverage: fetching coverage {version} (first run)")
        if probe(base, version, env, quality.FETCH_TIMEOUT) and probe(offline, version, env):
            return offline
    if probe([sys.executable], version, env):
        return [sys.executable]
    return None


def table(report: str) -> list[str]:
    """The `coverage report` lines: the header, the modules and the total, as they are."""
    return [line for line in report.splitlines() if line.strip()]


def total_of(report: str) -> str | None:
    """The total percentage of a `coverage report` (a string like «92%»), or None."""
    for line in report.splitlines():
        parts = line.split()
        if parts and parts[0] == "TOTAL" and parts[-1].endswith("%"):
            return parts[-1]
    return None


def run(
    root: PathLike,
    mode: str = "auto",
    jobs: int | None = None,
    pattern: str | None = None,
    env: Mapping[str, str] | None = None,
    say: Callable[[str], Any] = print,
) -> int:
    """The suite under coverage: 0 when it passed and the report was made, else 1; 0 with a SKIP line when coverage
    cannot be had in `auto`."""
    env = quality.tool_env(env)
    s = quality.settings(root)
    version = s.pins.get("coverage")
    if s.error or not version:
        say(f"FAIL coverage: {s.error or 'no coverage==VERSION in pyproject.toml [dependency-groups] dev'}")
        return 1
    py = find(version, env, say)
    if not py:
        why = (
            f"coverage: coverage {version} not available (no uv, not that version in this Python, or no network "
            f"the first time): {quality.INSTALL}"
        )
        if mode == "require":
            say(f"FAIL {why}")
            return 1
        say(f"SKIP {why}")
        return 0
    root = Path(root)
    tmp = tempfile.mkdtemp(prefix="foreman-coverage-")
    try:
        env = dict(
            env,
            FOREMAN_COVERAGE_PATH=env.get("PATH", ""),
            COVERAGE_FILE=str(Path(tmp) / ".coverage"),
            COVERAGE_RCFILE=str(root / "pyproject.toml"),
        )
        env.pop("FOREMAN_PYTHON", None)
        args = ["lib/main.py", "selftest", "--quality=off", "--mods=off"]
        args += (["-j", str(jobs)] if jobs is not None else []) + ([pattern] if pattern else [])
        sys.stdout.flush()
        rc = subprocess.run(py + ["-c", RUN, "run"] + args, cwd=str(root), env=env, check=False).returncode
        out = []
        for step in (["combine", "--keep"], ["report", "--sort=cover", "--skip-empty"]):
            r = subprocess.run(
                py + ["-m", "coverage"] + step,
                cwd=str(root),
                env=env,
                capture_output=True,
                text=True,
                timeout=TIMEOUT,
                check=False,
            )
            if r.returncode != 0 and step[0] == "report":
                why = (r.stderr or r.stdout).strip()[:300]
                say(f"FAIL coverage: report did not run (exit {r.returncode}): {why}")
                return 1
            out.append(r.stdout)
        say("\n".join(table(out[1])))
        total = total_of(out[1])
        suite = "passed" if rc == 0 else "FAILED"
        say(f"coverage: total {total or '?'} of lib/ (coverage {version}; the suite {suite})")
        return 0 if rc == 0 and total else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
