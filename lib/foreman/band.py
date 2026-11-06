"""The orchestrator's band: a short summary of the project's state, in the project's language, that foreman's hooks
module draws above the prompt of the marked orchestrator session (an `AbovePrompt` band) and prints for
`/foreman:status`.

lines(project) gives {"level", "rows"}: `level` is "bad" (the pass is late, no orchestrator session is marked, or
the band could not be read), "ask" (a question waits for the owner) or "ok"; `rows` are two lists of parts, each
part {"text", "role", "prio"} (and "n" for the `more` part). The module draws the parts of a row in their order and,
when they do not all fit, leaves out whole parts from the highest `prio` down (prio 0 never goes). Roles: "dot" (the
level as a coloured dot), "ask" (the owner's questions), "bad", "warn", "plain", "dim" and "more" (how many jobs are
not shown: its text keeps «{n}» for the module to fill with `n` plus the jobs it left out itself).
From what foreman already keeps (no transcript is read beyond the briefs the pass reads too):
1. the dot; the owner's questions not answered yet (the backlog's pending `decisions`: an answer from the panel
   resolves its decision), only when there are some; «no orchestrator session marked», or «pass late since HH:MM»
   when no pass came for orchestrator.loop_limit_min (the `loop_stale` rule without looking at processes); the marked
   session's memory against [loop] context_rotate («memory 121K of 200K», "warn" from MEMORY_WARN of it; «memory —»
   without a measure or before the first response after a renewal) and «renewed HH:MM» for an hour after a renewal;
   the branches ready to integrate (loop.ready_branches, the pass's own rule), only when there are some, and apart
   from them the measurements delivered to close (`integrate --measure`; the same rule, kind "measure"); the next
   pass ([loop] every_min after the last; «now» when due; nothing without a [loop] prompt; «no pass yet»); «foreman»;
2. the live jobs (loop.live_agents), oldest first: «short name (ISSUE Tn) N min», the short name being the issue's
   `short` field or its title trimmed (backlog.short_title); an agent without an issue by its name; at most
   MAX_AGENTS, then the `more` part (always, with `n` 0 when all are shown: the module counts in it the jobs it
   leaves out when the row does not fit); «nothing running» without any.

text(project) is the same parts, all of them (a `more` part of 0 left out), joined with « · », one row per line, the
dot said as a word (i18n `band_level_*`): `/foreman:status` and `hook mod status`. The module gets lines() with each
tick of the marked session (hooks_mod.tick, `band`), so the band costs no call of its own. lines() never raises: a
part it cannot read is left out, and a failure as a whole is one row with the dot and the error (i18n
`band_failed`), level "bad"."""

from __future__ import annotations

import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

from . import hooks_mod, i18n, loop, notes, orchestrator, util
from .backlog import short_title

if TYPE_CHECKING:
    from .config import Project
    from .identity import Agent, Identity
    from .orchestrator import Mark

Part = dict[str, Any]  # {"text", "role", "prio"} and "n" for the `more` part
Band = dict[str, Any]  # {"level": "ok"|"ask"|"bad", "rows": [[Part]]}

MAX_AGENTS = 3
MEMORY_WARN = 0.9  # the memory part is "warn" from this share of [loop] context_rotate
DOT = "●"
SEP = " · "

# What goes first when a row does not fit (the module drops the highest prio first; 0 never goes): «foreman», the
# renewal time, the next pass, the branches to integrate, the memory. To drop the memory before the next pass, swap
# PRIO_NEXT_PASS and PRIO_MEMORY. In the jobs row the newest job goes first (PRIO_JOBS + its place).
PRIO_KEEP = 0  # the dot, the questions, what is wrong, the `more` part
PRIO_JOBS = 1
PRIO_MEMORY = 2
PRIO_READY = 3
PRIO_NEXT_PASS = 4
PRIO_RENEWED = 6
PRIO_FOREMAN = 7


def _hhmm(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).strftime("%H:%M")


def _k(n: int) -> str:
    return f"{round(n / 1000)}K"


def part(text: str, role: str, prio: int, **extra: Any) -> Part:
    return {"text": text, "role": role, "prio": prio, **extra}


def pass_part(project: Project, s: dict[str, Any], o: Mark | None, now: float) -> Part | None:
    """«next pass 11:05» or «next pass now»; «no pass yet»; «pass late since 10:05" ("bad") when the marked session
    had no pass for orchestrator.loop_limit_min; None without a [loop] prompt."""
    if not orchestrator.watches_loop(project):
        return None
    last = util.iso_epoch(notes.heartbeats(project).get("loop"))
    if o:
        _, window = orchestrator.pass_window(project, o)
        if window is not None and now - window > orchestrator.loop_limit_min(project) * 60:
            return part(s["band_late"].format(time=_hhmm(last if last is not None else window)), "bad", PRIO_KEEP)
    if last is None:
        return part(s["band_no_pass"], "plain", PRIO_NEXT_PASS)
    due = last + hooks_mod.every_min(project) * 60
    text = s["band_next_pass_due"] if due <= now else s["band_next_pass"].format(time=_hhmm(due))
    return part(text, "plain", PRIO_NEXT_PASS)


RENEWED_SHOWN_S = 3600  # the band says the session's last renewal for this long


def memory_parts(project: Project, s: dict[str, Any], o: Mark | None, now: float) -> list[Part]:
    """The marked session's memory against [loop] context_rotate: «memory 121K of 200K» ("warn" from MEMORY_WARN of
    it), «memory 121K» when the threshold is 0, «memory —» without a measure or before the first response after a
    renewal; then «renewed HH:MM» for an hour after the session renewed its memory (renew.py)."""
    from . import renew

    m = hooks_mod.read_measure(project, o["session"]) if o else None
    tokens = m.get("tokens") if m else None
    limit = project.section("loop").get("context_rotate", 200000)
    limit = limit if isinstance(limit, int) and not isinstance(limit, bool) and limit > 0 else 0
    if not isinstance(tokens, int) or isinstance(tokens, bool):
        out = [part(s["band_memory_bare"].format(tokens="—"), "plain", PRIO_MEMORY)]
    elif limit:
        role = "warn" if tokens >= MEMORY_WARN * limit else "plain"
        out = [part(s["band_memory"].format(tokens=_k(tokens), limit=_k(limit)), role, PRIO_MEMORY)]
    else:
        out = [part(s["band_memory_bare"].format(tokens=_k(tokens)), "plain", PRIO_MEMORY)]
    at = renew.compacted_at(project, o["session"]) if o else None
    if at is not None and now - at <= RENEWED_SHOWN_S:
        out.append(part(s["band_renewed"].format(time=_hhmm(at)), "dim", PRIO_RENEWED))
    return out


def questions_part(s: dict[str, Any], n: int) -> Part | None:
    """«2 questions for you», «1 question for you»; None without any."""
    if n <= 0:
        return None
    return part((s["band_questions_1"] if n == 1 else s["band_questions_n"]).format(n=n), "ask", PRIO_KEEP)


def job_parts(
    project: Project, live: list[Agent], ids: dict[str, Identity], titles: dict[str, str], s: dict[str, Any], now: float
) -> list[Part]:
    """The live jobs as «short name (ISSUE Tn) 12 min», oldest first; the newest goes first when the row does not
    fit. At most MAX_AGENTS, then the `more` part, `n` 0 when all are shown; «nothing running» without any.
    `titles` are the short names of the backlog's issues; an issue not in it, or an agent without one, goes by the
    agent's name."""
    out = []
    for n, h in enumerate(sorted(live, key=lambda h: h.get("start") or "")):
        who = ids[h["id"]]
        issue = who.get("issue")
        name = (titles.get(issue) if issue else None) or who["name"] or h["id"][:8]
        text = name + (f" ({issue}" + (f" {who['sub']}" if who.get("sub") else "") + ")" if issue else "")
        start = util.iso_epoch(h.get("start"))
        if start is not None:
            text += " " + s["band_minutes"].format(n=max(0, int((now - start) / 60)))
        out.append(part(text, "dim", PRIO_JOBS + n))
    if not out:
        return [part(s["band_nothing"], "dim", PRIO_JOBS)]
    return out[:MAX_AGENTS] + [part(s["band_more"], "more", PRIO_KEEP, n=max(0, len(out) - MAX_AGENTS))]


def lines(project: Project, now: float | None = None) -> Band:
    """The band (module docstring); never raises."""
    s = i18n.for_project(project)
    try:
        return _lines(project, s, time.time() if now is None else now)
    except Exception as e:  # noqa: BLE001 (the band never fails the tick)
        why = f"{type(e).__name__}: {e}"[:160]
        return {
            "level": "bad",
            "rows": [[part(DOT, "dot", PRIO_KEEP), part(s["band_failed"].format(why=why), "bad", PRIO_KEEP)]],
        }


def _lines(project: Project, s: dict[str, Any], now: float) -> Band:
    o = orchestrator.current(project)
    snap = loop.Snapshot(project)
    asks, ready, measure = None, 0, 0
    titles: dict[str, str] = {}
    if snap.data is not None:  # else the pass says the backlog cannot be read; the band leaves its parts out
        asks = questions_part(s, len(snap.data.get("decisions") or []))
        ready = len(loop.ready_branches(snap))
        measure = len(loop.ready_branches(snap, kind="measure"))
        titles = {i["id"]: short_title(i) for i in snap.issues(archived=False)}
    pass_ = pass_part(project, s, o, now)
    first = [part(DOT, "dot", PRIO_KEEP)]
    if asks:
        first.append(asks)
    if not o:
        first.append(part(s["band_no_orchestrator"], "bad", PRIO_KEEP))
    if pass_ is not None and pass_["role"] == "bad":
        first.append(pass_)
    first += memory_parts(project, s, o, now)
    if ready:
        first.append(part(s["band_ready"].format(n=ready), "plain", PRIO_READY))
    if measure:
        first.append(part(s["band_measure"].format(n=measure), "plain", PRIO_READY))
    if pass_ is not None and pass_["role"] != "bad":
        first.append(pass_)
    first.append(part("foreman", "dim", PRIO_FOREMAN))
    bad = any(p["role"] == "bad" for p in first)
    level = "bad" if bad else "ask" if asks else "ok"
    return {"level": level, "rows": [first, job_parts(project, snap.live, snap.ids, titles, s, now)]}


def part_text(p: Part) -> str:
    """A part's text as drawn: the `more` part with its number."""
    return p["text"].replace("{n}", str(p.get("n", 0))) if p.get("role") == "more" else p["text"]


def text(project: Project, now: float | None = None) -> str:
    """The band as plain text (`/foreman:status`): every part, joined with « · », one row per line, the dot said as
    a word (i18n `band_level_*`)."""
    s = i18n.for_project(project)
    band = lines(project, now)
    word = s[f"band_level_{band['level']}"]
    return "\n".join(
        SEP.join(word if p["role"] == "dot" else part_text(p) for p in row if p["role"] != "more" or p.get("n"))
        for row in band["rows"]
    )
