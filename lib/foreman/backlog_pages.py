"""The generated pages of a backlog (backlog, archive, decisions): functions that take the Backlog and its data.

`render` writes the pages that changed and `stale` lists the ones that differ from the data; `rendered` is the text of
every page. They are rewritten after every write (Backlog.mutate), so they are never edited by hand.
"""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING

from . import util
from .backlog import CLOSED, PENDING_SUBS, SUB_SYMBOLS, BacklogError

if TYPE_CHECKING:
    from pathlib import Path

    from .backlog import Backlog, Data, Holds, Issue, Record


def link(b: Backlog, page: Path, path: str) -> str:
    if path.startswith("/"):  # a document of another product, linked where it is
        return path
    rel = os.path.relpath(b.root, page.parent)
    return f"{rel}/{path}" if rel != "." else path


def render_plan(b: Backlog, plan: list[Record], page: Path) -> str:
    return " · ".join(
        f"[{p['label']}]({link(b, page, p['path'])})" + (f" {p['note']}" if p.get("note") else "") for p in plan
    )


def render_sub(b: Backlog, s: Record, page: Path | None = None) -> str:
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
        rep = f"[{s_['report']}]({link(b, page or b.out_backlog, s['report'])})"
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


def render_subs(b: Backlog, subs: list[Record], page: Path | None = None) -> str:
    return " · ".join(render_sub(b, s, page) for s in subs)


def render_outcome(b: Backlog, data: Data, i: Issue) -> str:
    h = i.get("acceptance")
    if not h:
        return ""
    alias = f" ({', '.join(h['aliases'])})" if h.get("aliases") else ""
    if not h.get("claim"):  # the title states the goal
        return f"**{b.s['criterion_label']}**{alias}: {h['criterion']}"
    return f"**{b.s['claim_label']}**{alias}: {h['claim']} · **{b.s['criterion_inline']}:** {h['criterion']}"


def render_result(b: Backlog, data: Data, i: Issue) -> str:
    label = b.verdict_label(data, i)
    name = b.s["result_label"]
    if not i.get("result"):
        return f"**{name}:** {label}" if label else ""
    return f"**{name}**" + (f" ({label})" if label else "") + f": {i['result']}"


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


def render_row(
    b: Backlog,
    data: Data,
    i: Issue,
    page: Path,
    rel_blockers: dict[str, list[str]] | None = None,
    holds: Holds | None = None,
) -> str:
    detail = " · ".join(
        p for p in (render_fields(b, i), render_outcome(b, data, i), render_result(b, data, i), i["detail"]) if p
    )
    rb = b.relation_blockers(data) if rel_blockers is None else rel_blockers
    holds = b.decision_holds(data) if holds is None else holds
    cells = [
        i["id"],
        i["title"],
        i["status"],
        render_plan(b, i["plan"], page),
        render_subs(b, i["subs"], page),
        render_blocked(b, i, rb, holds=holds),
        detail,
    ]
    return "| " + " | ".join(cells) + " |"


def render_table(b: Backlog, data: Data, issues: list[Issue], page: Path) -> str:
    rb = b.relation_blockers(data)
    holds = b.decision_holds(data)
    return "\n".join([b.s["table_head"]] + [render_row(b, data, i, page, rb, holds) for i in issues])


def notice(b: Backlog) -> str:
    try:
        rel = b.data_path.relative_to(b.root)
    except ValueError:
        rel = b.data_path
    return b.s["notice"].format(file=rel)


def page_head(b: Backlog, page: Record, updated: str) -> str:
    parts = []
    if page.get("front_matter"):
        parts.append(
            "\n".join(["---"] + [f"{k}: {updated if k == 'updated' else v}" for k, v in page["front_matter"]] + ["---"])
        )
    parts += [f"# {page['heading']}", notice(b)]
    intro = "\n".join(page.get("intro", [])).strip("\n")
    if intro:
        parts.append(intro)
    return "\n\n".join(parts)


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
    return f"*{' · '.join(meta)}*" if meta else ""


def render_backlog(b: Backlog, data: Data) -> str:
    page = data["pages"]["backlog"]
    out = [page_head(b, page, data["updated"])]
    for p in data["projects"]:
        sec = [f"## {p['id']} · {p['title']}"]
        meta = render_project_meta(b, p)
        if meta:
            sec.append(meta)
        if p.get("intro"):
            sec.append(p["intro"])
        sec.append(render_table(b, data, p["issues"], b.out_backlog))
        out.append("\n\n".join(sec))
    if data.get("issues"):
        out.append(f"## {b.s['no_project']}\n\n{render_table(b, data, data['issues'], b.out_backlog)}")
    return "\n\n".join(out) + "\n"


def render_archive(b: Backlog, data: Data) -> str:
    page = data["pages"]["archive"]
    titles = {p["id"]: p["title"] for p in data["projects"]}
    out = [page_head(b, page, data["updated"])]
    for g in data["archive"]:
        head = f"{g['project']} · {titles.get(g['project'], '')}" if g["project"] else b.s["no_project"]
        out.append(f"## {head}\n\n{render_table(b, data, g['issues'], b.out_archive)}")
    return "\n\n".join(out) + "\n"


def render_decision(b: Backlog, d: Record, page: Path | None = None) -> str:
    """One decision as a line of the decisions page (`page`: that page, for the link to the decision's own page;
    None: the page's path as it is stored). What a decision of before has, as before; the effect of each option,
    what waits without an answer and the page come after the options, only when the decision has them."""
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
        name = d["page"].rsplit("/", 1)[-1]
        parts.append(f"{b.s['page_label']}: [{name}]({link(b, page, d['page']) if page else d['page']})")
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


def render_decisions_body(b: Backlog, data: Data) -> str:
    intro = "\n".join(data["pages"].get("decisions", {}).get("intro", [])).strip("\n")
    items = "\n".join(render_decision(b, d, b.out_decisions) for d in data["decisions"]) or b.s["none"]
    return "\n\n".join(x for x in (notice(b), intro, items) if x)


def splice_section(b: Backlog, md: str, title: str, body: str) -> str:
    """`md` with the body of section `title` (up to the next `## `) replaced."""
    m = re.search(rf"^{re.escape(title)}[^\n]*\n(.*?)(?=^## |\Z)", md, re.S | re.M)
    if not m:
        raise BacklogError(f"no section «{title}» in {b.out_decisions.name}")
    return md[: m.start(1)] + "\n" + body + "\n\n" + md[m.end(1) :]


def rendered(b: Backlog, data: Data) -> dict[Path, str]:
    """{path: text} of every generated page."""
    out = {b.out_backlog: render_backlog(b, data), b.out_archive: render_archive(b, data)}
    if b.decisions_section:  # a section of a page the project writes by hand
        if b.out_decisions.exists():
            out[b.out_decisions] = splice_section(
                b, b.out_decisions.read_text(), b.decisions_section, render_decisions_body(b, data)
            )
    else:
        heading = b.s["decisions_heading"].format(owner=b.project.owner)
        out[b.out_decisions] = f"# {heading}\n\n{render_decisions_body(b, data)}\n"
    return out


def render(b: Backlog, data: Data | None = None) -> list[Path]:
    """Writes the generated pages that changed; returns their paths."""
    data = data or b.load()
    return [p for p, text in rendered(b, data).items() if util.write_text_atomic(p, text)]


def stale(b: Backlog, data: Data | None = None) -> list[Path]:
    data = data or b.load()
    return [p for p, text in rendered(b, data).items() if not p.exists() or p.read_text() != text]
