"""GitHub's API quota: when gh says the limit is exceeded, every gh call waits for the reset. The pause is a file in
state/, so it survives a loop restart; the figures GitHub gave are kept in it."""

import json
import re
import time
from pathlib import Path
from typing import Any

from .. import proc, util
from ..config import Project
from . import RateLimited

RATE = re.compile(r"rate limit", re.I)
QUOTA = "github_quota.json"  # in state/: until when gh is paused, and the figures GitHub gave
FALLBACK_PAUSE = 60  # seconds, when the reset cannot be read
MAX_PAUSE = 3600  # a wrong clock or a hand-edited file never pauses gh for longer than this


def resources_of(raw: object) -> dict[str, dict[str, Any]]:
    """The quota resources of `gh api rate_limit` (name -> limit, remaining, reset); {} when it does not fit."""
    found = (util.json_object(raw) or {}).get("resources")
    items = (util.json_object(found) or {}).items()
    return {k: o for k, v in items if (o := util.json_object(v)) is not None}


def reset_at(raw: object, now: float) -> float:
    """When the exhausted quota resets, from `gh api rate_limit`; `now` + a minute when that cannot be read."""
    resets: list[float] = []
    for r in resources_of(raw).values():
        reset = r.get("reset")
        if r.get("remaining") == 0 and isinstance(reset, (int, float)) and reset > now:
            resets.append(reset)
    return float(max(resets)) if resets else now + FALLBACK_PAUSE


def waiting(until: float) -> str:
    return f"GitHub API rate limit: gh paused until {time.strftime('%H:%M:%S', time.localtime(until))}"


def paused_until(state: Path) -> float:
    try:
        kept = util.read_json(state / QUOTA, None)
    except ValueError:  # util.read_json no longer forgives a broken file; a broken pause file is no pause
        return 0.0
    until = (util.json_object(kept) or {}).get("until")
    return min(float(until), time.time() + MAX_PAUSE) if isinstance(until, (int, float)) else 0.0


def hold(state: Path) -> None:
    """No gh call while GitHub's limit is used up."""
    until = paused_until(state)
    if time.time() < until:
        raise RateLimited(waiting(until), until)


def limited(p: Project, text: str) -> RateLimited:
    """gh said the limit is exceeded: read when it resets (free call), keep it in state/, and pause."""
    try:
        raw: object = json.loads(proc.run(["gh", "api", "rate_limit"], cwd=p.repo).out)
    except (proc.ProcError, ValueError):
        raw = None
    until = reset_at(raw, time.time())
    keep = ("limit", "remaining", "reset")
    resources = {k: {f: v[f] for f in keep if f in v} for k, v in resources_of(raw).items()}
    util.write_json(p.state / QUOTA, {"seen": util.now(), "until": until, "error": text.strip()[:200], "resources": resources})
    return RateLimited(waiting(until), until)
