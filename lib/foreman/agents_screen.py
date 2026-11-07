"""The data of the new panel's «Agents» screen (/api/agents): who is running now (the panel's own agent list, the one
the old page shows), who finished today (costs.jsonl, one line per agent, the same lines «How it goes» sums), and when
the orchestrator's next pass is due. Read-only. A line of costs.jsonl counts only when its issue is one of the
product's backlog (as metrics.py does), so what this screen lists adds up to what the charts count."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from . import agents as agents_mod
from . import costs, metrics, orchestrator, util

if TYPE_CHECKING:
    from .config import Project

DONE_MAX = 50  # finished agents of the day the screen lists


def live_card(project: Project, a: dict[str, Any], titles: dict[str, str], model: str | None) -> dict[str, Any]:
    """One running agent of panel.agents as the screen's card. `task` is the issue's short name (the agent's own name
    when it has no issue of the backlog), `status` is panel.agents' («working», «holds a lock», «waiting for a
    lock», «waiting for its task», «maybe stuck»), `since` the start and `sign` the last
    sign of life (epoch seconds)."""
    issue = a.get("issue")
    return {
        "id": a["id"],
        "p": project.name,
        "name": a["name"],
        "task": titles.get(issue or "") or a["name"],
        "issue": issue,
        "sub": a.get("sub"),
        "type": a.get("type"),
        "family": costs.family(model),
        "status": a["status"],
        "since": a.get("start_ts"),
        "sign": a.get("activity_ts"),
        "msg": a.get("last"),
        "session": bool(a.get("session")),
        "link": a.get("link"),
    }


def done_today(project: Project, today: str, skip: set[str]) -> list[dict[str, Any]]:
    """The agents whose run ended `today` (local «YYYY-MM-DD»), latest first, from costs.jsonl; not the ids in `skip`
    (running again after a delivery)."""
    titles = metrics.own_issues(project)
    out = []
    for r in costs.runs(project):
        end, issue = r.get("end"), r.get("issue")
        if not (isinstance(end, str) and end.startswith(today)) or issue not in titles or r.get("agent") in skip:
            continue
        minutes, usd = r.get("minutes"), r.get("usd")
        out.append(
            {
                "agent": r.get("agent"),
                "p": project.name,
                "name": r.get("name"),
                "task": titles[issue],
                "issue": issue,
                "sub": r.get("sub"),
                "type": r.get("type"),
                "outcome": r.get("outcome"),
                "minutes": minutes if isinstance(minutes, (int, float)) else None,
                "family": costs.family(r.get("model")),
                "usd": usd if isinstance(usd, (int, float)) else 0,
                "end": end,
            }
        )
    out.sort(key=lambda x: x["end"], reverse=True)
    return out[:DONE_MAX]


def next_pass(project: Project) -> int | None:
    """When the orchestrator's next pass is due (epoch seconds: the last pass plus [loop] every_min); None for a
    product without a loop or before its first pass."""
    last = util.iso_epoch(orchestrator.last_pass(project)) if orchestrator.watches_loop(project) else None
    return None if last is None else int(last + orchestrator.every_min(project) * 60)


def build(project: Project, live: list[dict[str, Any]], model_of: Any, today: datetime) -> dict[str, Any]:
    """The screen's data of one product: `live` is panel.agents(...) (any state), `model_of(transcript)` the model a
    running agent runs on (cached by the caller)."""
    titles = metrics.own_issues(project)
    running = [a for a in live if a["state"] in agents_mod.LIVE_STATES]
    cards = [live_card(project, a, titles, model_of(a["transcript"])) for a in running]
    return {
        "live": cards,
        "done": done_today(project, today.strftime("%Y-%m-%d"), {a["id"] for a in running}),
        "next_pass": next_pass(project),
    }
