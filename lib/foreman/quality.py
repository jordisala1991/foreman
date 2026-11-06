"""The selftest's code quality checks: ruff (lint), ruff format and pyright, at the versions pinned in pyproject.toml.

Development tools, not run-time dependencies: foreman still needs only the standard library. pyproject.toml's
`[dependency-groups] dev` is the one list of versions; `[tool.foreman.quality] checks` says which checks run
("ruff", "format", "pyright"); `[tool.ruff]` and `[tool.pyright]` configure the tools themselves.

Finding a tool, in order: `uvx --offline --from SPEC` (uv's cache, no network); else `uvx --from SPEC` once, saying
so («quality: fetching ruff 0.17.3 (first run)»), and the offline form from then on; else, without uv, the tool on
the PATH only at the pinned version (another version finds other things). pyright is `pyright==V` when node is on
the PATH and `pyright[nodejs]==V` (node as a PyPI wheel, in uv's cache) when it is not, so pyright never downloads
node by itself (to ~/.cache).

Modes (`selftest --quality=…`): `auto` skips a tool it cannot find with a visible SKIP line (a user without uv does
not fail on what is not theirs), `require` makes that a failure (foreman's CI), `off` runs nothing. A tool that runs
and finds something is a failure in both: one line per finding with file:line, the rule and the command that fixes
it, at most MAX_LINES per tool. The summary line also counts the `# noqa` and `# pyright: ignore` comments, so that
their number grows in plain sight.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import tokenize
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import toml_reader, util

if TYPE_CHECKING:
    from .util import PathLike

MODES = ("auto", "require", "off")
CHECKS = ("ruff", "format", "pyright")
TOOL_OF = {"ruff": "ruff", "format": "ruff", "pyright": "pyright"}
MAX_LINES = 30
FIX = "bin/foreman selftest --fix"
RECHECK = "bin/foreman selftest --quality-only"
INSTALL = "install uv (https://docs.astral.sh/uv/) or pip install --group dev"
PROBE_TIMEOUT = 120
FETCH_TIMEOUT = 600
RUN_TIMEOUT = 600
PIN = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*==\s*([^\s;,]+)")
SUPPRESS = re.compile(r"#\s*(noqa\b|(?:pyright|type):\s*ignore\b)")


class Settings:
    """What pyproject.toml says: {package: version} of the dev group, and the checks to run (in CHECKS order)."""

    pins: dict[str, str]
    checks: list[str]
    error: str | None

    def __init__(self, pins: dict[str, str], checks: list[str], error: str | None = None) -> None:
        self.pins, self.checks, self.error = pins, checks, error


def settings(root: PathLike) -> Settings:
    f = Path(root) / "pyproject.toml"
    try:
        data = toml_reader.loads(f.read_text())
    except OSError:
        return Settings({}, [], f"no {f.name}")
    except toml_reader.TOMLDecodeError as e:
        return Settings({}, [], f"{f.name}: {e}")
    pins = {}
    for req in (data.get("dependency-groups") or {}).get("dev") or []:
        m = PIN.match(req) if isinstance(req, str) else None
        if m:
            pins[m.group(1).lower()] = m.group(3)
    checks = ((data.get("tool") or {}).get("foreman") or {}).get("quality", {}).get("checks", [])
    unknown = [c for c in checks if c not in CHECKS]
    if unknown:
        return Settings(
            pins, [], f"{f.name}: [tool.foreman.quality] checks: unknown {unknown} (known: {', '.join(CHECKS)})"
        )
    return Settings(pins, [c for c in CHECKS if c in checks])


def tool_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment the tools run in: pyright's PyPI wrapper must not look for a newer version online."""
    return dict(os.environ if env is None else env, PYRIGHT_PYTHON_IGNORE_WARNINGS="1")


def spec(tool: str, version: str, path: str) -> str:
    if tool == "pyright" and not shutil.which("node", path=path):
        return f"pyright[nodejs]=={version}"
    return f"{tool}=={version}"


def probe(cmd: list[str], version: str, env: Mapping[str, str], timeout: float = PROBE_TIMEOUT) -> bool:
    """Whether `cmd --version` runs and names `version`."""
    try:
        r = subprocess.run(
            cmd + ["--version"],
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and version in r.stdout.split()


def find(tool: str, version: str, env: Mapping[str, str], say: Callable[[str], Any]) -> list[str] | None:
    """The command that runs `tool` at `version` (a list), or None. Says «fetching» before the one download."""
    path = env.get("PATH", "")
    uvx, uv = shutil.which("uvx", path=path), shutil.which("uv", path=path)
    runner = [uvx] if uvx else [uv, "tool", "run"] if uv else None
    if runner:
        s = spec(tool, version, path)
        offline = runner + ["--offline", "--from", s, tool]
        if probe(offline, version, env):
            return offline
        say(f"quality: fetching {tool} {version} (first run)")
        if probe(runner + ["--from", s, tool], version, env, FETCH_TIMEOUT) and probe(offline, version, env):
            return offline
    exe = shutil.which(tool, path=path)
    if exe and probe([exe], version, env):
        return [exe]
    return None


def rel(root: PathLike, name: str) -> str:
    try:
        return Path(name).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return name


def capped(tool: str, lines: list[str], more: str) -> list[str]:
    """At most MAX_LINES lines of a tool, then how many more and how to see them all."""
    if len(lines) <= MAX_LINES:
        return lines
    return lines[:MAX_LINES] + [f"FAIL {tool}: {len(lines) - MAX_LINES} more not shown ({more})"]


def run_tool(cmd: list[str], root: PathLike, env: Mapping[str, str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            env=env,
            timeout=RUN_TIMEOUT,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, -1, "", f"timed out after {RUN_TIMEOUT} s")
    except OSError as e:
        return subprocess.CompletedProcess(cmd, -1, "", str(e))


def broken(tool: str, r: subprocess.CompletedProcess[str]) -> list[str]:
    why = " ".join((r.stderr or r.stdout or "").strip().splitlines()[:3])[:300]
    return [f"FAIL {tool}: did not run (exit {r.returncode}): {why}"]


def check_ruff(cmd: list[str], root: PathLike, env: Mapping[str, str]) -> tuple[int, list[str]]:
    """(count, FAIL lines) of `ruff check` with the project's configuration."""
    r = run_tool(cmd + ["check", "--no-fix", "--output-format", "json", "."], root, env)
    try:
        found = json.loads(r.stdout) if r.returncode in (0, 1) else None
    except ValueError:
        found = None
    if not isinstance(found, list):
        return 1, broken("ruff", r)
    lines = []
    for d in sorted(found, key=lambda d: (d.get("filename") or "", (d.get("location") or {}).get("row") or 0)):
        loc = d.get("location") or {}
        safe = (d.get("fix") or {}).get("applicability") == "safe"
        how = f"fix: {FIX}" if safe else f"fix by hand; recheck: {RECHECK}"
        msg = " ".join(str(d.get("message") or "").split())
        lines.append(
            f"FAIL ruff {rel(root, d.get('filename') or '?')}:{loc.get('row', 0)}:{loc.get('column', 0)} "
            f"{d.get('code') or 'syntax'} {msg}  ({how})"
        )
    return len(found), capped("ruff", lines, " ".join(cmd) + " check")


def check_format(cmd: list[str], root: PathLike, env: Mapping[str, str]) -> tuple[int, list[str]]:
    """(files, FAIL lines) of `ruff format --check`."""
    r = run_tool(cmd + ["format", "--check", "--output-format", "json", "."], root, env)
    try:
        found = json.loads(r.stdout) if r.returncode in (0, 1) else None
    except ValueError:
        found = None
    if not isinstance(found, list):
        return 1, broken("ruff format", r)
    files = sorted({rel(root, d.get("filename") or "?") for d in found})
    if not files:
        return 0, []
    shown = ", ".join(files[:MAX_LINES]) + (f" and {len(files) - MAX_LINES} more" if len(files) > MAX_LINES else "")
    return len(files), [f"FAIL ruff format: {util.plural(len(files), 'file')} would change: {shown}  (fix: {FIX})"]


def check_pyright(cmd: list[str], root: PathLike, env: Mapping[str, str]) -> tuple[int, list[str]]:
    """(errors, FAIL lines) of pyright with [tool.pyright]; warnings and notes do not count."""
    r = run_tool(cmd + ["--outputjson", "-p", str(root)], root, env)
    try:
        data = json.loads(r.stdout)
        diags = [d for d in data.get("generalDiagnostics", []) if d.get("severity") == "error"]
    except (ValueError, AttributeError):
        return 1, broken("pyright", r)
    lines = []
    for d in sorted(diags, key=lambda d: (d.get("file", ""), d.get("range", {}).get("start", {}).get("line", 0))):
        start = (d.get("range") or {}).get("start") or {}
        msg = " ".join(str(d.get("message") or "").split())
        lines.append(
            f"FAIL pyright {rel(root, d.get('file') or '?')}:{start.get('line', -1) + 1}:"
            f"{start.get('character', -1) + 1} {d.get('rule') or 'error'}: {msg}  "
            f"(fix by hand; recheck: {RECHECK})"
        )
    return len(diags), capped("pyright", lines, " ".join(cmd) + " --outputjson")


RUNNERS = {"ruff": check_ruff, "format": check_format, "pyright": check_pyright}
UNITS = {"ruff": "problem", "format": "file", "pyright": "error"}


def suppressions(root: PathLike) -> tuple[int, int]:
    """(noqa, pyright/type ignore) comments in the Python that ruff checks (lib/, tests/, hooks/, dev/; not the
    vendored _tomli): comments only, read with tokenize."""
    noqa = ignore = 0
    root = Path(root)
    for d in ("lib", "tests", "hooks", "dev"):
        for f in sorted((root / d).rglob("*.py")):
            if "_tomli" in f.parts:
                continue
            try:
                with open(f, "rb") as fh:
                    toks = [t.string for t in tokenize.tokenize(fh.readline) if t.type == tokenize.COMMENT]
            except (OSError, SyntaxError, tokenize.TokenError):
                continue
            for c in toks:
                for m in SUPPRESS.finditer(c):
                    if m.group(1).startswith("noqa"):
                        noqa += 1
                    else:
                        ignore += 1
    return noqa, ignore


class Report:
    """What one quality run found: the lines to print, the problems (each counts as a selftest failure), the tools
    skipped (auto) and the summary line; `word` is its part of the selftest's own last line."""

    lines: list[str]
    problems: int
    skipped: list[str]
    line: str
    word: str

    def __init__(self) -> None:
        self.lines, self.problems, self.skipped, self.line, self.word = [], 0, [], "", "quality off"


def run(
    root: PathLike, mode: str = "auto", env: Mapping[str, str] | None = None, say: Callable[[str], Any] = print
) -> Report:
    """The quality checks of `root` in `mode`: a Report. `say` prints the «fetching» line at once (the rest is in the
    report, printed by the caller after the static checks)."""
    rep = Report()
    if mode == "off":
        return rep
    env = tool_env(env)
    s = settings(root)
    if s.error:
        rep.lines.append(f"FAIL quality: {s.error}")
        rep.problems += 1
    parts, versions, found = [], [], {}
    for check in CHECKS:
        if check not in s.checks:
            parts.append(f"{check} off")
            continue
        tool = TOOL_OF[check]
        version = s.pins.get(tool)
        if not version:
            rep.lines.append(f"FAIL quality: no {tool}==VERSION in pyproject.toml [dependency-groups] dev")
            rep.problems += 1
            parts.append(f"{check} not pinned")
            continue
        if tool not in found:
            found[tool] = find(tool, version, env, say)
            if found[tool]:
                versions.append(f"{tool} {version}")
        cmd = found[tool]
        if not cmd:
            why = (
                f"quality: {tool} {version} not available (no uv, not on the PATH at that version, or no network "
                f"the first time): {INSTALL}"
            )
            if mode == "require":
                rep.lines.append(f"FAIL {why}")
                rep.problems += 1
            else:
                rep.lines.append(f"SKIP {why}")
            if check not in rep.skipped:
                rep.skipped.append(check)
            parts.append(f"{check} skipped")
            continue
        n, lines = RUNNERS[check](cmd, root, env)
        rep.lines += lines
        rep.problems += n
        parts.append(f"{check} {util.plural(n, UNITS[check])}")
    noqa, ignore = suppressions(root)
    rep.line = (
        f"quality: {', '.join(parts)}"
        + (f" ({', '.join(versions)})" if versions else "")
        + f"; {noqa} noqa, {ignore} pyright: ignore"
    )
    rep.word = (
        f"quality {util.plural(rep.problems, 'problem')}"
        if rep.problems
        else f"quality skipped: {', '.join(rep.skipped)}"
        if rep.skipped
        else "quality ok"
    )
    return rep


class Background:
    """run() in a thread, so the tools run while the static checks (and the test workers) do."""

    def __init__(self, root: PathLike, mode: str, env: Mapping[str, str] | None = None) -> None:
        self.report = None
        self.thread = threading.Thread(target=self._run, args=(root, mode, env), daemon=True)
        self.thread.start()

    def _run(self, root: PathLike, mode: str, env: Mapping[str, str] | None) -> None:
        try:
            self.report = run(root, mode, env, say=lambda s: print(s, flush=True))
        except Exception as e:  # noqa: BLE001 (a bug here is one failed check, not a lost selftest)
            self.report = Report()
            self.report.lines.append(f"FAIL quality: {type(e).__name__}: {e}")
            self.report.problems = 1
            self.report.word = "quality 1 problem"

    def wait(self) -> Report:
        self.thread.join()
        assert self.report is not None  # _run always sets it
        return self.report


def snapshot(root: PathLike) -> dict[str, str]:
    """{path: sha1} of the Python files under `root` (hidden folders and ops/ aside): what --fix may change."""
    out = {}
    root = Path(root)
    for f in root.rglob("*.py"):
        r = f.relative_to(root)
        if r.parts[0] == "ops" or any(p.startswith(".") for p in r.parts):
            continue
        with contextlib.suppress(OSError):
            out[r.as_posix()] = hashlib.sha1(f.read_bytes()).hexdigest()
    return out


def fix(root: PathLike, env: Mapping[str, str] | None = None, out: Callable[[str], Any] = print) -> int:
    """`selftest --fix`: ruff's safe fixes (`ruff check --fix`) and, when the format check is on, `ruff format`;
    then which files changed. 0, or 1 when ruff could not be found or run."""
    env = tool_env(env)
    s = settings(root)
    version = s.pins.get("ruff")
    if s.error or not version:
        out(f"FAIL quality: {s.error or 'no ruff==VERSION in pyproject.toml [dependency-groups] dev'}")
        return 1
    cmd = find("ruff", version, env, out)
    if not cmd:
        out(
            f"FAIL quality: ruff {version} not available (no uv, not on the PATH at that version, or no network the "
            f"first time): {INSTALL}"
        )
        return 1
    t0 = time.monotonic()
    before = snapshot(root)
    steps = [("ruff check --fix", ["check", "--fix", "."])] + (
        [("ruff format", ["format", "."])] if "format" in s.checks else []
    )
    rc = 0
    for label, args in steps:
        r = run_tool(cmd + args, root, env)
        if r.returncode not in (0, 1):  # 1: findings left that need a hand
            out(broken(label, r)[0])
            rc = 1
    after = snapshot(root)
    changed = sorted(k for k in after if before.get(k) != after[k])
    what = " and ".join(label for label, _ in steps)
    out(
        f"fix: {what} changed {util.plural(len(changed), 'file')}"
        + (f": {', '.join(changed)}" if changed else "")
        + f" ({time.monotonic() - t0:.1f} s)"
    )
    return rc
