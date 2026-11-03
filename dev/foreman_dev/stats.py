"""`foreman-dev stats`: what the Claude Code sessions of a product spent, in tokens, by session, agent, issue, agent
type, model and hour, from the transcripts Claude Code keeps (read by transcripts.py, the only module that knows the
format) and what only foreman knows (which session orchestrated, which agent did which issue).

- Sessions: every main transcript `<Claude projects>/*/<id>.jsonl` written within the window. A session belongs to the
  product its foreman hooks recorded (`<state root>/install/sessions.json`) or, failing that, to the product that owns
  the folder it was opened in (Project.owns: the home, the repos, the worktrees, the workspace). A session opened in
  another folder is not seen (it says so). With --all, every product, plus the sessions of none.
- Agents: each session's `subagents/agent-<id>.jsonl`. Their identity is the one foreman recorded (agents.json, kept
  24 h, or the launch token in the head of the transcript and launches.json); for an agent without that record the
  issue is guessed from its name in the backlog's sub-issues, «Issue: ID» in its brief or its description, and an
  agent with no issue found is counted under «no issue» (it says how many). The orchestrator's sessions are
  coordination, never split among issues.
- A call is one model response (deduplicated by message id); it is counted in the window when its timestamp is.
  Columns: calls, cache read, cache write, output, input, peak context (THE context of transcripts.py).
- Money: only the figure Claude Code itself records when a session closes (`cost-state`: the whole session since it
  started, its subagents included, at API prices), shown on session rows as an equivalent price, never computed,
  never split by day, agent or issue. SHOW_PRICE is the one switch that hides it everywhere. Claude Code's
  `cost-state` also counts calls it does not write to the transcript (a small model's), so its input and output can
  exceed the transcripts'; both are shown, each with its source.
- --usage appends the text of `claude -p "/usage" --no-session-persistence` (30 s at most), only when asked.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from foreman import config, identity, transcripts, util

if TYPE_CHECKING:
    import argparse

    from foreman.config import Project

Tokens = dict[str, int]  # blank(): the sums of a set of calls (TOKENS and "peak")
Unit = dict[str, Any]  # collect(): the sums and identity of one transcript
Row = dict[str, Any]  # group(): one row of a table

SHOW_PRICE = True  # Claude Code's own price on session rows (an equivalent, not what a subscription pays)

TOKENS = ("calls", "input", "cache_read", "cache_write", "cache_write_1h", "output", "thinking")
BY = ("project", "session", "agent", "issue", "type", "model", "hour")
NO_PRODUCT = "(no product)"
NO_ISSUE = "no issue"
COORDINATION = "coordination"
MAIN = "main session"
# «Outgoing session: NAME (session ID)»: a line of the handover notes foreman wrote before 0.44 (old notes are read)
OUTGOING = re.compile(r"Outgoing session: (\S+) \(session ([0-9a-f-]{8,})")
USAGE_TIMEOUT_S = 30


# ---------- time ----------


def _local(t: datetime | None, fmt: str = "%Y-%m-%d %H:%M") -> str | None:
    return t.astimezone().strftime(fmt) if t else None


# ---------- sums ----------


def blank() -> Tokens:
    return {**dict.fromkeys(TOKENS, 0), "peak": 0}


def add_call(t: Tokens, c: transcripts.Call) -> None:
    t["calls"] += 1
    for k in TOKENS[1:]:
        t[k] += c[k]
    t["peak"] = max(t["peak"], transcripts.context(c))


def merge(t: Tokens, u: Tokens) -> None:
    for k in TOKENS:
        t[k] += u[k]
    t["peak"] = max(t["peak"], u["peak"])


# ---------- what foreman knows ----------


class Context:
    """What foreman knows about the products in scope, read once: the registered products, which product each
    recorded session ran in, the orchestrators' sessions, and per product its agents, launches and backlog."""

    def __init__(self, projects: list[Project]) -> None:
        self.projects = projects
        self.by_name = {p.name: p for p in projects}
        try:
            from foreman import versions

            rec = util.read_json(versions.sessions_file(), {})
        except Exception:  # noqa: BLE001 (an older or broken install record: fall back to the folders)
            rec = {}
        self.recorded = (
            {sid: r.get("project") for sid, r in rec.items() if isinstance(r, dict)} if isinstance(rec, dict) else {}
        )
        self.orchestrators: dict[str, str] = {}  # session id → name
        self._per: dict[str, dict[str, Any]] = {}
        for p in projects:
            self._orchestrators_of(p)

    def _orchestrators_of(self, p: Project) -> None:
        try:
            for f in sorted(p.notes_dir.glob("*.md")):
                for name, sid in OUTGOING.findall(f.read_text(encoding="utf-8", errors="replace")):
                    self.orchestrators[sid] = name
        except OSError:
            pass
        from foreman import orchestrator

        o = orchestrator.current(p)
        if o:
            self.orchestrators[o["session"]] = o["name"] or self.orchestrators.get(o["session"], "")

    def product_of(self, path: util.PathLike, sid: str | None) -> str | None:
        name = self.recorded.get(sid)
        if name in self.by_name:
            return name
        cwd = transcripts.session_info(path)["cwd"]
        if cwd:
            for p in self.projects:
                if p.owns(cwd, others=self.projects):
                    return p.name
        return None

    def per(self, name: str) -> dict[str, Any]:
        """{agents, launches, subs: {agent name: (issue, sub)}, issue_re} of a product (cached)."""
        if name not in self._per:
            p = self.by_name[name]
            agents = util.read_json(p.state / "agents.json", {})
            launches = util.read_json(identity.launches_file(p), {})
            subs: dict[str, tuple[Any, Any]] = {}
            try:
                from foreman.backlog import Backlog

                b = Backlog(p)
                if b.exists():
                    for _, i, _ in b.iter_issues(b.load(), archived=True):
                        for s in i.get("subs") or []:
                            if s.get("agent"):
                                subs.setdefault(s["agent"], (i["id"], s.get("id")))
            except Exception:  # noqa: BLE001 (a broken backlog only costs the guess by name)
                subs = {}
            body = p.section("ids").get("issue", r"[A-Z]{2}-\d+").strip("^$")
            self._per[name] = {
                "agents": agents if isinstance(agents, dict) else {},
                "launches": launches if isinstance(launches, dict) else {},
                "subs": subs,
                "issue_re": re.compile(rf"(?<![\w-])((?:{body}))(?![\w-])"),
            }
        return self._per[name]

    def agent(self, product: str | None, aid: str, meta: dict[str, Any], path: str) -> dict[str, Any]:
        """{name, type, issue, sub, issue_from} of a subagent: `issue_from` is "launch" (foreman recorded it),
        "guess" (name in the backlog, brief, description) or None (no issue found)."""
        atype = meta.get("agentType") if isinstance(meta.get("agentType"), str) else None
        if product is None:
            return {"name": None, "type": atype, "issue": None, "sub": None, "issue_from": None}
        p, k = self.by_name[product], self.per(product)
        head = transcripts.head(path)
        h = k["agents"].get(aid)
        if not isinstance(h, dict):
            rec = k["launches"].get(identity.token_in(head) or "")
            h = {**rec, "named_by": "launch"} if isinstance(rec, dict) else {}
        h = {**h, "transcript": path}
        if atype and not h.get("type"):
            h["type"] = atype
        who = identity.of(p, h, head=head)
        issue, sub, source = h.get("issue") or None, h.get("sub") or None, "launch" if h.get("issue") else None
        if not issue and who["name"] and who["name"] in k["subs"]:
            issue, sub = k["subs"][who["name"]]
            source = "guess"
        if not issue and who["issue"]:
            issue, source = who["issue"], "guess"
        if not issue:
            m = k["issue_re"].search(str(meta.get("description") or ""))
            if m:
                issue, source = m.group(1), "guess"
        return {"name": who["name"], "type": h.get("type") or atype, "issue": issue, "sub": sub, "issue_from": source}


# ---------- reading ----------


def read_unit(path: util.PathLike, since: datetime | None, until: datetime | None, main: bool) -> Unit | None:
    """The sums of one transcript within the window, or None when it has no call there: {tot, models, hours, first,
    last, ctx_first, ctx_last, ctx_sum} (the context figures over the main chain, for a session's main file)."""
    cs = transcripts.calls(path, since, until)
    if not cs:
        return None
    u: Unit = {
        "tot": blank(),
        "models": {},
        "hours": {},
        "first": None,
        "last": None,
        "ctx_first": None,
        "ctx_last": None,
        "ctx_sum": 0,
    }
    for c in cs:
        add_call(u["tot"], c)
        add_call(u["models"].setdefault(c["model"] or "?", blank()), c)
        hour = _local(c["ts"], "%Y-%m-%d %H") if c["ts"] else "?"
        add_call(u["hours"].setdefault(hour, blank()), c)
        if c["ts"]:
            u["first"] = c["ts"] if u["first"] is None else min(u["first"], c["ts"])
            u["last"] = c["ts"] if u["last"] is None else max(u["last"], c["ts"])
        if main and not c["sidechain"]:
            ctx = transcripts.context(c)
            u["ctx_first"] = ctx if u["ctx_first"] is None else u["ctx_first"]
            u["ctx_last"] = ctx
            u["ctx_sum"] += ctx
    if not main:
        u["ctx_sum"] = None
    return u


def collect(
    ctx: Context, scope: set[str] | None, since: datetime | None, until: datetime | None, session: str | None = None
) -> tuple[list[Unit], set[str]]:
    """The units of the window: one per session's main file and one per subagent file with calls in it, each
    {kind ("session"|"agent"), product, session, agent, type, name, issue, sub, issue_from, …read_unit}. `scope`:
    the product names counted (None: every session, the ones of no product too); `session`: only the session whose
    id starts with it. Also returns the set of Claude Code versions seen."""
    newer = since.timestamp() if since else None
    units: list[Unit] = []
    seen_versions: set[str | None] = set()
    for path, sid in transcripts.sessions():
        if session and not sid.startswith(session):
            continue
        subs = transcripts.subagents(path)
        if newer is not None:
            subs = [a for a in subs if _mtime(a["path"]) >= newer]
            if _mtime(path) < newer and not subs:
                continue
        product = ctx.product_of(path, sid)
        if scope is not None and product not in scope:
            continue
        before = len(units)
        u = read_unit(path, since, until, main=True)
        if u:
            name = ctx.orchestrators.get(sid)
            units.append(
                {
                    **u,
                    "kind": "session",
                    "product": product,
                    "session": sid,
                    "agent": None,
                    "type": MAIN,
                    "name": name,
                    "orchestrator": name is not None,
                    "issue": None,
                    "sub": None,
                    "issue_from": None,
                    "cost": transcripts.cost_state_at(path),
                }
            )
        for a in subs:
            v = read_unit(a["path"], since, until, main=False)
            if v:
                who = ctx.agent(product, a["id"], a["meta"], a["path"])
                units.append(
                    {
                        **v,
                        **who,
                        "kind": "agent",
                        "product": product,
                        "session": sid,
                        "agent": a["id"],
                        "type": who["type"] or "?",
                        "orchestrator": False,
                        "cost": None,
                    }
                )
        if len(units) > before:
            seen_versions.add(transcripts.session_info(path)["version"])
    return units, {v for v in seen_versions if v}


def _mtime(path: util.PathLike) -> float:
    try:
        return os.stat(path).st_mtime
    except OSError:
        return 0


# ---------- grouping ----------


def _key(u: Unit, by: str) -> str:
    if by == "project":
        return u["product"] or NO_PRODUCT
    if by == "session":
        return u["session"]
    if by == "agent":
        return u["agent"] or u["session"]
    if by == "issue":
        return COORDINATION if u["kind"] == "session" else (u["issue"] or NO_ISSUE)
    if by == "type":
        return u["type"]
    if by == "kind":
        return "main sessions" if u["kind"] == "session" else "agents"
    raise ValueError(by)


def claude_code(cost_at: Any) -> dict[str, Any] | None:
    """Claude Code's own figures of a closed session (its last cost-state, with the time it was written), summed over
    its models, or None: {input, output, cache_read, cache_write, started, closed, price_usd (SHOW_PRICE)}."""
    cost, at = cost_at if isinstance(cost_at, tuple) else (cost_at, None)
    if not isinstance(cost, dict) or not isinstance(cost.get("modelUsage"), dict):
        return None
    out: dict[str, Any] = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    names = {
        "input": "inputTokens",
        "output": "outputTokens",
        "cache_read": "cacheReadInputTokens",
        "cache_write": "cacheCreationInputTokens",
    }
    for m in cost["modelUsage"].values():
        if isinstance(m, dict):
            for k, n in names.items():
                v = m.get(n)
                out[k] += v if isinstance(v, int) and not isinstance(v, bool) else 0
    start = cost.get("startTime")
    out["started"] = (
        _local(datetime.fromtimestamp(start / 1000).astimezone())
        if isinstance(start, (int, float)) and not isinstance(start, bool)
        else None
    )
    out["closed"] = _local(at)
    if SHOW_PRICE and isinstance(cost.get("totalCostUSD"), (int, float)):
        out["price_usd"] = round(float(cost["totalCostUSD"]), 2)
    return out


def group(units: list[Unit], by: str) -> list[Row]:
    """Rows of `by` (BY or "kind"), the largest cache read first: {key, …sums, units, and the fields of that view}."""
    rows: dict[str, Row] = {}
    for u in units:
        if by in ("model", "hour"):
            for k, t in u["models" if by == "model" else "hours"].items():
                r = rows.setdefault(k, {"key": k, "tot": blank(), "units": set()})
                merge(r["tot"], t)
                r["units"].add((u["session"], u["agent"]))
            continue
        k = _key(u, by)
        r = rows.setdefault(k, {"key": k, "tot": blank(), "units": set()})
        merge(r["tot"], u["tot"])
        r["units"].add((u["session"], u["agent"]))
        if by == "session":
            r.setdefault("product", u["product"])
            if u["kind"] == "session":
                r.update(
                    name=u["name"],
                    orchestrator=u["orchestrator"],
                    main=dict(u["tot"]),
                    first=u["first"],
                    last=u["last"],
                    ctx_first=u["ctx_first"],
                    ctx_last=u["ctx_last"],
                    ctx_sum=u["ctx_sum"],
                    claude_code=claude_code(u["cost"]),
                )
            else:
                r["agents"] = r.get("agents", 0) + 1
        elif by == "agent":
            r.update({f: u[f] for f in ("product", "session", "name", "type", "issue", "sub", "issue_from")})
            r["first"], r["last"] = u["first"], u["last"]
        elif by == "issue":
            r["agents"] = r.get("agents", 0) + (u["kind"] == "agent")
    out: list[Row] = []
    for r in rows.values():
        r["units"] = len(r["units"])
        if by == "session":
            r.setdefault("agents", 0)
            first, last = r.pop("first", None), r.pop("last", None)
            hours = (last - first).total_seconds() / 3600 if first and last else 0
            r["from"], r["to"], r["hours"] = _local(first), _local(last), round(hours, 2)
            growth = (r.get("ctx_last") or 0) - (r.get("ctx_first") or 0)
            r["growth_per_hour"] = round(growth / hours) if hours >= 0.25 and "ctx_last" in r else None
            r["role"] = "orchestrator" if r.get("orchestrator") else ("other session" if "main" in r else "?")
            r.pop("orchestrator", None)
        elif by == "agent":
            first, last = r.pop("first", None), r.pop("last", None)
            r["from"], r["to"] = _local(first), _local(last)
        out.append(r)
    return sorted(out, key=lambda r: (-r["tot"]["cache_read"], r["key"]))


def flat(r: Row) -> dict[str, Any]:
    """A row for --json: its sums at the top level."""
    d = {k: v for k, v in r.items() if k != "tot"}
    return {"key": d.pop("key"), **r["tot"], **d}


# ---------- text ----------


def plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def count(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def human(n: float | None) -> str:
    if n is None:
        return "-"
    n = int(n)
    for div, unit, digits in ((10**9, "G", 2), (10**6, "M", 1), (10**3, "K", 1)):
        if abs(n) >= div:
            return f"{n / div:.{digits}f}{unit}"
    return str(n)


def table(head: list[str], rows: Sequence[Sequence[object]]) -> list[str]:
    """Plain text columns: the first ones left-aligned (those whose head ends with '<'), the rest right-aligned."""
    left = [h.endswith("<") for h in head]
    head = [h.rstrip("<") for h in head]
    cells = [head] + [[str(c) for c in r] for r in rows]
    w = [max(len(r[i]) for r in cells) for i in range(len(head))]
    return ["  ".join((c.ljust(w[i]) if left[i] else c.rjust(w[i])) for i, c in enumerate(r)).rstrip() for r in cells]


NUM_HEAD = ["calls", "cache read", "share", "cache write", "output", "input", "peak ctx"]


def nums(t: Tokens, total: int) -> list[str]:
    share = f"{100 * t['cache_read'] / total:.1f} %" if total else "-"
    return [
        count(t["calls"]),
        human(t["cache_read"]),
        share,
        human(t["cache_write"]),
        human(t["output"]),
        human(t["input"]),
        human(t["peak"]),
    ]


def render(by: str, rows: list[Row], total: int, all_products: bool) -> list[str]:
    """The lines of one table."""
    if by == "session":
        head = ["session<", "product<", "who<", "from<", "to<", "agents"] + NUM_HEAD + ["ctx/h"]
        if not all_products:
            head.remove("product<")
        body: list[list[Any]] = []
        for r in rows:
            who = r.get("name") or r["role"]
            line = (
                [
                    r["key"][:8],
                    r.get("product") or NO_PRODUCT,
                    who,
                    (r["from"] or "?")[5:],
                    (r["to"] or "?")[11:],
                    r["agents"],
                ]
                + nums(r["tot"], total)
                + [human(r["growth_per_hour"])]
            )
            if not all_products:
                line.pop(1)
            body.append(line)
        out = table(head, body)
        return out
    if by == "agent":
        head = ["agent<", "type<", "issue<", "session<", "from<"] + NUM_HEAD
        body = [
            [
                r.get("name") or r["key"][:8],
                r.get("type") or "?",
                COORDINATION
                if r["type"] == MAIN
                else (
                    " ".join(x for x in (r.get("issue"), r.get("sub")) if x)
                    + ("?" if r.get("issue_from") == "guess" else "")
                    or NO_ISSUE
                ),
                r["session"][:8],
                (r.get("from") or "?")[5:],
            ]
            + nums(r["tot"], total)
            for r in rows
        ]
        return table(head, body)
    label = {"kind": "", "project": "product", "issue": "issue", "type": "type", "model": "model", "hour": "hour"}[by]
    head = [label + "<"] + (["agents"] if by == "issue" else []) + NUM_HEAD
    body = [[r["key"]] + ([r["agents"]] if by == "issue" else []) + nums(r["tot"], total) for r in rows]
    return table(head, body)


def cc_lines(rows: list[Row]) -> list[str]:
    """Claude Code's own figures of the closed sessions among `rows` (session rows), one line each."""
    cc = [(r["key"][:8], r["claude_code"]) for r in rows if r.get("claude_code")]
    if not cc:
        return []
    out = [
        "Claude Code's own figures for the whole session, from its start to its last close (its price is an "
        "equivalent at API prices, not what a subscription pays):"
    ]
    for sid, c in cc:
        price = f"{c['price_usd']:.2f} $ equivalent, " if "price_usd" in c else ""
        out.append(
            f"  {sid}  {price}input {human(c['input'])}, output {human(c['output'])}, cache read "
            f"{human(c['cache_read'])}, cache write {human(c['cache_write'])} ({c['started'] or '?'} → "
            f"{c['closed'] or '?'})"
        )
    return out


def claude_usage(timeout: float = USAGE_TIMEOUT_S) -> tuple[str | None, str | None]:
    """(text, None) from `claude -p "/usage" --no-session-persistence`, or (None, why not)."""
    exe = shutil.which("claude")
    if not exe:
        return None, "claude is not on the PATH"
    try:
        r = subprocess.run(
            [exe, "-p", "/usage", "--no-session-persistence"],
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None, f"claude -p /usage gave no answer in {timeout} s"
    except OSError as e:
        return None, f"claude -p /usage: {e}"
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip().splitlines()
        return None, f"claude -p /usage exited {r.returncode}" + (f": {err[0]}" if err else "")
    return r.stdout.rstrip(), None


# ---------- the command ----------


def add_parser(sp: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    a = sp.add_parser(
        "stats", help="what the Claude Code sessions spent, in tokens, by session, agent, issue, type, model or hour"
    )
    a.add_argument("--since", help="24h (default), 7d, 30m, HH:MM (today), a date or a date and time")
    a.add_argument("--until", help="the same forms (default: now)")
    a.add_argument("--all", action="store_true", help="every registered product, and the sessions of none")
    a.add_argument("--project", dest="stats_project", metavar="P", help="this product (as foreman --project P)")
    a.add_argument("--by", choices=BY, help="one table grouped by this (default: a summary)")
    a.add_argument("--session", help="one session (its id or a prefix), by agent; the whole session unless --since")
    a.add_argument("--top", type=int, default=10, help="rows of the grouped table (default 10; 0: all)")
    a.add_argument("--json", action="store_true", help="the same figures as JSON")
    a.add_argument("--usage", action="store_true", help='also the text of `claude -p "/usage"` (runs claude)')


def run(args: argparse.Namespace, project_name: str | None) -> int:
    name = args.stats_project or project_name
    try:
        registered = config.registered_projects()
    except config.ConfigError:
        registered = []
    scope = None  # every session, of every product or none
    if not args.all and not (args.session and not name):
        try:
            p = config.resolve(name)
        except config.ConfigError as e:
            print(f"foreman-dev stats: {e} (or --all)", file=sys.stderr)
            return 2
        if p.name not in {q.name for q in registered}:
            registered.append(p)
        scope = {p.name}
    now = datetime.now().astimezone()
    try:
        since = (
            util.parse_when(args.since, now) if args.since else (None if args.session else now - timedelta(hours=24))
        )
        until = util.parse_when(args.until, now) if args.until else None
    except ValueError:
        print(
            f"foreman-dev stats: --since/--until {args.since or args.until!r}: use 24h, 7d, 30m, HH:MM, a date "
            "(YYYY-MM-DD) or a date and time",
            file=sys.stderr,
        )
        return 2
    if args.top < 0:
        print("foreman-dev stats: --top is 0 (all) or more", file=sys.stderr)
        return 2
    ctx = Context(registered)
    units, versions = collect(ctx, scope, since, until, args.session)
    if args.session and len({u["session"] for u in units}) > 1:
        ids = sorted({u["session"] for u in units})
        print(
            f"foreman-dev stats: --session {args.session} matches {len(ids)} sessions: {', '.join(ids)}",
            file=sys.stderr,
        )
        return 2
    total = blank()
    for u in units:
        merge(total, u["tot"])
    by = args.by or ("agent" if args.session else None)
    views = [by] if by else (["project"] if scope is None else []) + ["kind", "type", "model", "session"]
    tables: dict[str, tuple[list[Row], int]] = {}
    for v in views:
        rows = group(units, v)
        top = args.top if (by or v == "session") else 0
        tables[v] = (rows[:top] if top else rows, len(rows))
    no_record = [u for u in units if u["kind"] == "agent" and u["issue_from"] != "launch"]
    guessed = sum(1 for u in no_record if u["issue_from"] == "guess")
    unknown = sorted(v for v in versions if v not in transcripts.KNOWN_VERSIONS)
    usage = claude_usage() if args.usage else None
    head = {
        "products": sorted(scope) if scope is not None else "all",
        "session": args.session,
        "since": since.isoformat(timespec="seconds") if since else None,
        "until": (until or now).isoformat(timespec="seconds"),
    }
    if args.json:
        out = {
            **head,
            "by": by,
            "total": total,
            "sessions": len({u["session"] for u in units}),
            "agents": sum(1 for u in units if u["kind"] == "agent"),
            "tables": {v: [flat(r) for r in rows] for v, (rows, _) in tables.items()},
            "claude_code": {
                u["session"]: claude_code(u["cost"]) for u in units if u["kind"] == "session" and claude_code(u["cost"])
            },
            "agents_without_launch_record": {
                "agents": len(no_record),
                "issue_guessed": guessed,
                "no_issue": len(no_record) - guessed,
            },
            "untested_claude_code_versions": unknown,
        }
        if usage is not None:  # args.usage
            out["usage"] = {"text": usage[0], "error": usage[1]}
        print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
        return 0
    what = ("every product" if scope is None else ", ".join(sorted(scope))) + (
        f", session {args.session}" if args.session else ""
    )
    window = f"{_local(since)} → {_local(until or now)}" if since else f"whole session → {_local(until or now)}"
    print(f"foreman-dev stats: {what}, {window} (Claude Code transcripts)")
    print(
        f"total: {count(total['calls'])} calls, cache read {human(total['cache_read'])}, cache write "
        f"{human(total['cache_write'])}, output {human(total['output'])}, input {human(total['input'])} "
        f"({plural(len({u['session'] for u in units}), 'session')}, "
        f"{plural(sum(1 for u in units if u['kind'] == 'agent'), 'agent')})"
    )
    for v, (rows, n) in tables.items():
        if not rows:
            continue
        print()
        if v == "session" and not by:
            print(f"largest sessions ({len(rows)} of {n}):" if n > len(rows) else "sessions:")
        print("\n".join(render(v, rows, total["cache_read"], scope is None)))
        if by and n > len(rows):
            print(f"… {n - len(rows)} more rows (--top 0: all)")
        cc = cc_lines(rows if v == "session" else group(units, "session") if args.session else [])
        if cc:
            print("\n".join(cc))
    notes: list[str] = []
    if not units:
        notes.append("no model call in the window")
    if no_record:
        notes.append(
            f"{len(no_record)} agents without a launch record: {guessed} matched an issue by their name, "
            f"brief or description (marked ?), {len(no_record) - guessed} counted under «{NO_ISSUE}»"
        )
    if scope is not None:
        notes.append("sessions opened outside the product's folders (home, repos, worktrees) are not counted")
    if unknown:
        notes.append(
            f"Claude Code {', '.join(unknown)}: a transcript format this foreman was not tested with "
            f"(tested: {', '.join(transcripts.KNOWN_VERSIONS)}); figures may be off"
        )
    if notes:
        print()
        for n in notes:
            print(f"note: {n}")
    if usage is not None:  # args.usage
        print()
        print("claude /usage:")
        print(usage[0] if usage[0] is not None else f"  (not available: {usage[1]})")
    return 0
