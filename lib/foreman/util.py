"""Small helpers shared by the whole package, with no foreman dependencies (no config, no project): JSON files,
atomic writes, local ISO times, process ids and git.

- `read_json(path, default)`: the parsed file, or `default` if it is missing or not JSON.
- `read_jsonl(path, tail=None, numbered=False)`: the JSON objects (dicts) of the readable lines of a line file, the
  last `tail` lines only when given; [] if it is missing. With `numbered`, (line number, object) pairs.
- `prune_older(folder, pattern, days, keep=None)`: deletes the files of `folder` matching `pattern` whose mtime is
  more than `days` old (not `keep`; `now`, epoch seconds, replaces the clock); what cannot be read or deleted is left.
- `write_text_atomic(path, text)`: writes only if the content changes (temporary file in the same folder, fsync,
  rename; keeps the file's permissions); True if it wrote.
- `write_json(path, obj)`: `obj` as indented UTF-8 JSON through write_text_atomic.
- `fromiso(s)`: datetime.fromisoformat that also reads, on Python 3.9 and 3.10, the forms 3.11 added and foreman
  meets: a `Z` offset (Claude Code's transcripts) and fractions of 1 to 9 digits. ValueError as fromisoformat.
- `parse_when(value, now)`: a `--since`/`--until` text (`24h`, `7d`, `30m`, `HH:MM`, a date, a date and time) as an
  aware datetime; ValueError otherwise.
- `iso_epoch(s)`: epoch seconds of an ISO time (local when it has no offset), or None; each caller picks its default.
- `now()`: the local time (`datetime.now()`), unless `$FOREMAN_NOW` (an ISO local time) sets the clock for this
  process: then it reads that time and keeps running from it. A hook for tests that must not depend on the hour they
  run at (a panel as its own process, a note's day); only the code paths that call it honour it.
- `pid_alive(pid)`: True if the process exists (another user's too), False if not, None without a pid.
- `git(repo, *args, check, error, timeout)`: git's stdout, stripped; on failure raises `error` (check) or gives what
  git printed (usually nothing).
- `no_abbrev(parser)`: the parser with abbreviated options turned off in it and in every subparser below it
  (`backlog list --all` is an error, not `--all-products`); every command line of foreman parses through it.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Union

if TYPE_CHECKING:
    from argparse import ArgumentParser as ArgParser

PathLike = Union[str, os.PathLike[str]]  # what open() and Path() take: a str or a Path


_clock: tuple[str, timedelta] | None = None  # (the FOREMAN_NOW text, its offset from the real clock)


def now() -> datetime:
    """The local time; $FOREMAN_NOW (ISO, local) shifts it so that a test runs at the same hour whatever the real one
    (the clock keeps running from that time)."""
    global _clock
    real = datetime.now()
    fake = os.environ.get("FOREMAN_NOW")
    if not fake:
        return real
    if _clock is None or _clock[0] != fake:
        _clock = (fake, fromiso(fake) - real)
    return real + _clock[1]


def read_json(path: PathLike, default: Any) -> Any:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def read_jsonl(path: PathLike, tail: int | None = None, numbered: bool = False) -> list[Any]:
    """The dicts of the readable lines of a line file ([] if missing); the last `tail` lines when given. With
    `numbered`, (line number, dict) pairs: the number counts every line, readable or not."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return []
    first = max(len(lines) - tail, 0) if tail is not None else 0
    out: list[Any] = []
    for n, ln in enumerate(lines[first:], first + 1):
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        if isinstance(d, dict):
            out.append((n, d) if numbered else d)
    return out


def prune_older(
    folder: PathLike, pattern: str, days: float, keep: PathLike | None = None, now: float | None = None
) -> None:
    """Deletes the files of `folder` matching `pattern` last changed more than `days` days ago, except `keep`;
    `now` (epoch seconds) is the clock, default the real one."""
    cut = (time.time() if now is None else now) - days * 86400
    try:
        for f in Path(folder).glob(pattern):
            try:
                if f != keep and f.stat().st_mtime < cut:
                    f.unlink()
            except OSError:
                pass
    except OSError:
        pass


def write_text_atomic(path: PathLike, text: str) -> bool:
    path = Path(path)
    try:
        if path.read_text(encoding="utf-8") == text:
            return False
    except (OSError, ValueError):
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        try:
            mode = path.stat().st_mode & 0o777
        except OSError:
            mode = 0o644
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    return True


def write_json(path: PathLike, obj: Any) -> None:
    write_text_atomic(path, json.dumps(obj, ensure_ascii=False, indent=1))


ISO_FRACTION = re.compile(r"([T ]\d\d:\d\d:\d\d)\.(\d{1,9})(?=$|[+-])")


def fromiso(s: str) -> datetime:
    s = s.strip()
    if s[-1:] in ("Z", "z"):
        s = s[:-1] + "+00:00"
    s = ISO_FRACTION.sub(lambda m: f"{m.group(1)}.{(m.group(2) + '00000')[:6]}", s, count=1)
    return datetime.fromisoformat(s)


def parse_when(value: str | None, now: datetime | None = None) -> datetime:
    """`24h`, `7d`, `30m` (that long before now), `HH:MM` (today), a date (`2026-10-31`, its midnight) or a date and
    time, as an aware datetime (local time unless the text says otherwise); ValueError otherwise."""
    now = now or datetime.now().astimezone()
    v = (value or "").strip()
    m = re.fullmatch(r"(\d+)\s*([dhm])", v)
    if m:
        return now - timedelta(**{{"d": "days", "h": "hours", "m": "minutes"}[m.group(2)]: int(m.group(1))})
    m = re.fullmatch(r"(\d{1,2}):(\d\d)", v)
    if m:
        return now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
    t = fromiso(v)  # ValueError for anything else
    return t.astimezone() if t.tzinfo is None else t


def iso_epoch(s: object) -> float | None:
    try:
        return fromiso(str(s)).timestamp() if s else None
    except (TypeError, ValueError, OverflowError):
        return None


def pid_alive(pid: int | str | None) -> bool | None:
    if not pid:
        return None
    try:
        os.kill(int(pid), 0)
    except PermissionError:
        return True
    except (OSError, ValueError, OverflowError):
        return False
    return True


# Options for every git foreman runs itself (this function, integrate, worktree, release), except `push`: no hooks
# and no fsmonitor. A worktree shares hooks/ and config with its main checkout, so a hook an agent left there would
# otherwise run with the orchestrator's power when it integrates. `push` keeps its hooks: a pre-push is
# where tools like git-lfs upload their objects. The config itself (filters, textconv, merge drivers) is not
# covered: no single option turns those off (docs/internals.md, «The git foreman runs»).
GIT_QUIET: tuple[str, ...] = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false")


def git_run(
    repo: PathLike, *args: str, input: str | bytes | None = None, binary: bool = False, timeout: float | None = None
) -> subprocess.CompletedProcess[Any]:
    """git -C repo args, without hooks nor fsmonitor (GIT_QUIET): never for a push. Never raises on a status; an OSError
    or a timeout propagates."""
    return subprocess.run(
        [shutil.which("git") or "git", *GIT_QUIET, "-C", str(repo), *args],
        capture_output=True,
        close_fds=False,  # Python's own fds are not inheritable; with the full path this lets 3.9+ use posix_spawn
        text=not binary,
        input=input,
        timeout=timeout,
        check=False,
    )


def git(
    repo: PathLike, *args: str, check: bool = True, error: type[Exception] = RuntimeError, timeout: float | None = None
) -> str:
    """The stripped stdout of git_run; on failure, `error` is raised (check) or "" returned."""
    try:
        r = git_run(repo, *args, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        if check:
            raise error(f"git {' '.join(args)}: {e}") from e
        return ""
    if check and r.returncode != 0:
        raise error(f"git {' '.join(args)}: {(r.stderr or r.stdout).strip()[:400]}")
    return r.stdout.strip()


def no_abbrev(parser: ArgParser) -> ArgParser:
    """`parser` with allow_abbrev off in it and in its subparsers, at every depth (argparse reads it when parsing)."""
    import argparse

    parser.allow_abbrev = False
    for a in parser._actions:
        if isinstance(a, argparse._SubParsersAction):
            for sub in set(a.choices.values()):
                no_abbrev(sub)
    return parser
