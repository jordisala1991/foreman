"""The backlog as one-line text for the terminal (`foreman backlog show|get`, `foreman start`, `foreman integrate`):
functions that take the Backlog and its data. Paths (reports, a decision's page) are printed as they are stored,
relative to the home."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .backlog import CLOSED, OUTCOME, PENDING_SUBS, SUB_SYMBOLS

if TYPE_CHECKING:
    from .backlog import Backlog, Holds, Issue, Record


def render_sub(b: Backlog, s: Record) -> str:
    s_ = b.s
    who = ""
    if s.get("agent") and s.get("branch"):
        who = f"{s_['agent']} {s['agent']}, {s_['branch']} `{s['branch']}`"
    elif s.get("agent"):
        who = f"{s_['agent']} {s['agent']}"
    elif s.get("branch"):
        who = f"{s_['branch']} `{s['branch']}`"
    rep = ""
    if s.get("report"):
        rep = f"{s_['report']} {s['report']}"
    parts = [
        s["id"],
        s.get("title", ""),
        s_["measure"] if s.get("kind") == "measure" else "",
        SUB_SYMBOLS[s["status"]],
        s.get("commit", ""),
        who,
        s.get("moved_to", ""),
        s.get("text", ""),
        rep,
    ]
    return " ".join(p for p in parts if p)


def render_subs(b: Backlog, subs: list[Record]) -> str:
    return " · ".join(render_sub(b, s) for s in subs)


def render_outcome(b: Backlog, i: Issue) -> str:
    h = i.get(OUTCOME)
    if not h:
        return ""
    alias = f" ({', '.join(h['aliases'])})" if h.get("aliases") else ""
    if not h.get("claim"):  # the title states the goal
        return f"{b.s['criterion_label']}{alias}: {h['criterion']}"
    return f"{b.s['claim_label']}{alias}: {h['claim']} · {b.s['criterion_inline']}: {h['criterion']}"


def render_result(b: Backlog, i: Issue) -> str:
    label = b.verdict_label(i)
    name = b.s["result_label"]
    if not i.get("result"):
        return f"{name}: {label}" if label else ""
    return name + (f" ({label})" if label else "") + f": {i['result']}"


def render_fields(b: Backlog, i: Issue) -> str:
    """The fields of an issue, one short cell part («priority high · assignee A · …»)."""
    s_ = b.s
    parts = []
    if i.get("priority") and i["priority"] != "none":
        parts.append(f"{s_['priority']} {i['priority']}")
    if i.get("assignee"):
        parts.append(f"{s_['assignee']} {i['assignee']}")
    if i.get("repo"):
        parts.append(f"{s_['repo']} {i['repo']}")
    if i.get("wanted"):
        parts.append(f"{s_['wanted']} {i['wanted']}")
    rel = i.get("relations") or {}
    for key in ("blocks", "related"):
        if rel.get(key):
            parts.append(f"{s_['rel_' + key]} {', '.join(rel[key])}")
    if rel.get("duplicate_of"):
        parts.append(f"{s_['rel_duplicate']} {rel['duplicate_of']}")
    return " · ".join(parts)


def render_held(b: Backlog, i: Issue, holds: Holds | None, label: str | None = None) -> str:
    """What of an open issue waits for a pending decision: «waits for decision XX-1 J1» (the whole issue), «T3
    waits for decision XX-1 J2» (each open or not yet created sub-issue it names). "" when nothing does."""
    h = (holds or {}).get(i["id"]) if i["status"] not in CLOSED else None
    if not h:
        return ""
    label = b.s["held_by"] if label is None else label
    status = {s["id"]: s["status"] for s in i["subs"]}
    parts = [f"{label} {', '.join(h[None])}"] if h.get(None) else []
    parts += [
        f"{sid} {label} {', '.join(ids)}"
        for sid, ids in h.items()
        if sid is not None and ids and status.get(sid, "todo") in PENDING_SUBS
    ]
    return " · ".join(parts)


def render_blocked(
    b: Backlog,
    i: Issue,
    rel_blockers: dict[str, list[str]],
    label: str | None = None,
    holds: Holds | None = None,
    hold_label: str | None = None,
) -> str:
    """The blocking cell: the free-text reason, the open issues that block it through a relation, then what waits
    for a pending decision (render_held)."""
    rel = rel_blockers.get(i["id"]) if i["status"] not in CLOSED else None
    label = b.s["rel_blocked_by"] if label is None else label
    held = render_held(b, i, holds, hold_label)
    return " · ".join(p for p in (i["blocked"], f"{label} {', '.join(rel)}" if rel else "", held) if p)


def render_project_meta(b: Backlog, p: Record) -> str:
    s_ = b.s
    meta = [
        x
        for x in (
            p.get("status"),
            p.get("priority") and p["priority"] != "none" and f"{s_['priority']} {p['priority']}",
            p.get("repos") and f"{s_['repo']} {', '.join(p['repos'])}",
        )
        if x
    ]
    return " · ".join(meta)


def render_decision(b: Backlog, d: Record) -> str:
    """One decision as one line. What a decision of before has, as before; the effect of each option, what waits
    without an answer and its page (the path as it is stored) come after the options, only when the decision has
    them."""
    head = f"**{d['id']}, {d['title']}**" if d.get("id") else f"**{d['title']}**"
    rec = d.get("recommendation", "")
    parts = [
        f"- {head} ({d['date']}):",
        d.get("text", ""),
        f"{b.s['recommendation_label']}: {rec}" if rec else "",
        f"{b.s['holds_label']}: " + ", ".join(d["blocks"]) + "." if d.get("blocks") else "",
        f"{b.s['options_label']}: " + " · ".join(d["options"]),
        render_effects(b, d),
    ]
    u = d.get("unanswered") or {}
    if u.get("waits"):
        when = f" ({b.s['unanswered_by'].format(date=u['by'])})" if u.get("by") else ""
        parts.append(f"{b.s['unanswered_label']}{when}: {u['waits']}")
    if d.get("page"):
        parts.append(f"{b.s['page_label']}: {d['page']}")
    return " ".join(p for p in parts if p)


def render_effects(b: Backlog, d: Record) -> str:
    """«What each option changes: A: effect (cost; can be undone) · B: …» for the options with details; empty
    when none has them."""
    det = d.get("details") or {}
    items = []
    for o in d.get("options", []):
        x = det.get(o)
        if not isinstance(x, dict) or not x:
            continue
        extra = [x["cost"]] if x.get("cost") else []
        if x.get("undo") in ("yes", "hard", "no"):
            extra.append(b.s[f"undo_{x['undo']}"])
        items.append(o + (f": {x['effect']}" if x.get("effect") else "") + (f" ({'; '.join(extra)})" if extra else ""))
    return f"{b.s['effects_label']}: " + " · ".join(items) if items else ""
