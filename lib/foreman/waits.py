"""`foreman wait …`: waits for something to finish without matching command lines, so it never finds itself.
Forked from an earlier in-house version (waits with `until ! pgrep -f "text"` matched their own command and hung).
  foreman wait [-t SECONDS] [-i SECONDS] pid PID...        until every PID has exited
  foreman wait … file PATH...                               until every PATH exists
  foreman wait … line PATH REGEX                            until PATH has a line matching REGEX
  foreman wait … lock RESOURCE WHO                          until WHO neither holds RESOURCE nor waits for it
-t gives up after that long (exit 124); -i polls that often (default 10). Exit 0 when the condition holds.
"""

from __future__ import annotations

import inspect
import os
import re
import sys
import time
from typing import TYPE_CHECKING

from . import lock, util

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .config import Project

DOC = inspect.cleandoc(__doc__ or "")  # the help text, the same on every Python (3.13+ dedents docstrings itself)


def done_yet(project: Project | None, mode: str, args: Sequence[str]) -> bool:
    if mode == "pid":
        return not any(util.pid_alive(p) for p in args)
    if mode == "file":
        return all(os.path.exists(f) for f in args)
    if mode == "line":
        path, rx = args[0], re.compile(args[1])
        try:
            with open(path, errors="replace") as f:
                return any(rx.search(line) for line in f)
        except OSError:
            return False
    if mode == "lock":
        name, who = args[0], args[1]
        res = lock.resource_config(project, name, tolerant=True)
        return not lock.involved(name, res, who)
    raise ValueError(f"unknown mode {mode}")


def run(project: Project | None, mode: str, args: Sequence[str], timeout: float = 0, every: float = 10) -> int:
    need = {"pid": 1, "file": 1, "line": 2, "lock": 2}
    if mode not in need or len(args) < need[mode]:
        print(DOC, file=sys.stderr)
        return 2
    start = time.monotonic()
    while not done_yet(project, mode, args):
        if timeout and time.monotonic() - start >= timeout:
            print(f"foreman wait: gave up after {timeout} s ({mode} {' '.join(args)})", file=sys.stderr)
            return 124
        time.sleep(every)
    return 0
