"""What the system knows about how it works, per task: cost (its agents' transcripts, list prices), time (filed →
in review → done), rework (sent back by the owner), nudges (ended a turn without finishing), questions to the owner,
guard stops, and what misled its agents (their own words, from `fm done --confused`)."""

from datetime import datetime
from typing import Any

from . import backlog, costs, lock, util
from .config import Project


def _minutes(a: str | None, b: str | None) -> float | None:
    try:
        f = "%Y-%m-%d %H:%M:%S"
        return round((datetime.strptime(b or "", f) - datetime.strptime(a or "", f)).total_seconds() / 60, 1)
    except ValueError:
        return None


def per_issue(p: Project) -> list[dict[str, Any]]:
    d = backlog.load(p)
    events = util.read_jsonl(p.state / "events.jsonl")
    stops = util.read_jsonl(p.state / "guard.jsonl")
    lock_uses = lock.uses(list(lock.declared(p.cfg)))
    out: list[dict[str, Any]] = []
    for i in backlog.issues(d):
        mine = [e for e in events if e.get("issue") == i["id"]]
        to_review = next((e["time"] for e in mine if e["text"].startswith("→ in review")), None)
        closed = next((e["time"] for e in mine if e["text"].startswith("→ done")), None)
        check: backlog.Check = i.get("check") or {}
        report: backlog.Report = i.get("report") or {}
        sids = [s for s in i.get("sessions", []) if s]
        kinds: dict[str, str] = i.get("session_kinds") or {}
        seen: set[str] = set()  # one set for the whole task: a message replayed in a resumed session counts once, in its first
        per = {s: costs.of_sessions([s], seen) for s in dict.fromkeys(sids)}  # each transcript is read once
        by_kind = {k: round(sum(c["usd"] for s, c in per.items() if kinds.get(s) == k), 4) for k in ("define", "work")}
        c = {k: round(sum(x[k] for x in per.values()), 4) for k in ("usd", "tokens")}
        starts = [x["first"] for x in per.values() if x.get("first")]
        ends = [x["last"] for x in per.values() if x.get("last")]
        c["minutes"] = round((max(ends) - min(starts)).total_seconds() / 60, 1) if starts and ends else 0
        out.append(
            {
                "id": i["id"],
                "title": i["title"],
                "status": i["status"],
                "usd": c["usd"],
                "plan_usd": by_kind["define"],
                "work_usd": by_kind["work"],
                "rounds": len(i.get("reviews") or []),
                "tokens": c["tokens"],
                "agent_minutes": c["minutes"],
                "to_review_min": _minutes(i.get("created"), to_review),
                "to_done_min": _minutes(i.get("created"), closed),
                "rework": i.get("rework", 0),
                "conflicts": i.get("conflicts", 0),
                "nudges": i.get("nudges", 0),
                "questions": sum(
                    1 for q in d["decisions"] + d["decisions_resolved"] if q.get("issue") == i["id"] and q.get("kind") in ("question", "permission")
                ),
                "guard_stops": sum(1 for s in stops if s.get("issue") == i["id"]),
                "lock_wait_min": round(sum(u.get("waited_s", 0) for u in lock_uses if u.get("issue") == i["id"]) / 60, 1),
                "risk": check.get("risk"),
                "confused": report.get("confused", ""),
            }
        )
    return out


def text(rows: list[dict[str, Any]], calls: dict[str, dict[str, float]] | None = None) -> str:
    head = (
        f"{'id':8} {'status':12} {'$':>7} {'plan$':>6} {'work$':>6} {'rounds':>6} {'min':>6} {'rework':>6} "
        f"{'confl':>5} {'nudge':>5} {'ask':>3} {'stop':>4} {'risk':6}"
    )
    lines = [head]
    for r in rows:
        lines.append(
            f"{r['id']:8} {r['status']:12} {r['usd']:7.2f} {r['plan_usd']:6.2f} {r['work_usd']:6.2f} {r['rounds']:6} "
            f"{r['to_review_min'] or 0:6.0f} {r['rework']:6} {r['conflicts']:5} "
            f"{r['nudges']:5} {r['questions']:3} {r['guard_stops']:4} {r['risk'] or '':6}"
        )
    lines.append(f"total ${sum(r['usd'] for r in rows):.2f}")
    for r in rows:
        if r["confused"] and r["confused"].strip().lower() not in ("none", "nothing", "-"):
            lines.append(f"{r['id']} misled by: {r['confused']}")
    for o, n in (calls or {}).items():
        lines.append(f"GitHub calls {o:8} last hour {n['last_hour']:5.0f} · average {n['per_hour']:6.1f}/h (24 h)")
    return "\n".join(lines)
