"""Who spends GitHub's API quota: each `gh` call is logged with its origin (`loop`, or the task whose agent ran it, as the
guard sees it), and `by_origin` counts them per hour. One call of `gh` is one line; `gh pr view` may cost a little more than
one request on GitHub's side, so these are calls, not exact quota."""

import os
import time

from . import util
from .config import Project

WINDOW_H = 24
ORIGIN = "owner"  # who runs this process: the loop sets "loop"; inside an agent's session FM_ISSUE names the task


def record(p: Project, origin: str | None = None) -> None:
    who = origin or os.environ.get("FM_ISSUE") or ORIGIN
    util.append_jsonl(p.state / "gh_calls.jsonl", {"time": util.now(), "epoch": time.time(), "origin": who})


def by_origin(p: Project, at: float | None = None) -> dict[str, dict[str, float]]:
    """Per origin: the calls of the last hour, and the average per hour over the last 24 h."""
    at = time.time() if at is None else at
    last: dict[str, int] = {}
    day: dict[str, int] = {}
    for r in util.read_jsonl(p.state / "gh_calls.jsonl"):
        age = at - float(r.get("epoch", 0))
        if 0 <= age < WINDOW_H * 3600:
            o = str(r.get("origin", "?"))
            day[o] = day.get(o, 0) + 1
            last[o] = last.get(o, 0) + (age < 3600)
    return {o: {"last_hour": last[o], "per_hour": round(day[o] / WINDOW_H, 1)} for o in sorted(day)}
