"""Notes, progress lines, the owner's inbox, heartbeats and recurring tasks of a project.

- Notes (forked from an earlier in-house version): `notes/YYYY-MM-DD.md`, append only, with the real time.
- Progress (`foreman progress`): one JSON line per update in `<state>/progress.jsonl` (keys time, agent, text;
  agent_id when the caller is known by it, done and outcome (ok, partial, failed) for a delivery).
- Inbox: `<state>/inbox.json`, messages between the owner (panel, phone) and the orchestrator
  (keys id, time, from, text, read, and optionally decision / move; an answer from the panel that resolved its
  decision carries did, resolved and freed too, and free when it is the owner's own words, to interpret).
- Heartbeats: `<state>/heartbeats.json`, {name: ISO time}.
- Recurring tasks (`[[recurring]]` in foreman.toml): each has sources, a heartbeat, a
  file glob under the home or an entry of a log page, with the maximum hours between two runs (`max_h`) or a daily
  local time (`at = "HH:MM"`: late once that time has passed with nothing done since); a `models` source is a glob of
  reviews, late when agents run on a model its newest review's `Models:` line does not name.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import i18n, util

if TYPE_CHECKING:
    from .config import Project, Table

Message = dict[
    str, Any
]  # an inbox message: id, time, from, text, read, optionally decision (did, resolved, freed) / move
Beats = dict[str, Any]  # heartbeats.json: {name: ISO time}


def who_am_i(default: str = "orchestrator") -> str:
    """The caller's name from the environment only (identity.who: FOREMAN_LOCK_WHO, FOREMAN_WHO, AGENT), else
    `default`. The CLI also looks up the call the guard wrote down (identity.who with the command line)."""
    from . import identity

    return identity.who(default)[0]


# ---------- notes ----------


def note(project: Project, title: str, body: str) -> Path:
    """Appends `## HH:MM title` + body (or `- HH:MM body` when title is "-") to today's notes. Returns the file."""
    if not body.strip():
        raise ValueError('no text, nothing written; usage: foreman note "Title" "text" (or - "text")')
    d = project.notes_dir
    d.mkdir(parents=True, exist_ok=True)
    at = util.now()
    today, now = at.strftime("%Y-%m-%d"), at.strftime("%H:%M")
    f = d / f"{today}.md"
    with util.file_lock(f, project.state / "locks-notes"):
        if not f.exists():
            f.write_text(i18n.for_project(project)["notes_heading"].format(date=today) + "\n")
        with open(f, "a") as out:
            if title == "-":
                out.write(f"- {now} {body}\n")
            else:
                out.write(f"\n## {now} {title}\n{body}\n")
    return f


def read_stdin_briefly(seconds: float = 2) -> str:
    """Text piped on stdin, waiting at most `seconds` (an agent with an open, empty stdin used to hang)."""
    import select

    if sys.stdin is None or sys.stdin.isatty():
        return ""
    try:
        ready, _, _ = select.select([sys.stdin], [], [], seconds)
    except (OSError, ValueError):
        return ""
    return sys.stdin.read().rstrip("\n") if ready else ""


# ---------- progress ----------


def progress(
    project: Project,
    text: str,
    who: str | None = None,
    done: bool = False,
    agent_id: str | None = None,
    outcome: str | None = None,
    report: str | None = None,
    kind: str | None = None,
    agent_type: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Appends a line to <state>/progress.jsonl; `done` marks the agent's delivery (`foreman progress --done`: the
    agents hook reads it from the command line, not from here); `agent_id` when the caller is known by it (the
    panel can tie the line to its agent even if the name changes); `outcome` (ok, partial, failed) and `report` (the
    report it names, relative to the home, checked by the caller) and `kind` ("measure": it brings no code for main;
    "code" or None writes nothing) go with `done`, and so do `type` (the agent's type, without a plugin prefix) and
    `model` (the model it ran on) when known: progress.jsonl is not pruned, so each report keeps who wrote it."""
    f = project.state / "progress.jsonl"
    f.parent.mkdir(parents=True, exist_ok=True)
    line: dict[str, Any] = {
        "time": util.now().strftime("%Y-%m-%d %H:%M"),
        "agent": who or who_am_i(),
        "text": text[:500],
    }
    if agent_id:
        line["agent_id"] = agent_id
    if done:
        line["done"] = True
        line["outcome"] = outcome or "ok"
        if report:
            line["report"] = report
        if kind == "measure":
            line["kind"] = kind
        if agent_type:
            line["type"] = agent_type.split(":", 1)[-1]
        if model:
            line["model"] = model
    with util.file_lock(f), open(f, "a") as out:
        out.write(json.dumps(line, ensure_ascii=False) + "\n")
    return line


# ---------- inbox ----------


def inbox_path(project: Project) -> Path:
    return project.state / "inbox.json"


def inbox(project: Project) -> list[Message]:
    return util.read_json(inbox_path(project), [])


def inbox_new(project: Project) -> list[Message]:
    return [m for m in inbox(project) if m.get("from") == "owner" and not m.get("read")]


def inbox_add(project: Project, text: str, sender: str = "owner", **extra: Any) -> Message:
    f = inbox_path(project)
    with util.file_lock(f):
        msgs = util.read_json(f, [])
        m = {
            "id": max((x["id"] for x in msgs), default=0) + 1,
            "time": util.now().strftime("%Y-%m-%d %H:%M"),
            "from": sender,
            "text": text,
            "read": False,  # the orchestrator's reply waits for the owner (panel: «Mark as read»)
            **extra,
        }
        msgs.append(m)
        util.write_json(f, msgs[-200:])
    return m


def inbox_read(project: Project, ids: Sequence[str] = (), sender: str = "owner") -> None:
    """Marks as read the messages `from` `sender` (all of them, or those whose id is in `ids`): the owner's, for the
    orchestrator that reads its inbox; the orchestrator's, for the owner who read them in the panel."""
    f = inbox_path(project)
    with util.file_lock(f):
        msgs = util.read_json(f, [])
        for m in msgs:
            if m.get("from") == sender and (not ids or str(m["id"]) in ids):
                m["read"] = True
        util.write_json(f, msgs)


# the panel's mark on a triage issue, or its discard (canceled)
WANT_KINDS = {"on": "owner wants it", "off": "owner no longer wants it", "reject": "owner discarded it"}


def inbox_kind(m: Message) -> str:
    free = "; free text: interpret it" if m.get("free") else ""
    if m.get("decision") and m.get("resolved"):
        freed = [f.removeprefix("freed: ") for f in m.get("freed") or [] if f.startswith("freed: ")]
        if "changed_from" in m:  # the owner changed his answer before it was read: the new one wins
            free += f"; answer changed, it was «{m['changed_from']}»: undo what the old one started, if anything"
        return f"decision «{m['decision']}» (resolved from the panel{free}" + (
            f"; freed: {', '.join(freed)})" if freed else ")"
        )
    if m.get("decision"):
        return f"decision «{m['decision']}»" + (" (free text: interpret it)" if free else "")
    if m.get("want") in WANT_KINDS:
        return WANT_KINDS[m["want"]]
    return "backlog move" if m.get("move") else "message"


def inbox_line(m: Message) -> str:
    return f"{m['id']}\t{m['time']}\t{inbox_kind(m)}: {m['text']}"


def inbox_counts(msgs: Sequence[Message]) -> str:
    """« (1 decision answered (already resolved): apply it before launching what it freed; 1 in free text: read
    and interpret it; 1 wanted from triage, 2 backlog moves)» for the inbox notice: the panel's answers (those in the
    owner's own words apart, as they need interpreting), marks and moves among `msgs` ("" when there are none)."""
    answered = sum(1 for m in msgs if m.get("decision") and m.get("resolved"))
    free = sum(1 for m in msgs if m.get("decision") and m.get("free"))
    first = (
        f"{answered} decision{'s' * (answered != 1)} answered (already resolved): apply "
        f"{'them' if answered != 1 else 'it'} before launching what {'they' if answered != 1 else 'it'} freed"
        if answered
        else ""
    )
    if free:
        first = "; ".join(
            x for x in (first, f"{free} in free text: read and interpret {'them' if free != 1 else 'it'}") if x
        )
    counts = (
        (sum(1 for m in msgs if m.get("want") == "on"), "wanted from triage"),
        (sum(1 for m in msgs if m.get("want") == "off"), "no longer wanted"),
        (sum(1 for m in msgs if m.get("want") == "reject"), "discarded from triage"),
        (sum(1 for m in msgs if m.get("move") and not m.get("want")), "backlog moves"),
    )
    parts = [f"{n} {what}" for n, what in counts if n]
    text = "; ".join(x for x in (first, ", ".join(parts)) if x)
    return f" ({text})" if text else ""


# ---------- heartbeats and recurring tasks ----------


def heartbeats(project: Project) -> Beats:
    return util.read_json(project.state / "heartbeats.json", {})


def beat(project: Project, name: str) -> str:
    f = project.state / "heartbeats.json"
    with util.file_lock(f):
        d = util.read_json(f, {})
        d[name] = util.now().isoformat(timespec="seconds")
        util.write_json(f, d)
    return d[name]


def last_time(project: Project, source: Table, beats: Beats) -> datetime | None:
    if "heartbeat" in source:
        v = beats.get(source["heartbeat"])
        return util.fromiso(v) if v else None
    if "glob" in source or "models" in source:
        files = list(project.home.glob(source.get("glob") or source["models"]))
        return datetime.fromtimestamp(max(f.stat().st_mtime for f in files)) if files else None
    if "log" in source:  # the last «## [YYYY-MM-DD] … <word>» entry of a log page (at noon: it has no time)
        try:
            text = (project.home / source.get("file", "log.md")).read_text(errors="replace")
        except OSError:
            return None
        dates = re.findall(rf"^## \[(\d{{4}}-\d\d-\d\d)\] [^\n]*{re.escape(source['log'])}", text, re.M | re.I)
        return util.fromiso(max(dates) + "T12:00:00") if dates else None
    return None


def models_seen(project: Project, after: datetime | None = None) -> dict[str, datetime]:
    """{model: the last time an agent ran on it} for the agents seen after `after` (all when None): each agents.json
    entry's `model` (the one its transcript said, written at its stop) at its end (else its last activity, else its
    start), and each delivery of progress.jsonl that names one (`foreman progress --done` records it; that file is
    never pruned, agents.json is)."""
    seen: dict[str, datetime] = {}

    def add(model: Any, when: Any) -> None:
        t = util.when(when)
        if isinstance(model, str) and model and t and (after is None or t > after):
            seen[model] = max(t, seen.get(model, t))

    agents = util.read_json(project.state / "agents.json", {})
    for h in agents.values() if isinstance(agents, dict) else ():
        if isinstance(h, dict):
            add(h.get("model"), h.get("end") or h.get("activity") or h.get("start"))
    for x in util.read_jsonl(project.state / "progress.jsonl"):
        if x.get("done"):
            add(x.get("model"), x.get("time"))
    return seen


def models_reviewed(path: Path) -> set[str]:
    """The model ids of the `Models:` lines of a review (`Models: claude-a, claude-b (a note)`): the first word of
    each comma-separated item, without backticks."""
    from .delivery import report_lines

    try:
        text = path.read_text(errors="replace")
    except OSError:
        return set()
    items = (i for line in report_lines(text, ("Models",))["Models"] for i in line.split(","))
    return {m.group(1) for m in (re.match(r"[\s`*]*([^\s`*,()]+)", i) for i in items) if m}


def new_models(project: Project, source: Table, last: datetime | None) -> list[str]:
    """The models a `models` source has not seen reviewed: those agents ran on after its newest review (`last`, its
    file's time) that are not in that review's `Models:` line; every model seen when there is no review."""
    if last is None:
        return sorted(models_seen(project))
    files = list(project.home.glob(source["models"]))
    newest = max(files, key=lambda f: f.stat().st_mtime)
    known = models_reviewed(newest)
    return sorted(m for m in models_seen(project, last) if m not in known)


def last_at(at: str, when: datetime) -> datetime:
    """The last time the daily HH:MM `at` came round at or before `when` (today's if it has passed, else
    yesterday's), in local time."""
    h, m = (int(x) for x in at.split(":"))
    t = when.replace(hour=h, minute=m, second=0, microsecond=0)
    return t if t <= when else t - timedelta(days=1)


def recurring(project: Project, beats: Beats | None = None, when: datetime | None = None) -> list[dict[str, Any]]:
    """[{name, every, late, parts: [{label, last, hours, late}]}] from [[recurring]] of foreman.toml. `beats` replaces
    the project's heartbeats file; `when` replaces the clock.

    A source with `max_h` (default 24) is late when its last run is more than that many hours old. A source with
    `at = "HH:MM"` is late when its last run is before the last time HH:MM came round (today's once it has passed,
    yesterday's until then): a daily task due at a fixed local time, done when its heartbeat, glob or log has
    something after that time. A log entry has only a date: with `at` it counts as done for its whole day. A
    `models` source (a glob of reviews) is late when an agent ran on a model its newest review's `Models:` line does
    not name (new_models; the part's `new` lists them), and after `max_h` hours only when `max_h` is given. Never
    run is late in every case."""
    beats = heartbeats(project) if beats is None else beats
    when = util.now() if when is None else when
    out: list[dict[str, Any]] = []
    for t in project.recurring:
        parts: list[dict[str, Any]] = []
        for src in t["sources"]:
            last = last_time(project, src, beats)
            hours = (when - last).total_seconds() / 3600 if last else None
            new = new_models(project, src, last) if "models" in src else []
            if "models" in src:  # late on a model not reviewed, and after max_h hours only when max_h is given
                late = hours is None or bool(new) or hours > src.get("max_h", float("inf"))
            elif "at" in src:
                done = last.replace(hour=23, minute=59, second=59) if last and "log" in src else last
                late = done is None or done < last_at(src["at"], when)
            else:
                late = hours is None or hours > src.get("max_h", 24)
            parts.append(
                {
                    "label": src.get("label"),
                    "last": last.strftime("%Y-%m-%d %H:%M") if last else None,
                    "hours": None if hours is None else max(0, round(hours, 1)),
                    "late": late,
                    **({"at": src["at"]} if "at" in src else {}),
                    **({"new": new} if "models" in src else {}),
                }
            )
        out.append(
            {"name": t["name"], "every": t.get("every", ""), "parts": parts, "late": any(p["late"] for p in parts)}
        )
    return out
