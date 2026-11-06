"""The product's commands: the one contract and the one runner for every shell command foreman takes from a project's
configuration (docs/extending.md, «The contract»).

A product command is a string from foreman.toml or the registry that foreman runs at a fixed point: the loop's
`[loop] checks`, a resource's `busy` (the panel's probe; lock.sh runs it in shell with the same FOREMAN_API and
FOREMAN_POINT), and the `[repos.*]` steps `local_ci` (with `ci_runs`), `main_ci`, `ci_summary`, `privacy_scan`,
`pre_merge_report`, `post_merge` and `post_create`; and the `setup` and cases of a hidden acceptance check
(holdout.py: a `holdout.toml` of the product's folder of hidden checks, not foreman.toml; no placeholders, its
values as variables only). `POINTS` lists them with their placeholders and time limits.

- **How it runs:** `bash -c CMD` in a new session (its own process group), in the folder its caller gives (the
  documented one of each point), stdin the text its point gives or /dev/null, stdout and stderr to temporary files
  (or both to a log file), never pipes: a command that leaves a child alive on purpose (a server) does not keep
  foreman waiting for the pipe to close.
- **Environment** (on top of the inherited one): FOREMAN_API (API), FOREMAN_POINT (the point's name),
  FOREMAN_BIN (the `bin/foreman` of the running code, to call the same version), and with a project FOREMAN_PROJECT,
  FOREMAN_HOME, FOREMAN_STATE and FOREMAN_LANGUAGE; every value of the point as FOREMAN_<NAME> (FOREMAN_WT,
  FOREMAN_MAIN, FOREMAN_BRANCH, FOREMAN_CI, FOREMAN_REPO, FOREMAN_NAME, FOREMAN_BEFORE, FOREMAN_HEAD), unquoted.
- **Placeholders:** `{name}`, a lowercase name in braces, is replaced by the point's value when the point knows it
  (`post_create` quotes the values with shlex.quote, the repo steps do not: `{ci}` may be several arguments on
  purpose). Anything else in braces is left as it is: `${HOME}`, `{ cmd; }`, `{a,b}`, `{print $1}`, `{}`. An unknown
  `{name}` outside single quotes is an error (PlaceholderError: a typo, or a placeholder of another point), checked
  before anything runs; inside single quotes it is left (an awk or jq program: `awk '{print}'`).
- **Time limit:** the point's (None: no limit; `timeout=` overrides it). Past it, SIGTERM to the whole group and,
  KILL_GRACE seconds later, SIGKILL to the group: no grandchild survives. A command that ends on its own is not
  killed, nor are the children it leaves. If foreman itself is interrupted while waiting, the group is stopped too.
- **Result:** Result(rc, stdout, stderr, seconds, timed_out); rc is None when it could not start (`stderr` says
  why) and the signal's negative number when cut.

The version, FOREMAN_API, goes up only when a change breaks a command written for the previous one (a variable
renamed, an exit code that means something else); new variables or points do not raise it.

This module may import config (it is foreman's side of the contract); util must not import it.
"""

from __future__ import annotations

import contextlib
import os
import re
import shlex
import signal
import subprocess
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from . import util
    from .config import Project

API = 1
KILL_GRACE = 5.0
BIN = Path(__file__).resolve().parents[2] / "bin" / "foreman"


@dataclass(frozen=True)
class Point:
    key: str  # where it is configured
    cwd: str  # where it runs (documentation; the caller passes the folder)
    placeholders: tuple[str, ...] = ()
    variables: tuple[str, ...] = ()  # values given as FOREMAN_<NAME> only, not as placeholders
    stdin: str = ""  # what it reads on stdin ("" nothing)
    timeout: float | None = None
    on_failure: str = ""


REPO_CI = ("wt", "main", "branch", "ci")
POINTS = {
    "loop.checks": Point(
        "[loop] checks",
        "the home",
        timeout=30,
        on_failure="a check_failed notice (rc other than 0 or 1, or cut); the pass goes on",
    ),
    "resource.busy": Point(
        "[resources.*] busy",
        "the caller's (lock.sh); the panel: the registry's folder or the home",
        timeout=5,
        on_failure="lock.sh: free; the panel: «timeout» or «error» under the resource",
    ),
    "repo.local_ci": Point(
        "[repos.*] local_ci, ci_runs",
        "the worktree (the main checkout after a fast-forward)",
        REPO_CI,
        ("repo",),
        on_failure="the integration stops, nothing merged",
    ),
    "repo.main_ci": Point(
        "[repos.*] main_ci", "the main checkout", REPO_CI, ("repo",), on_failure="main goes back, the integration stops"
    ),
    "repo.ci_summary": Point(
        "[repos.*] ci_summary",
        "where the CI ran",
        REPO_CI,
        ("repo",),
        "the run's output",
        timeout=300,
        on_failure="«(ci_summary failed, rc N)» in the PR comment; it goes on",
    ),
    "repo.privacy_scan": Point(
        "[repos.*] privacy_scan",
        "the home",
        (),
        ("repo",),
        "the text to publish",
        timeout=300,
        on_failure="the integration stops (cut too: it fails closed)",
    ),
    "repo.pre_merge_report": Point(
        "[repos.*] pre_merge_report",
        "the main checkout",
        ("wt", "main", "branch"),
        ("repo",),
        timeout=300,
        on_failure="its rc and stderr are shown under the report; it goes on",
    ),
    "repo.post_merge": Point(
        "[repos.*] post_merge",
        "the main checkout",
        ("main", "branch", "before", "head"),
        ("repo",),
        timeout=600,
        on_failure="a warning, a progress line and the note; the merge stands",
    ),
    "repo.post_create": Point(
        "[repos.*] post_create",
        "the new worktree",
        ("main", "wt", "repo", "name", "branch"),
        timeout=600,
        on_failure="the worktree (and a branch it made) is removed",
    ),
    "holdout.setup": Point(
        "holdout.toml setup",
        "the copy of the branch's tip a hidden check runs in",
        (),
        ("wt", "main", "repo", "branch"),
        timeout=600,
        on_failure="the hidden check stops before its cases (exit 2), nothing merged",
    ),
    "holdout.scenario": Point(
        "holdout.toml [[scenario]] command",
        "the copy of the branch's tip a hidden check runs in",
        (),
        ("wt", "main", "repo", "branch"),
        timeout=300,
        on_failure="the case fails",
    ),
}
QUOTED = {"repo.post_create"}
# What a point gives about itself: FOREMAN_API, FOREMAN_POINT and each point's FOREMAN_<NAME>. A command run at a point
# never inherits these from an outer one (foreman called from inside a command, e.g. a CI that takes a lock): a
# variable it sees is its own point's or none. FOREMAN_BIN and the project's variables are not in it (a nested
# foreman keeps acting on the same project).
POINT_VARIABLES = tuple(
    sorted(
        {"FOREMAN_API", "FOREMAN_POINT"}
        | {f"FOREMAN_{n.upper()}" for p in POINTS.values() for n in (*p.placeholders, *p.variables)}
    )
)
NAME_RE = re.compile(r"(?<!\$)\{([a-z][a-z0-9_]*)\}")
DEFAULT: Any = object()  # run(timeout=): the point's own limit
# A point's values, {name: value} (Path, int or str; None: not given): placeholders and FOREMAN_<NAME> variables.
Values = Mapping[str, Any]


class PlaceholderError(ValueError):
    pass


@dataclass
class Result:
    rc: int | None
    stdout: str = ""
    stderr: str = ""
    seconds: float = 0.0
    timed_out: bool = False


def single_quoted(cmd: str) -> list[tuple[int, int]]:
    """The spans of CMD inside single quotes, read as bash does (a quote inside double quotes or escaped does not
    open one)."""
    spans: list[tuple[int, int]] = []
    i, n, start = 0, len(cmd), 0
    state: str | None = None
    while i < n:
        c = cmd[i]
        if state is None:
            if c == "\\":
                i += 1
            elif c == "'":
                state, start = "'", i
            elif c == '"':
                state = '"'
        elif state == '"':
            if c == "\\":
                i += 1
            elif c == '"':
                state = None
        elif c == "'":
            spans.append((start, i))
            state = None
        i += 1
    if state == "'":
        spans.append((start, n))
    return spans


def expand(point: str, cmd: str, values: Values | None = None) -> str:
    """CMD with the point's placeholders replaced by `values` ({name: value}; quoted for post_create). A placeholder
    the point does not know, outside single quotes, raises PlaceholderError; without values only that check runs."""
    p = POINTS[point]
    quoted = single_quoted(cmd)
    bad = [
        m.group(0)
        for m in NAME_RE.finditer(cmd)
        if m.group(1) not in p.placeholders and not any(a < m.start() < b for a, b in quoted)
    ]
    if bad:
        known = ", ".join(f"{{{k}}}" for k in p.placeholders) or "none"
        raise PlaceholderError(f"bad placeholder {', '.join(dict.fromkeys(bad))} (known: {known})")
    if values is None:
        return cmd
    q: Callable[[str], str] = shlex.quote if point in QUOTED else str

    def sub(m: re.Match[str]) -> str:
        name = m.group(1)
        return q(str(values[name])) if name in p.placeholders and name in values else m.group(0)

    return NAME_RE.sub(sub, cmd)


def environment(
    point: str, project: Project | None = None, values: Values | None = None, base: Mapping[str, str] | None = None
) -> dict[str, str]:
    """The environment a command of `point` runs with: `base` (os.environ by default) without an outer point's
    variables (POINT_VARIABLES), plus the contract's variables."""
    env = {k: v for k, v in (os.environ if base is None else base).items() if k not in POINT_VARIABLES}
    env.update({"FOREMAN_API": str(API), "FOREMAN_POINT": point, "FOREMAN_BIN": str(BIN)})
    if project is not None:
        env.update(
            {
                "FOREMAN_PROJECT": project.name,
                "FOREMAN_HOME": str(project.home),
                "FOREMAN_STATE": str(project.state),
                "FOREMAN_LANGUAGE": project.language,
            }
        )
    p = POINTS[point]
    for name in (*p.placeholders, *p.variables):
        if values and name in values and values[name] is not None:
            env[f"FOREMAN_{name.upper()}"] = str(values[name])
    return env


def stop_group(proc: subprocess.Popen[bytes], grace: float | None = None) -> None:
    """SIGTERM to the process group of `proc`, SIGKILL to it `grace` seconds later (or at once once the leader is
    gone: what it leaves behind dies with it)."""
    grace = KILL_GRACE if grace is None else grace
    for sig in (signal.SIGTERM, signal.SIGKILL):
        with contextlib.suppress(OSError):
            os.killpg(proc.pid, sig)
        if sig == signal.SIGTERM:
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=grace)
    proc.wait()


def run(
    point: str,
    cmd: str,
    project: Project | None = None,
    values: Values | None = None,
    cwd: util.PathLike | None = None,
    stdin: str | bytes | None = None,
    timeout: float | None = DEFAULT,
    log: util.PathLike | None = None,
    env: Mapping[str, str] | None = None,
    limit: int | None = None,
) -> Result:
    """Runs CMD (already expanded) at `point`: see the module's docstring. `log`: a file both stdout and stderr are
    appended to as they come (Result.stdout is what this run appended). `limit`: read at most that many bytes of
    stdout and stderr (the rest is dropped). `env`: the base environment instead of os.environ."""
    if timeout is DEFAULT:
        timeout = POINTS[point].timeout
    full = environment(point, project, values, env)
    t0 = time.monotonic()
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err, tempfile.TemporaryFile() as inp:
        if stdin is not None:
            inp.write(stdin.encode() if isinstance(stdin, str) else stdin)
            inp.seek(0)
        logf: IO[bytes] | None = open(log, "ab") if log else None
        try:
            start = logf.tell() if logf else 0
            try:
                proc = subprocess.Popen(
                    ["bash", "-c", cmd],
                    cwd=cwd,
                    env=full,
                    start_new_session=True,
                    stdin=inp if stdin is not None else subprocess.DEVNULL,
                    stdout=logf or out,
                    stderr=subprocess.STDOUT if logf else err,
                )
            except OSError as e:
                return Result(None, "", str(e), time.monotonic() - t0)
            cut = False
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                cut = True
                stop_group(proc)
            except BaseException:
                stop_group(proc, grace=1.0)
                raise
        finally:
            if logf:
                logf.close()
        if log:
            with open(log, "rb") as f:
                f.seek(start)
                raw_out, raw_err = f.read(-1 if limit is None else limit), b""
        else:
            out.seek(0)
            err.seek(0)
            raw_out = out.read(-1 if limit is None else limit)
            raw_err = err.read(-1 if limit is None else limit)
    return Result(
        proc.returncode, raw_out.decode(errors="replace"), raw_err.decode(errors="replace"), time.monotonic() - t0, cut
    )


def run_logged(point: str, cmd: str, log: util.PathLike, head: str, **kw: Any) -> Result:
    """run() with its output appended to LOG between a line «== TIME HEAD: CMD» and one «== rc N in S s» (« (cut)»
    after N when the time limit stopped it; «== could not start: why» when it did not start)."""
    from . import util

    with open(log, "a") as f:
        f.write(f"== {util.now():%Y-%m-%d %H:%M:%S} {head}: {cmd}\n")
    r = run(point, cmd, log=log, **kw)
    end = f"rc {r.rc}{' (cut)' if r.timed_out else ''} in {int(r.seconds)} s" if r.rc is not None else ""
    with open(log, "a") as f:
        f.write(f"== {end or 'could not start: ' + r.stderr}\n")
    return r
