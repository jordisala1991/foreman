"""What each agent cost, from its session transcript (`~/.claude/projects/*/<session>.jsonl`, its subagents in
`<session>/subagents/*.jsonl`), priced at the Claude API list prices: a figure to compare, not a bill. Adapted from
foreman's costs.py and transcripts.py: a response is counted once per message id; unreadable lines are skipped."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

# $ per million tokens: input, cache write (5 min), cache write (1 h), cache read, output. As in foreman's costs.py.
PRICES = {
    "opus": (4.0, 5.0, 8.0, 0.20, 20.0),
    "sonnet": (2.0, 2.5, 4.0, 0.10, 10.0),
    "haiku": (0.10, 0.125, 0.20, 0.01, 0.50),
}


def projects_dir() -> Path:
    return Path(os.environ.get("FM_CLAUDE_PROJECTS") or Path.home() / ".claude/projects")


def files_of(sid: str) -> list[Path]:
    if not sid or "/" in sid:
        return []
    out = []
    for f in projects_dir().glob(f"*/{sid}.jsonl"):
        out.append(f)
        out += sorted((f.parent / sid / "subagents").glob("*.jsonl"))
    return out


def family(model: str) -> str | None:
    return next((k for k in PRICES if k in (model or "").lower()), None)


def usage(files: list[Path]) -> dict[str, Any]:
    """{usd, tokens, calls, first, last, models: {model: usd}} over the files."""
    seen: set[str] = set()
    usd = 0.0
    tokens = calls = 0
    models: dict[str, float] = {}
    first = last = None
    for f in files:
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for ln in lines:
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            m = r.get("message") if isinstance(r, dict) else None
            if r.get("type") != "assistant" or not isinstance(m, dict) or not isinstance(m.get("usage"), dict):
                continue
            mid = m.get("id") or ""
            if mid in seen or m.get("model") == "<synthetic>":
                continue
            seen.add(mid)
            u = m["usage"]
            cw = u.get("cache_creation_input_tokens") or 0
            cw1h = ((u.get("cache_creation") or {}).get("ephemeral_1h_input_tokens") or 0)
            inp, cr, out = u.get("input_tokens") or 0, u.get("cache_read_input_tokens") or 0, u.get("output_tokens") or 0
            p = PRICES.get(family(m.get("model", "")) or "")
            cost = (inp * p[0] + (cw - cw1h) * p[1] + cw1h * p[2] + cr * p[3] + out * p[4]) / 1e6 if p else 0.0
            usd += cost
            tokens += inp + cw + cr + out
            calls += 1
            models[m.get("model", "?")] = round(models.get(m.get("model", "?"), 0) + cost, 4)
            ts = r.get("timestamp")
            if ts:
                t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                first = t if first is None or t < first else first
                last = t if last is None or t > last else last
    return {"usd": round(usd, 4), "tokens": tokens, "calls": calls, "models": models,
            "minutes": round((last - first).total_seconds() / 60, 1) if first and last else 0}


def of_sessions(sids: list[str]) -> dict[str, Any]:
    return usage([f for s in sids for f in files_of(s)])
