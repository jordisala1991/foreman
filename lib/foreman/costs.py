"""What each agent run cost, kept with the backlog (`costs.jsonl` beside backlog.json, one line per agent id).

- **Recorded** from the agent's transcript (transcripts.usage: responses once per id) at its delivery (`foreman
  progress --done`: `record`), for one that stopped without delivering when the loop pass sees it (`sweep`), and for
  older runs by `backfill`, which finds the issue and sub-issue in the launch line of the brief. A resumed agent's
  line is replaced by its totals (the transcript is cumulative) with `deliveries` counted: rework is a delivery
  after the first of the same sub-issue, by the same agent (`deliveries` > 1) or by another one.
- **Priced** at list prices of the Claude API (PRICES, $ per million tokens, one table, by model family); the price an
  API user would pay, not what a subscription charges: it serves to compare. Haiku's higher rate above 100K tokens
  of prompt is not applied.
- **Read** by `foreman agents --cost` (`rows`, `text`) and the panel's issue page (`of_issue`).
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import TYPE_CHECKING, Any

from . import transcripts, util

if TYPE_CHECKING:
    from collections.abc import Iterable

    from .config import Project

Run = dict[str, Any]  # one line of costs.jsonl, see `build`

# $ per million tokens: Anthropic's published list prices (platform.claude.com/docs/en/about-claude/pricing), read on
# 2026-11-06. Sonnet is half of Opus. Keys: input, 5-minute cache write, 1-hour cache write, cache read, output.
PRICES = {
    "opus": (4.0, 5.0, 8.0, 0.20, 20.0),
    "sonnet": (2.0, 2.5, 4.0, 0.10, 10.0),
    "haiku": (0.10, 0.125, 0.20, 0.01, 0.50),
}
CHEAP = ("sonnet", "haiku")
BY = ("issue", "sub", "type", "model", "week", "day")
LAUNCH = re.compile(r"\[foreman launch [a-z2-7]{10}\] [^·\n]*· ([A-Z]+-\d+)(?: ([A-Z]\d+[\w+–-]*))?")


def family(model: str | None) -> str | None:
    """opus, sonnet or haiku, from a model id; None for any other."""
    m = (model or "").lower()
    return next((f for f in PRICES if f in m), None)


def price(model: str | None, u: dict[str, int]) -> float:
    """The list-price cost in $ of a usage dict ({input, cache_write, cache_write_1h, cache_read, output}); 0 for a
    model of no known family."""
    p = PRICES.get(family(model) or "")
    if not p:
        return 0.0
    w5 = u["cache_write"] - u["cache_write_1h"]
    return (
        u["input"] * p[0] + w5 * p[1] + u["cache_write_1h"] * p[2] + u["cache_read"] * p[3] + u["output"] * p[4]
    ) / 1e6


def tokens(u: dict[str, int]) -> int:
    return u["input"] + u["cache_write"] + u["cache_read"] + u["output"]


def file_of(project: Project) -> Any:
    from .backlog import Backlog

    return Backlog(project).data_path.parent / "costs.jsonl"


def build(
    path: str, aid: str, issue: str | None, sub: str | None, type_: str | None, name: str | None, outcome: str | None
) -> Run | None:
    """The line for one agent's transcript, None when it holds no response."""
    used, first, last = transcripts.usage(path)
    if not used:
        return None
    models = {m: {**u, "tokens": tokens(u), "usd": round(price(m, u), 4)} for m, u in sorted(used.items())}
    main = max(models, key=lambda m: models[m]["usd"] or models[m]["tokens"])
    minutes = round((last - first).total_seconds() / 60, 1) if first and last else None
    return {
        "agent": aid,
        "name": name,
        "issue": issue,
        "sub": sub,
        "type": (type_ or "").split(":", 1)[-1] or None,
        "model": main,
        "outcome": outcome,
        "start": first.astimezone().strftime("%Y-%m-%d %H:%M") if first else None,
        "end": last.astimezone().strftime("%Y-%m-%d %H:%M") if last else None,
        "minutes": minutes,
        "deliveries": 1 if outcome else 0,
        "tokens": sum(m["tokens"] for m in models.values()),
        "usd": round(sum(m["usd"] for m in models.values()), 4),
        "models": models,
    }


def store(project: Project, runs: Iterable[Run]) -> None:
    """Adds the lines, replacing those of the same agent (its `deliveries` kept from the old one, +1 when the new
    one has an outcome the old had too)."""
    new = list(runs)
    if not new:
        return
    f = file_of(project)
    f.parent.mkdir(parents=True, exist_ok=True)
    with util.file_lock(f):
        old = {r["agent"]: r for r in util.read_jsonl(f) if isinstance(r, dict) and r.get("agent")}
        for r in new:
            o = old.get(r["agent"])
            if o and r["outcome"]:
                r["deliveries"] = (o.get("deliveries") or 0) + 1 if o["outcome"] else max(1, o.get("deliveries") or 0)
            elif o:
                r["deliveries"] = o.get("deliveries") or 0
                r["outcome"] = o.get("outcome")
            old[r["agent"]] = r
        util.write_text_atomic(f, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in old.values()))


def runs(project: Project) -> list[Run]:
    return [r for r in util.read_jsonl(file_of(project)) if isinstance(r, dict)]


def of_entry(h: dict[str, Any], aid: str, outcome: str | None) -> Run | None:
    path = transcripts.subagent_file(aid, h.get("transcript")) or h.get("transcript")
    if not path:
        return None
    return build(path, aid, h.get("issue"), h.get("sub"), h.get("type"), h.get("name"), outcome)


def record(project: Project, aid: str | None, name: str | None, outcome: str | None) -> None:
    """At `progress --done`: the delivering agent's line. Never raises (a cost is no reason to refuse a delivery)."""
    from . import agents as agents_mod

    try:
        d = agents_mod.agents(project)
        mine = [k for k, h in d.items() if k == aid or (not aid and name and h.get("name") == name)]
        if mine:
            k = max(mine, key=lambda k: str(d[k].get("start") or ""))
            if d[k].get("issue"):
                store(project, [r for r in [of_entry(d[k], k, outcome)] if r])
    except Exception:  # noqa: BLE001
        pass


def sweep(project: Project) -> None:
    """In the pass: a line for each agent tied to an issue that is not working and has none yet (it stopped without
    delivering, or its delivery came without an id). Never raises."""
    from . import agents as agents_mod

    try:
        have = {r.get("agent") for r in runs(project)}
        todo = [
            (aid, h)
            for aid, h in agents_mod.agents(project).items()
            if h.get("issue") and aid not in have and agents_mod.agent_state(h) not in agents_mod.LIVE_STATES
        ]
        made = [of_entry(h, aid, "stopped" if agents_mod.agent_state(h) == "stopped" else None) for aid, h in todo]
        store(project, [r for r in made if r])
    except Exception:  # noqa: BLE001
        pass


def backfill(project: Project, since: str) -> int:
    """Lines for every subagent transcript of the machine changed on or after `since` (YYYY-MM-DD) whose brief has a
    launch line of an issue of this project; the agent's type from its .meta.json, its outcome from progress.jsonl.
    The number stored."""
    from .backlog import Backlog

    day = datetime.strptime(since, "%Y-%m-%d").timestamp()
    ids = Backlog(project).new_issue_id
    done = {}
    for ln in util.read_jsonl(project.state / "progress.jsonl"):
        if ln.get("done") and ln.get("agent_id"):
            done[ln["agent_id"]] = ln.get("outcome") or "ok"
    out = []
    for f in sorted(transcripts.claude_projects_dir().glob("*/*/subagents/agent-*.jsonl")):
        try:
            if f.stat().st_mtime < day:
                continue
        except OSError:
            continue
        m = LAUNCH.search(transcripts.head(f, 4000))
        if not m or not ids.match(m.group(1)):
            continue
        aid = f.stem[len("agent-") :]
        meta = util.read_json(f.with_suffix(".meta.json"), {})
        out.append(build(str(f), aid, m.group(1), m.group(2), meta.get("agentType"), None, done.get(aid)))
    runs_ = [r for r in out if r]
    store(project, runs_)
    return len(runs_)


def _items(r: Run, by: str) -> list[tuple[str, dict[str, Any]]]:
    """The (group key, {tokens, cheap, usd}) parts of one line for a grouping."""
    if by == "model":
        return [(family(m) or m, u) for m, u in r["models"].items()]
    cheap = sum(u["tokens"] for m, u in r["models"].items() if family(m) in CHEAP)
    whole = {"tokens": r["tokens"], "usd": r["usd"], "cheap": cheap}
    if by == "week":
        d = (r.get("end") or "")[:10]
        k = "{}-W{:02d}".format(*datetime.strptime(d, "%Y-%m-%d").isocalendar()[:2]) if d else "?"
    else:
        k = (r.get("end") or "?")[:10] if by == "day" else r.get(by) or "-"
    return [(k, whole)]


def rows(project: Project, by: str = "issue", since: str | None = None, until: str | None = None) -> dict[str, Any]:
    """{"by", "rows": [{key, runs, rework, minutes, tokens, usd, cheap_share}], "total": same}: the lines whose `end`
    day is in [since, until), grouped by `by` (BY), the dearest first. `cheap_share`: the share of tokens on Sonnet or
    Haiku. `rework`: the deliveries of a sub-issue after its first (an agent resumed, or more agents of the same
    sub-issue, not counting reviewers and planners), put on the group of its first line."""
    rs = [
        r
        for r in runs(project)
        if (not since or (r.get("end") or "") >= since) and (not until or (r.get("end") or "") < until)
    ]
    new = lambda k: {"key": k, "runs": 0, "rework": 0, "minutes": 0.0, "tokens": 0, "usd": 0.0, "cheap": 0}  # noqa: E731
    acc: dict[str, dict[str, Any]] = {}
    subs: dict[tuple[Any, Any], list[Any]] = {}  # (issue, sub) -> [group of its first line, deliveries]
    for r in rs:
        parts = _items(r, by)
        for k, u in parts:
            a = acc.setdefault(k, new(k))
            a["runs"] += 1
            a["minutes"] += (r.get("minutes") or 0) if by != "model" else 0
            a["tokens"] += u["tokens"]
            a["usd"] += u["usd"]
            a["cheap"] += u["cheap"] if "cheap" in u else (u["tokens"] if k in CHEAP else 0)
        if r.get("sub") and r.get("type") not in ("reviewer", "planner"):
            s = subs.setdefault((r["issue"], r["sub"]), [parts[0][0], 0])
            s[1] += max(1, r.get("deliveries") or 0)
    for group, n in subs.values():
        acc[group]["rework"] += n - 1
    out = sorted(acc.values(), key=lambda a: -a["usd"])
    tot = new("total")
    for a in out:
        for k in ("runs", "rework", "minutes", "tokens", "usd", "cheap"):
            tot[k] += a[k]
    for a in [*out, tot]:
        a["usd"], a["minutes"] = round(a["usd"], 2), round(a["minutes"], 1)
        a["cheap_share"] = round(100 * a.pop("cheap") / a["tokens"], 1) if a["tokens"] else 0.0
    return {"by": by, "rows": out, "total": tot}


def text(res: dict[str, Any]) -> str:
    head = f"{res['by']:<16}{'runs':>5}{'rework':>7}{'min':>7}{'MTok':>8}{'$ list':>9}{'Sonnet+Haiku':>14}"
    lines = [head]
    for a in [*res["rows"], res["total"]]:
        lines.append(
            f"{str(a['key'])[:15]:<16}{a['runs']:>5}{a['rework']:>7}{a['minutes']:>7.0f}{a['tokens'] / 1e6:>8.1f}"
            f"{a['usd']:>9.2f}{a['cheap_share']:>13.1f}%"
        )
    return "\n".join(lines)


def by_issue(project: Project) -> dict[str, str]:
    """{issue id: «$ 12.40 · 31 MTok · 5 runs · 3 % on Sonnet or Haiku»} for the panel's issue page."""
    return {
        a[
            "key"
        ]: f"$ {a['usd']:.2f} · {a['tokens'] / 1e6:.1f} MTok · {a['runs']} runs · {a['cheap_share']:.0f} % Sonnet/Haiku"
        for a in rows(project, "issue")["rows"]
        if a["key"] != "-"
    }
