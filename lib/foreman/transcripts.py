"""The one reader of Claude Code's session transcripts (`~/.claude/projects/<dir>/<session>.jsonl`, and a session's
subagents in `<session>/subagents/agent-<id>.jsonl` with a `.meta.json` beside each).

Everything foreman knows about that format lives here; the rest of foreman calls these functions with foreman's own
data. The format is internal to Claude Code and not documented, so the reader is tolerant: a line it does not
understand (broken JSON, an unknown record type, a field of another type) is skipped, and no function raises to its
caller (a missing or unreadable file gives None, [] or {}).

What is assumed of the format (the tests pin each):
- A model response with several content blocks is written as SEVERAL `assistant` lines with the same `message.id`
  and the same `usage` repeated: a response counts once (`calls` keeps one entry per id).
- `assistant` records carry `message.model`, `message.usage` and `timestamp` (UTC, `...Z`); Claude Code's own
  placeholder responses have the model `<synthetic>` and zero usage and are never a call.
- `isSidechain` is true on a subagent's records (always, in its own file); the main chain of a session has none.
- `cost-state` records (written when a session is closed or resumed) accumulate the whole session, its subagents
  included, per model.

THE definition of "context tokens" (the memory a session carries into its next step): `input_tokens` +
`cache_read_input_tokens` + `cache_creation_input_tokens` of its last response; the response's own `output_tokens` are
not in it (they join the context only on the next call, as that call's input or cache). The panel's agent cards and
`stats` show this figure. The orchestrator's own context (`context_rotate` notice, `foreman session`) comes from
Claude Code's measure instead (hooks_mod.read_measure), which defines `tokens` the same way: uncached + cache-written
+ cache-read input of the last response.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

# Claude Code version this reader was written against (the `version` of its records); another one still reads, but a
# caller that cares can say the format was not tested with it.
if TYPE_CHECKING:
    from .util import PathLike

Record = dict[str, Any]  # one parsed line of a transcript (Claude Code's own format: its keys are not ours)
Call = dict[
    str, Any
]  # parse_call(): {id, ts, model, input, cache_read, cache_write, cache_write_1h, output, thinking, sidechain}
Entry = dict[str, Any]  # entries(): one simplified piece, see its docstring

KNOWN_VERSIONS = ("2.1.311",)

CONTEXT_FIRST_WINDOW = 256 * 1024
CONTEXT_MAX_WINDOW = 8 * 1024 * 1024  # above the largest gap measured between two assistant turns (2.7 MB)

SYNTHETIC = "<synthetic>"


def claude_projects_dir() -> Path:
    return Path(os.environ.get("FOREMAN_CLAUDE_PROJECTS") or Path.home() / ".claude/projects").expanduser()


def transcript_of(session_id: str | None) -> str | None:
    """The path of a session's transcript (the first <Claude projects>/*/<id>.jsonl that exists), or None."""
    sid = (session_id or "").strip()
    if not sid or any(c in sid for c in "/\\*?[]") or sid.startswith("."):
        return None
    try:
        for d in sorted(claude_projects_dir().iterdir()):
            f = d / f"{sid}.jsonl"
            if f.is_file():
                return str(f)
    except OSError:
        pass
    return None


def _int(v: object) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


_TS = re.compile(r"^(\d{4}-\d\d-\d\d)[T ](\d\d:\d\d:\d\d)(?:\.(\d+))?(Z|[+-]\d\d:?\d\d)?$")


def parse_ts(s: object) -> datetime | None:
    """An ISO timestamp (`Z` or an offset, any number of fraction digits; none means UTC) as an aware datetime, or
    None. Python 3.9's fromisoformat takes neither `Z` nor a fraction that is not 3 or 6 digits."""
    m = _TS.match(s.strip()) if isinstance(s, str) else None
    if not m:
        return None
    day, clock, frac, zone = m.groups()
    try:
        t = datetime.fromisoformat(f"{day}T{clock}.{(frac or '0')[:6].ljust(6, '0')}")
        if zone and zone != "Z":
            sign = -1 if zone[0] == "-" else 1
            z = zone[1:].replace(":", "")
            tz = timezone(sign * timedelta(hours=int(z[:2]), minutes=int(z[2:])))
        else:
            tz = timezone.utc
        return t.replace(tzinfo=tz)
    except ValueError:
        return None


def _record(line: bytes | str) -> Record | None:
    """The JSON object of a transcript line (bytes or str), or None."""
    try:
        d = json.loads(line)
    except (ValueError, TypeError):
        return None
    return d if isinstance(d, dict) else None


def parse_call(line: bytes | str) -> Call | None:
    """One model response from a transcript line, or None when the line is not an assistant record with usage (or is
    a `<synthetic>` one): {"id", "ts", "model", "input", "cache_read", "cache_write", "cache_write_1h", "output",
    "thinking", "sidechain"}. `ts` is an aware datetime or None; every count is an int (0 when absent)."""
    if isinstance(line, bytes):
        if b'"usage"' not in line:
            return None
    elif not isinstance(line, str) or '"usage"' not in line:
        return None
    return _call_of(_record(line))


def _call_of(d: Record | None) -> Call | None:
    """parse_call on an already parsed record (None for anything that is not a call)."""
    if d is None or d.get("type") != "assistant":
        return None
    m = d.get("message")
    if not isinstance(m, dict) or not isinstance(m.get("usage"), dict) or m.get("model") == SYNTHETIC:
        return None
    u = m["usage"]
    cc = u.get("cache_creation") if isinstance(u.get("cache_creation"), dict) else {}
    det = u.get("output_tokens_details") if isinstance(u.get("output_tokens_details"), dict) else {}
    model = m.get("model")
    return {
        "id": m.get("id") if isinstance(m.get("id"), str) and m.get("id") else None,
        "ts": parse_ts(d.get("timestamp")),
        "model": model if isinstance(model, str) and model else None,
        "input": _int(u.get("input_tokens")) or 0,
        "cache_read": _int(u.get("cache_read_input_tokens")) or 0,
        "cache_write": _int(u.get("cache_creation_input_tokens")) or 0,
        "cache_write_1h": _int(cc.get("ephemeral_1h_input_tokens")) or 0,
        "output": _int(u.get("output_tokens")) or 0,
        "thinking": _int(det.get("thinking_tokens")) or 0,
        "sidechain": d.get("isSidechain") is True,
    }


def _local(t: datetime | None) -> datetime | None:
    return t.astimezone() if t is not None and t.tzinfo is None else t


def calls(path: PathLike, since: datetime | None = None, until: datetime | None = None) -> list[Call]:
    """The model responses of a transcript, in file order, ONE per `message.id` (a response written on several lines
    counts once: its place is that of its first line, its usage that of its last); a response without an id counts as
    it is. `since` and `until` (datetimes, naive ones local) keep those with `since <= ts < until`; a response
    without a timestamp is kept only when no bound is given. [] for a missing or unreadable file. Never raises."""
    since, until = _local(since), _local(until)
    out: list[Call] = []
    at: dict[str, int] = {}
    try:
        with open(path, "rb") as f:
            for line in f:
                c = parse_call(line)
                if c is None:
                    continue
                if c["id"] is not None and c["id"] in at:
                    out[at[c["id"]]].update({k: v for k, v in c.items() if k != "ts"})
                    continue
                if c["id"] is not None:
                    at[c["id"]] = len(out)
                out.append(c)
    except (OSError, TypeError, ValueError):
        return []
    if since is None and until is None:
        return out
    return [
        c
        for c in out
        if c["ts"] is not None and (since is None or c["ts"] >= since) and (until is None or c["ts"] < until)
    ]


TOKEN_KEYS = ("input", "cache_write", "cache_write_1h", "cache_read", "output")


def usage(
    path: PathLike, since: datetime | None = None, until: datetime | None = None
) -> tuple[dict[str, dict[str, int]], datetime | None, datetime | None]:
    """What a transcript spent, from `calls` (one per response id): ({model: {"calls", "input", "cache_write",
    "cache_write_1h", "cache_read", "output"}}, first time, last time). `cache_write` includes `cache_write_1h`.
    Responses with no model count under "unknown". ({}, None, None) for a missing file. Never raises."""
    out: dict[str, dict[str, int]] = {}
    first = last = None
    for c in calls(path, since, until):
        m = out.setdefault(c["model"] or "unknown", dict.fromkeys(("calls", *TOKEN_KEYS), 0))
        m["calls"] += 1
        for k in TOKEN_KEYS:
            m[k] += c[k]
        if c["ts"] is not None:
            first = c["ts"] if first is None or c["ts"] < first else first
            last = c["ts"] if last is None or c["ts"] > last else last
    return out, first, last


def context(call: Call) -> int:
    """The context tokens of one call (see the module's docstring): input + cache read + cache write."""
    return call["input"] + call["cache_read"] + call["cache_write"]


def tail_lines(path: PathLike, first: int = CONTEXT_FIRST_WINDOW, cap: int = CONTEXT_MAX_WINDOW) -> Iterator[bytes]:
    """The lines of a file from the end back, each whole line once, as bytes: it reads the end in growing windows
    (`first` doubling up to `cap`), so a reader that stops early reads only the tail. Raises what open() raises."""
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = end = f.tell()  # [end, size) is already scanned, as whole lines
        window = first
        while True:
            start = max(0, size - window)
            f.seek(start)
            lines = f.read(end - start).split(b"\n")
            head = lines.pop(0) if start > 0 else b""  # maybe a partial line: the next window reads it whole
            yield from reversed(lines)
            if start == 0 or window >= cap:
                return
            end = start + len(head)
            window *= 2


def last_context(path: PathLike, sidechain: bool = False) -> int | None:
    """The context tokens of the last response in a transcript, or None (no file, no such response). The main chain
    only, unless `sidechain` (a subagent's own file, where every record is a sidechain one). It reads only the tail
    (tail_lines: 256 KB doubling up to 8 MB). Never raises."""
    try:
        for ln in tail_lines(path):
            c = parse_call(ln)
            if c is not None and (sidechain or not c["sidechain"]):
                return context(c)
    except (OSError, TypeError, ValueError):
        pass
    return None


def model(path: PathLike) -> str | None:
    """The model a transcript's responses ran on (the last real one: `<...>` placeholders do not count), or None
    when the file is missing or has none (nothing is guessed). Never raises."""
    found = None
    try:
        with open(path, "rb") as f:
            for line in f:
                if b'"model"' not in line:
                    continue
                d = _record(line)
                m = d.get("message") if d else None
                v = m.get("model") if isinstance(m, dict) else None
                if isinstance(v, str) and v and not v.startswith("<"):
                    found = v
    except (OSError, TypeError, ValueError):
        return None
    return found


def head(path: PathLike, size: int = 20000) -> str:
    """The first `size` characters of a transcript ('' when it cannot be read): where a subagent's brief is."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(size)
    except (OSError, TypeError, ValueError):
        return ""


def is_prompt(message: Record) -> bool:
    """Whether a main-chain `user` record's message starts a turn (a prompt, a scheduled one, a task notification),
    not a tool result."""
    c = message.get("content")
    if isinstance(c, str):
        return True
    return isinstance(c, list) and not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c)


def text_of(content: object, tags: bool = True) -> str:
    """The text of a message's content: a string as it is, or its text blocks joined by lines. `tags=False` leaves
    out the blocks that start with «<» (system reminders, command output)."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(
        b["text"]
        for b in content
        if isinstance(b, dict)
        and b.get("type") == "text"
        and isinstance(b.get("text"), str)
        and (tags or not b["text"].lstrip().startswith("<"))
    )


def _chain_tail(path: PathLike, sidechain: bool) -> Iterator[tuple[Record, Record]]:
    """(record, message) of the `user` and `assistant` records in the transcript's tail, newest first; those of a
    sidechain only with `sidechain` (a subagent's own file, where every record is one). Raises OSError."""
    for ln in tail_lines(path):
        if b'"assistant"' not in ln and b'"user"' not in ln:
            continue
        d = _record(ln)
        if d is not None and (sidechain or d.get("isSidechain") is not True) and isinstance(d.get("message"), dict):
            yield d, d["message"]


def last_message(path: PathLike) -> dict[str, str] | None:
    """A subagent's last message (its own file): {"uuid", "text"} with the text of its assistant records since the
    last prompt (a prompt written after them, like the one being submitted, is passed over), `uuid` the newest of
    those records; None with no transcript or no assistant record in its tail. Reads only the tail. Never raises."""
    texts: list[str] = []
    uuid: str | None = None
    try:
        for d, m in _chain_tail(path, sidechain=True):
            if d.get("type") == "assistant" and m.get("model") != SYNTHETIC:
                if uuid is None:
                    uuid = str(d.get("uuid") or m.get("id") or "")
                t = text_of(m.get("content"))
                if t.strip():
                    texts.append(t)
            elif d.get("type") == "user" and uuid is not None and is_prompt(m):
                break
    except (OSError, TypeError, ValueError):
        pass
    if uuid is None:
        return None
    return {"uuid": uuid, "text": "\n\n".join(reversed(texts)).strip()}


def last_text(path: PathLike) -> str | None:
    """A subagent's last text block (its own file): the newest non-empty one of its assistant records, stripped;
    None with none in the tail. Reads only the tail. Never raises."""
    try:
        for _, m in _chain_tail(path, sidechain=True):
            c = m.get("content")
            if m.get("role") != "assistant" or not isinstance(c, list):
                continue
            for b in reversed(c):
                t = b.get("text") if isinstance(b, dict) and b.get("type") == "text" else None
                if isinstance(t, str) and t.strip():
                    return t.strip()
    except (OSError, TypeError, ValueError):
        pass
    return None


SKIP = ("<", "Caveat:", "[Request interrupted")  # prompts no person typed (notifications, command output)


def owner_messages(path: PathLike | None, prompt: str, n: int) -> tuple[list[dict[str, str]], str]:
    """([{time, text}] the last `n` messages a person typed in the transcript's main chain, oldest first; the
    session's answer to the newest of them). No loop pass (`prompt`), notification, other agent's message, command
    output, compaction summary or meta record. Reads only the tail. Never raises."""
    found: list[dict[str, str]] = []
    answer = ""
    texts: list[str] = []
    if not path:
        return [], ""
    try:
        for d, m in _chain_tail(path, sidechain=False):
            if d.get("type") == "assistant":
                t = text_of(m.get("content"), tags=False)
                if t.strip():
                    texts.append(t)
                continue
            if d.get("type") != "user" or not is_prompt(m) or d.get("isMeta") or d.get("isCompactSummary"):
                continue
            t = text_of(m.get("content"), tags=False).strip()
            if t and not t.startswith(SKIP) and not (prompt and t.startswith(prompt)):
                if not found:
                    answer = "\n\n".join(reversed(texts)).strip()
                ts = _local(parse_ts(d.get("timestamp")))
                found.append({"time": ts.strftime("%m-%d %H:%M") if ts else "", "text": t})
                if len(found) >= n:
                    break
            texts = []
    except (OSError, TypeError, ValueError):
        pass
    return list(reversed(found)), answer


def final_chars(path: PathLike) -> int | None:
    """The length in characters of a subagent's final message (the text of its last turn), or None with no
    transcript or no message. Never raises."""
    m = last_message(path)
    return len(m["text"]) if m else None


_TIMESTAMP = re.compile(rb'"timestamp":\s*"([^"]+)"')


def cost_state_at(path: PathLike) -> tuple[Record | None, datetime | None]:
    """(the last `cost-state` record, the time it was written) or (None, None). The record carries no time of its
    own: its time is that of the last timestamped record before it (an aware datetime, or None). Never raises."""
    found, at, last = None, None, None
    try:
        with open(path, "rb") as f:
            for line in f:
                if b'"cost-state"' in line:
                    d = _record(line)
                    if d and d.get("type") == "cost-state":
                        found, at = d, last
                        continue
                m = _TIMESTAMP.search(line)
                if m:
                    last = m.group(1)
    except (OSError, TypeError, ValueError):
        return None, None
    return found, (parse_ts(at.decode("ascii", "replace")) if found is not None and at else None)


def subagents(session_path: PathLike) -> list[dict[str, Any]]:
    """The subagents of a session: [{"path", "id", "meta"}] sorted by id, one per `<session>/subagents/agent-*.jsonl`,
    `meta` the dict of its `.meta.json` (`agentType`, `description`, `requestShape`…; {} when missing or broken).
    [] when the session has none. Never raises."""
    out: list[dict[str, Any]] = []
    try:
        folder = Path(session_path).with_suffix("") / "subagents"
        for f in sorted(folder.glob("agent-*.jsonl")):
            try:
                meta = json.loads((folder / (f.stem + ".meta.json")).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                meta = {}
            out.append({"path": str(f), "id": f.stem[len("agent-") :], "meta": meta if isinstance(meta, dict) else {}})
    except (OSError, TypeError, ValueError):
        return []
    return out


def session_info(path: PathLike, max_lines: int = 400) -> dict[str, str | None]:
    """What the head of a transcript says about where and how it ran: {"cwd", "version"} (the working folder and the
    Claude Code version of the first records that carry them; None when absent in the first `max_lines` lines). A
    session is filed under the folder it was opened in: its `cwd` is that folder until a `cd`. Never raises."""
    out: dict[str, str | None] = {"cwd": None, "version": None}
    try:
        with open(path, "rb") as f:
            for n, line in enumerate(f):
                if n >= max_lines or (out["cwd"] and out["version"]):
                    break
                if b'"cwd"' not in line and b'"version"' not in line:
                    continue
                d = _record(line)
                if d is None:
                    continue
                for k in ("cwd", "version"):
                    if out[k] is None and isinstance(d.get(k), str) and d[k]:
                        out[k] = d[k]
    except (OSError, TypeError, ValueError):
        pass
    return out


def sessions(newer_than: float | None = None) -> list[tuple[str, str]]:
    """The main transcripts of every session Claude Code keeps: [(path, session id)], `<projects>/*/<id>.jsonl`,
    sorted by path; with `newer_than` (an epoch), only the files written since then (a file not written since a time
    holds no response after it). [] when the folder cannot be read. Never raises."""
    out: list[tuple[str, str]] = []
    try:
        for d in sorted(claude_projects_dir().iterdir()):
            if not d.is_dir():
                continue
            for f in sorted(d.glob("*.jsonl")):
                try:
                    if newer_than is not None and f.stat().st_mtime < newer_than:
                        continue
                except OSError:
                    continue
                out.append((str(f), f.stem))
    except OSError:
        return []
    return out


def first_prompt(path: PathLike, max_lines: int = 50) -> str:
    """The text of a transcript's first `user` message (a subagent's brief: the instructions it was given), its text
    blocks joined; '' when the file is missing or has none in its first `max_lines` lines. Never raises."""
    try:
        with open(path, "rb") as f:
            for n, line in enumerate(f):
                if n >= max_lines:
                    break
                if b'"user"' not in line:
                    continue
                d = _record(line)
                m = d.get("message") if d else None
                if (
                    not d
                    or not isinstance(m, dict)
                    or d.get("type", "user") != "user"
                    or m.get("role", "user") != "user"
                ):
                    continue
                t = text_of(m.get("content"))
                if t:
                    return t
    except (OSError, TypeError, ValueError):
        pass
    return ""


def session_of_subagent(path: PathLike | None) -> str | None:
    """The session id a subagent's transcript belongs to (`<projects>/<dir>/<session>/subagents/agent-<id>.jsonl`, or
    the `<session>/tasks/<id>.output` link Claude Code keeps to it), or None."""
    parts = Path(str(path or "")).parts
    for marker in ("subagents", "tasks"):
        if marker in parts:
            i = parts.index(marker)
            return parts[i - 1] if i > 0 else None
    return None


def subagent_file(agent_id: str | None, transcript: PathLike | None = None) -> str | None:
    """The real path of a subagent's transcript: `transcript` (the link foreman recorded, followed) when it exists,
    else `<projects>/*/*/subagents/agent-<id>.jsonl`; None when neither is found. Never raises."""
    try:
        if transcript and os.path.exists(transcript):
            return os.path.realpath(transcript) if os.path.islink(transcript) else str(transcript)
        aid = str(agent_id or "")
        if aid.startswith("agent-"):
            aid = aid[len("agent-") :]
        if not aid or any(c in aid for c in "/\\*?[]"):
            return None
        sid = session_of_subagent(transcript)
        pattern = f"*/{sid}/subagents/agent-{aid}.jsonl" if sid else f"*/*/subagents/agent-{aid}.jsonl"
        for f in sorted(claude_projects_dir().glob(pattern)):
            return str(f)
    except (OSError, TypeError, ValueError):
        pass
    return None


def find(prefix: str | None) -> list[tuple[str, str, bool]]:
    """The transcripts whose id starts with `prefix`: [(path, id, is_agent)], the main ones of every session
    (`<projects>/*/<id>.jsonl`) and every subagent's (`…/<session>/subagents/agent-<id>.jsonl`; `agent-` in the
    prefix is optional), sorted by path. [] for an empty prefix, one with a glob or path character, or no folder.
    Never raises."""
    p = (prefix or "").strip()
    if not p or any(c in p for c in "/\\*?[]") or p.startswith("."):
        return []
    aid = p[len("agent-") :] if p.startswith("agent-") else p
    out: list[tuple[str, str, bool]] = []
    try:
        root = claude_projects_dir()
        for f in sorted(root.glob(f"*/{p}*.jsonl")):
            out.append((str(f), f.stem, False))
        if aid:
            for f in sorted(root.glob(f"*/*/subagents/agent-{aid}*.jsonl")):
                out.append((str(f), f.stem[len("agent-") :], True))
    except (OSError, ValueError):
        return []
    return sorted(out)


_ENTRY_TYPES = (
    b'"type":"assistant"',
    b'"type":"user"',
    b'"type":"attachment"',
    b'"type": "assistant"',
    b'"type": "user"',
    b'"type": "attachment"',
)


def _blocks_text(content: object) -> tuple[str, int]:
    """(the text of a message's content, its image blocks): a string, or the text of its text blocks joined."""
    if isinstance(content, str):
        return content, 0
    if not isinstance(content, list):
        return "", 0
    texts: list[str] = []
    images = 0
    for b in content:
        if isinstance(b, str):
            texts.append(b)
        elif isinstance(b, dict):
            if b.get("type") == "image":
                images += 1
            elif isinstance(b.get("text"), str):
                texts.append(b["text"])
    return "\n".join(texts), images


def _origin(d: Record, text: str) -> str:
    """What kind of prompt a `user` record with text is (see entries)."""
    o = d.get("origin")
    kind = o.get("kind") if isinstance(o, dict) else o if isinstance(o, str) else None
    if kind == "task-notification" or text.startswith("<task-notification>"):
        return "task-notification"
    if kind == "peer" or text.startswith("Another Claude session sent a message"):
        return "peer"
    if d.get("turnOrigin") == "scheduled" or d.get("wakeupSource"):
        return "scheduled"
    if d.get("isMeta") is True:
        return "skill" if d.get("sourceToolUseID") or text.startswith("Base directory for this skill") else "meta"
    return "owner"


def entries(path: PathLike) -> Iterator[Entry]:
    """What a transcript put in front of the model, in file order, one simplified entry per piece (lines of another
    record type, or that do not parse, are skipped). Each has `kind`, `ts` (an aware datetime or None) and `sidechain`:
    - "call": one assistant LINE (a response written on several lines gives several entries with the same `id`):
      `id`, `usage` (parse_call's dict, None for a `<synthetic>` one or without usage) and `blocks`, each
      {"type" (text, thinking, tool_use…), "chars" (the text, the thinking, or the tool input as JSON), "name" and
      "tool_id" and "input" (tool_use)}.
    - "result": one tool_result block: `tool_id`, `chars` (its text), `images` (its image blocks), `text`.
    - "prompt": the text blocks of a `user` record that is not a tool result: `origin` (task-notification: an agent's
      final message; peer: another session's message; scheduled: a loop prompt; skill: a skill's text; meta: Claude
      Code's reminders and notes; owner: what the person typed), `chars`, `text`.
    - "attachment": one attachment Claude Code rendered for the model (those it only records are skipped): `type`
      (hook_additional_context, queued_command, skill_listing…), `hook` (its hook event, or None), `texts` (a hook's
      texts, one per hook output; else the rendered text), `chars` (the rendered text).
    Never raises: an unreadable file ends the entries."""
    try:
        f = open(path, "rb")
    except (OSError, TypeError, ValueError):
        return
    with f:
        try:
            for line in f:
                if not any(t in line for t in _ENTRY_TYPES):
                    continue
                d = _record(line)
                if d is None:
                    continue
                yield from _entries_of(d)
        except (OSError, ValueError):
            return


def _entries_of(d: Record) -> Iterator[Entry]:
    t = d.get("type")
    base = {"ts": parse_ts(d.get("timestamp")), "sidechain": d.get("isSidechain") is True}
    m = d.get("message")
    if t == "assistant" and isinstance(m, dict):
        blocks: list[dict[str, Any]] = []
        content = m.get("content")
        for b in (
            content
            if isinstance(content, list)
            else [{"type": "text", "text": content}]
            if isinstance(content, str)
            else []
        ):
            if not isinstance(b, dict):
                continue
            k = b.get("type")
            if k == "tool_use":
                inp = b.get("input") if isinstance(b.get("input"), dict) else {}
                blocks.append(
                    {
                        "type": k,
                        "chars": len(json.dumps(inp, ensure_ascii=False)),
                        "name": b.get("name"),
                        "tool_id": b.get("id"),
                        "input": inp,
                    }
                )
            else:
                txt = b.get("thinking") if k == "thinking" else b.get("text")
                blocks.append({"type": k, "chars": len(txt) if isinstance(txt, str) else 0})
        mid = m.get("id")
        yield {
            **base,
            "kind": "call",
            "id": mid if isinstance(mid, str) and mid else None,
            "usage": _call_of(d),
            "blocks": blocks,
        }
    elif t == "user" and isinstance(m, dict):
        content = m.get("content")
        rest: list[Any] = []
        for b in content if isinstance(content, list) else []:
            if isinstance(b, dict) and b.get("type") == "tool_result":
                text, images = _blocks_text(b.get("content"))
                yield {
                    **base,
                    "kind": "result",
                    "tool_id": b.get("tool_use_id"),
                    "chars": len(text),
                    "images": images,
                    "text": text,
                }
            else:
                rest.append(b)
        text, _ = _blocks_text(content if isinstance(content, str) else rest)
        if text:
            yield {**base, "kind": "prompt", "origin": _origin(d, text), "chars": len(text), "text": text}
    elif t == "attachment" and isinstance(d.get("attachment"), dict) and isinstance(d.get("rendered"), list):
        a = d["attachment"]
        rendered = "\n".join(_blocks_text(r.get("content"))[0] for r in d["rendered"] if isinstance(r, dict))
        if not rendered:
            return
        raw = a.get("content")
        texts = (
            [x for x in raw if isinstance(x, str) and x]
            if isinstance(raw, list)
            else [raw]
            if isinstance(raw, str) and raw
            else [rendered]
        )
        yield {
            **base,
            "kind": "attachment",
            "type": a.get("type"),
            "hook": a.get("hookEvent"),
            "texts": texts or [rendered],
            "chars": len(rendered),
        }
