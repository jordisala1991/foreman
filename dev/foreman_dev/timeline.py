"""`foreman-dev timeline`: the product's history (lib/foreman/timeline.py, what `foreman catchup` reads), one event
per line in time order, with the token spend of each agent and of the orchestrator sessions in the window, for
whoever develops foreman to debug with: where a spend came from, where foreman failed, which agent went wrong and with
which instructions.

The spend comes from the transcripts (transcripts.py, totals only): each agent's, and the marked orchestrator
session's within the window. Every line ends with `@ pointer`: where to dig. `--agent NAME` also prints that
agent's brief (the first message of its transcript).
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from foreman import timeline, transcripts, util
from foreman.timeline import Event, World, event, naive, where

if TYPE_CHECKING:
    import argparse
    from collections.abc import Collection

    from foreman.config import Project

KINDS = (
    *timeline.KINDS[: timeline.KINDS.index("stop") + 1],
    "spend",
    *timeline.KINDS[timeline.KINDS.index("stop") + 1 :],
)
DEFAULT_LIMIT = 200
BRIEF_CHARS = 1500


# ---------- short pointers ----------


def tilde(s: str) -> str:
    home = str(Path.home())
    if home and home != "/" and (s == home or s.startswith(home + "/")):
        return "~" + s[len(home) :]
    return s


class Paths:
    """The text output's short pointers: `$state` (the product's state folder), `$notes` (its notes) and `$S1`, `$S2`…
    (a Claude Code session's folder, `<projects>/<dir>/<session id>`: `$S1.jsonl` is its transcript,
    `$S1/subagents/agent-ID.jsonl` its agents'), each said once in the legend."""

    SESSION = re.compile(r"^(.*/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?=[./]|$)")

    def __init__(self, project: Project, refs: list[str | None]) -> None:
        self.names = [("$state", str(project.state)), ("$notes", str(project.notes_dir))]
        seen: list[str] = []
        for r in refs:
            m = self.SESSION.match(r or "")
            if m and m.group(1) not in seen:
                seen.append(m.group(1))
        self.names += [(f"$S{i}", d) for i, d in enumerate(seen, 1)]
        self.used: set[str] = set()

    def short(self, ref: str) -> str:
        for name, d in sorted(self.names, key=lambda x: -len(x[1])):
            if ref == d or ref.startswith(d + "/") or (name.startswith("$S") and ref.startswith(d + ".")):
                self.used.add(name)
                return name + ref[len(d) :]
        return tilde(ref)

    def legend(self) -> list[str]:
        return [f"{name} = {tilde(d)}" for name, d in self.names if name in self.used]


def human(n: float | None) -> str:
    n = int(n or 0)
    for div, unit, digits in ((10**9, "G", 2), (10**6, "M", 1), (10**3, "K", 1)):
        if abs(n) >= div:
            return f"{n / div:.{digits}f}{unit}"
    return str(n)


def totals(cs: list[transcripts.Call]) -> dict[str, Any]:
    tot = {"calls": len(cs), "cache_read": 0, "cache_write": 0, "output": 0, "input": 0, "peak": 0}
    for c in cs:
        for k in ("cache_read", "cache_write", "output", "input"):
            tot[k] += c[k]
        tot["peak"] = max(tot["peak"], transcripts.context(c))
    return tot


def spend_text(tot: dict[str, Any]) -> str:
    return (
        f"{tot['calls']} call{'' if tot['calls'] == 1 else 's'}, cache read {human(tot['cache_read'])}, "
        f"write {human(tot['cache_write'])}, "
        f"output {human(tot['output'])}, peak context {human(tot['peak'])}"
    )


# ---------- the spend ----------


def find(w: World, name: str) -> list[str]:
    """The agent ids that `name` names (its name, its id or an id prefix of 6+ characters)."""
    if name in w.agents:
        return [name]
    got = list(w.by_name.get(name, []))
    if not got and len(name) >= 6:
        got = [aid for aid in w.agents if aid.startswith((name, "agent-" + name))]
    return got


def agent_spend(w: World, since: datetime, until: datetime) -> list[Event]:
    """Each agent's totals, from its transcript, at its last call (the agents timeline.from_agents shows)."""
    out: list[Event] = []
    for aid, h in w.agents.items():
        if not isinstance(h, dict):
            continue
        start, end = timeline.when(h.get("start")), timeline.when(h.get("end"))
        last = end or timeline.when(h.get("activity")) or start
        if (start and start >= until) or (last and last < since and not h.get("delivery_incomplete")):
            continue
        path = w.transcript(aid)
        cs = transcripts.calls(path) if path else []
        if not cs:
            continue
        tot = totals(cs)
        t = naive(cs[-1]["ts"]) if cs[-1]["ts"] else last
        if t:
            models = sorted({c["model"] for c in cs if c["model"]})
            out.append(
                event(
                    t,
                    "spend",
                    spend_text(tot) + (f", {'/'.join(models)}" if models else ""),
                    w.ref(aid, w.p.state / "agents.json"),
                    who=h.get("name") or aid[:8],
                    issue=w.issue_of(h),
                    agent_id=aid,
                    session=w.session(aid),
                    branch=h.get("branch"),
                    **tot,
                )
            )
    return out


def orchestrator_spend(w: World, since: datetime, until: datetime) -> list[Event]:
    """The orchestrator session's spend within the window (timeline.orchestrators: the marked one)."""
    out: list[Event] = []
    tz_since = since.astimezone()
    tz_until = until.astimezone()
    for sid, name in sorted(w.orchestrators.items()):
        path = transcripts.transcript_of(sid)
        if not path:
            continue
        try:
            if os.stat(path).st_mtime < tz_since.timestamp():
                continue
        except OSError:
            continue
        cs = [c for c in transcripts.calls(path, tz_since, tz_until) if not c["sidechain"]]
        if not cs:
            continue
        tot = totals(cs)
        t = naive(cs[-1]["ts"])
        out.append(
            event(
                t,
                "spend",
                "orchestrator, in the window: "
                + spend_text(tot)
                + f", context {human(transcripts.context(cs[0]))} → {human(transcripts.context(cs[-1]))}",
                where(path),
                who=name,
                session=sid,
                **tot,
            )
        )
    return out


def collect(project: Project, since: datetime, until: datetime, kinds: Collection[str]) -> tuple[World, list[Event]]:
    """timeline.collect, plus the `spend` events when asked, in the same order (KINDS within the same minute)."""
    w, evs = timeline.collect(project, since, until, set(kinds) - {"spend"})
    if "spend" in kinds:
        spent = orchestrator_spend(w, since, until) + agent_spend(w, since, until)
        evs += [e for e in spent if e["t"] and since <= e["t"] < until]
        order = {k: i for i, k in enumerate(KINDS)}
        evs.sort(key=lambda e: (e["t"], order.get(e["kind"], 99)))
    return w, evs


# ---------- filters and output ----------


def matches_issue(e: Event, issue: str) -> bool:
    got = e["issue"]
    return bool(got) and (got == issue or got.startswith(issue + " "))


def select(
    w: World, evs: list[Event], agent: str | None = None, issue: str | None = None, session: str | None = None
) -> list[Event]:
    if agent:
        ids = set(find(w, agent))
        branches = {w.info(a).get("branch") for a in ids} - {None, ""}
        names = {w.info(a).get("name") for a in ids} | {agent}
        evs = [
            e
            for e in evs
            if e["agent_id"] in ids
            or (e["who"] in names and e["kind"] != "loop")
            or (e["branch"] and e["branch"] in branches)
        ]
    if issue:
        evs = [e for e in evs if matches_issue(e, issue)]
    if session:
        evs = [e for e in evs if (e["session"] or "").startswith(session)]
    return evs


def collapse(evs: list[Event]) -> list[Event]:
    """Text output: a run of skipped passes with nothing to act on is one line."""
    out: list[Event] = []
    run: list[Event] = []

    def flush() -> None:
        if len(run) > 2:
            first, last = run[0], run[-1]
            ref = first["ref"].rsplit(":", 1)[0] + f":{first['data']['line']}-{last['data']['line']}"
            out.append(dict(first, text=f"{len(run)} skipped passes until {last['t']:%H:%M}", ref=ref))
        else:
            out.extend(run)
        run.clear()

    for e in evs:
        if e["kind"] == "loop" and e["data"].get("skipped") and not e["data"].get("actionable"):
            run.append(e)
            continue
        flush()
        out.append(e)
    flush()
    return out


def line(e: Event, fmt: str, paths: Paths) -> str:
    who = " ".join(x for x in (e["who"], e["issue"]) if x)
    return f"{e['t']:{fmt}} {e['kind']:<9} " + (f"{who}: " if who else "") + f"{e['text']} @ {paths.short(e['ref'])}"


def briefs(w: World, agent: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for aid in find(w, agent)[-3:]:
        path = w.transcript(aid)
        text = transcripts.first_prompt(path) if path else ""
        h = w.info(aid)
        out.append(
            {
                "agent_id": aid,
                "name": h.get("name"),
                "transcript": path,
                "chars": len(text),
                "brief": text[:BRIEF_CHARS],
            }
        )
    return out


def as_json(e: Event) -> dict[str, Any]:
    return {
        "time": e["t"].isoformat(timespec="seconds"),
        "kind": e["kind"],
        "who": e["who"] or None,
        "issue": e["issue"] or None,
        "agent_id": e["agent_id"],
        "session": e["session"],
        "branch": e["branch"],
        "text": e["text"],
        "ref": e["ref"],
        **e["data"],
    }


# ---------- the command ----------


def add_parser(sp: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    a = sp.add_parser(
        "timeline",
        help="what happened, one event per line in time order, each with where to dig "
        "(to debug spend, failures and agents)",
    )
    a.add_argument("--since", help="24h (default), 2h, 30m, HH:MM (today), a date or a date and time")
    a.add_argument("--until", help="the same forms (default: now)")
    a.add_argument("--agent", metavar="NAME", help="one agent (name or id): its events, and its brief first")
    a.add_argument("--issue", metavar="ID", help="one issue (AB-12, or AB-12 T3)")
    a.add_argument("--session", metavar="ID", help="one Claude Code session (id or prefix)")
    a.add_argument(
        "--kind", action="append", metavar="K", help=f"only these kinds (repeat or comma-separate): {', '.join(KINDS)}"
    )
    a.add_argument(
        "--limit", type=int, default=DEFAULT_LIMIT, help=f"the last N lines (default {DEFAULT_LIMIT}; 0: all)"
    )
    a.add_argument("--json", action="store_true", help="the events as JSON (no collapsing)")


def run(args: argparse.Namespace, project: Project) -> int:
    now = datetime.now().astimezone()
    try:
        since = util.parse_when(args.since, now) if args.since else now - timedelta(hours=24)
        until = util.parse_when(args.until, now) if args.until else now
    except ValueError:
        print(
            f"foreman-dev timeline: --since/--until {args.since or args.until!r}: use 24h, 2h, 30m, HH:MM, a date "
            "(YYYY-MM-DD) or a date and time",
            file=sys.stderr,
        )
        return 2
    since, until = naive(since), naive(until)
    kinds = [k.strip() for v in args.kind or [] for k in v.split(",") if k.strip()]
    bad = [k for k in kinds if k not in KINDS]
    if bad:
        print(f"foreman-dev timeline: unknown kind {', '.join(bad)}; kinds: {', '.join(KINDS)}", file=sys.stderr)
        return 2
    if args.limit < 0:
        print("foreman-dev timeline: --limit is 0 (all) or more", file=sys.stderr)
        return 2
    w, evs = collect(project, since, until, set(kinds or KINDS))
    if args.agent and not find(w, args.agent):
        print(
            f"foreman-dev timeline: no agent {args.agent!r} in agents.json (it keeps about a day); its progress lines, "
            "if any, follow",
            file=sys.stderr,
        )
    evs = select(w, evs, args.agent, args.issue, args.session)
    bs = briefs(w, args.agent) if args.agent else []
    if args.json:
        shown = evs[-args.limit :] if args.limit else evs
        print(
            json.dumps(
                {
                    "project": project.name,
                    "since": since.isoformat(timespec="seconds"),
                    "until": until.isoformat(timespec="seconds"),
                    "events": [as_json(e) for e in shown],
                    "omitted": len(evs) - len(shown),
                    "briefs": bs,
                },
                ensure_ascii=False,
                indent=1,
            )
        )
        return 0
    lines = collapse(evs)
    shown = lines[-args.limit :] if args.limit else lines
    one_day = since.date() == (until - timedelta(microseconds=1)).date()
    fmt = "%H:%M" if one_day else "%m-%d %H:%M"
    paths = Paths(project, [e["ref"] for e in shown] + [b["transcript"] for b in bs])
    body = []
    for b in bs:
        cut = f", first {BRIEF_CHARS} of {b['chars']} chars" if b["chars"] > BRIEF_CHARS else ""
        body.append(
            f"brief of {b['name'] or b['agent_id']} ({b['agent_id']}{cut}) @ "
            + (paths.short(b["transcript"]) if b["transcript"] else "no transcript")
        )
        body += ["  | " + ln for ln in (b["brief"] or "(empty)").splitlines()]
    if len(lines) > len(shown):
        body.append(f"… {len(lines) - len(shown)} earlier lines (--limit 0: all)")
    body += [line(e, fmt, paths) for e in shown]
    if not lines:
        body.append("no event in the window")
    print(
        f"foreman-dev timeline: {project.name}, {since:%Y-%m-%d %H:%M} → {until:%Y-%m-%d %H:%M} (local time; "
        f"{len(shown)} lines; @ where to dig)"
    )
    legend = paths.legend()
    if legend:
        print("paths: " + "; ".join(legend))
    print("\n".join(body))
    return 0
