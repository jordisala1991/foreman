"""`foreman-dev fill SESSION`: what fills one session's context, piece by piece.

The method: between two consecutive calls of the session the context grows by Δ = ctx(i) − ctx(i−1) (THE context of
transcripts.py). Of that, the previous response's own output tokens are exact (the response stays in the memory) and
are split among its blocks (its thinking by the thinking tokens Claude Code records, the rest by characters: text to
the owner, the tool calls by kind); the rest of Δ is split by characters among what entered between the two calls
(tool results by tool and by Bash command, agents' final messages, prompts, attachments, hook texts). Each piece also
weighs what it is re-read: its tokens times the calls after the one it entered (each of those reads it again from the
cache). An image counts as IMAGE_CHARS characters. A Δ that is not positive (a compaction, a cache dropped) is not
split: the table says how many there were. Δ with nothing that entered is «not attributed»; pieces with no text at
all (a ToolSearch result, which loads tool schemas) share their Δ evenly.

What foreman itself put in the context (its hook texts) is grouped by notice class: a class is recognised by the
fixed opening of its text, from foreman's catalogue (i18n: every text up to its first placeholder) and the loop's
notices (LOOP_OPENINGS); a hook text with none of them is another hook's.

Every figure is read through transcripts.entries (the only reader of the format). Tokens are estimates except the
totals (growth, output, thinking), which are Claude Code's own counts.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from foreman import i18n, transcripts

if TYPE_CHECKING:
    import argparse

    from foreman.config import Project

Block = dict[str, Any]  # a block of a response or a tool call: transcripts.entries' blocks
Piece = dict[str, Any]  # what entered the context between two calls: {row, chars, ts, label, text, notices, result}
FillCall = dict[str, Any]  # read(): {ts, usage, blocks, pieces}
Figures = dict[str, Any]  # fill(): the figures of `foreman-dev fill`

IMAGE_CHARS = 6000  # an image is ~1.5K tokens (Claude Code sends screenshots scaled down), ~6,000 chars of text
PASS_MARK = "Loop pass "  # the heading of a pass's summary (loop.loop_pass), in the prompt foreman's hooks module
# submits (or, in older sessions, in a hook text): the start is measured up to the first one
# foreman commands whose text is written; `handover` reads the transcripts of foreman before 0.44, which had it
NOTE_COMMANDS = ("note", "backlog", "decision", "handover", "progress")
READ_COMMANDS = ("sed", "cat", "grep", "head", "tail", "rg")
TOP = 10
TEXT_CHARS = 80

# The loop's notices (loop.py, notices.py) are written in code, not in the catalogue: their fixed
# openings, by kind (tests/test_fill.py checks each one is still in the code). Matched before the catalogue's.
LOOP_OPENINGS = (
    ("loop_pass", "Loop pass"),
    ("wip_production", "WIP (pipeline not full): production"),
    ("wip_ready", "WIP (pipeline not full): unblocked ready"),
    ("inbox", "Inbox: "),
    ("stalled_agents", "Agents idle without a lock: "),
    ("dead_agents", "Agents still marked live that are dead"),
    ("ready_to_integrate", "Ready to integrate (no live agent): "),
    ("measure_delivered", "Measurements delivered (nothing to merge): "),
    ("loose_worktrees", "Worktrees the backlog does not name: "),
    ("branch_no_worktree", "Unmerged branches of open sub-issues with no worktree: "),
    ("loose_branches", "Unmerged branches with no worktree that the backlog does not name: "),
    ("orphan_in_progress", "`in progress` with no live agent or branch: "),
    ("backlog_problems", "foreman check, backlog: "),
    ("backlog_unreadable", "backlog could not be read: "),
    ("hung_shells", "Hung shells (foreman hung-shells --kill): "),
    ("recurring_late", "Recurring tasks overdue (pass skill, «Closing a batch and recurring tasks»): "),
    ("delivery_incomplete", "Stopped with an incomplete delivery"),
    ("lock_long", "Locks: "),
    ("summary_failed", "the summary failed ("),
)
LOOP_HEAD = "loop_pass"  # a pass's own heading: one per pass, never «repeated»

# The rows of the table, in order: (key, label, level). Level 1 rows are parts of the level 0 row above them.
ROWS = (
    ("own", "own output", 0),
    ("out_foreman", "foreman note/backlog/decision/handover/progress", 1),
    ("out_text", "text to the owner and the chat", 1),
    ("out_agent", "Agent briefs", 1),
    ("out_bash", "other Bash commands", 1),
    ("out_thinking", "thinking", 1),
    ("out_send", "SendMessage", 1),
    ("out_other", "other tool calls", 1),
    ("bash", "Bash results", 0),
    ("bash_read", "sed/cat/grep/head/tail", 1),
    ("bash_agents", "foreman agents", 1),
    ("bash_integrate", "foreman integrate", 1),
    ("bash_other", "other commands", 1),
    ("finals", "agents' final messages (task-notification)", 0),
    ("agent_result", "Agent call results", 0),
    ("read", "Read", 0),
    ("tools_other", "other tool results", 0),
    ("attachments", "Claude Code attachments and reminders", 0),
    ("peer_skill", "other sessions' messages and skill text", 0),
    ("prompts", "prompts (owner, loop)", 0),
    ("hooks_foreman", "foreman hook texts", 0),
    ("hooks_other", "other hooks' texts", 0),
    ("unattributed", "not attributed", 0),
)

# The «to spend less» rules: (key, threshold).
FINALS_MEDIAN = 1500
GROWTH_PER_HOUR = 70000
START = 40000
REPEATED = 10


# ---------- reading the pieces ----------


def _words(command: str | None) -> list[str]:
    """The words of the first command of a shell line that is not a `cd` or a variable assignment."""
    for seg in re.split(r"&&|\|\||;|\n|\|", command or ""):
        words = seg.split()
        while words and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]):
            words.pop(0)
        if words and words[0] not in ("cd", "export", "set"):
            return words
    return []


def command_kind(command: str | None) -> tuple[str | None, str | None]:
    """(program, foreman subcommand or None) of a Bash command: `cd X && FOREMAN_WHO=y bin/foreman --project p agents`
    gives ("foreman", "agents")."""
    w = _words(command)
    if not w:
        return None, None
    prog = Path(w[0]).name
    if prog != "foreman":
        return prog, None
    i = 1
    while i < len(w) and w[i].startswith("-"):
        i += 2 if w[i] in ("--project", "-p") else 1
    return prog, (w[i] if i < len(w) else None)


_FOREMAN = re.compile(r"(?:^|[\s;&|(\"'`=])(?:\S*/)?foreman((?:\s+--project\s+\S+|\s+-[-\w]*)*)\s+([a-z][-a-z]*)")


def foreman_subcommands(command: str | None) -> list[str]:
    """The subcommands of every foreman call written in a shell line (in a variable, a function, after a pipe…)."""
    return [m.group(2) for m in _FOREMAN.finditer(command or "")]


def output_kind(block: Block) -> str:
    """The row of one block of the session's own output."""
    t = block["type"]
    if t == "thinking":
        return "out_thinking"
    if t != "tool_use":
        return "out_text"
    name = block.get("name")
    if name == "Agent" or name == "Task":
        return "out_agent"
    if name == "SendMessage":
        return "out_send"
    if name == "Bash":
        subs = foreman_subcommands(block["input"].get("command"))
        return "out_foreman" if any(x in NOTE_COMMANDS for x in subs) else "out_bash"
    return "out_other"


def result_kind(tool: Block | None) -> str:
    """The row of a tool result, from the call that asked for it ({"name", "input"} or None)."""
    tool = tool or {}
    name = tool.get("name")
    if name == "Bash":
        prog, sub = command_kind((tool.get("input") or {}).get("command"))
        if prog in READ_COMMANDS:
            return "bash_read"
        if prog == "foreman" and sub in ("agents", "integrate"):
            return f"bash_{sub}"
        return "bash_other"
    if name in ("Agent", "Task"):
        return "agent_result"
    if name == "Read":
        return "read"
    return "tools_other"


def tool_label(tool: Block | None) -> str:
    tool = tool or {}
    name = tool.get("name") or "?"
    if name == "Bash":
        return "Bash " + " ".join(_words((tool.get("input") or {}).get("command")))[:40]
    if name == "Read":
        return "Read " + Path(str((tool.get("input") or {}).get("file_path") or "?")).name[:35]
    return name


# ---------- foreman's notices ----------


def openings() -> list[tuple[str, str]]:
    """[(class, opening)] of foreman's notices, the longest first: the loop's, then every catalogue text (both
    languages) up to its first placeholder, when that is at least 12 characters."""
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for k, o in LOOP_OPENINGS:
        seen.add(o)
        out.append((k, o))
    for lang in ("en", "es"):
        for k, v in i18n.strings(lang).items():
            if not isinstance(v, str):
                continue
            o = v.split("{", 1)[0].lstrip("> ").rstrip()
            if len(o) >= 12 and o not in seen:
                seen.add(o)
                out.append((k, o))
    return sorted(out, key=lambda x: -len(x[1]))


def notices_in(text: str, ops: list[tuple[str, str]]) -> list[tuple[str, int]]:
    """[(class, characters)] of the foreman notices found in a hook text, in order (each spans to the next one; text
    before the first one goes to the first); [] when none is found."""
    hits: list[tuple[int, int, str]] = []
    for k, o in ops:
        start = 0
        while True:
            i = text.find(o, start)
            if i < 0:
                break
            if not any(a <= i < b for a, b, _ in hits):
                hits.append((i, i + len(o), k))
            start = i + 1
    if not hits:
        return []
    hits.sort()
    out: list[tuple[str, int]] = []
    for n, (i, _, k) in enumerate(hits):
        end = hits[n + 1][0] if n + 1 < len(hits) else len(text)
        out.append((k, end - (0 if n == 0 else i)))
    return out


def final_result(text: str) -> str:
    """The final message inside a task notification (its <result>), or the whole text."""
    m = re.search(r"<result>(.*?)</result>", text, re.S)
    return m.group(1).strip() if m else text


# ---------- the session ----------


def read(path: str, agent: bool = False) -> list[FillCall]:
    """The calls of a transcript with what entered before each: [{"ts", "usage", "blocks", "pieces"}], one per
    response, in order. A piece is {"row", "chars", "ts", "label", "text", "notices", "result"}. The main chain only,
    or (an agent's own transcript) every record."""
    calls: list[FillCall] = []
    at: dict[str, int] = {}
    pending: list[Piece] = []
    tools: dict[str, Block] = {}
    ops = openings()
    for e in transcripts.entries(path):
        if e["sidechain"] != agent:
            continue
        k = e["kind"]
        if k == "call":
            if e["usage"] is None:
                continue
            for b in e["blocks"]:
                if b["type"] == "tool_use" and b.get("tool_id"):
                    tools[b["tool_id"]] = b
            if e["id"] is not None and e["id"] in at:
                c = calls[at[e["id"]]]
                c["blocks"].extend(e["blocks"])
                c["usage"] = e["usage"]
                continue
            if e["id"] is not None:
                at[e["id"]] = len(calls)
            calls.append(
                {"ts": e["ts"] or e["usage"]["ts"], "usage": e["usage"], "blocks": list(e["blocks"]), "pieces": pending}
            )
            pending = []
            continue
        piece = {
            "ts": e["ts"],
            "chars": e.get("chars", 0),
            "label": None,
            "text": None,
            "notices": None,
            "result": False,
        }
        if k == "result":
            tool = tools.get(e["tool_id"])
            piece.update(
                row=result_kind(tool),
                chars=e["chars"] + IMAGE_CHARS * e["images"],
                label=tool_label(tool),
                text=e["text"] or (f"[{e['images']} image(s)]" if e["images"] else "(no text)"),
                result=True,
            )
            pending.append(piece)
        elif k == "prompt":
            row = {
                "task-notification": "finals",
                "peer": "peer_skill",
                "skill": "peer_skill",
                "meta": "attachments",
            }.get(e["origin"], "prompts")
            piece.update(row=row, text=e["text"] if row == "finals" else None)
            piece["pass"] = row == "prompts" and PASS_MARK in (e["text"] or "")
            pending.append(piece)
        elif k == "attachment":
            if e["type"] == "hook_additional_context" or e["hook"]:
                total = sum(len(t) for t in e["texts"]) or 1
                for t in e["texts"]:
                    ns = notices_in(t, ops)
                    pending.append(
                        {
                            **piece,
                            "row": "hooks_foreman" if ns else "hooks_other",
                            "chars": e["chars"] * len(t) / total,
                            "text": t,
                            "notices": ns or [("other: " + " ".join(t.split())[:40], len(t))],
                        }
                    )
            else:
                pending.append({**piece, "row": "prompts" if e["type"] == "queued_command" else "attachments"})
    return calls


def fill(calls: list[FillCall]) -> Figures:
    """The figures of `fill` over the calls read(): {rows, growth, drops, largest, hooks, finals, ctx_first, ctx_last,
    first, last, calls, loop}."""
    rows = {k: {"growth": 0.0, "reread": 0.0} for k, _, _ in ROWS}
    n = len(calls)
    drops = 0
    results: list[tuple[Any, ...]] = []
    hooks: dict[str, dict[str, Any]] = {}
    finals: list[int] = []

    def add(row: str, tokens: float, after: int) -> None:
        rows[row]["growth"] += tokens
        rows[row]["reread"] += tokens * after

    for i in range(1, n):
        prev, cur = calls[i - 1], calls[i]
        delta = transcripts.context(cur["usage"]) - transcripts.context(prev["usage"])
        after = n - 1 - i
        for p in cur["pieces"]:
            if p["row"] == "finals":
                finals.append(len(final_result(p["text"] or "")))
        if delta <= 0:
            drops += 1
            continue
        out = prev["usage"]["output"]
        own = min(out, delta)
        if own > 0:
            think_chars = sum(b["chars"] for b in prev["blocks"] if b["type"] == "thinking")
            think = min(prev["usage"]["thinking"], out) if prev["usage"]["thinking"] else 0
            others = [b for b in prev["blocks"] if b["type"] != "thinking"]
            chars = sum(b["chars"] for b in others)
            if not think and think_chars:  # no thinking count: thinking shares by characters too
                others, chars = prev["blocks"], chars + think_chars
            scale = own / out
            if think:
                add("out_thinking", think * scale, after)
            rest = (out - think) * scale
            if chars:
                for b in others:
                    add(output_kind(b), rest * b["chars"] / chars, after)
            elif rest:
                add("out_thinking" if think_chars or think else "out_text", rest, after)
        left = delta - own
        pieces = cur["pieces"]
        weight = sum(p["chars"] for p in pieces)
        if left > 0 and not pieces:
            add("unattributed", left, after)
        for p in pieces:
            # by characters; pieces with no text at all (a ToolSearch result loads tool schemas) share it evenly
            share = p["chars"] / weight if weight else 1 / len(pieces)
            tokens = left * share if left > 0 else 0.0
            add(p["row"], tokens, after)
            if p["result"]:
                results.append((p["chars"], p["ts"], p["label"], p["text"], tokens))
            if p["notices"]:
                total = sum(c for _, c in p["notices"]) or 1
                for k, c in p["notices"]:
                    h = hooks.setdefault(
                        k, {"count": 0, "chars": 0, "tokens": 0.0, "foreman": not k.startswith("other: ")}
                    )
                    h["count"] += 1
                    h["chars"] += c
                    h["tokens"] += tokens * c / total
    for parent, sub in (("own", "out_"), ("bash", "bash_")):
        for k in rows:
            if k.startswith(sub):
                rows[parent]["growth"] += rows[k]["growth"]
                rows[parent]["reread"] += rows[k]["reread"]
    loop: dict[str, Any] | None = None  # the call before the first loop pass: what the start cost
    for i in range(1, n):
        if any(p.get("pass") or any(k == LOOP_HEAD for k, _ in p["notices"] or ()) for p in calls[i]["pieces"]):
            loop = {"at": calls[i - 1]["ts"], "context": transcripts.context(calls[i - 1]["usage"])}
            break
    results.sort(key=lambda r: -r[0])
    return {
        "rows": rows,
        "drops": drops,
        "largest": results[:TOP],
        "hooks": hooks,
        "finals": sorted(finals),
        "calls": n,
        "ctx_first": transcripts.context(calls[0]["usage"]) if calls else None,
        "ctx_last": transcripts.context(calls[-1]["usage"]) if calls else None,
        "first": calls[0]["ts"] if calls else None,
        "last": calls[-1]["ts"] if calls else None,
        "loop": loop,
    }


def median(xs: list[int]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2


def hours(f: Figures) -> float:
    if not f["first"] or not f["last"]:
        return 0
    return (f["last"] - f["first"]).total_seconds() / 3600


def growth_per_hour(f: Figures) -> int | None:
    h = hours(f)
    return round((f["ctx_last"] - f["ctx_first"]) / h) if h >= 0.25 and f["ctx_last"] is not None else None


def spend_less(f: Figures, agent: bool) -> list[dict[str, Any]]:
    """The «to spend less» lines that hold: [{"rule", "figure", "text"}]."""
    from .stats import count, human

    out: list[dict[str, Any]] = []
    med = median(f["finals"])
    if med is not None and med > FINALS_MEDIAN:
        out.append(
            {
                "rule": "finals_median",
                "figure": med,
                "text": f"agents' final messages: median {count(round(med))} characters over "
                f"{len(f['finals'])} (more than {count(FINALS_MEDIAN)}): ask for the short final "
                "message (docs/agent.md «At the end»); the detail belongs in the report",
            }
        )
    gph = growth_per_hour(f)
    if not agent and gph is not None and gph > GROWTH_PER_HOUR:
        out.append(
            {
                "rule": "growth_per_hour",
                "figure": gph,
                "text": f"the context grows {human(gph)}/h (more than {human(GROWTH_PER_HOUR)}/h): read less "
                "into it (the largest pieces above), or renew it sooner (`foreman renew`)",
            }
        )
    if f["loop"] and f["loop"]["context"] > START:
        out.append(
            {
                "rule": "start",
                "figure": f["loop"]["context"],
                "text": f"the start took {human(f['loop']['context'])} before the first loop pass (more than "
                f"{human(START)}): read less at the start (whole docs, notes, plans) and look up the "
                "rest when needed",
            }
        )
    for k, h in sorted(f["hooks"].items(), key=lambda x: (-x[1]["count"], x[0])):
        if h["foreman"] and k != LOOP_HEAD and h["count"] > REPEATED:
            out.append(
                {
                    "rule": "repeated_notice",
                    "figure": h["count"],
                    "notice": k,
                    "text": f"foreman's notice {k} came {h['count']} times (more than {REPEATED}): each copy is "
                    "re-read until the end; act on it or quiet its cause",
                }
            )
    return out


# ---------- the command ----------


def resolve(arg: str) -> tuple[Any, ...]:
    """(path, id, is_agent) of the transcript SESSION names (a file, a session id or a unique prefix of one, an agent
    id or a prefix of one), or (None, message)."""
    p = Path(arg).expanduser()
    if p.suffix == ".jsonl" and p.is_file():
        return (
            str(p),
            p.stem[len("agent-") :] if p.stem.startswith("agent-") else p.stem,
            transcripts.session_of_subagent(str(p)) is not None,
        )
    found = transcripts.find(arg)
    exact = [f for f in found if f[1] == arg or f[1] == arg[len("agent-") :]]
    found = exact or found
    if not found:
        return None, f"no session or agent transcript matches {arg!r}"
    if len(found) > 1:
        return None, f"{arg!r} matches {len(found)} transcripts: " + ", ".join(f[1] for f in found[:8]) + (
            "…" if len(found) > 8 else ""
        )
    return found[0]


def who(path: str, tid: str, agent: bool, registered: list[Project]) -> str | None:
    """A label for the session (its orchestrator's name, the agent's name), or None."""
    try:
        from .stats import Context

        ctx = Context(registered)
        if not agent:
            return ctx.orchestrators.get(tid)
        sid = transcripts.session_of_subagent(path)
        main = transcripts.transcript_of(sid) if sid else None
        product = ctx.product_of(main, sid) if main else None
        meta = next((a["meta"] for a in transcripts.subagents(main) if a["id"] == tid), {}) if main else {}
        return ctx.agent(product, tid, meta, path)["name"] if product else None
    except Exception:  # noqa: BLE001 (a label only: never worth failing the command)
        return None


def add_parser(sp: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    a = sp.add_parser(
        "fill",
        help="what fills one session's context, piece by piece (a session or agent id, a unique prefix, or a "
        "transcript file)",
    )
    a.add_argument("session", metavar="SESSION", help="a session or agent id, a unique prefix, or a transcript file")
    a.add_argument("--json", action="store_true", help="the same figures as JSON")


def run(args: argparse.Namespace, registered: list[Project]) -> int:
    import json
    import sys

    from .stats import _local, count, human, plural, table

    r = resolve(args.session)
    if r[0] is None:
        print(f"foreman-dev fill: {r[1]}", file=sys.stderr)
        return 2
    path, tid, agent = r
    calls = read(path, agent)
    if not calls:
        print(f"foreman-dev fill: {tid}: no model call in {path}", file=sys.stderr)
        return 2
    f = fill(calls)
    name = who(path, tid, agent, registered)
    growth = f["ctx_last"] - f["ctx_first"]
    attributed = sum(v["growth"] for k, v in f["rows"].items() if k in [x for x, _, lvl in ROWS if lvl == 0])
    reread = sum(v["reread"] for k, v in f["rows"].items() if k in [x for x, _, lvl in ROWS if lvl == 0])
    lines = spend_less(f, agent)
    hooks = sorted(f["hooks"].items(), key=lambda x: (not x[1]["foreman"], -x[1]["count"], -x[1]["chars"], x[0]))
    if args.json:
        out = {
            "session": tid,
            "path": path,
            "kind": "agent" if agent else "session",
            "who": name,
            "from": _local(f["first"]),
            "to": _local(f["last"]),
            "calls": f["calls"],
            "ctx_first": f["ctx_first"],
            "ctx_last": f["ctx_last"],
            "growth": growth,
            "growth_per_hour": growth_per_hour(f),
            "drops": f["drops"],
            "loop": {"at": _local(f["loop"]["at"]), "context": f["loop"]["context"]} if f["loop"] else None,
            "rows": [
                {
                    "key": k,
                    "label": lbl,
                    "level": lvl,
                    "growth": round(f["rows"][k]["growth"]),
                    "reread": round(f["rows"][k]["reread"]),
                }
                for k, lbl, lvl in ROWS
            ],
            "reread": round(reread),
            "largest": [
                {
                    "time": _local(ts, "%H:%M") if ts else None,
                    "tool": lbl,
                    "chars": ch,
                    "tokens": round(tok),
                    "text": " ".join((txt or "").split())[:TEXT_CHARS],
                }
                for ch, ts, lbl, txt, tok in f["largest"]
            ],
            "hooks": [{"class": k, **h, "tokens": round(h["tokens"])} for k, h in hooks],
            "finals": {"count": len(f["finals"]), "median_chars": median(f["finals"])},
            "spend_less": lines,
        }
        print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
        return 0
    kind = "agent" if agent else "session"
    print(
        f"foreman-dev fill: {kind} {tid}"
        + (f" ({name})" if name else "")
        + f", {_local(f['first']) or '?'} → {_local(f['last']) or '?'}, {count(f['calls'])} calls"
    )
    gph = growth_per_hour(f)
    print(
        f"context {human(f['ctx_first'])} → {human(f['ctx_last'])}: growth {human(growth)}"
        + (f" ({human(gph)}/h)" if gph is not None else "")
        + (
            f"; {plural(f['drops'], 'call')} with less context than the one before (a compaction: not split; the "
            f"table splits the {human(round(attributed))} that entered)"
            if f["drops"]
            else ""
        )
    )
    if f["loop"]:
        print(
            f"start: {human(f['ctx_first'])} at the first call; {human(f['loop']['context'])} before the first loop "
            f"pass ({_local(f['loop']['at'], '%H:%M')})"
        )
    elif not agent:
        print(f"start: {human(f['ctx_first'])} at the first call; no loop pass in this session")
    print()

    def pct(v: float, tot: float) -> str:
        return f"{100 * v / tot:.1f}" if tot else "-"

    body: list[list[Any]] = []
    for k, row_label, lvl in ROWS:
        v = f["rows"][k]
        lbl = row_label
        if k == "finals" and f["finals"]:
            lbl += f": {len(f['finals'])}, median {count(round(median(f['finals']) or 0))} chars"
        body.append(
            [
                ("  · " if lvl else "") + lbl,
                human(round(v["growth"])),
                pct(v["growth"], attributed),
                human(round(v["reread"])),
                pct(v["reread"], reread),
            ]
        )
    body.append(
        ["total", human(round(attributed)), pct(attributed, attributed), human(round(reread)), pct(reread, reread)]
    )
    print("\n".join(table(["piece<", "growth", "%", "re-read", "%"], body)))
    print("(growth: tokens that entered the context; re-read: those tokens times the calls after they entered)")
    if f["largest"]:
        print()
        print(f"largest tool outputs ({len(f['largest'])}):")
        print(
            "\n".join(
                table(
                    ["time<", "chars", "~tokens", "tool<", "first characters<"],
                    [
                        [
                            _local(ts, "%H:%M") if ts else "?",
                            count(ch),
                            human(round(tok)),
                            lbl,
                            " ".join((txt or "").split())[:TEXT_CHARS],
                        ]
                        for ch, ts, lbl, txt, tok in f["largest"]
                    ],
                )
            )
        )
    if hooks:
        print()
        print("hook texts by notice (foreman's recognised by their opening):")
        print(
            "\n".join(
                table(
                    ["notice<", "times", "chars", "~tokens"],
                    [[k, h["count"], count(h["chars"]), human(round(h["tokens"]))] for k, h in hooks],
                )
            )
        )
    print()
    if lines:
        print("to spend less:")
        for ln in lines:
            print(f"- {ln['text']}")
    else:
        print("to spend less: nothing over its threshold (final messages, growth per hour, start, repeated notices)")
    return 0
