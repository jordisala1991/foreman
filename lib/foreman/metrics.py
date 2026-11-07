"""The series of the new panel's charts (/api/metrics), read-only, by local day over a period of 7 or 30 days that
ends today; every day of the period is present, 0 when nothing happened.

Today only `cost`: list-price dollars (costs.py) per day and model family, from costs.jsonl. Only the lines of the
product's own issues count (an issue id of its backlog, active or archived): a line of another product's issue or of
none is left out, since the file may hold some.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from . import costs
from .backlog import Backlog

if TYPE_CHECKING:
    from .config import Project

PERIODS = (7, 30)
FAMILIES = (*costs.PRICES, "other")  # opus, sonnet, haiku, then a model of no known family


def own_ids(project: Project) -> set[str]:
    """The issue ids of the product's backlog, archive included; none when it has no backlog."""
    b = Backlog(project)
    if not b.data_path.exists():
        return set()
    return {i["id"] for _, i, _ in Backlog.iter_issues(b.load())}


def period(days: int, today: date | None = None) -> list[str]:
    if days not in PERIODS:
        raise ValueError(f"days must be one of {PERIODS}")
    end = today or date.today()
    return [(end - timedelta(days=n)).isoformat() for n in range(days - 1, -1, -1)]


def cost(project: Project, days: int = 7, today: date | None = None) -> dict[str, Any]:
    """{"days": [YYYY-MM-DD…], "cost": {day: {opus, sonnet, haiku, other, usd}}, "total": usd of the period,
    "since": the first day of any own line (also before the period), or None}. Dollars rounded to cents; a day is
    the line's `end` (local)."""
    ds = period(days, today)
    out = {d: dict.fromkeys((*FAMILIES, "usd"), 0.0) for d in ds}
    ids = own_ids(project)
    since = None
    for r in costs.runs(project):
        day = (r.get("end") or "")[:10]
        if r.get("issue") not in ids or not day:
            continue
        since = min(since or day, day)
        if day not in out:
            continue
        for model, u in (r.get("models") or {}).items():
            usd = u.get("usd") or 0
            out[day][costs.family(model) or "other"] += usd
            out[day]["usd"] += usd
    rounded = {d: {k: round(v, 2) for k, v in v_.items()} for d, v_ in out.items()}
    return {"days": ds, "cost": rounded, "total": round(sum(v["usd"] for v in out.values()), 2), "since": since}
