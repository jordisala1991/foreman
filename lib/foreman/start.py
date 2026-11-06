"""`foreman start`: what a new orchestrator session needs to start, in one short summary.

Instead of reading the procedure, the owner's guide, the rules, the board, the tail of the day's notes and the backlog,
and hunting its own session id, the incoming session runs `foreman start` and reads the rest only when a line says it
needs it. In order:

- its own session id (and name, when it is the marked one), and how it was found: $CLAUDE_CODE_SESSION_ID, else the
  live session of this folder (or a parent) by its heartbeat (heartbeat.py), else the newest transcript of this
  folder;
- who is marked now and whether it lives (orchestrator.health);
- the steps: the exact `foreman session` line, how the loop pass comes once marked (foreman's hooks module sends it
  every [loop] every_min; nothing to create), whom to tell; a PAUSED line while the product is paused;
- the board; the active backlog (no closed issues, triage as a count); what waits for the owner (decisions, unread
  inbox); resources and locks; the agents foreman counts as live, each with whether its session still runs;
- the rules file (path, size, its sections) and «read next only if needed» pointers.

Texts by the project's language (i18n `start`); `--json` gives the same fields. Only reads.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import backlog_text, i18n, identity, lock, loop, notes, orchestrator, pause, transcripts
from .backlog import Backlog, BacklogError

if TYPE_CHECKING:
    import argparse

    from .config import Project
    from .util import PathLike

Summary = dict[str, Any]  # collect(): every field of the summary, as `--json` gives it

SUBS_SHOWN = 3  # open sub-issues shown per started issue
STARTED = ("in progress", "ready", "refining")  # issues shown with their open sub-issues and plan
ORDER = STARTED + ("backlog",)


# ---------- this session ----------


def folder_key(path: PathLike) -> str:
    """Claude Code's folder name for a working directory under its projects folder."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def folders(cwd: PathLike) -> list[str]:
    """The working directory and its parents (not the root): a session opened in a parent of where foreman runs."""
    p = Path(cwd).resolve()
    out = [p] + [q for q in p.parents if q != Path(q.anchor)]
    return [str(x) for x in out]


def own_session(project: Project, env: Mapping[str, str] | None = None, cwd: str | None = None) -> dict[str, Any]:
    """{"session", "name", "how", "transcript"} of the session running this command; session None when not found.
    `how` is the i18n key of how it was found: $CLAUDE_CODE_SESSION_ID, else the live sessions of the project whose
    folder is this one or a parent (their heartbeats, newest tick first), else the newest transcript of the folder.
    The name is the one recorded when the session was marked (Claude Code tells it to ListAgents only)."""
    from . import heartbeat

    env = os.environ if env is None else env
    places = folders(cwd or os.getcwd())
    sid, how = (env.get("CLAUDE_CODE_SESSION_ID") or "").strip(), "how_env"
    n = 0
    if not sid:
        here = [x for x, d in heartbeat.live(project) if d.get("cwd") in places]
        if here:
            sid, how, n = here[0], "how_beat", len(here)
    path = transcripts.transcript_of(sid) if sid else None
    if not sid:
        newest: tuple[float, Path] | None = None
        for place in places:
            try:
                for f in (transcripts.claude_projects_dir() / folder_key(place)).glob("*.jsonl"):
                    m = f.stat().st_mtime
                    if newest is None or m > newest[0]:
                        newest = (m, f)
            except OSError:
                continue
            if newest:
                break
        if newest:
            sid, how, path = newest[1].stem, "how_transcript", str(newest[1])
        else:
            how = "how_none"
    o = orchestrator.current(project) or {}
    return {
        "session": sid or None,
        "name": o.get("name", "") if sid and o.get("session") == sid else "",
        "how": how,
        "files": n,
        "transcript": path,
    }


# ---------- the backlog ----------


def active_backlog(project: Project) -> dict[str, Any]:
    """{"issues": [{id, status, title, subs_done, subs_total, blocked, plan, open_subs}], "triage": [ids],
    "decisions": [{id, date, title, blocks?}]}; empty lists without a backlog. `blocked` also says what waits for a
    pending decision («T3 waits for decision XX-1 J1»; an answer from the panel resolves its decision)."""
    out: dict[str, Any] = {"issues": [], "triage": [], "decisions": []}
    b = Backlog(project)
    try:
        if not b.exists():
            return out
        data = b.load()
    except (BacklogError, OSError, ValueError, KeyError):
        return out
    rb = b.relation_blockers(data)
    holds = b.decision_holds(data)
    for _, i, _ in b.iter_issues(data, archived=False):
        st = i.get("status")
        if st == "triage":
            out["triage"].append(i["id"])
        if st not in ORDER:
            continue
        subs = [s for s in i.get("subs", []) if s.get("status") not in ("canceled", "moved")]
        open_subs = [
            {k: s[k] for k in ("id", "status", "title", "agent", "branch") if s.get(k)}
            for s in subs
            if s.get("status") != "done"
        ]
        blocked = i.get("blocked") or (("by " + ", ".join(rb[i["id"]])) if rb.get(i["id"]) else "")
        blocked = " · ".join(x for x in (blocked, backlog_text.render_held(b, i, holds)) if x)
        out["issues"].append(
            {
                "id": i["id"],
                "status": st,
                "title": re.sub(r"\*\*|`", "", i.get("title", "")),
                "subs_done": sum(s.get("status") == "done" for s in subs),
                "subs_total": len(subs),
                "blocked": blocked,
                "plan": [p["path"] for p in i.get("plan", []) if p.get("path")],
                "open_subs": open_subs if st in STARTED else [],
            }
        )
    out["issues"].sort(key=lambda x: ORDER.index(x["status"]))
    out["decisions"] = [
        {"id": d.get("id"), "date": d.get("date"), "title": d.get("title", "")}
        | ({"blocks": d["blocks"]} if d.get("blocks") else {})
        for d in data.get("decisions", [])
    ]
    return out


# ---------- agents and locks ----------


def agents_now(project: Project) -> list[dict[str, Any]]:
    """The agents foreman counts as live (loop.live_agents), each with whether the session it runs in still does:
    [{id, name, type, issue, sub, branch, start, activity, session, session_state}]."""
    s = loop.Snapshot(project)
    out: list[dict[str, Any]] = []
    for h in s.live:
        ident = identity.of(project, h)  # reads the brief only when the entry lacks something
        sid = loop.session_of(h)
        out.append(
            {
                "id": h["id"],
                "name": ident.get("name") or h["id"][:8],
                "type": h.get("type"),
                "issue": ident.get("issue"),
                "sub": ident.get("sub"),
                "branch": ident.get("branch"),
                "start": h.get("start"),
                "activity": h.get("activity"),
                "session": sid,
                "session_state": s.session_state(sid) if sid else "unknown",
            }
        )
    return out


def resources_now(project: Project) -> list[dict[str, Any]]:
    """[{name, holders: [{who, what}], queue: [who]}] of the project's resources."""
    out = []
    for name, st in lock.all_status(project).items():
        out.append(
            {
                "name": name,
                "holders": [{"who": h["who"], "what": h["what"]} for h in st["holders"]],
                "queue": [q["who"] for q in st["queue"]],
            }
        )
    return out


# ---------- the summary ----------


def kb(path: PathLike) -> float | None:
    try:
        return round(Path(path).stat().st_size / 1024, 1)
    except OSError:
        return None


def lines(path: PathLike) -> int | None:
    try:
        with open(path, "rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return None


def collect(
    project: Project, env: Mapping[str, str] | None = None, cwd: str | None = None, now: datetime | None = None
) -> Summary:
    """Every field of the summary, as `--json` gives it."""
    now = now or datetime.now()
    me = own_session(project, env, cwd)
    o = orchestrator.current(project)
    marked = None
    if o:
        h = orchestrator.health(project, o) or {}
        marked = {
            **o,
            "verdict": h.get("verdict"),
            "alive": h.get("alive"),
            "last_pass": h.get("last_pass"),
            "window_from": h.get("window_from"),
            "is_me": bool(me["session"]) and me["session"] == o["session"],
        }
    cfg = project.section("loop")
    prompt = cfg.get("prompt") or ""
    loop_info = {"prompt": prompt, "every_min": cfg.get("every_min", 60)}
    rules = project.path(project.raw["project"]["rules"])
    try:
        headings = [x[3:].strip() for x in rules.read_text(errors="replace").splitlines() if x.startswith("## ")]
    except OSError:
        headings = None
    board = project.board.read_text(errors="replace").strip() if project.board.exists() else None
    today = project.notes_dir / f"{now:%Y-%m-%d}.md"
    return {
        "project": project.name,
        "home": str(project.home),
        "time": now.isoformat(timespec="minutes"),
        "me": me,
        "marked": marked,
        "paused": pause.since(project),
        "loop": loop_info,
        "board": {"path": str(project.board), "text": board},
        "backlog": active_backlog(project),
        "inbox": [
            {"id": m.get("id"), "time": m.get("time"), "text": m.get("text", "")} for m in notes.inbox_new(project)
        ],
        "resources": resources_now(project),
        "agents": agents_now(project),
        "rules": {"path": str(rules), "kb": kb(rules), "headings": headings},
        "read_next": {
            "owner": "foreman docs owner",
            "procedure": "foreman docs orchestrator",
            "notes": {"path": str(today), "lines": lines(today)},
        },
    }


def short(text: object, n: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def render(project: Project, d: Summary, renewal: bool = False) -> str:
    """The plain text of the summary (collect's fields). `renewal`: for the pack of a session that renewed its
    memory in place (renew.pack): no steps to mark it, which are not its case."""
    s = i18n.for_project(project)["start"]
    owner = project.owner
    me, mk, lp = d["me"], d["marked"], d["loop"]
    name = me["name"] or s["no_name"]
    how = s["how_beat_many" if me["how"] == "how_beat" and me.get("files", 0) > 1 else me["how"]].format(
        n=me.get("files") or 0
    )
    out = [s["title"].format(project=d["project"], time=d["time"].replace("T", " ")), ""]
    out.append(
        f"{s['you']}: "
        + (
            s["you_line"].format(session=me["session"], name=me["name"] or "?", how=how)
            if me["session"]
            else s["you_unknown"].format(how=how)
        )
    )

    def hm(iso: str | None) -> str:
        return iso[11:16] if iso else "-"

    if not mk:
        marked = s["marked_none"]
    elif mk["is_me"]:
        marked = s["marked_me"].format(since=mk["since"])
    else:
        v = mk.get("verdict")
        if v == "gone":
            verdict = s["verdict_gone"]
        elif v in ("no_loop", "silent"):
            verdict = s["verdict_" + v].format(last=hm(mk.get("window_from")))
        elif mk.get("alive") == "alive":
            verdict = s["verdict_ok"]
        else:
            verdict = s["verdict_unknown"].format(last=hm(mk.get("last_pass")))
        marked = s["marked_line"].format(
            name=mk["name"] or "(no name)", session=mk["session"], since=mk["since"], verdict=verdict
        )
    out.append(f"{s['marked']}: {marked}")
    if d["paused"]:
        out.append(s["paused"].format(since=d["paused"], owner=owner))
    if not renewal:  # its own steps: not the case of a renewed session
        steps = []
        if mk and mk["is_me"]:
            steps.append(s["step_marked"])
        else:
            rel = s["step_relieves"].format(name=mk["name"] or mk["session"][:8]) if mk else ""
            steps.append(
                s["step_mark"].format(
                    project=d["project"], session=me["session"] or "<session id>", name=name, relieves=rel
                )
            )
        if lp["prompt"]:
            steps.append(s["step_loop"].format(every=lp["every_min"], prompt=lp["prompt"]))
        else:
            steps.append(s["step_no_loop"])
        if mk and not mk["is_me"]:
            who = mk["name"] or mk["session"][:8]
            steps.append(s["step_out_gone" if mk.get("verdict") == "gone" else "step_tell_out"].format(name=who))
            steps.append(s["step_owner"].format(owner=owner))
        steps.append(s["step_pass"].format(project=d["project"]))
        out += ["", f"## {s['steps']}"] + [f"{k}. {t}" for k, t in enumerate(steps, 1)]

    at: dict[str, int] = {"board": len(out)}  # where each section starts (the renewal's order is another)
    out += ["", f"## {s['board'].format(path=d['board']['path'])}", d["board"]["text"] or s["board_none"]]

    bl = d["backlog"]
    at["backlog"] = len(out)
    out += ["", f"## {s['backlog'].format(n=len(bl['issues']))}"]
    for i in bl["issues"]:
        sc = f"{i['subs_done']}/{i['subs_total']}" if i["subs_total"] else ""
        line = f"- {i['id']} {i['status']}" + (f" {sc}" if sc else "") + f": {short(i['title'], 90)}"
        if i["blocked"]:
            line += f" [{s['blocked']}: {short(i['blocked'], 60)}]"
        out.append(line)
        if i["status"] in STARTED:
            for sub in i["open_subs"][:SUBS_SHOWN]:
                who = ", ".join(x for x in (sub.get("agent"), sub.get("branch")) if x)
                out.append(
                    f"    {sub['id']} {sub.get('status', 'todo')}"
                    + (f" ({who})" if who else "")
                    + (f": {short(sub.get('title'), 70)}" if sub.get("title") else "")
                )
            if len(i["open_subs"]) > SUBS_SHOWN:
                out.append(f"    … +{len(i['open_subs']) - SUBS_SHOWN}")
            if i["plan"]:
                out.append(f"    {s['plan']}: {i['plan'][-1]}")
    if not bl["issues"]:
        out.append(s["backlog_none"])
    if bl["triage"]:
        out.append(s["triage"].format(n=len(bl["triage"]), ids=", ".join(bl["triage"])))

    at["owner"] = len(out)
    out += ["", f"## {s['owner'].format(owner=owner)}"]
    waits = [
        f"- {s['decision']} {x['id']} ({x['date']}): {short(x['title'], 100)}"
        + (f" [{s['holds']}: {', '.join(x['blocks'])}]" if x.get("blocks") else "")
        for x in bl["decisions"]
    ]
    waits += [f"- {s['inbox']} {m['id']} ({m['time']}): {short(m['text'], 100)}" for m in d["inbox"]]
    out += waits or [s["owner_none"]]

    at["resources"] = len(out)
    out += ["", f"## {s['resources']}"]
    for r in d["resources"]:
        holders = ", ".join(f"{h['who']}: {short(h['what'], 50)}" for h in r["holders"]) or s["free"]
        out.append(f"- {r['name']}: {holders}" + (f"; {s['queue']}: {', '.join(r['queue'])}" if r["queue"] else ""))
    if not d["resources"]:
        out.append(s["resources_none"])

    at["agents"] = len(out)
    out += ["", f"## {s['agents']}"]
    for a in d["agents"]:
        state = {"gone": s["agent_dead"], "alive": s["agent_live"]}.get(a["session_state"], s["agent_unknown"])
        what = " ".join(x for x in (a["issue"], a["sub"]) if x)
        out.append(
            f"- {a['name']} ({a['type'] or '?'}"
            + (f", {what}" if what else "")
            + (f", {a['branch']}" if a["branch"] else "")
            + f"): {state}; "
            + s["agent_times"].format(start=hm(a["start"]), last=hm(a["activity"]))
        )
    if not d["agents"]:
        out.append(s["agents_none"])

    at["rest"] = len(out)
    r = d["rules"]
    out.append("")
    if r["headings"] is None:
        out.append(s["rules_missing"].format(path=r["path"]))
    else:
        out.append(
            s["rules"].format(
                path=r["path"],
                kb=r["kb"],
                tokens=int((r["kb"] or 0) * 1024 / 4),
                headings=" · ".join(r["headings"]) or "-",
            )
        )
    rn = d["read_next"]
    nxt = [s["next_owner"].format(owner=owner), s["next_procedure"]]
    nxt += [
        s["next_plan"].format(issue=i["id"], path=i["plan"][-1])
        for i in bl["issues"]
        if i["status"] == "in progress" and i["plan"]
    ]
    if rn["notes"]["lines"] is not None:
        nxt.append(s["next_notes"].format(path=rn["notes"]["path"], lines=rn["notes"]["lines"]))
    nxt.append(s["next_tools"])
    out += ["", f"## {s['next']}"] + [f"- {x}" for x in nxt]
    if renewal:  # what waits for the owner and the live agents first: a pack cut to fit keeps them
        names = ["board", "backlog", "owner", "resources", "agents", "rest"]
        ends = [at[n] for n in names[1:]] + [len(out)]
        part = {n: out[at[n] : e] for n, e in zip(names, ends)}
        out = out[: at["board"]] + [
            x for n in ("owner", "agents", "board", "backlog", "resources", "rest") for x in part[n]
        ]
    return "\n".join(out)


def add_parser(sp: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    a = sp.add_parser("start", help="what a new orchestrator session needs to start, in one short summary")
    a.add_argument("--json", action="store_true", help="the same fields as JSON")


def run(args: argparse.Namespace, project: Project) -> int:
    d = collect(project)
    print(json.dumps(d, ensure_ascii=False, indent=1) if args.json else render(project, d))
    return 0
