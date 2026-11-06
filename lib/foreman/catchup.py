"""`foreman catchup`: what the owner missed, always in the same four parts, already filtered, for the orchestrator to
put into plain words (the owner's skill runs it once and reads `--json` only for what he then asks about):

- done: issues integrated in the window (one entry per issue with its sub-issues, and its result line when it was
  closed), measurements; the issues closed without an integration in the window, as a count;
- running: the live agents (loop.live_agents through start.agents_now) with their last progress line, production
  first, planners apart (`role`); the issues in progress with no live agent, as a count;
- waiting: the pending decisions (how many days, what they hold), the permission requests ([deny.grants]), branches
  awaiting the owner's approval to integrate and not integrated since, and the notices of now that only the owner
  solves (paused, foreman_behind, sessions_behind);
- wrong: partial or failed deliveries, production agents stopped without delivering or let go with their delivery
  incomplete (never planners, reviewers or agents without an issue), CI runs of `foreman integrate` that failed on
  main or on a branch not integrated afterwards, gaps between loop passes longer than orchestrator.loop_limit_min,
  and the notices of now dead_agents, orchestrator_only_denied and backlog_problems.

The window: `--since` (24h, 2h, 30m, HH:MM, a date or a date and time: util.parse_when), else since the last
`foreman catchup` without `--since` (`<state>/catchup.json`, `{last, session}`), else 24 h; a mark older than 7 days
counts 7 days. Only a run without `--since` reads and moves the mark: an orchestrator looking for itself passes
`--since`.

It only reads, except that mark: the history comes from timeline.collect (no transcript is read) and the notices of
now from loop.notices_for_pass with mark=False (no notice marker is spent).
Headings and the texts foreman composes are in the project's language (i18n `catchup_*`); result and progress
lines go as they were written. `--json` gives every item, each with `ref` (where to dig, a file and line).
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from . import backlog, delivery, grants, i18n, identity, loop, orchestrator, start, timeline, util

if TYPE_CHECKING:
    import argparse

    from .config import Project
    from .notices import Notice

Item = dict[str, Any]  # {time, issue, sub, title, text, ref, kind, …}

MAX_LINES = 8  # lines of a part in the text output, its «and N more» and count lines included
DEFAULT_HOURS = 24
CAP_DAYS = 7
LOOKBACK = timedelta(days=CAP_DAYS)  # how far back what is pending now is looked for (approvals awaiting, loop gaps)
KINDS = timeline.KINDS
WAITING = ("paused", "foreman_behind", "sessions_behind")
WRONG = ("dead_agents", "orchestrator_only_denied", "backlog_problems")
# integrate.py's progress line when a branch waits for the owner: «PR #3 (repo:branch) green, awaiting approval: why»
# or «repo:branch green, awaiting approval to merge: why»
AWAITING = re.compile(r"\(?[\w.-]+:(\S+?)\)? green, awaiting approval(?: to merge)?: (.*)$")
ROLES = ("production", "planner", "other")


def mark_file(project: Project) -> Any:
    return project.state / "catchup.json"


def iso(t: datetime | None) -> str | None:
    return t.isoformat(timespec="seconds") if t else None


def split(ref: str | None) -> tuple[str, str]:
    """«AB-1 T2» → ("AB-1", "T2"); «AB-1» → ("AB-1", "")."""
    parts = (ref or "").split(None, 1)
    return (parts[0] if parts else "", parts[1] if len(parts) > 1 else "")


def same_branch(a: str | None, b: str | None) -> bool:
    """The same branch, also when one is a CI log's name (`ab1-t2-ci.log` for `ab1/t2`: an agent foreman no longer
    keeps in agents.json leaves no way back to the `/`)."""
    return bool(a and b) and str(a).replace("/", "-") == str(b).replace("/", "-")


def item(t: datetime | None, issue: str, sub: str, title: str, text: str, ref: str, **extra: Any) -> Item:
    return {
        "time": iso(t),
        "issue": issue or None,
        "sub": sub or None,
        "title": title or None,
        "text": text,
        "ref": ref,
        **extra,
    }


# ---------- the window ----------


def window(project: Project, since_arg: str | None, now: datetime) -> tuple[datetime, str]:
    """(since, since_from): `arg` (--since), `mark` (the last catchup), `default` (no mark: 24 h) or `capped` (a mark
    older than 7 days: 7 days). ValueError for a --since it cannot read."""
    if since_arg:
        return timeline.naive(util.parse_when(since_arg, now.astimezone())), "arg"
    rec = util.read_json(mark_file(project), {})
    last = timeline.when(rec.get("last")) if isinstance(rec, dict) else None
    if last is None or last > now:
        return now - timedelta(hours=DEFAULT_HOURS), "default"
    if last < now - timedelta(days=CAP_DAYS):
        return now - timedelta(days=CAP_DAYS), "capped"
    return last, "mark"


def write_mark(project: Project, until: datetime) -> None:
    util.write_json(
        mark_file(project), {"last": iso(until), "session": os.environ.get("CLAUDE_CODE_SESSION_ID") or None}
    )


# ---------- what it reads ----------


class Issues:
    """The backlog, read once: an issue by id (the archive too), its short name, a sub-issue's title, the decisions."""

    def __init__(self, project: Project) -> None:
        self.b = backlog.Backlog(project)
        self.data: dict[str, Any] = {}
        try:
            if self.b.exists():
                self.data = self.b.load()
        except (backlog.BacklogError, OSError, ValueError, KeyError):
            self.data = {}
        self.by_id: dict[str, dict[str, Any]] = {}
        if self.data:
            for _, i, _ in self.b.iter_issues(self.data):
                self.by_id[i["id"]] = i

    def get(self, iid: str) -> dict[str, Any]:
        return self.by_id.get(iid) or {}

    def title(self, iid: str) -> str:
        i = self.by_id.get(iid)
        return backlog.short_title(i) if i else ""

    def sub_title(self, iid: str, sid: str) -> str:
        for s in self.get(iid).get("subs") or []:
            if s.get("id") == sid:
                return re.sub(r"\*\*|`", "", str(s.get("title") or ""))
        return ""

    def closed_since(self, i: dict[str, Any], since: datetime) -> bool:
        return i.get("status") in backlog.CLOSED and str(i.get("closed") or "") >= since.date().isoformat()


def progress_lines(project: Project) -> list[tuple[int, dict[str, Any], datetime]]:
    """(line number, record, time) of every readable line of progress.jsonl."""
    out = []
    for n, d in util.read_jsonl(project.state / "progress.jsonl", numbered=True):
        t = timeline.when(d.get("time"))
        if t:
            out.append((n, d, t))
    return out


def production(project: Project, w: timeline.World, aid: str | None, name: str | None) -> bool | None:
    """Whether the agent (agents.json by id, else its launch by name) is a production one; None when unknown. The
    brief is not read (head ""): the identity recorded at launch or the type decide."""
    h = w.info(aid) if aid else {}
    if not h and name:
        ids = w.by_name.get(name) or []
        h = w.info(ids[-1]) if ids else {}
        rec = w.names.get(name)
        h = h or (rec if isinstance(rec, dict) else {})
    if not h:
        return None
    return identity.is_production(project, h, "")


# ---------- the four parts ----------


def done_part(
    project: Project, s: dict[str, Any], bl: Issues, evs: list[timeline.Event], since: datetime
) -> tuple[list[Item], list[str]]:
    """The integrations, one item per issue, and the measurements; and the ids closed without an integration."""
    groups: dict[str, dict[str, Any]] = {}
    out: list[Item] = []
    seen: set[str] = set()
    for e in evs:
        if e["kind"] not in ("integrate", "measure"):
            continue
        iid, sid = split(e["issue"])
        if iid and not sid:  # the note's title names the sub-issue: «Integrated repo:branch (AB-1 T2)»
            m = re.search(rf"\({re.escape(iid)} ([A-Za-z]+\d+)\)", e["text"])
            sid = m.group(1) if m else ""
        seen.add(iid)
        if e["kind"] == "measure":
            what = bl.sub_title(iid, sid) or e["text"]
            out.append(
                item(
                    e["t"],
                    iid,
                    sid,
                    bl.title(iid),
                    s["catchup_measure"].format(text=what),
                    e["ref"],
                    kind="measure",
                    branch=e["branch"],
                )
            )
            continue
        g = groups.setdefault(iid or e["text"], {"iid": iid, "subs": [], "branches": []})
        g["t"], g["ref"], g["text"] = e["t"], e["ref"], e["text"]
        if sid and sid not in g["subs"]:
            g["subs"].append(sid)
        if e["branch"] and e["branch"] not in g["branches"]:
            g["branches"].append(e["branch"])
    for g in groups.values():
        iid = g["iid"]
        i = bl.get(iid)
        closed = bool(i) and i.get("status") == "done" and bl.closed_since(i, since)
        if closed and i.get("result"):
            text = str(i["result"])
        else:
            text = "; ".join(f"{x}: {bl.sub_title(iid, x)}" if bl.sub_title(iid, x) else x for x in g["subs"])
            text = text or g["text"]
        out.append(
            item(
                g["t"],
                iid,
                ", ".join(g["subs"]),
                bl.title(iid),
                text,
                g["ref"],
                kind="integrate",
                subs=g["subs"],
                branches=g["branches"],
                closed=closed,
                status=i.get("status"),
            )
        )
    others = [iid for iid, i in bl.by_id.items() if iid not in seen and bl.closed_since(i, since)]
    out.sort(key=lambda x: x["time"] or "")
    return out, others


def running_part(
    project: Project, w: timeline.World, bl: Issues, progress: list[tuple[int, dict[str, Any], datetime]], now: datetime
) -> tuple[list[Item], list[str]]:
    """The live agents with their last progress line (production, then planners, then the rest); and the issues in
    progress with no live agent."""
    last: dict[str, tuple[int, dict[str, Any], datetime]] = {}
    for n, d, t in progress:
        if d.get("done") is True:
            continue
        for k in (d.get("agent_id"), d.get("agent")):
            if isinstance(k, str) and k:
                last[k] = (n, d, t)
    out: list[Item] = []
    busy: set[str] = set()
    for a in start.agents_now(project):
        h = w.info(a["id"]) or {"type": a["type"]}
        role = "production" if identity.is_production(project, h, "") else identity.role(project, a["type"])
        started = timeline.when(a.get("start"))
        p = last.get(a["id"]) or last.get(a["name"])
        if p and started and p[2] < started:
            p = None
        iid = a.get("issue") or ""
        if iid:
            busy.add(iid)
        ref = timeline.where(project.state / "progress.jsonl", p[0]) if p else timeline.where(loop.agents_file(project))
        out.append(
            item(
                started,
                iid,
                a.get("sub") or "",
                bl.title(iid),
                str(p[1].get("text") or "") if p else "",
                ref,
                kind="agent",
                role=role,
                agent=a["name"],
                minutes=int((now - started).total_seconds() // 60) if started else None,
                progress_time=iso(p[2]) if p else None,
                session_state=a.get("session_state"),
            )
        )
    out.sort(key=lambda x: (ROLES.index(x["role"]) if x["role"] in ROLES else 9, x["time"] or ""))
    idle = [iid for iid, i in bl.by_id.items() if i.get("status") == "in progress" and iid not in busy]
    return out, idle


def waiting_part(
    project: Project,
    s: dict[str, Any],
    bl: Issues,
    progress: list[tuple[int, dict[str, Any], datetime]],
    integrated: list[timeline.Event],
    notices: Sequence[Notice],
    now: datetime,
) -> list[Item]:
    out: list[Item] = []
    for d in bl.data.get("decisions") or []:
        did = str(d.get("id") or "")
        iid, _ = split(did)
        iid = iid if iid in bl.by_id else ""
        day = timeline.when(f"{d.get('date')}T00:00:00") if d.get("date") else None
        holds = [str(x) for x in d.get("blocks") or []]
        text = s["catchup_decision"].format(title=re.sub(r"\*\*|`", "", str(d.get("title") or "")))
        if holds:
            text += "; " + s["catchup_holds"].format(what=", ".join(holds))
        out.append(
            item(
                day,
                iid,
                "",
                bl.title(iid),
                text,
                f"{bl.b.data_path}#{did}",
                kind="decision",
                id=did,
                days=(now.date() - day.date()).days if day else None,
                holds=holds,
            )
        )
    for g, r in grants.pending(project).items():
        out.append(
            item(
                timeline.when(r.get("at")),
                "",
                "",
                g,
                s["catchup_permission"].format(command=timeline.short(r.get("command"), 120)),
                f"{grants.file(project)}#{g}",
                kind="permission",
                group=g,
                expires=r.get("expires"),
            )
        )
    asked: dict[str, tuple[int, dict[str, Any], datetime, str]] = {}
    for n, d, t in progress:
        m = AWAITING.search(str(d.get("text") or "")) if t >= now - LOOKBACK else None
        if m:
            asked[m.group(1)] = (n, d, t, m.group(2))
    for branch, (n, _, t, why) in asked.items():
        if any(same_branch(e["branch"], branch) and e["t"] >= t for e in integrated):
            continue
        out.append(
            item(
                t,
                "",
                "",
                "",
                s["catchup_approval"].format(why=timeline.short(why, 120)),
                timeline.where(project.state / "progress.jsonl", n),
                kind="approval",
                branch=branch,
            )
        )
    out += [item(now, "", "", "", n["text"], "foreman loop", kind=n["kind"]) for n in notices if n["kind"] in WAITING]
    out.sort(key=lambda x: x["time"] or "")
    return out


def wrong_part(
    project: Project,
    s: dict[str, Any],
    w: timeline.World,
    bl: Issues,
    progress: list[tuple[int, dict[str, Any], datetime]],
    evs: list[timeline.Event],
    integrated: list[timeline.Event],
    notices: Sequence[Notice],
    since: datetime,
    until: datetime,
) -> list[Item]:
    out: list[Item] = []
    # deliveries that did not go well
    for n, d, t in progress:
        outcome = d.get("outcome")
        if d.get("done") is not True or outcome not in ("partial", "failed") or not since <= t < until:
            continue
        aid, name = d.get("agent_id"), d.get("agent")
        if production(project, w, aid, name) is False:
            continue
        iid, sid = split((w.issue_of(w.info(aid)) if aid else "") or w.issue_of_name(name or ""))
        out.append(
            item(
                t,
                iid,
                sid,
                bl.title(iid),
                s[f"catchup_outcome_{outcome}"].format(text=timeline.short(d.get("text"))),
                timeline.where(project.state / "progress.jsonl", n),
                kind="delivery",
                outcome=outcome,
                agent=name,
            )
        )
    # production agents stopped without delivering, or let go with their delivery incomplete
    for aid, h in w.agents.items():
        if not isinstance(h, dict) or not identity.is_production(project, h, ""):
            continue
        iid, sid = h.get("issue") or "", h.get("sub") or ""
        if not iid:  # an agent without an issue is not production work
            continue
        ref = timeline.where(loop.agents_file(project))
        inc = h.get("delivery_incomplete")
        if isinstance(inc, dict) and not h.get("stopped"):
            missing = ", ".join(map(str, inc.get("missing") or [])) or "?"
            out.append(
                item(
                    timeline.when(inc.get("at")),
                    iid,
                    sid,
                    bl.title(iid),
                    s["catchup_incomplete"].format(missing=missing),
                    ref,
                    kind="incomplete",
                    agent=h.get("name"),
                )
            )
            continue
        end = timeline.when(h.get("end"))
        if not end or loop.agent_state(h, until.timestamp()) != "stopped":
            continue
        gone = end + timedelta(seconds=loop.WAIT_LIMIT)  # when a stop without delivery stops counting as waiting
        if not (since <= end < until or since <= gone < until):
            continue
        if delivery.delivery(project, aid, h)[0]:
            continue
        out.append(item(end, iid, sid, bl.title(iid), s["catchup_stopped"], ref, kind="stopped", agent=h.get("name")))
    # CI runs that failed: on main, or on a branch not integrated afterwards
    for e in evs:
        if e["kind"] != "ci" or (e["data"].get("rc") == 0) or ("rc" not in e["data"]):
            continue
        if e["data"].get("rc") is None and "could not start" not in e["text"]:
            continue  # no result in the log: running or cut
        on_main = " on main: " in e["text"]
        if not on_main and any(same_branch(x["branch"], e["branch"]) and x["t"] >= e["t"] for x in integrated):
            continue
        cmd = e["text"].rsplit("; ", 1)[-1]
        iid, sid = split(e["issue"])
        out.append(
            item(
                e["t"],
                iid,
                sid,
                bl.title(iid),
                s["catchup_ci_main" if on_main else "catchup_ci_branch"].format(cmd=cmd),
                e["ref"],
                kind="ci",
                place="main" if on_main else "worktree",
                branch=e["branch"],
                rc=e["data"].get("rc"),
            )
        )
    # gaps between loop passes
    if orchestrator.watches_loop(project):
        limit = orchestrator.loop_limit_min(project)
        passes = timeline.from_loop(w, since - LOOKBACK, until, {})
        for a, b in zip(passes, passes[1:]):
            gap = (b["t"] - a["t"]).total_seconds() / 60
            if gap > limit and since <= b["t"] < until:
                out.append(
                    item(
                        a["t"],
                        "",
                        "",
                        "",
                        s["catchup_loop_gap"].format(
                            start=f"{a['t']:%H:%M}", end=f"{b['t']:%H:%M}", minutes=int(gap), limit=int(limit)
                        ),
                        b["ref"],
                        kind="loop_gap",
                        minutes=int(gap),
                        until=iso(b["t"]),
                    )
                )
    out += [item(until, "", "", "", n["text"], "foreman loop", kind=n["kind"]) for n in notices if n["kind"] in WRONG]
    out.sort(key=lambda x: x["time"] or "")
    return out


# ---------- the whole ----------


def collect(project: Project, since: datetime, until: datetime, since_from: str) -> dict[str, Any]:
    """Every field of the catch-up, as `--json` gives it."""
    s = i18n.for_project(project)
    bl = Issues(project)
    w, evs = timeline.collect(project, since, until, set(KINDS))
    _, older = timeline.collect(project, min(since, until - LOOKBACK), until, {"integrate"})
    integrated = [e for e in older if e["kind"] == "integrate" and e["branch"]]
    progress = progress_lines(project)
    notices = loop.notices_for_pass(project, None, mark=False)
    done, closed_other = done_part(project, s, bl, evs, since)
    running, idle = running_part(project, w, bl, progress, until)
    return {
        "project": project.name,
        "since": iso(since),
        "until": iso(until),
        "since_from": since_from,
        "done": done,
        "running": running,
        "waiting": waiting_part(project, s, bl, progress, integrated, notices, until),
        "wrong": wrong_part(project, s, w, bl, progress, evs, integrated, notices, since, until),
        "closed_other": closed_other,
        "in_progress_idle": idle,
    }


def line(project: Project, s: dict[str, Any], x: Item, today: str) -> str:
    t = timeline.when(x["time"])
    if not t:
        when = ""
    elif x.get("kind") == "decision":  # a decision keeps only its date
        when = f"{t:%m-%d}"
    else:
        when = f"{t:%H:%M}" if x["time"].startswith(today) else f"{t:%m-%d %H:%M}"
    ref = " ".join(v for v in (x["issue"], x["sub"]) if v)
    name = f"{x['title']} ({ref})" if x["title"] and ref else x["title"] or ref
    text = x["text"]
    if x.get("kind") == "agent":
        text = text or s["catchup_no_progress"]
        if x["role"] == "planner":
            text = f"{s['catchup_planning']}: {text}"
        if x.get("minutes") is not None:
            text += f" ({s['catchup_minutes'].format(n=x['minutes'])})"
    return "- " + " · ".join(v for v in (when, name, timeline.short(text)) if v)


def part(s: dict[str, Any], lines: list[str], extras: list[str], newest: bool = False) -> list[str]:
    """A part's body: at most MAX_LINES lines, «and N more» and the count lines included; «nothing» when empty.
    `newest`: a cut keeps the latest lines (in time order), else the first ones."""
    if not lines and not extras:
        return [s["catchup_nothing"]]
    if len(lines) + len(extras) <= MAX_LINES:
        return lines + extras
    shown = max(MAX_LINES - 1 - len(extras), 0)
    kept = (lines[len(lines) - shown :] if shown else []) if newest else lines[:shown]
    return kept + [s["catchup_more"].format(n=len(lines) - shown)] + extras


def render(project: Project, d: dict[str, Any]) -> str:
    s = i18n.for_project(project)
    since, until = timeline.when(d["since"]), timeline.when(d["until"])
    assert since and until
    today = until.date().isoformat()
    fmt = "%H:%M" if since.date() == until.date() else "%Y-%m-%d %H:%M"
    out = [
        s["catchup_since"].format(
            project=d["project"],
            since=f"{since:{fmt}}",
            until=f"{until:%H:%M}",
            source=s["catchup_from_" + d["since_from"]],
        )
    ]

    def ls(key: str) -> list[str]:
        return [line(project, s, x, today) for x in d[key]]

    def ids(xs: list[str]) -> str:
        return ", ".join(xs[:6]) + (", …" if len(xs) > 6 else "")

    extras = {
        "done": [s["catchup_closed_other"].format(n=len(d["closed_other"]), ids=ids(d["closed_other"]))]
        if d["closed_other"]
        else [],
        "running": [s["catchup_idle"].format(n=len(d["in_progress_idle"]), ids=ids(d["in_progress_idle"]))]
        if d["in_progress_idle"]
        else [],
    }
    for key in ("done", "running", "waiting", "wrong"):
        out.append(f"## {s['catchup_' + key]}")
        out += part(s, ls(key), extras.get(key, []), newest=key in ("done", "wrong"))
    return "\n".join(out)


# ---------- the command ----------


def add_parser(sp: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    a = sp.add_parser(
        "catchup",
        help="what the owner missed, in four parts: done, running, waiting for them, went wrong",
    )
    a.add_argument(
        "--since",
        help="2h, 30m, HH:MM (today), a date or a date and time; neither reads nor moves the mark "
        "(default: since the last catchup, else 24h, at most 7 days)",
    )
    a.add_argument("--json", action="store_true", help="every item of the four parts, each with where to dig (ref)")


def run(args: argparse.Namespace, project: Project) -> int:
    now = util.now().replace(microsecond=0)
    try:
        since, since_from = window(project, args.since, now)
    except ValueError:
        print(
            f"foreman catchup: --since {args.since!r}: use 24h, 2h, 30m, HH:MM, a date (YYYY-MM-DD) or a date and time",
            file=sys.stderr,
        )
        return 2
    d = collect(project, since, now, since_from)
    print(json.dumps(d, ensure_ascii=False, indent=1) if args.json else render(project, d))
    if not args.since:
        write_mark(project, now)
    return 0
