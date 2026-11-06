"""The product's history, read by `foreman catchup`: what happened, as events in time order, each with where to dig.

It only reads, and merges what foreman already writes (a source that is missing or broken is skipped):
- the day notes' headings (`## HH:MM title`, `- HH:MM text`; «Integrated …» ones are `integrate` events,
  «Measured …» ones (`integrate --measure`) `measure` events);
- `<state>/progress.jsonl` (progress lines; deliveries with their outcome);
- `<state>/launches.json` (each `foreman launch`) and `<state>/agents.json` (start, delivery-check refusals, stop with
  outcome, model and final message length);
- `<state>/integrate/*-ci.log` (each CI run of `foreman integrate`: where, command, exit code, time);
- `<state>/loop.jsonl` (each loop pass: skipped or not, its notices);
- `<state>/inbox.json` (the owner's messages, the orchestrator's replies, decisions) and `orchestrator.json`.

Every event has a `ref`: where to dig (a transcript, a note's file and line, a log's line, a state file). agents.json
and launches.json keep about a day, so older agents show only through the notes and progress lines. No token spend:
that, and printing the history, is the developer tool's (dev/foreman-dev timeline, not part of the product).
"""

from __future__ import annotations

import re
from collections.abc import Collection
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, overload

from . import delivery, identity, orchestrator, transcripts, util

if TYPE_CHECKING:
    from .config import Project

Event = dict[str, Any]  # event(): {t, kind, who, issue, text, ref, agent_id, session, branch, data}
Rec = dict[str, Any]  # an entry of agents.json or launches.json (identity.Agent, identity.Launch)

KINDS = (
    "session",
    "launch",
    "start",
    "progress",
    "block",
    "delivery",
    "stop",
    "ci",
    "integrate",
    "measure",
    "note",
    "loop",
    "inbox",
    "decision",
)
TEXT_CHARS = 160
# «Outgoing session: NAME (session ID)»: a line of the handover notes foreman wrote before 0.44 (old notes are read)
OUTGOING = re.compile(r"Outgoing session: (\S+) \(session ([0-9a-f-]{8,})")
HEADING = re.compile(r"^## (\d\d):(\d\d) (.*)$")
SHORT_NOTE = re.compile(r"^- (\d\d):(\d\d) (.*)$")
INTEGRATED = re.compile(r"^(Integrated|Measured) \S+?:(\S+) \(")
CI_START = re.compile(r"^== (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) in (.*?): (.*)$")
CI_END = re.compile(r"^== (?:rc (-?\d+) in (\d+) s|could not start: (.*))$")


# ---------- small helpers ----------


@overload
def naive(t: datetime) -> datetime: ...
@overload
def naive(t: None) -> None: ...
def naive(t: datetime | None) -> datetime | None:
    """A datetime as naive local time (state files write local times without a zone)."""
    if t is None:
        return None
    return t.astimezone().replace(tzinfo=None) if t.tzinfo else t


def when(s: object) -> datetime | None:
    """A state file's time (`2026-10-31T09:30:00`, `2026-10-31 09:30`) as a naive local datetime, or None."""
    if not isinstance(s, str) or not s.strip():
        return None
    try:
        return naive(util.fromiso(s.strip().replace(" ", "T", 1)))
    except (ValueError, TypeError):
        t = transcripts.parse_ts(s)
        return naive(t) if t else None


def short(text: object, n: int = TEXT_CHARS) -> str:
    t = " ".join(str(text or "").split())
    return t if len(t) <= n else t[: n - 1] + "…"


def where(path: object, line: int | None = None) -> str:
    """A pointer: the path, and `:LINE` when given (the text output shortens it, `Paths`)."""
    return f"{path}:{line}" if line else str(path)


def event(
    t: datetime | None,
    kind: str,
    text: str,
    ref: str,
    who: str | None = None,
    issue: str | None = None,
    **extra: Any,
) -> Event:
    return {
        "t": t,
        "kind": kind,
        "who": who or "",
        "issue": issue or "",
        "text": text,
        "ref": ref,
        "agent_id": extra.pop("agent_id", None),
        "session": extra.pop("session", None),
        "branch": extra.pop("branch", None),
        "data": extra,
    }


# ---------- what foreman knows about agents ----------


class World:
    """The product's agents (agents.json and launches.json), read once: by id, by name and by branch."""

    def __init__(self, project: Project) -> None:
        self.p = project
        agents = util.read_json(project.state / "agents.json", {})
        launches = util.read_json(identity.launches_file(project), {})
        self.agents = agents if isinstance(agents, dict) else {}
        self.launches = launches if isinstance(launches, dict) else {}
        self.by_token: dict[str, str] = {}
        for tok, rec in self.launches.items():
            if isinstance(rec, dict) and rec.get("agent"):
                self.by_token[rec["agent"]] = tok
        self.by_name: dict[str, list[str]] = {}
        self.by_branch: dict[str, Rec] = {}
        for aid, h in self.agents.items():
            if not isinstance(h, dict):
                continue
            if h.get("name"):
                self.by_name.setdefault(h["name"], []).append(aid)
            if h.get("branch"):
                self.by_branch[h["branch"]] = h
        for rec in self.launches.values():
            if isinstance(rec, dict) and rec.get("branch") and rec["branch"] not in self.by_branch:
                self.by_branch[rec["branch"]] = rec
        self.names = {rec.get("name"): rec for rec in self.launches.values() if isinstance(rec, dict)}
        body = project.section("ids").get("issue", r"[A-Z]{2}-\d+").strip("^$")
        self.issue_re = re.compile(rf"(?<![\w-])((?:{body}))(?![\w-])")
        self._paths: dict[str, str | None] = {}
        self.orchestrators: dict[str, str] = {}  # {session: name}, set by collect (orchestrators)
        self.mark: orchestrator.Mark | None = None  # the marked orchestrator, set by collect

    def info(self, aid: str) -> Rec:
        h = self.agents.get(aid)
        return h if isinstance(h, dict) else {}

    def issue_of(self, rec: Rec) -> str:
        return " ".join(x for x in (rec.get("issue"), rec.get("sub")) if x)

    def issue_of_name(self, name: str) -> str:
        for aid in reversed(self.by_name.get(name, [])):
            got = self.issue_of(self.info(aid))
            if got:
                return got
        rec = self.names.get(name)
        return self.issue_of(rec) if isinstance(rec, dict) else ""

    def issue_of_branch(self, branch: str) -> str:
        rec = self.by_branch.get(branch)
        return self.issue_of(rec) if isinstance(rec, dict) else ""

    def transcript(self, aid: str) -> str | None:
        if aid not in self._paths:
            h = self.info(aid)
            self._paths[aid] = transcripts.subagent_file(aid, h.get("transcript"))
        return self._paths[aid]

    def session(self, aid: str) -> str | None:
        h = self.info(aid)
        rec = self.launches.get(self.by_token.get(aid) or h.get("launch") or "")
        if isinstance(rec, dict) and rec.get("session"):
            return rec["session"]
        return transcripts.session_of_subagent(h.get("transcript")) or transcripts.session_of_subagent(
            self.transcript(aid)
        )

    def ref(self, aid: str, fallback: object) -> str:
        return where(self.transcript(aid) or fallback)


# ---------- sources ----------


def from_agents(w: World, since: datetime, until: datetime) -> list[Event]:
    out: list[Event] = []
    state = w.p.state
    for aid, h in w.agents.items():
        if not isinstance(h, dict):
            continue
        start, end = when(h.get("start")), when(h.get("end"))
        last = end or when(h.get("activity")) or start
        if (start and start >= until) or (last and last < since and not h.get("delivery_incomplete")):
            continue
        name, issue, sid = h.get("name") or aid[:8], w.issue_of(h), w.session(aid)
        ref = w.ref(aid, state / "agents.json")
        common = {"who": name, "issue": issue, "agent_id": aid, "session": sid, "branch": h.get("branch")}
        if start:
            out.append(
                event(
                    start,
                    "start",
                    f"{h.get('type') or '?'} started"
                    + (f", asked model {h['model_asked']}" if h.get("model_asked") else ""),
                    ref,
                    **common,
                )
            )
        if h.get("blocked_at"):
            n = h.get("delivery_blocks") or 1
            out.append(
                event(
                    when(h["blocked_at"]),
                    "block",
                    f"the delivery check refused its stop ({n}×, last here)",
                    ref,
                    **common,
                )
            )
        inc = h.get("delivery_incomplete")
        if isinstance(inc, dict) and when(inc.get("at")):
            out.append(
                event(
                    when(inc["at"]),
                    "block",
                    "let go INCOMPLETE, missing: " + ", ".join(map(str, inc.get("missing") or [])),
                    ref,
                    **common,
                )
            )
        if end:
            done, outcome = delivery.delivery(w.p, aid, h)
            how = f"delivered {outcome}" if done else "stopped WITHOUT delivery (no progress --done)"
            bits = [how]
            if h.get("model"):
                bits.append(f"model {h['model']}")
            if h.get("final_chars") is not None:
                bits.append(f"final message {h['final_chars']} chars")
            out.append(event(end, "stop", ", ".join(bits), ref, **common))
    return out


def from_launches(w: World, since: datetime, until: datetime) -> list[Event]:
    out: list[Event] = []
    for tok, rec in w.launches.items():
        t = when(rec.get("at")) if isinstance(rec, dict) else None
        if not t or not since <= t < until:
            continue
        bits = [rec.get("type") or "?"]
        if rec.get("branch"):
            bits.append(f"branch {rec['branch']}")
        if rec.get("model"):
            bits.append(f"model {rec['model']}")
        if not rec.get("agent"):
            bits.append("no agent took it")
        aid = rec.get("agent")
        ref = w.ref(aid, identity.launches_file(w.p)) if aid else f"{where(identity.launches_file(w.p))}#{tok}"
        out.append(
            event(
                t,
                "launch",
                ", ".join(bits),
                ref,
                who=rec.get("name"),
                issue=w.issue_of(rec),
                agent_id=aid,
                session=rec.get("session"),
                branch=rec.get("branch"),
            )
        )
    return out


def from_progress(w: World, since: datetime, until: datetime) -> list[Event]:
    out: list[Event] = []
    f = w.p.state / "progress.jsonl"
    for n, d in util.read_jsonl(f, numbered=True):
        t = when(d.get("time"))
        if not t or not since <= t < until:
            continue
        name, aid = d.get("agent") or "?", d.get("agent_id")
        if not aid and name in w.by_name:
            aid = w.by_name[name][-1]
        issue = w.issue_of(w.info(aid)) if aid else ""
        issue = issue or w.issue_of_name(name)
        done = d.get("done") is True
        text = (f"{d.get('outcome') or 'ok'}: " if done else "") + short(d.get("text"))
        ref = w.ref(aid, f) if aid and w.transcript(aid) else where(f, n)
        out.append(
            event(
                t,
                "delivery" if done else "progress",
                text,
                ref,
                who=name,
                issue=issue,
                agent_id=aid,
                session=w.session(aid) if aid else None,
                branch=w.info(aid).get("branch") if aid else None,
            )
        )
    return out


def day_files(project: Project, since: datetime, until: datetime) -> list[tuple[date, Path]]:
    d = since.date()
    out: list[tuple[date, Path]] = []
    while d <= until.date():
        out.append((d, project.notes_dir / f"{d.isoformat()}.md"))
        d += timedelta(days=1)
    return out


def from_notes(w: World, since: datetime, until: datetime, handovers: dict[str, str]) -> list[Event]:
    """Notes headings in the window; `handovers` gets {session: name} of the outgoing orchestrators they name."""
    out: list[Event] = []
    for day, f in day_files(w.p, since, until):
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for n, ln in enumerate(lines, 1):
            for name, sid in OUTGOING.findall(ln):
                handovers[sid] = name
            m = HEADING.match(ln) or SHORT_NOTE.match(ln)
            if not m:
                continue
            try:
                t = datetime(day.year, day.month, day.day, int(m.group(1)), int(m.group(2)))
            except ValueError:
                continue
            if not since <= t < until:
                continue
            title = m.group(3).strip()
            im = w.issue_re.search(title)
            b = INTEGRATED.match(title)
            if b:
                rec = w.by_branch.get(b.group(2)) or {}
                out.append(
                    event(
                        t,
                        "integrate" if b.group(1) == "Integrated" else "measure",
                        short(title),
                        where(f, n),
                        who=rec.get("name"),
                        issue=w.issue_of_branch(b.group(2)) or (im.group(1) if im else ""),
                        branch=b.group(2),
                    )
                )
            else:
                out.append(event(t, "note", short(title), where(f, n), issue=im.group(1) if im else ""))
    return out


def from_ci(w: World, since: datetime, until: datetime) -> list[Event]:
    out: list[Event | None] = []
    folder = w.p.state / "integrate"
    try:
        logs = sorted(folder.glob("*-ci.log"))
    except OSError:
        return []
    branches = {b.replace("/", "-"): b for b in w.by_branch}
    try:
        main_dirs = {str(p) for p in w.p._own_roots()}
    except Exception:  # noqa: BLE001 (a missing own-roots listing only costs the guess)
        main_dirs = set()
    for f in logs:
        stem = f.name[: -len("-ci.log")]
        branch = branches.get(stem, stem)
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        run: dict[str, Any] | None = None
        for n, ln in enumerate(lines, 1):
            m = CI_START.match(ln)
            if m:
                run = {"t": when(m.group(1)), "where": m.group(2), "cmd": m.group(3), "line": n}
                continue
            e = CI_END.match(ln)
            if e and run:
                run["rc"] = int(e.group(1)) if e.group(1) is not None else None
                run["secs"] = int(e.group(2)) if e.group(2) is not None else None
                run["error"] = e.group(3)
                out.append(ci_event(w, f, branch, run, main_dirs, since, until))
                run = None
        if run:
            out.append(ci_event(w, f, branch, run, main_dirs, since, until))
    return [e for e in out if e]


def ci_event(
    w: World, f: Path, branch: str, run: dict[str, Any], main_dirs: set[str], since: datetime, until: datetime
) -> Event | None:
    t = run["t"]
    if not t or not since <= t < until:
        return None
    place = "main" if run["where"] in main_dirs else "worktree"
    if "rc" not in run:
        result = "no result in the log (running or cut)"
    elif run.get("error"):
        result = "could not start: " + short(run["error"], 60)
    elif run["rc"] == 0:
        result = f"passed in {run['secs']} s"
    else:
        result = f"FAILED rc {run['rc']} in {run['secs']} s"
    rec = w.by_branch.get(branch) or {}
    return event(
        t,
        "ci",
        f"{branch} on {place}: {result}; {short(run['cmd'], 70)}",
        where(f, run["line"]),
        who=rec.get("name"),
        issue=w.issue_of_branch(branch),
        branch=branch,
        rc=run.get("rc"),
        seconds=run.get("secs"),
    )


def from_loop(w: World, since: datetime, until: datetime, names: dict[str, str]) -> list[Event]:
    out: list[Event] = []
    f = w.p.state / "loop.jsonl"
    for n, d in util.read_jsonl(f, numbered=True):
        t = when(d.get("time"))
        if not t or not since <= t < until:
            continue
        act = [k for k in d.get("actionable") or [] if isinstance(k, str)]
        info = [k for k in d.get("notices") or [] if isinstance(k, str) and k not in act]
        secs = d.get("duration_s")
        bits = ["skipped" if d.get("skipped") else "pass"]
        if act:
            bits.append("act on " + ", ".join(act))
        if info:
            bits.append("info " + ", ".join(info))
        if isinstance(secs, (int, float)):
            bits.append(f"{secs:.1f} s")
        sid: Any = d.get("session")
        out.append(
            event(
                t,
                "loop",
                "; ".join(bits),
                where(f, n),
                who=names.get(sid, ""),
                session=sid,
                skipped=d.get("skipped") is True,
                actionable=act,
                notices=d.get("notices") or [],
                line=n,
            )
        )
    return out


def from_inbox(w: World, since: datetime, until: datetime) -> list[Event]:
    out: list[Event] = []
    f = w.p.state / "inbox.json"
    msgs = util.read_json(f, [])
    for m in msgs if isinstance(msgs, list) else []:
        t = when(m.get("time")) if isinstance(m, dict) else None
        if not t or not since <= t < until:
            continue
        if m.get("decision"):
            kind, text = "decision", f"{short(m['decision'], 90)} → {short(m.get('text'), 90)}"
        else:
            kind, text = "inbox", short(m.get("text"))
            for k in ("move", "approve"):
                if m.get(k):
                    text = f"{k} {short(m[k], 60)}: {text}"
        im = w.issue_re.search(f"{m.get('decision') or ''} {m.get('text') or ''}")
        out.append(
            event(t, kind, text, f"{where(f)}#{m.get('id')}", who=m.get("from") or "?", issue=im.group(1) if im else "")
        )
    return out


def orchestrators(project: Project, handovers: dict[str, str]) -> tuple[dict[str, str], orchestrator.Mark | None]:
    """{session: name} of the product's orchestrators: the marked one and those handover notes name."""
    names = dict(handovers)
    cur = orchestrator.current(project)
    if cur:
        names[cur["session"]] = cur["name"] or names.get(cur["session"]) or "orchestrator"
    return names, cur


def from_orchestrators(
    w: World,
    since: datetime,
    until: datetime,
    names: dict[str, str],
    cur: orchestrator.Mark | None,
) -> list[Event]:
    out: list[Event] = []
    if cur:
        t = when(cur.get("since"))
        if t and since <= t < until:
            path = transcripts.transcript_of(cur["session"])
            out.append(
                event(
                    t,
                    "session",
                    f"marked as the orchestrator (session {cur['session'][:8]})",
                    where(path or w.p.state / "orchestrator.json"),
                    who=names.get(cur["session"]),
                    session=cur["session"],
                )
            )
    return out


# ---------- the history ----------


def collect(project: Project, since: datetime, until: datetime, kinds: Collection[str]) -> tuple[World, list[Event]]:
    """The events of `kinds` within [since, until), in time order (by KINDS within the same minute)."""
    w = World(project)
    handovers: dict[str, str] = {}
    evs = from_notes(w, since, until, handovers)
    names, cur = orchestrators(project, handovers)
    w.orchestrators, w.mark = names, cur
    evs += from_orchestrators(w, since, until, names, cur)
    evs += from_launches(w, since, until)
    evs += from_agents(w, since, until)
    evs += from_progress(w, since, until)
    evs += from_ci(w, since, until)
    evs += from_loop(w, since, until, names)
    evs += from_inbox(w, since, until)
    evs = [e for e in evs if e["t"] and since <= e["t"] < until and e["kind"] in kinds]
    order = {k: i for i, k in enumerate(KINDS)}
    evs.sort(key=lambda e: (e["t"], order.get(e["kind"], 99)))
    return w, evs
