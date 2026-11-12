"""The one place fm runs other programs (git, gh, claude, a test command). Generic: it knows nothing about any of them.
`run` waits and returns a `Result`; `spawn` hands back a live process. A program that is missing or too slow is a `ProcError`
with a clear message, never a bare OSError; a non-zero exit is not an error here: callers read `Result.code`.
`env`, when given, is the whole environment of the child (callers add to `os.environ` themselves)."""

import json
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Result:
    code: int
    out: str
    err: str


class ProcError(Exception):
    """The program did not run to the end: it does not exist, cannot start, or ran past its time limit."""

    def __init__(self, message: str, timed_out: bool = False) -> None:
        super().__init__(message)
        self.timed_out = timed_out


def _name(args: Sequence[str]) -> str:
    return " ".join(args[:3])


def run(args: Sequence[str], *, cwd: Path | None = None, timeout: float = 60, env: Mapping[str, str] | None = None, input: str | None = None) -> Result:
    try:
        r = subprocess.run(
            list(args), cwd=cwd, env=None if env is None else dict(env), input=input, capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        raise ProcError(f"{_name(args)}: timed out after {timeout:g}s", timed_out=True) from None
    except OSError as e:
        raise ProcError(f"{_name(args)}: could not start: {e.strerror or e}") from e
    return Result(r.returncode, r.stdout, r.stderr)


def run_json(args: Sequence[str], *, cwd: Path | None = None, timeout: float = 60, env: Mapping[str, str] | None = None, input: str | None = None) -> object:
    """`run`, then the standard output as JSON; ProcError when it is not JSON. The exit code is not checked."""
    r = run(args, cwd=cwd, timeout=timeout, env=env, input=input)
    try:
        return json.loads(r.out)
    except ValueError:
        raise ProcError(f"{_name(args)}: output is not JSON: {(r.out or r.err).strip()[:200]!r}") from None


def spawn(args: Sequence[str], *, cwd: Path | None = None, env: Mapping[str, str] | None = None) -> subprocess.Popen[str]:
    """A live process, for what must be waited on or stopped by the caller."""
    try:
        return subprocess.Popen(list(args), cwd=cwd, env=None if env is None else dict(env), text=True)
    except OSError as e:
        raise ProcError(f"{_name(args)}: could not start: {e.strerror or e}") from e
