"""The Claude Code process a foreman call runs under: what ties the orchestrator's mark to one process.

A copy of Claude Code started with the orchestrator's session (`claude -p --resume ID`, `--session-id ID`) sends its
hook events with the marked session id and no agent_id, like the orchestrator's main thread. What tells them apart
is the process: the mark (orchestrator.mark) keeps the pid and start time of the `claude` process that marked
itself, and an orchestrator-only command (guard.py, why=process) passes only when that same process is the nearest
`claude` among its own ancestors. The nearest one, not any one: a
copy launched from the orchestrator's Bash (by a subagent, say) has the orchestrator's process further up.

- table() reads every process once: `ps -Ao pid=,ppid=,lstart=,comm=` from /bin/ps or /usr/bin/ps (never from
  PATH, which a copy's environment sets), with LC_ALL=C and TZ=UTC so two callers print the same start time.
  `run_ps` is the one place that runs it: the tests replace it with a simulated `ps`.
- claude_ancestor() walks up the parents from this process (at most MAX_DEPTH steps) to the first whose command's
  base name is `claude` (the native binary, also the one the desktop app and other hosts run). Claude Code run by `node`
  from an npm install is not recognised: no process, so the check fails closed.
- alive() says whether a recorded (pid, start) still runs: a recycled pid has another start time.

Without `ps`, or with output it cannot read, table() raises Unavailable and the callers fail closed: the guard
refuses (why=process), the wrapper refuses, and `foreman session` does not record a process (see cli.py).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import NamedTuple

PS_PATHS = ("/bin/ps", "/usr/bin/ps")
PS_ARGS = ("-Ao", "pid=,ppid=,lstart=,comm=")
PS_TIMEOUT_S = 5
MAX_DEPTH = 8
CLAUDE = "claude"


class Proc(NamedTuple):
    pid: int
    ppid: int
    start: str  # lstart, in UTC: "Sun Nov  1 10:18:40 2026" with the spaces collapsed
    comm: str


class Unavailable(Exception):
    """`ps` could not be run or read: the callers fail closed."""


def run_ps() -> str:
    """The output of `ps -Ao pid=,ppid=,lstart=,comm=` (the tests replace this function). Raises Unavailable."""
    exe = next((p for p in PS_PATHS if os.access(p, os.X_OK)), None)
    if not exe:
        raise Unavailable("no ps in " + " or ".join(PS_PATHS))
    try:
        r = subprocess.run(
            [exe, *PS_ARGS],
            capture_output=True,
            text=True,
            timeout=PS_TIMEOUT_S,
            env={"LC_ALL": "C", "TZ": "UTC"},
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise Unavailable(f"ps: {e}") from e
    if r.returncode:
        raise Unavailable(f"ps exited {r.returncode}")
    return r.stdout


def parse(text: str) -> dict[int, Proc]:
    """ps's lines: pid, ppid, the five words of lstart, then the command (which may hold spaces)."""
    out = {}
    for line in text.splitlines():
        f = line.split(None, 7)
        if len(f) < 8:
            continue
        try:
            pid, ppid = int(f[0]), int(f[1])
        except ValueError:
            continue
        out[pid] = Proc(pid, ppid, " ".join(f[2:7]), f[7].strip())
    if not out:
        raise Unavailable("ps printed no process")
    return out


def table() -> dict[int, Proc]:
    """Every process, by pid. Raises Unavailable."""
    return parse(run_ps())


def is_claude(comm: str) -> bool:
    return Path(comm).name == CLAUDE


def claude_ancestor(pid: int | None = None, procs: dict[int, Proc] | None = None) -> Proc | None:
    """The nearest `claude` process among `pid` (default: this process) and its parents, or None. Raises
    Unavailable when `ps` cannot be read."""
    procs = table() if procs is None else procs
    cur = procs.get(os.getpid() if pid is None else pid)
    for _ in range(MAX_DEPTH):
        if cur is None:
            return None
        if is_claude(cur.comm):
            return cur
        if cur.ppid in (0, cur.pid):
            return None
        cur = procs.get(cur.ppid)
    return None


def alive(pid: int, start: str | None, procs: dict[int, Proc] | None = None) -> bool:
    """Whether the process `pid` that started at `start` still runs. Without `ps` (procs None and table() fails),
    whether some process has that pid at all (os.kill 0): a live pid counts as alive, so a refusal holds."""
    try:
        procs = table() if procs is None else procs
    except Unavailable:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except OSError:
            return True  # it exists, owned by someone else
        return True
    p = procs.get(pid)
    return bool(p and (start is None or p.start == start))
