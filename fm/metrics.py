"""What the system knows about how it works, per task: cost (its agents' transcripts, list prices), time (filed →
in review → done), rework (rejected by the owner), nudges (ended a turn without finishing), questions to the owner,
guard stops, and what misled its agents (their own words, from `fm done --confused`)."""

from typing import Any

from . import backlog, costs, lock, util
from .config import Project


def _minutes(a: str | None, b: str | None) -> float | None:
    since_a, since_b = util.age_s(a), util.age_s(b)
    return None if since_a is None or since_b is None else round((since_a - since_b) / 60, 1) + 0.0  # + 0.0: no "-0.0" for equal stamps


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
    return "\n".join(lines)
