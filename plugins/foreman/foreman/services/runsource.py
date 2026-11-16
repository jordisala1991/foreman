"""Runs a source (an executable in `<home>/sources/`) once."""

import os
from pathlib import Path

from .. import proc


class SourceError(Exception):
    pass


def lines(exe: Path, cursor: str) -> list[str]:
    """What the source `exe` prints, given the cursor it last printed (or empty)."""
    try:
        return proc.run([str(exe)], env={**os.environ, "FOREMAN_CURSOR": cursor}).out.splitlines()
    except proc.ProcError as e:
        raise SourceError(str(e)) from e
