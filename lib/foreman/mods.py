"""The selftest's check of foreman's hooks module (hooks/mod/foreman.ts) with the installed Claude Code.

Two commands of Claude Code, neither of which needs a session, the network or writes into the repo:

- `claude plugin validate --strict --json .claude-plugin/plugin.json`: the manifest, the classic hooks file and the
  module's source as the engine reads them. It must pass, the module must hook exactly MODULE_HOOKS (its `hooks:`
  note) and every gating hook it has must have a `.catch` (`gatingHooks[].hasCatch`).
- `claude plugin test .`: the module's tests (tests/mod/*.test.ts, with tests/mod/contract.ts) against the engine.

The binary is `$CLAUDE_CODE_EXECPATH` (set inside a Claude Code session) or `claude` on the PATH. Modes, like the
quality checks (`selftest --mods=…`): `auto` skips with a visible SKIP line when there is no Claude Code or it is
older than versions.CLAUDE_CODE_MIN (it may not know mods), `require` makes both a failure (foreman's own CI), `off`
runs nothing. A command that runs and finds something is a failure in both modes.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from . import quality, util, versions

if TYPE_CHECKING:
    from .util import PathLike

MODES = ("auto", "require", "off")
MODULE = "./mod/foreman.ts"
MODULE_HOOKS = [
    "session.start",
    "session.measure",
    "turn.start",
    "turn.complete",
    "tool.call",
    "prompt.submit",
    "ui.render{component=AbovePrompt}",
    "command.run{command=foreman:status}",
    "session.end",
]  # what the module hooks today: a hook added or lost is a deliberate change here
TIMEOUT = 300
PASSED = re.compile(r"^\s*(\d+) pass\s*$", re.M)
FAILED = re.compile(r"^\s*(\d+) fail\s*$", re.M)


def claude(env: Mapping[str, str]) -> str | None:
    exe = env.get("CLAUDE_CODE_EXECPATH")
    if exe and os.path.isfile(exe):
        return exe
    return shutil.which("claude", path=env.get("PATH"))


def call(cmd: list[str], root: PathLike, env: Mapping[str, str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            env=env,
            timeout=TIMEOUT,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, -1, "", f"timed out after {TIMEOUT} s")
    except OSError as e:
        return subprocess.CompletedProcess(cmd, -1, "", str(e))


def short(r: subprocess.CompletedProcess[str]) -> str:
    return " ".join((r.stderr or r.stdout or "").strip().splitlines()[-3:])[:300]


def check_validate(exe: str, root: PathLike, env: Mapping[str, str]) -> tuple[list[str], str]:
    """FAIL lines of `claude plugin validate --strict --json` on the manifest, and its summary."""
    r = call([exe, "plugin", "validate", "--strict", "--json", ".claude-plugin/plugin.json"], root, env)
    try:
        d = json.loads(r.stdout)
    except ValueError:
        return [f"FAIL mods: claude plugin validate did not run (exit {r.returncode}): {short(r)}"], "validate failed"
    lines = []
    parts = [d.get("manifest") or {}] + list(d.get("contents") or [])
    for part in parts:
        name = Path(part.get("file") or "?").name
        for kind in ("errors", "warnings"):
            for x in part.get(kind) or []:
                lines.append(f"FAIL mods: validate {name}: {x.get('path') or ''} {x.get('message') or x}".strip())
    if not d.get("success") and not lines:
        lines.append(f"FAIL mods: claude plugin validate --strict failed (exit {r.returncode}): {short(r)}")
    notes = [n for part in parts for n in part.get("notes") or []]
    hooked = next((n.split(" hooks: ", 1)[1] for n in notes if n.startswith(f"{MODULE} hooks: ")), None)
    got = [h.strip() for h in hooked.split(",")] if hooked else []
    if got != MODULE_HOOKS:
        lines.append(
            f"FAIL mods: {MODULE} hooks {', '.join(got) or 'nothing'}; expected {', '.join(MODULE_HOOKS)} "
            "(lib/foreman/mods.py MODULE_HOOKS)"
        )
    gating = [g for part in parts for g in part.get("gatingHooks") or []]
    for g in gating:
        if not g.get("hasCatch"):
            lines.append(f"FAIL mods: gating hook without .catch: {g.get('module')} {g.get('hook')}")
    return lines, f"validate hooks {', '.join(got) or 'nothing'}, {len(gating)} gating"


def check_test(exe: str, root: PathLike, env: Mapping[str, str]) -> tuple[list[str], str]:
    """FAIL lines of `claude plugin test`, and its summary («7 tests pass»)."""
    r = call([exe, "plugin", "test", "."], root, env)
    out = (r.stdout or "") + (r.stderr or "")
    passed = sum(int(m) for m in PASSED.findall(out))
    failed = sum(int(m) for m in FAILED.findall(out))
    if r.returncode == 0 and passed and not failed:
        return [], f"{util.plural(passed, 'test')} pass"
    lines = [ln for ln in out.splitlines() if ln.startswith("(fail)")][: quality.MAX_LINES]
    lines = [f"FAIL mods: plugin test {ln[len('(fail)') :].strip()}" for ln in lines]
    if not lines:
        lines = [f"FAIL mods: claude plugin test (exit {r.returncode}, {passed} pass, {failed} fail): {short(r)}"]
    return lines, f"{passed} pass, {failed} fail"


def run(root: PathLike, mode: str = "auto", env: Mapping[str, str] | None = None) -> quality.Report:
    """The mods check of `root` in `mode`: a quality.Report (lines, problems, skipped, line, word)."""
    rep = quality.Report()
    rep.word = "mods off"
    if mode == "off":
        return rep
    env = dict(os.environ if env is None else env)
    exe = claude(env)
    found = None
    if exe:
        r = call([exe, "--version"], root, env)
        t = versions.version_tuple(r.stdout) if r.returncode == 0 else None
        found = ".".join(str(x) for x in t) if t else None
    why = (
        "no Claude Code (claude on the PATH or $CLAUDE_CODE_EXECPATH)"
        if not exe
        else "cannot read `claude --version`"
        if not found
        else f"Claude Code {found} is older than {versions.CLAUDE_CODE_MIN}, foreman's minimum"
        if versions.claude_code_below_min(found)
        else None
    )
    if why:
        if mode == "require":
            rep.lines.append(f"FAIL mods: {why}")
            rep.problems += 1
        else:
            rep.lines.append(f"SKIP mods: {why}")
            rep.skipped.append("mods")
        rep.line = f"mods: skipped ({why})" if mode != "require" else f"mods: {why}"
        rep.word = "mods 1 problem" if rep.problems else "mods skipped"
        return rep
    assert exe is not None  # `why` names a missing claude, and returned
    v_lines, v_sum = check_validate(exe, root, env)
    t_lines, t_sum = check_test(exe, root, env)
    rep.lines += v_lines + t_lines
    rep.problems = len(v_lines) + len(t_lines)
    rep.line = f"mods: {v_sum}; plugin test {t_sum} (Claude Code {found})"
    rep.word = f"mods {util.plural(rep.problems, 'problem')}" if rep.problems else "mods ok"
    return rep


class Background(quality.Background):
    """run() in a thread, beside the quality checks and the test workers."""

    def _run(self, root: PathLike, mode: str, env: Mapping[str, str] | None) -> None:
        try:
            self.report = run(root, mode, env)
        except Exception as e:  # noqa: BLE001 (a bug here is one failed check, not a lost selftest)
            self.report = quality.Report()
            self.report.lines.append(f"FAIL mods: {type(e).__name__}: {e}")
            self.report.problems = 1
            self.report.word = "mods 1 problem"
