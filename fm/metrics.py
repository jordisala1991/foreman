"""What the system knows about how it works, per task: cost (its agents' transcripts, list prices), time (filed →
in review → done), rework (rejected by the owner), nudges (ended a turn without finishing), questions to the owner,
guard stops, and what misled its agents (their own words, from `fm done --confused`)."""

import re
import statistics
from typing import Any

from . import backlog, costs, lock, util
from .config import Project


def _minutes(a: str | None, b: str | None) -> float | None:
    since_a, since_b = util.age_s(a), util.age_s(b)
    return None if since_a is None or since_b is None else round((since_a - since_b) / 60, 1) + 0.0  # + 0.0: no "-0.0" for equal stamps


PARTS = ("slot", "planning", "working", "review", "owner", "merging")
CYCLE_TITLE = "cycle, minutes from the go to the merge"
_GO = ("filed (backlog", "filed (ready", "→ backlog", "→ ready")
_LAUNCHED = {"planning": "planning", "define": "planning", "responsible": "working", "work": "working"}  # define/work: before FN-124
_RETURNED = re.compile(r"→ in progress: (returned to its agent|sent back) \(review\)")
WEEK_S = 7 * 86400
_SUBTASKS_DONE = "→ done: its subtasks are closed"


def _phase(text: str) -> str | None:
    """The phase an event puts a task in, None when it changes nothing."""
    launched = re.match(r"launched \S+ \((\w+),", text)
    if launched:
        return _LAUNCHED.get(launched[1])
    starts = {"→ backlog": "slot", "→ ready": "slot", "→ in progress": "working", "→ in review": "review", "review round": "owner", "accepted by": "merging"}
    return next((phase for start, phase in starts.items() if text.startswith(start)), None)


def cycle(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Minutes a merged task spent in each part between the owner's go and its merge; all None for one that was not merged."""
    out: dict[str, Any] = {**dict.fromkeys(PARTS), "total": None, "merged_at": None}
    go = next((e for e in events if e["text"].startswith(_GO)), None)
    done = next((e for e in events if e["text"].startswith("→ done") and e["text"] != _SUBTASKS_DONE), None)
    if go is None or done is None:
        return out
    phase, since = "slot", go["time"]
    for e in events[events.index(go) + 1 : events.index(done) + 1]:
        nxt = "end" if e is done else _phase(e["text"])
        if nxt is None:
            continue
        part = "review" if phase == "owner" and _RETURNED.match(e["text"]) else phase  # returned at once by the loop: no wait for the owner
        out[part] = (out[part] or 0) + (_minutes(since, e["time"]) or 0)
        phase, since = nxt, e["time"]
    return {**out, "total": _minutes(go["time"], done["time"]), "merged_at": done["time"]}


def last_week(rows: list[dict[str, Any]]) -> tuple[dict[str, float | None], dict[str, Any] | None]:
    """The median per part of the tasks merged in the last 7 days, and the slowest of them (one whole task)."""
    merged = [r for r in rows if r["status"] == "done" and (util.age_s(r["cycle"]["merged_at"]) or WEEK_S + 1) <= WEEK_S]
    median: dict[str, float | None] = {}
    for k in (*PARTS, "total"):
        values = [r["cycle"][k] for r in merged if r["cycle"][k] is not None]
        median[k] = statistics.median(values) if values else None
    return median, max(merged, key=lambda r: r["cycle"]["total"] or 0, default=None)


def per_task(p: Project) -> list[dict[str, Any]]:
    d = backlog.load(p)
    events = util.read_jsonl(p.state / "events.jsonl")
    stops = util.read_jsonl(p.state / "guard.jsonl")
    lock_uses = lock.uses(list(lock.declared(p.cfg)))
    out: list[dict[str, Any]] = []
    for i in backlog.tasks(d):
        mine = [e for e in events if e.get("task") == i["id"]]
        to_review = next((e["time"] for e in mine if e["text"].startswith("→ in review")), None)
        closed = next((e["time"] for e in mine if e["text"].startswith("→ done")), None)
        check: backlog.Check = i.get("check") or {}
        report: backlog.Report = i.get("report") or {}
        sids = [s for s in i.get("sessions", []) if s]
        kinds: dict[str, str] = i.get("session_kinds") or {}
        seen: set[str] = set()  # one set for the whole task: a message replayed in a resumed session counts once, in its first
        per = {s: costs.of_sessions([s], seen) for s in dict.fromkeys(sids)}  # each transcript is read once
        by_kind = {k: round(sum(c["usd"] for s, c in per.items() if kinds.get(s) == k), 4) for k in ("planning", "responsible")}
        usd = round(sum(x["usd"] for x in per.values()), 4)
        agent: dict[str, Any] = i.get("agent") or {}
        ran = agent if agent.get("kind") == "responsible" else i
        out.append(
            {
                "id": i["id"],
                "title": i["title"],
                "status": i["status"],
                "usd": usd,
                "model": ran.get("model"),
                "effort": ran.get("effort"),
                "planning_usd": by_kind["planning"],
                "responsible_usd": by_kind["responsible"],
                "rounds": len(i.get("reviews") or []),
                "to_review_min": _minutes(i.get("created"), to_review),
                "to_done_min": _minutes(i.get("created"), closed),
                "rework": i.get("rework", 0),
                "conflicts": i.get("conflicts", 0),
                "nudges": i.get("nudges", 0),
                "questions": sum(
                    1 for q in d["decisions"] + d["decisions_resolved"] if q.get("task") == i["id"] and q.get("kind") in ("question", "permission")
                ),
                "guard_stops": sum(1 for s in stops if s.get("task") == i["id"]),
                "lock_wait_min": round(sum(u.get("waited_s", 0) for u in lock_uses if u.get("task") == i["id"]) / 60, 1),
                "risk": check.get("risk"),
                "confused": report.get("confused", ""),
                "cycle": cycle(mine),
            }
        )
    return out


def text(rows: list[dict[str, Any]]) -> str:
    head = (
        f"{'id':8} {'status':12} {'$':>7} {'model':7} {'effort':6} {'planning$':>9} {'responsible$':>12} {'rounds':>6} {'min':>6} {'rework':>6} "
        f"{'confl':>5} {'nudge':>5} {'ask':>3} {'stop':>4} {'risk':6}"
    )
    lines = [head]
    for r in rows:
        lines.append(
            f"{r['id']:8} {r['status']:12} {r['usd']:7.2f} {r['model'] or '-':7} {r['effort'] or '-':6} "
            f"{r['planning_usd']:9.2f} {r['responsible_usd']:12.2f} {r['rounds']:6} "
            f"{r['to_review_min'] or 0:6.0f} {r['rework']:6} {r['conflicts']:5} "
            f"{r['nudges']:5} {r['questions']:3} {r['guard_stops']:4} {r['risk'] or '':6}"
        )
    lines.append(f"total ${sum(r['usd'] for r in rows):.2f}")
    for r in rows:
        if r["confused"] and r["confused"].strip().lower() not in ("none", "nothing", "-"):
            lines.append(f"{r['id']} misled by: {r['confused']}")
    return "\n".join(lines + _cycle_lines(rows))


def _cycle_lines(rows: list[dict[str, Any]]) -> list[str]:
    def line(first: str, c: dict[str, Any]) -> str:
        return f"{first:21} " + " ".join(f"{'-' if c[k] is None else format(c[k], '.0f'):>{w}}" for k, w in (("total", 7), *((p, 8) for p in PARTS)))

    median, worst = last_week(rows)
    lines = ["", CYCLE_TITLE, f"{'id':8} {'status':12} {'total':>7} " + " ".join(f"{p:>8}" for p in PARTS)]
    lines += [line(f"{r['id']:8} {r['status']:12}", r["cycle"]) for r in rows if r["status"] in backlog.CLOSED]
    lines.append(line("median (7 days)", median))
    lines.append(line(f"worst {worst['id']}" if worst else "worst -", worst["cycle"] if worst else dict.fromkeys(median)))
    return lines
