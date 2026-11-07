"""The series of the new panel's charts (/api/metrics), read-only, by local day over a period of 7 or 30 days that
ends today; every day of the period is present, 0 when nothing happened.

`build` gives one dict (see its doc); the pieces are `cost`, `deliveries`, `reviews`, `guard` and `top`, each from one
source, each a function of (project, days) so a test can check one alone:

- cost: list-price dollars (costs.py) per day and model family, from costs.jsonl. Only the lines of the product's own
  issues count (an issue id of its backlog, active or archived): a line of another product's issue or of none is left
  out, since the file may hold some. **A run's cost goes whole to the day of its `end`** (local time): an agent that
  works across midnight puts all its cost on the day it stopped; it is not split by minutes (the line keeps no
  per-hour usage). The same for its tokens and for the by-issue and by-type totals.
- deliveries: «Integrated …» and «Measured …» headings of the day's notes (timeline.from_notes, as `foreman catchup`),
  and the `done` lines of progress.jsonl by outcome (every agent that delivered, whatever its type).
- reviews: the review files of the home's reviews folder (review.py's header), on the day in their name
  (YYYY-MM-DD-…), else on their last change; «back» is a review that leaves H or M open.
- guard: the refusals of guard.jsonl (permissions.py) by `who`, and the three rules that refused most.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from . import backlog, costs, hooks_mod, permissions, review, timeline, util
from .backlog import Backlog

if TYPE_CHECKING:
    from .config import Project

PERIODS = (7, 30)
FAMILIES = (*costs.PRICES, "other")  # opus, sonnet, haiku, then a model of no known family
OUTCOMES = ("ok", "partial", "failed")
WHO = ("agent", "orchestrator", "other")
TOP = 6  # tasks in «where the cost goes»
RULES = 3  # rules in «what the guard refuses most»
DAY_IN_NAME = re.compile(r"^(\d{4}-\d\d-\d\d)-")


def own_issues(project: Project) -> dict[str, str]:
    """{issue id: short name} of the product's backlog, archive included; empty when it has no backlog."""
    b = Backlog(project)
    if not b.data_path.exists():
        return {}
    return {i["id"]: backlog.short_title(i) for _, i, _ in Backlog.iter_issues(b.load())}


def own_ids(project: Project) -> set[str]:
    return set(own_issues(project))


def period(days: int, today: date | None = None) -> list[str]:
    if days not in PERIODS:
        raise ValueError(f"days must be one of {PERIODS}")
    end = today or date.today()
    return [(end - timedelta(days=n)).isoformat() for n in range(days - 1, -1, -1)]


def _first(since: str | None, day: str) -> str:
    return min(since or day, day)


def cost(project: Project, days: int = 7, today: date | None = None) -> dict[str, Any]:
    """{"days": [YYYY-MM-DD…], "cost": {day: {opus, sonnet, haiku, other, usd, tokens, cheap_share}}, "total": usd of
    the period, "since": the first day of any own line (also before the period), or None, "top_issues": [{issue,
    short, usd, runs}] (the most expensive TOP of the period), "by_type": [{type, usd, runs}]}. Dollars rounded to
    cents; `cheap_share` is the share of the day's tokens on Sonnet or Haiku (None on a day without tokens); a run
    counts on its `end` day, whole (module doc)."""
    ds = period(days, today)
    out = {d: {**dict.fromkeys((*FAMILIES, "usd"), 0.0), "tokens": 0, "cheap": 0} for d in ds}
    issues = own_issues(project)
    since = None
    by_issue: dict[str, dict[str, Any]] = {}
    by_type: dict[str, dict[str, Any]] = {}
    for r in costs.runs(project):
        day = (r.get("end") or "")[:10]
        if r.get("issue") not in issues or not day:
            continue
        since = _first(since, day)
        if day not in out:
            continue
        usd_run = 0.0
        for model, u in (r.get("models") or {}).items():
            if not isinstance(u, dict):
                continue
            usd = u.get("usd") or 0
            fam = costs.family(model)
            out[day][fam or "other"] += usd
            out[day]["usd"] += usd
            usd_run += usd
            n = u.get("tokens") or 0
            out[day]["tokens"] += n
            if fam in costs.CHEAP:
                out[day]["cheap"] += n
        for table, key in ((by_issue, r["issue"]), (by_type, r.get("type") or "other")):
            e = table.setdefault(key, {"usd": 0.0, "runs": 0})
            e["usd"] += usd_run
            e["runs"] += 1
    rounded = {}
    for d, v in out.items():
        row = {k: round(v[k], 2) for k in (*FAMILIES, "usd")}
        row["tokens"] = v["tokens"]
        row["cheap_share"] = round(v["cheap"] / v["tokens"], 4) if v["tokens"] else None
        rounded[d] = row
    top = sorted(by_issue.items(), key=lambda kv: -kv[1]["usd"])[:TOP]
    return {
        "days": ds,
        "cost": rounded,
        "total": round(sum(v["usd"] for v in out.values()), 2),
        "since": since,
        "top_issues": [{"issue": k, "short": issues[k], "usd": round(v["usd"], 2), "runs": v["runs"]} for k, v in top],
        "by_type": [
            {"type": k, "usd": round(v["usd"], 2), "runs": v["runs"]}
            for k, v in sorted(by_type.items(), key=lambda kv: -kv[1]["usd"])
        ],
    }


def _bounds(ds: list[str]) -> tuple[datetime, datetime]:
    first = datetime.fromisoformat(ds[0])
    return first, datetime.fromisoformat(ds[-1]) + timedelta(days=1)


def deliveries(project: Project, ds: list[str]) -> dict[str, Any]:
    """{"deliveries": {day: {integrated, measured, ok, partial, failed}}, "since": first day with a note heading or a
    `done` line (also before the period; None without any)}."""
    out = {d: dict.fromkeys(("integrated", "measured", *OUTCOMES), 0) for d in ds}
    since = None
    lo, hi = _bounds(ds)
    for e in timeline.from_notes(timeline.World(project), lo, hi):
        if e["kind"] in ("integrate", "measure"):
            out[e["t"].date().isoformat()]["integrated" if e["kind"] == "integrate" else "measured"] += 1
    for d in util.read_jsonl(project.state / "progress.jsonl"):
        day = str(d.get("time") or "")[:10]
        if d.get("done") and day:
            since = _first(since, day)
            if day in out:
                o = d.get("outcome")
                out[day][o if o in OUTCOMES else "ok"] += 1
    return {"deliveries": out, "since": since}


def reviews(project: Project, ds: list[str]) -> dict[str, Any]:
    """{"reviews": {day: {clean, back}}, "rounds": {"1": n, …} of the period, "since": first day of a review}."""
    out = {d: {"clean": 0, "back": 0} for d in ds}
    rounds: dict[str, int] = {}
    since = None
    folder = project.reviews_dir
    for path in sorted(folder.rglob("*review*.md")) if folder.is_dir() else []:
        r = review.read(project.home, path)
        if r is None or not isinstance(r[1], review.Review):
            continue
        rv = r[1]
        m = DAY_IN_NAME.match(path.name)
        day = m.group(1) if m else datetime.fromtimestamp(rv.mtime).date().isoformat()
        since = _first(since, day)
        if day in out:
            out[day]["back" if rv.open else "clean"] += 1
            rounds[str(rv.round)] = rounds.get(str(rv.round), 0) + 1
    return {"reviews": out, "rounds": dict(sorted(rounds.items())), "since": since}


def guard(project: Project, ds: list[str]) -> dict[str, Any]:
    """{"guard": {day: {agent, orchestrator, other}}, "guard_rules": [{rule, band, n}] (top RULES of the period),
    "since": first day with a refusal (also before the period)}."""
    out: dict[str, dict[str, int]] = {d: dict.fromkeys(WHO, 0) for d in ds}
    lo, _ = _bounds(ds)
    since = None
    for rec in permissions.refusals(project, datetime(1970, 1, 1)):
        day = str(rec.get("at") or "")[:10]
        if day:
            since = _first(since, day)
        if day in out:
            who = str(rec["who"]) if rec.get("who") in WHO else "other"
            out[day][who] += 1
    rules = [{"rule": c["rule"], "band": c["band"], "n": c["n"]} for c in permissions.counts(project, lo)[:RULES]]
    return {"guard": out, "guard_rules": rules, "since": since}


def _avg_before(values: list[float], n: int = 6) -> float:
    prev = values[-1 - n : -1]
    return round(sum(prev) / len(prev), 2) if prev else 0.0


def today_figures(c: dict[str, Any], dl: dict[str, Any], rv: dict[str, Any]) -> dict[str, Any]:
    """The figures of Inicio, with their comparison and the last 7 days for the small chart: cost (today, by
    family, the mean of the 6 days before), integrated (today, measured, yesterday), reviews (back and total today
    and yesterday)."""
    ds = c["days"]
    usd = [c["cost"][d]["usd"] for d in ds]
    last7 = ds[-7:]
    t, y = ds[-1], ds[-2]
    return {
        "cost": {**{k: c["cost"][t][k] for k in (*FAMILIES, "usd")}, "avg_before": _avg_before(usd), "spark": usd[-7:]},
        "integrated": {
            "integrated": dl["deliveries"][t]["integrated"],
            "measured": dl["deliveries"][t]["measured"],
            "yesterday": dl["deliveries"][y]["integrated"],
            "spark": [dl["deliveries"][d]["integrated"] for d in last7],
        },
        "reviews": {
            "back": rv["reviews"][t]["back"],
            "total": sum(rv["reviews"][t].values()),
            "yesterday_back": rv["reviews"][y]["back"],
            "yesterday_total": sum(rv["reviews"][y].values()),
        },
    }


def build(project: Project, days: int = 7, today: date | None = None) -> dict[str, Any]:
    """Every series of /api/metrics for the period (all but `quota`, which is the account's and live: quota()):
    `days`, `cost`, `total`, `top_issues`, `by_type`, `deliveries`, `reviews`, `rounds`, `guard`, `guard_rules`,
    `today` (see today_figures) and `since`, the first day with data of each series ({cost, deliveries, reviews,
    guard}: a day, or None). A ValueError only for a bad `days`."""
    c = cost(project, days, today)
    ds = c["days"]
    dl, rv, g = deliveries(project, ds), reviews(project, ds), guard(project, ds)
    return {
        **{k: c[k] for k in ("days", "cost", "total", "top_issues", "by_type")},
        "deliveries": dl["deliveries"],
        "reviews": rv["reviews"],
        "rounds": rv["rounds"],
        "guard": g["guard"],
        "guard_rules": g["guard_rules"],
        "today": today_figures(c, dl, rv),
        "since": {"cost": c["since"], "deliveries": dl["since"], "reviews": rv["since"], "guard": g["since"]},
    }


def quota(projects: Any) -> list[dict[str, Any]]:
    """The account's limit windows now, as Claude Code reported them ([{kind, percent_used, resets_at}], percent
    as is; hooks_mod.quota)."""
    return hooks_mod.quota(projects)


def deps(project: Project, days: int, today: date | None = None) -> list[Any]:
    """The files `build` reads, for the panel's cache: when one changes the answer is read again."""
    ds = period(days, today)
    lo, hi = _bounds(ds)
    notes = [f for _, f in timeline.day_files(project, lo, hi)]
    folder = project.reviews_dir
    revs = sorted(folder.rglob("*review*.md")) if folder.is_dir() else []
    return [
        costs.file_of(project),
        Backlog(project).data_path,
        project.state / "progress.jsonl",
        permissions.log_file(project),
        *notes,
        *revs,
    ]
