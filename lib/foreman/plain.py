"""`foreman plain`: a rule-based check of a text the owner reads (docs/owner.md), without a model or the network.

It flags what makes a message unreadable for someone who has not followed the work:

- **code**: an issue, sub-issue, decision or question id without what it is beside it («T3», «J1», «AB-12 T3»), and
  «step N» in any language foreman has (i18n `plain_step_word`). An id is *glossed*, so not flagged, when it is in
  brackets («the dead-orchestrator warning (AB-54)») or followed by «(», «:», «,», «—» or «-» and words
  («AB-54, the dead-orchestrator warning»); after a step a comma is no gloss («step 4, fixed»). The rule is «an id
  next to words», not understanding the sentence.
- **internal**: snake_case and UPPER_SNAKE names (config keys, notice kinds, environment variables), `[section] key`
  of foreman.toml, a key of the config in backticks, paths and `file:line`, file names with a code extension,
  `function()`, commit hashes and session ids (hex with a digit and a letter, 7-40 characters; UUIDs).
- **name**: the names of this project's agents, sessions and branches, read from its state (agents.json,
  launches.json, orchestrator.json) and its backlog (each sub-issue's agent and branch). Names made only of letters
  are skipped: they are often ordinary words.

Not flagged: fenced code blocks, inline code that is a command the owner types (it starts with `/` or `foreman `),
URLs, and the terms of `[project] owner_knows` (case-insensitive, whole words).

Callers (decision add, the warnings elsewhere, the chat reviewer, stats) use `findings(project, text)`.
`summary(found)` is the one-line list for stderr. `foreman plain` itself always checks: it is an explicit request.

Where the check runs (docs/owner.md): `backlog decision add` refuses a question that fails it (`check` gives the
findings of each field, the caller blocks; `--as-is` records it anyway). Every other text that reaches the owner
gets a warning on stderr that never blocks (`warn`): issue titles, result lines, progress lines, inbox replies and
the PR title and body of `foreman integrate`. The chat reviewer (`review_answer`, from foreman's hooks module:
`hook mod answer` at the end of each turn of the marked session) checks the orchestrator's answer, logs counts for
`foreman plain --stats`, and its reminder of what the answer left unexplained rides the owner's next prompt.

Exit codes of `foreman plain`: 0 nothing flagged, 1 something flagged, 2 bad usage (no text).
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Any, TextIO

from . import config, i18n, util

if TYPE_CHECKING:
    from datetime import datetime
    from re import Match, Pattern

    from .config import Project

Finding = dict[str, Any]  # findings(): {kind, what, text, start, end, reason}

# The short ids of a sub-issue, decision or question, alone («T3», «J1», «O2»).
SHORT_ID = r"[TJO]\d+"
# Words that, after a comma, start a new clause rather than a gloss («AB-12, and then…»).
NOT_A_GLOSS = {"and", "or", "but", "so", "then", "y", "e", "o", "u", "ni", "pero", "luego", "etc"}
CODE_EXT = (
    "py",
    "js",
    "ts",
    "json",
    "jsonl",
    "toml",
    "md",
    "sh",
    "yml",
    "yaml",
    "txt",
    "log",
    "html",
    "css",
    "swift",
    "kt",
    "go",
    "rs",
    "rb",
    "java",
    "c",
    "h",
    "cpp",
    "lock",
    "plist",
    "sql",
    "csv",
    "ini",
    "cfg",
)
CONFIG_SECTIONS = set(config.DEFAULTS) | {"repos", "resources", "recurring"}

REASONS = {
    "issue": "an issue id with nothing beside it: say what it is, the id in brackets",
    "sub": "a sub-issue, decision or question id alone: say what it is, the id in brackets",
    "step": "a step number: say what the step is",
    "snake": "an internal name (config key, notice kind, variable): say what it means, or leave it out",
    "env": "an environment variable: say what it does, or leave it out",
    "config": "a foreman.toml key: say what it changes, or leave it out",
    "path": "a path: say what the file is, or leave it out",
    "file_line": "a file:line reference: say what was there, or leave it out",
    "file": "a file name: say what the file is, or leave it out",
    "function": "a function name: say what it does, or leave it out",
    "hash": "a commit hash or session id: leave it out",
    "agent": "an agent's name: say what it is doing instead",
    "session": "an orchestrator session's name: say «the orchestrator» or what it did",
    "branch": "a branch name: say what the work is instead",
}
KINDS = {"issue": "code", "sub": "code", "step": "code", "agent": "name", "session": "name", "branch": "name"}


def _step_words() -> list[str]:
    words = set()
    for f in i18n.HERE.glob("*.json"):
        try:
            w = json.loads(f.read_text()).get("plain_step_word")
        except (OSError, ValueError):
            continue
        if w:
            words.add(w)
    return sorted(words or {"step"})


def _id_bodies(project: Project | None) -> list[str]:
    """Regex bodies of the project's issue ids (and legacy ids), anchors dropped."""
    ids = (project.section("ids") if project else {}) or config.DEFAULTS["ids"]
    out = [(ids.get("issue") or config.DEFAULTS["ids"]["issue"]).strip("^$")]
    if ids.get("legacy"):
        out.append(ids["legacy"].strip("^$"))
    return out


def known_names(project: Project | None) -> dict[str, str]:
    """{name: what} of this project's agents ("agent"), orchestrator sessions ("session") and branches ("branch"),
    from its state and backlog; empty when there is none. Names of letters only, or shorter than 3, are left out."""
    out = {}
    if project is None:
        return out

    def add(name: object, what: str) -> None:
        if isinstance(name, str):
            name = name.strip()
            if len(name) >= 3 and not name.isalpha() and name not in out:
                out[name] = what

    try:
        for h in (util.read_json(project.state / "agents.json", {}) or {}).values():
            if isinstance(h, dict):
                add(h.get("name"), "agent")
                add(h.get("branch"), "branch")
        for r in (util.read_json(project.state / "launches.json", {}) or {}).values():
            if isinstance(r, dict):
                add(r.get("name"), "agent")
                add(r.get("branch"), "branch")
        o = util.read_json(project.state / "orchestrator.json", {}) or {}
        if isinstance(o, dict):
            add(o.get("name"), "session")
    except Exception:  # noqa: BLE001 (never fail a check over the state)
        pass
    try:
        from . import backlog

        b = backlog.Backlog(project)
        if b.exists():
            data = b.load(raw=True)
            for i in list(data.get("issues") or []) + list(data.get("archive") or []):
                for s in (i.get("subs") or []) if isinstance(i, dict) else []:
                    if isinstance(s, dict):
                        add(s.get("agent"), "agent")
                        add(s.get("branch"), "branch")
    except Exception:  # noqa: BLE001 (a check over the state never fails)
        pass
    mains = {str(r.get("main") or "main") for r in project.repos.values()} | {"main", "master"}
    return {k: v for k, v in out.items() if k not in mains}


def _skipped_spans(text: str) -> list[tuple[int, int]]:
    """Spans no rule looks at: fenced code blocks, inline commands the owner types and URLs."""
    spans = [m.span() for m in re.finditer(r"```.*?(?:```|\Z)", text, re.S)]
    for m in re.finditer(r"`([^`\n]+)`", text):
        if m.group(1).startswith(("/", "foreman ")):
            spans.append(m.span())
    spans += [m.span() for m in re.finditer(r"\bhttps?://\S+", text)]
    return spans


def _known_spans(text: str, knows: Iterable[str] | None) -> list[tuple[int, int]]:
    spans = []
    for raw in knows or ():
        term = raw.strip()
        if term:
            spans += [m.span() for m in re.finditer(rf"(?<![\w-]){re.escape(term)}(?![\w-])", text, re.I)]
    return spans


def _inside(span: Sequence[int], spans: Iterable[tuple[int, int]]) -> bool:
    return any(a <= span[0] and span[1] <= b for a, b in spans)


def _in_brackets(text: str, start: int) -> bool:
    """True if `start` is inside (…) or […] opened earlier in the same paragraph."""
    para = text[:start].rsplit("\n\n", 1)[-1]
    return para.count("(") > para.count(")") or para.count("[") > para.count("]")


def _glossed(text: str, start: int, end: int, id_any: Pattern[str], comma: bool = True) -> bool:
    """An id is glossed when it is in brackets or followed by «(», «:», «,» (unless comma=False), «—», «–» or « - »
    and a word."""
    if _in_brackets(text, start):
        return True
    # markdown emphasis and opening marks may sit around the punctuation: «**AB-54:** the…», «AB-54: ¿who…?»
    m = re.match(
        r"[*_`]*\s*(?:[(:—–]|\s-" + ("|," if comma else "") + r")[*_`]*\s*[(¿¡«\"'“‘*_`]*"
        r"([^\W\d_][\w'’-]*)",
        text[end:],
    )
    if not m:
        return False
    word = m.group(1)
    if id_any.fullmatch(word) or word.lower() in NOT_A_GLOSS:
        return False
    return len(word) >= 2


def findings(project: Project | None, text: str | None) -> list[Finding]:
    """What `text` names without explaining, per docs/owner.md: a list of {kind, what, text, start, end, reason}
    sorted by position. kind: code (a bare id or step), internal (a name from the code or config), name (an agent,
    session or branch of this project); what: the rule (REASONS); start/end: the span in `text`."""
    text = text or ""
    skip = _skipped_spans(text)
    knows = project.section("project").get("owner_knows", []) if project else []
    known = _known_spans(text, knows)
    issue = "(?:" + "|".join(_id_bodies(project)) + ")"
    id_any = re.compile(rf"{issue}|{SHORT_ID}")
    raw = []

    def hit(what: str, m: Match[str], group: int = 0) -> None:
        raw.append((m.start(group), m.end(group), what))

    # code: «AB-12», «AB-12 T3», «AB-12 J1», «T3», «step 4»
    for m in re.finditer(rf"(?<![\w-]){issue}(?:\s+{SHORT_ID})?(?![\w-])", text):
        if not _glossed(text, m.start(), m.end(), id_any):
            hit("issue" if not re.search(rf"\s{SHORT_ID}$", m.group()) else "sub", m)
    for m in re.finditer(rf"(?<![\w-]){SHORT_ID}(?![\w-])", text):
        if not _glossed(text, m.start(), m.end(), id_any):
            hit("sub", m)
    steps = "|".join(re.escape(w) for w in _step_words())
    for m in re.finditer(rf"(?<!\w)(?:{steps})\s+\d+(?!\w)", text, re.I):
        if not _glossed(text, m.start(), m.end(), id_any, comma=False):  # «step 4, fixed» is no gloss
            hit("step", m)
    # internal names
    keys = set().union(
        *(set(d) for d in config.DEFAULTS.values()), config.REPO_DEFAULTS, config.RESOURCE_DEFAULTS, {"name"}
    )
    for m in re.finditer(r"(?<![\w$/.-])\[([a-z][\w.*-]*)\](?:\s+([a-z][a-z0-9_]*))?(?!\()", text):
        if m.group(1).split(".")[0] in CONFIG_SECTIONS:
            if m.group(2) in keys:
                hit("config", m)
            else:  # «[deny] sigue»: the section alone, the next word is ordinary text
                raw.append((m.start(), m.end(1) + 1, "config"))
    for m in re.finditer(r"`([a-z][a-z0-9_]*)`", text):
        if m.group(1) in keys:
            hit("config", m, 1)
    for m in re.finditer(r"(?<![\w$-])\$?[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+(?![\w-])", text):
        hit("env", m)
    for m in re.finditer(r"(?<![\w-])[a-z][a-z0-9]*(?:_[a-z0-9]+)+(?![\w-])", text):
        hit("snake", m)
    ext = "|".join(CODE_EXT)
    for m in re.finditer(rf"(?<![\w/.~-])[\w.~/-]*[\w-]\.(?:{ext}):\d+(?:-\d+)?(?!\w)", text):
        hit("file_line", m)
    for m in re.finditer(
        r"(?<![\w:/~.-])(?:~/|/)(?:[\w.-]+/)+[\w.-]*|(?<![\w:/~.-])/[\w.-]+/?(?=[\s,;)»\"']|$)"
        r"|(?<![\w:/~.-])[\w.-]+(?:/[\w.*-]+)+",
        text,
    ):
        path = m.group().rstrip(".")
        segs = [s for s in path.split("/") if s]
        letters = re.search(r"[A-Za-z]", path)
        if not letters or (len(segs) < 2 and not path.startswith("~")):
            continue
        if not (path.startswith(("~", "/")) or len(segs) >= 3 or "." in segs[-1]):
            continue  # «and/or», «km/h», a bare «docs/owner» is left to the other rules
        raw.append((m.start(), m.start() + len(path), "path"))
    for m in re.finditer(rf"(?<![\w/.~-])[\w-]+\.(?:{ext})(?![\w/.])", text):
        hit("file", m)
    for m in re.finditer(r"(?<![\w.])[A-Za-z_][\w.]*\(\)", text):
        hit("function", m)
    for m in re.finditer(
        r"(?<![\w-])(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
        r"|[0-9a-f]{7,40})(?![\w-])",
        text,
    ):
        if re.search(r"\d", m.group()) and re.search(r"[a-f]", m.group()):
            hit("hash", m)
    # names of this project
    for name, what in known_names(project).items():
        for m in re.finditer(rf"(?<![\w/-]){re.escape(name)}(?![\w-]|/\w)", text):
            hit(what, m)
    # keep what is not skipped or known; of overlapping hits, the longest (then the first) wins
    raw = [r for r in raw if not _inside(r[:2], skip) and not _inside(r[:2], known)]
    raw.sort(key=lambda r: (r[0], -(r[1] - r[0])))
    out, last_end = [], -1
    for start, end, what in raw:
        if start < last_end:
            continue
        out.append(
            {
                "kind": KINDS.get(what, "internal"),
                "what": what,
                "text": text[start:end],
                "start": start,
                "end": end,
                "reason": REASONS[what],
            }
        )
        last_end = end
    return out


def summary(found: list[Finding], limit: int = 8) -> str:
    """One line for stderr: «T5 (a sub-issue… alone), main_ci (an internal name…)», at most `limit` of them."""
    parts = [f"«{f['text']}» ({f['reason']})" for f in found[:limit]]
    more = f" and {len(found) - limit} more" if len(found) > limit else ""
    return "; ".join(parts) + more


def ids_re(project: Project | None) -> Pattern[str]:
    """Any issue, sub-issue, decision or question id, glossed or not (a public repo carries none: integrate)."""
    issue = "(?:" + "|".join(_id_bodies(project)) + ")"
    return re.compile(rf"(?<![\w-])(?:{issue}|{SHORT_ID})(?![\w-])")


def check(project: Project | None, fields: Iterable[tuple[str, str | None]]) -> list[tuple[str, list[Finding]]]:
    """[(label, findings)] of the (label, text) pairs that fail the check."""
    out = []
    for label, text in fields:
        found = findings(project, text) if text else []
        if found:
            out.append((label, found))
    return out


def warn(project: Project | None, label: str, text: str | None, out: TextIO | None = None) -> list[Finding]:
    """A warning on stderr (never blocking) when `text`, which reaches the owner, names something without explaining
    it; returns the findings."""
    found = check(project, [(label, text)])
    if found:
        print(
            f"foreman: warning: the {label} reaches the owner and names without explaining: {summary(found[0][1])}. "
            "Rewrite it as docs/owner.md says (`foreman docs owner`); nothing was blocked",
            file=out or sys.stderr,
        )
    return found[0][1] if found else []


def line_col(text: str, pos: int) -> tuple[int, int]:
    line = text.count("\n", 0, pos) + 1
    return line, pos - (text.rfind("\n", 0, pos) + 1) + 1


def run(project: Project | None, words: list[str], as_json: bool = False) -> int:
    """`foreman plain [TEXT…|-] [--json]`: exit 0 clean, 1 something flagged, 2 no text."""
    if not words or words == ["-"]:
        if sys.stdin is None or sys.stdin.isatty():
            print('usage: foreman plain "text" | foreman plain - (the text on stdin) [--json]', file=sys.stderr)
            return 2
        text = sys.stdin.read()
    else:
        text = " ".join(words)
    if not text.strip():
        print("foreman plain: no text to check", file=sys.stderr)
        return 2
    found = findings(project, text)
    if as_json:
        print(json.dumps({"words": len(text.split()), "findings": found}, ensure_ascii=False, indent=1))
        return 1 if found else 0
    if not found:
        print("plain: nothing to explain")
        return 0
    for f in found:
        line, col = line_col(text, f["start"])
        print(f"{line}:{col}  {f['kind']:<8} «{f['text']}»: {f['reason']}")
    print(f"{len(found)} to explain for the owner (docs/owner.md); terms the owner knows go in [project] owner_knows")
    return 1


# ---------- the chat reviewer and its stats ----------
# The orchestrator's answer at the end of each of its turns (foreman's hooks module, `hook mod answer`: the main
# loop of the marked session, never a subagent's). One line per answer in <state>/plain.jsonl (counts, never the
# text) feeds `foreman plain --stats`; the module adds the reminder to the owner's next prompt, not to a
# notification, another session's message or the loop pass.

LOG = "plain.jsonl"
ECHO_LIMIT = 8


def review_answer(project: Project, session_id: str, turn: str, text: object) -> tuple[bool, str | None]:
    """The chat reviewer on one answer of the marked session: (reviewed, reminder). For an empty answer: (False,
    None). Otherwise it logs the answer's counts (`message` is the turn's id) and
    returns the reminder for the model (i18n `plain_echo`) when something was flagged, else None."""
    if not isinstance(text, str) or not text.strip():
        return False, None
    text = text.strip()
    found = findings(project, text)
    record(project, session_id, {"uuid": turn or "", "text": text}, found)
    if not found:
        return True, None
    items = list(dict.fromkeys(f["text"] for f in found))
    more = f" (+{len(items) - ECHO_LIMIT})" if len(items) > ECHO_LIMIT else ""
    return True, i18n.for_project(project)["plain_echo"].format(items=", ".join(items[:ECHO_LIMIT]) + more)


def record(project: Project, session_id: str | None, msg: dict[str, str], found: list[Finding]) -> dict[str, Any]:
    """One line in <state>/plain.jsonl: time, session, message (the turn's id), words and the findings counted by
    kind and rule."""
    from datetime import datetime

    kinds, whats = {}, {}
    for f in found:
        kinds[f["kind"]] = kinds.get(f["kind"], 0) + 1
        whats[f["what"]] = whats.get(f["what"], 0) + 1
    line = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "session": session_id or "",
        "message": msg["uuid"],
        "words": len(msg["text"].split()),
        "findings": len(found),
        "kinds": kinds,
        "what": whats,
    }
    f = project.state / LOG
    f.parent.mkdir(parents=True, exist_ok=True)
    with util.file_lock(f), open(f, "a") as out:
        out.write(json.dumps(line, ensure_ascii=False) + "\n")
    return line


def stats(project: Project, since: datetime | None = None, session: str | None = None) -> dict[str, Any]:
    """{messages, flagged, share, days: [{day, messages, flagged, share}], kinds: {kind: messages}, what: {rule:
    messages}} from <state>/plain.jsonl, optionally since a datetime and for one session. share is a % (1 decimal)."""
    rows = []
    for d in util.read_jsonl(project.state / LOG):
        try:
            t = util.fromiso(d["time"])
        except (ValueError, KeyError, TypeError):
            continue
        if (since and t < since) or (session and d.get("session") != session):
            continue
        rows.append((t, d))

    def share(n: int, total: int) -> float:
        return round(100.0 * n / total, 1) if total else 0.0

    days, kinds, whats = {}, {}, {}
    for t, d in rows:
        day = days.setdefault(t.strftime("%Y-%m-%d"), [0, 0])
        day[0] += 1
        if d.get("findings"):
            day[1] += 1
        for k in d.get("kinds") or {}:
            kinds[k] = kinds.get(k, 0) + 1
        for w in d.get("what") or {}:
            whats[w] = whats.get(w, 0) + 1
    total = len(rows)
    flagged = sum(1 for _, d in rows if d.get("findings"))
    return {
        "messages": total,
        "flagged": flagged,
        "share": share(flagged, total),
        "days": [
            {"day": k, "messages": v[0], "flagged": v[1], "share": share(v[1], v[0])} for k, v in sorted(days.items())
        ],
        "kinds": dict(sorted(kinds.items(), key=lambda kv: -kv[1])),
        "what": dict(sorted(whats.items(), key=lambda kv: -kv[1])),
    }


def run_stats(
    project: Project | None, since: str | None = None, session: str | None = None, as_json: bool = False
) -> int:
    """`foreman plain --stats [--since 7d] [--session ID] [--json]`: the share of the orchestrator's messages to the
    owner that named something without explaining it, per day. Exit 0, 2 on bad usage."""
    if project is None:
        print("foreman plain --stats: needs a project (--project NAME or run it inside one)", file=sys.stderr)
        return 2
    try:
        start = util.parse_when(since).astimezone().replace(tzinfo=None) if since else None
    except ValueError:
        print(f"foreman plain --stats: --since {since!r}: use 7d, 12h, 30m or a date (2026-10-31)", file=sys.stderr)
        return 2
    s = stats(project, start, session)
    if as_json:
        print(
            json.dumps(
                {
                    "project": project.name,
                    "since": start.isoformat(timespec="minutes") if start else None,
                    "session": session,
                    **s,
                },
                ensure_ascii=False,
                indent=1,
            )
        )
        return 0
    scope = (f" since {start:%Y-%m-%d %H:%M}" if start else "") + (f", session {session}" if session else "")
    if not s["messages"]:
        print(
            f"{project.name}: no message to the owner checked yet{scope} (the chat reviewer logs one per owner "
            "prompt in the marked session)"
        )
        return 0
    print(
        f"{project.name}: {s['flagged']} of {s['messages']} messages to the owner named something without "
        f"explaining it ({s['share']} %){scope}"
    )
    for d in s["days"]:
        print(f"  {d['day']}  {d['flagged']:>4} / {d['messages']:<4} {d['share']:>5} %")
    if s["kinds"]:
        print("  by kind (messages): " + ", ".join(f"{k} {n}" for k, n in s["kinds"].items()))
    if s["what"]:
        print("  by rule (messages): " + ", ".join(f"{k} {n}" for k, n in s["what"].items()))
    return 0
