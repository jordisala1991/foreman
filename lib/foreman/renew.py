"""The orchestrator's memory renewed in place: the same session compacts and gets foreman's pack back.

When the context of the marked orchestrator's session reaches [loop] context_rotate, the session renews itself
in place (docs/orchestrator.md, «Renewal»); no new session takes over:

1. The `context_rotate` notice asks the model to put in the board whatever only its memory holds and to run
   `foreman renew` (request(): <state>/renew.json {session, at, note?, now}). Only the marked session may ask.
2. A tick of foreman's hooks module in that session decides (due()): a request of this session, or none and the
   context at FORCE_FACTOR × context_rotate (the safety trigger, no model turn); no main-loop turn (`busy` false;
   a held tick, with a pass under way, never gets here: hooks_mod.tick answers it first), no attempt still open,
   none started in the last COOLDOWN_S (`--now` skips that); after a renewal that freed too little (trouble()), only
   the safety trigger or `--now`; and no live agent of the session (foreman's and the module's `$.agent.list()`; a
   tick without `agents` cannot tell and counts as one, _live), unless the context is at FORCE_FACTOR ×
   context_rotate or the request said `--now`. The attempt is written first
   ({at, session, id, state: "asked", why, tokens_before, note?} in <state>/renewals.jsonl, the request removed),
   then the tick answers `renew` (the id) with `instructions`; the module runs `/compact <instructions>` with
   `$.command.run` from its timer. Claude Code's own compaction runs (a summariser call over the whole context):
   a user-tier `session.compact` hook never runs on a machine with managed settings, and `$.session.compact` is
   refused in an SDK host, so the module neither answers the compaction nor uses that call.
3. The classic SessionStart hook with source `compact` (`foreman hook resume`, hooks.hook_resume) fires after
   every compaction, in a terminal and in an SDK host alike: in the marked session it prints pack() (the model reads
   it as the hook's context) and settles an open attempt as `compacted` (compacted()). The pack also comes back
   after a compaction foreman did not ask for (Claude Code's automatic one, the owner's /compact).
4. The first measure after it (`hook mod measure`; null until the first response) closes it (measured()): state
   `ok` with `tokens_after`, the note «HH:MM Renewal (session NAME): 208K → 14K» and the owner's line; at
   LOW_FACTOR × context_rotate or more it says the renewal freed too little: the next automatic one waits for the
   safety trigger.
5. A pass is due right after a renewal (hooks_mod.pass_due: a renewal compacted after the last pass), and it goes
   to the model even with nothing actionable (hooks_mod.renewed_since_pass): `/compact` leaves no turn behind, so
   that pass is the session's next one. Until a turn measures the new context, the measure from before the
   compaction does not count as the session's context (measure()).

Failures: the `/compact` call rejected (`hook mod renewed` with `error`), or no compaction within WAIT_S (RAN_S once
the call settled): `failed`. Two in a row, or a renewal that freed too little, and trouble() says why: only for
the texts (the `context_rotate` notice, the owner's line, `foreman session`); nothing stops trying. After two
failures the next attempt comes COOLDOWN_S later, by request or at FORCE_FACTOR × context_rotate, and the owner can
type /compact in the session meanwhile (the pack comes back the same). COOLDOWN_S keeps any two attempts apart.
Below all this, Claude Code's own automatic compaction near the end of the window, which foreman also answers.

What the pack holds (J1: foreman's pack, with a minimal summary from the engine): a head (who the session still is,
where the transcript before is), the model's note (`foreman renew --note`), the owner's last OWNER_MESSAGES
messages verbatim (read from the transcript the hook names: prompts a person typed, no loop pass, no notification,
no message from another agent) and the orchestrator's last answer to them, the summary of `foreman start` in its
renewal mode (board, active backlog, the owner's open decisions, resources, live agents), and the rules file. The
whole of it is written to <state>/renewal-pack.md; what the hook prints is cut at PACK_MAX_CHARS (a hook's context
is held to about 10,000 characters), never inside the head, the note or the owner's messages, and then says where
the rest is.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from typing import TYPE_CHECKING, Any

from . import heartbeat, i18n, notes, transcripts, util

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from .config import Project

Attempt = dict[str, Any]  # {at, session, id, state, why, tokens_before, tokens_after?, note?, error?}

COOLDOWN_S = 30 * 60  # two attempts of a session at least this far apart (`--now` skips it)
WAIT_S = 10 * 60  # an attempt with no compaction after this long failed
RAN_S = 2 * 60  # … or this long after the `/compact` call settled with no compaction
FORCE_FACTOR = 1.5  # the context at this × context_rotate: renew with live agents, and with no request
LOW_FACTOR = 0.8  # a renewal that leaves this × context_rotate or more freed too little
OWNER_MESSAGES = 5
OWNER_CHARS = 1000  # each owner message in the pack, and the last answer
PACK_MAX_CHARS = 9500  # what the hook prints


class RenewError(Exception):
    """A request `foreman renew` refuses (the message says why)."""


def renew_file(project: Project) -> Path:
    return project.state / "renew.json"


def renewals_file(project: Project) -> Path:
    return project.state / "renewals.jsonl"


def pack_file(project: Project) -> Path:
    return project.state / "renewal-pack.md"


def limit(project: Project) -> int:
    """[loop] context_rotate (0: off)."""
    n = project.section("loop").get("context_rotate", 200000)
    return n if isinstance(n, int) and not isinstance(n, bool) and n > 0 else 0


def _stamp(now: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now))


def measure(project: Project, session: str) -> dict[str, Any] | None:
    """The session's measure (hooks_mod.read_measure) as its current context, or None: none written, or one taken
    before the compaction of the session's last renewal (compacted_at). Claude Code measures only after a turn, and
    a `/compact` with no turn after it leaves the figure from before it; counted, it kept the session above [loop]
    context_rotate, so every pass after the renewal was skipped until the owner wrote."""
    from . import hooks_mod

    m = hooks_mod.read_measure(project, session)
    if m is None:
        return None
    at, renewed = util.iso_epoch(m.get("at")), compacted_at(project, session)
    if renewed is not None and (at is None or at < renewed):
        return None
    return m


def _tokens(project: Project, session: str) -> int | None:
    n = (measure(project, session) or {}).get("tokens")
    return n if isinstance(n, int) and not isinstance(n, bool) else None


def k(n: object) -> str:
    """208558 → «208K»; «?» for anything else."""
    return f"{n // 1000}K" if isinstance(n, int) and not isinstance(n, bool) else "?"


# ---------- the record ----------


def _lines(project: Project) -> list[dict[str, Any]]:
    return [
        d
        for d in util.read_jsonl(renewals_file(project))
        if isinstance(d.get("session"), str) and isinstance(d.get("id"), str)
    ]


def _append(project: Project, rec: dict[str, Any]) -> None:
    f = renewals_file(project)
    f.parent.mkdir(parents=True, exist_ok=True)
    with util.file_lock(f), open(f, "a") as out:
        out.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")


def attempts(project: Project, session: str) -> list[Attempt]:
    """The session's attempts, oldest first, each with its fields merged line by line (`at` stays the first one's;
    `settled_at` is the last line's)."""
    out: dict[str, Attempt] = {}
    for d in _lines(project):
        if d["session"] != session:
            continue
        if d["id"] not in out:
            out[d["id"]] = dict(d)
        else:
            out[d["id"]].update({**{x: v for x, v in d.items() if x != "at"}, "settled_at": d.get("at")})
    return sorted(out.values(), key=lambda a: a.get("at") or "")


def state_of(a: Attempt, now: float | None = None) -> str:
    """ "asked", "compacted", "ok" or "failed"; an attempt still asked WAIT_S after it was written, or RAN_S after its
    call settled, failed."""
    state = a.get("state")
    if state in ("compacted", "ok", "failed"):
        return state
    now = time.time() if now is None else now
    at, ran = util.iso_epoch(a.get("at")), util.iso_epoch(a.get("ran_at"))
    if at is None or now - at > WAIT_S or (ran is not None and now - ran > RAN_S):
        return "failed"
    return "asked"


def last(project: Project, session: str | None) -> Attempt | None:
    """The session's newest attempt with its state (`state` as state_of says), or None."""
    if not session:
        return None
    a = attempts(project, session)
    return {**a[-1], "state": state_of(a[-1])} if a else None


def done_today(project: Project, session: str) -> int:
    today = time.strftime("%Y-%m-%d")
    return sum(
        1 for a in attempts(project, session) if state_of(a) in ("compacted", "ok") and str(a["at"]).startswith(today)
    )


def trouble(project: Project, session: str | None) -> str | None:
    """What went wrong with the session's renewals, or None: the last two attempts failed, or the last renewal freed
    too little (LOW_FACTOR). Only for the texts, and for due() to wait for the safety trigger after one that freed
    too little; it never stops a renewal."""
    if not session:
        return None
    states = [(a, state_of(a)) for a in attempts(project, session)]
    if len(states) >= 2 and states[-1][1] == states[-2][1] == "failed":
        return "the last two renewals failed"
    lim = limit(project)
    if states and states[-1][1] == "ok" and lim:
        after = states[-1][0].get("tokens_after")
        if isinstance(after, int) and after >= LOW_FACTOR * lim:
            return f"the last renewal freed too little ({k(states[-1][0].get('tokens_before'))} → {k(after)})"
    return None


def pending(project: Project, session: str | None, now: float | None = None) -> Attempt | None:
    """The session's attempt still waiting for its compaction (asked, within WAIT_S), or None."""
    if not session:
        return None
    return next((a for a in reversed(attempts(project, session)) if state_of(a, now) == "asked"), None)


def _settle(project: Project, a: Attempt, state: str, **extra: Any) -> Attempt:
    rec: dict[str, Any] = {"at": util.now().strftime("%Y-%m-%dT%H:%M:%S"), "session": a["session"], "id": a["id"]}
    rec.update({"state": state, **{x: v for x, v in extra.items() if v is not None}})
    _append(project, rec)
    return {**a, **rec, "at": a.get("at")}


def expire(project: Project, session: str | None, now: float | None = None) -> Attempt | None:
    """Settles as failed the session's attempts that state_of says failed but whose record still says asked;
    returns the newest of them, or None."""
    if not session:
        return None
    gone = None
    for a in attempts(project, session):
        if a.get("state") == "asked" and state_of(a, now) == "failed":
            gone = _settle(project, a, "failed", error=a.get("error") or "no compaction came")
    return gone


# ---------- asking ----------


def requested(project: Project, session: str | None) -> dict[str, Any] | None:
    """The request of `session` (<state>/renew.json), or None."""
    d = util.read_json(renew_file(project), None)
    if not isinstance(d, dict) or not session or d.get("session") != session:
        return None
    return d


def request(project: Project, session: str | None, note: str | None = None, now: bool = False) -> dict[str, Any]:
    """`foreman renew`: writes the request of the marked session `session` and returns it. Refused (RenewError)
    for another session, or without `now` below context_rotate or, after a renewal that freed too little (low_last),
    below FORCE_FACTOR × context_rotate."""
    from . import orchestrator

    o = orchestrator.current(project)
    if not session:
        raise RenewError("no session id ($CLAUDE_CODE_SESSION_ID): run it from the orchestrator's session")
    if not o or o["session"] != session:
        raise RenewError(f"session {session} is not {project.name}'s marked orchestrator: only that one renews")
    lim = limit(project)
    n = _tokens(project, session)
    if not now and (not lim or not isinstance(n, int) or n < lim):
        raise RenewError(
            f"context {k(n)} is below [loop] context_rotate ({k(lim) if lim else 'off'}): nothing to renew; "
            "`--now` renews anyway"
        )
    if not now and low_last(project, session) and isinstance(n, int) and n < FORCE_FACTOR * lim:
        raise RenewError(
            f"the last renewal freed too little: foreman renews again by itself at {k(int(FORCE_FACTOR * lim))} "
            "(see the context_rotate notice for what weighs); `--now` renews anyway"
        )
    rec: dict[str, Any] = {"session": session, "at": _stamp(time.time()), "now": bool(now)}
    if note and note.strip():
        rec["note"] = note.strip()[:4000]
    renew_file(project).parent.mkdir(parents=True, exist_ok=True)
    util.write_json(renew_file(project), rec)
    return rec


def _live(project: Project, session: str, agents: object) -> bool:
    """Whether an agent of the session is live: one foreman launched, or a subagent in the module's list (`agents`,
    [{id, status}]) whose status is not over. No list (the tick left `agents` out: `$.agent.list()` rejected, or the
    module had not asked yet) cannot tell, and counts as live."""
    from . import loop

    if not isinstance(agents, list):
        return True
    listed = heartbeat.agent_statuses(agents)
    return bool(loop.session_agents(project, session)) or any(
        s not in heartbeat.ENDED_STATUSES for s in listed.values()
    )


def due(project: Project, session: str | None, ev: Mapping[str, Any], now: float | None = None) -> Attempt | None:
    """The attempt this tick of the marked session `session` starts (see the module doc), already written; else
    None."""
    if not session or ev.get("busy") is not False:
        return None
    lim = limit(project)
    req = requested(project, session)
    n = _tokens(project, session)
    forced = bool(lim) and isinstance(n, int) and n >= FORCE_FACTOR * lim
    if req is None and not forced:
        return None
    asked_now = bool(req and req.get("now"))
    now = time.time() if now is None else now
    if pending(project, session, now) is not None:
        return None
    if not asked_now:
        if not forced and low_last(project, session):
            return None  # it freed too little: only the safety trigger (or `--now`) tries again
        prev = attempts(project, session)
        at = util.iso_epoch(prev[-1].get("at")) if prev else None
        if at is not None and now - at < COOLDOWN_S:
            return None
        if not forced and _live(project, session, ev.get("agents")):
            return None
    a: Attempt = {
        "at": _stamp(now),
        "session": session,
        "id": f"r{int(now)}",
        "state": "asked",
        "why": "now" if asked_now else "request" if req else "safety",
        "tokens_before": n,
    }
    if req and req.get("note"):
        a["note"] = req["note"]
    _append(project, a)
    with contextlib.suppress(OSError):
        renew_file(project).unlink()
    return a


def instructions(project: Project) -> str:
    """The `/compact` instructions: a minimal summary, since foreman's pack carries the state (J1)."""
    return (
        f"foreman renews the memory of {project.name}'s orchestrator session in place. Right after this compaction "
        "foreman gives the session back its rules, board, active backlog, live agents, the owner's open decisions "
        "and the owner's last messages verbatim, so leave all that out. Keep this summary short (at most about "
        "1,500 words) and keep only what none of those holds: what the owner asked in this conversation that is "
        "still open and not on the board, what was agreed with them, what you were in the middle of and your next "
        "step. No history of finished work, no tool output, no file contents."
    )


# ---------- after the compaction ----------


def compacted(project: Project, session: str | None) -> Attempt | None:
    """The classic SessionStart `compact` of the session: settles its open attempt (asked, however long ago) as
    `compacted` and returns it; None when there is none."""
    if not session:
        return None
    a = next((x for x in reversed(attempts(project, session)) if x.get("state") == "asked"), None)
    return _settle(project, a, "compacted", compacted_at=util.now().strftime("%Y-%m-%dT%H:%M:%S")) if a else None


def settled(project: Project, session: str | None, aid: object, text: str, error: str | None) -> Attempt | None:
    """`hook mod renewed`: the module's `/compact` call settled. Rejected (`error`): the open attempt `aid` failed.
    Resolved: noted (`ran_at`, `text`); the compaction's own SessionStart settles it, or RAN_S later it failed.
    Returns the attempt as it stands, or None when `aid` is not open."""
    if not session or not isinstance(aid, str):
        return None
    a = next((x for x in attempts(project, session) if x["id"] == aid), None)
    if a is None or a.get("state") != "asked":
        return None
    if error:
        return _settle(project, a, "failed", error=error[:300])
    at = util.now().strftime("%Y-%m-%dT%H:%M:%S")
    rec: dict[str, Any] = {"at": at, "session": session, "id": aid, "ran_at": at}
    if text:
        rec["text"] = text[:300]
    _append(project, rec)
    return {**a, **{x: v for x, v in rec.items() if x != "at"}}


def measured(project: Project, session: str | None, tokens: object) -> Attempt | None:
    """The first measure with tokens after a renewal compacted: settles it `ok` with `tokens_after`, writes the
    note, and returns it; None otherwise."""
    if not session or not isinstance(tokens, int) or isinstance(tokens, bool):
        return None
    a = next((x for x in reversed(attempts(project, session)) if x.get("state") == "compacted"), None)
    if a is None:
        return None
    from . import orchestrator

    o = orchestrator.current(project) or {}
    name = o.get("name") if o.get("session") == session else None
    before = a.get("tokens_before")
    body = f"{k(before)} → {k(tokens)}: memory renewed in the same session ({a.get('why')})."
    lim = limit(project)
    if lim and tokens >= LOW_FACTOR * lim:
        body += f" Freed too little: the next automatic renewal waits for {k(int(FORCE_FACTOR * lim))}."
    notes.note(project, f"Renewal (session {name or session}): {k(before)} → {k(tokens)}", body)
    return _settle(project, a, "ok", tokens_after=tokens)


def low(project: Project, a: Attempt) -> bool:
    lim = limit(project)
    after = a.get("tokens_after")
    return bool(lim) and isinstance(after, int) and after >= LOW_FACTOR * lim


def low_last(project: Project, session: str | None) -> bool:
    """Whether the session's newest attempt is a renewal that freed too little (low())."""
    a = last(project, session)
    return bool(a) and a["state"] == "ok" and low(project, a)


def compacted_at(project: Project, session: str | None) -> float | None:
    """When the session's newest renewal compacted (epoch), or None: a pass is due after it."""
    if not session:
        return None
    for a in reversed(attempts(project, session)):
        if state_of(a) in ("compacted", "ok"):
            return util.iso_epoch(a.get("compacted_at"))
    return None


# ---------- the pack ----------


def _text_of(content: object) -> str:
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
        and not b["text"].lstrip().startswith("<")
    )


SKIP = ("<", "Caveat:", "[Request interrupted")


def owner_messages(path: str | None, prompt: str, n: int = OWNER_MESSAGES) -> tuple[list[dict[str, str]], str]:
    """([{time, text}] the last `n` messages a person typed in the transcript's main chain, oldest first; the
    orchestrator's answer to the newest of them). No loop pass (`prompt`), notification, other agent's message,
    command output, compaction summary or meta record. Reads only the tail. Never raises."""
    found: list[dict[str, str]] = []
    answer = ""
    texts: list[str] = []
    if not path:
        return [], ""
    try:
        for ln in transcripts.tail_lines(path):
            if b'"user"' not in ln and b'"assistant"' not in ln:
                continue
            d = transcripts._record(ln)
            if d is None or d.get("isSidechain") is True or not isinstance(d.get("message"), dict):
                continue
            m = d["message"]
            if d.get("type") == "assistant":
                t = _text_of(m.get("content"))
                if t.strip():
                    texts.append(t)
                continue
            if d.get("type") != "user" or not transcripts.is_prompt(m) or d.get("isMeta") or d.get("isCompactSummary"):
                continue
            t = _text_of(m.get("content")).strip()
            person = bool(t) and not t.startswith(SKIP) and not (prompt and t.startswith(prompt))
            if person:
                if not found:
                    answer = "\n\n".join(reversed(texts)).strip()
                ts = transcripts._local(transcripts.parse_ts(d.get("timestamp")))
                found.append({"time": ts.strftime("%m-%d %H:%M") if ts else "", "text": t})
                if len(found) >= n:
                    break
            texts = []
    except (OSError, TypeError, ValueError):
        pass
    return list(reversed(found)), answer


def _cut(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def pack(project: Project, session: str, transcript: str | None, a: Attempt | None, now: float | None = None) -> str:
    """The whole pack (module doc), and writes it to pack_file(). `a`: the attempt this compaction settles (a
    renewal), or None (a compaction foreman did not ask for)."""
    from . import orchestrator, start

    s = i18n.for_project(project)
    now = time.time() if now is None else now
    hm = time.strftime("%H:%M", time.localtime(now))
    o = orchestrator.current(project) or {}
    name = o.get("name") or session[:8]
    where = transcript or "(not known)"
    head = s["renew_pack_head" if a else "renew_pack_head_compact"].format(
        time=hm,
        name=name,
        project=project.name,
        before=k((a or {}).get("tokens_before")),
        transcript=where,
        pack=pack_file(project),
    )
    parts = [f"# {s['renew_pack_title'].format(time=hm)}", head]
    if a and a.get("note"):
        parts += [f"## {s['renew_pack_note']}", str(a["note"])]
    msgs, answer = owner_messages(transcript, project.section("loop").get("prompt") or "")
    parts.append(f"## {s['renew_pack_owner'].format(owner=project.owner, n=len(msgs))}")
    parts += [f"[{m['time']}] {_cut(m['text'], OWNER_CHARS)}" for m in msgs] or [s["renew_pack_owner_none"]]
    if answer:
        parts += [f"## {s['renew_pack_answer']}", _cut(answer, OWNER_CHARS)]
    fixed = "\n\n".join(parts)
    d = start.collect(project, env={"CLAUDE_CODE_SESSION_ID": session}, cwd=str(project.home))
    summary = start.render(project, d, renewal=True)
    rules = project.path(project.raw["project"]["rules"])
    try:
        rules_text = rules.read_text(errors="replace").strip()
    except OSError:
        rules_text = ""
    rest = summary + (f"\n\n## {s['renew_pack_rules'].format(path=rules)}\n\n{rules_text}" if rules_text else "")
    whole = fixed + "\n\n" + rest
    f = pack_file(project)
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_name(f.name + ".tmp")
        tmp.write_text(whole + "\n")
        os.replace(tmp, f)
    except OSError:
        pass
    if len(whole) <= PACK_MAX_CHARS:
        return whole
    tail = "\n\n" + s["renew_pack_cut"].format(pack=f)
    room = max(0, PACK_MAX_CHARS - len(fixed) - len(tail) - 2)
    return fixed + "\n\n" + rest[:room].rstrip() + tail


def resume_text(project: Project, ev: Mapping[str, Any]) -> str | None:
    """hooks.hook_resume with source `compact` in the marked session: settles the open renewal (compacted()) and
    returns the pack; None without a session id."""
    session = ev.get("session_id") if isinstance(ev.get("session_id"), str) else None
    if not session:
        return None
    a = compacted(project, session)
    tp = ev.get("transcript_path") if isinstance(ev.get("transcript_path"), str) else None
    return pack(project, session, tp or transcripts.transcript_of(session), a)


def status_line(project: Project, session: str | None) -> str:
    """`foreman session`'s line: the last renewal and how many today, or none; the trouble when there is one."""
    a = last(project, session)
    if a is None:
        return "renewals: none"
    why = trouble(project, session)
    when = str(a.get("at") or "")[11:16]
    st = a["state"]
    fig = f" ({k(a.get('tokens_before'))} → {k(a.get('tokens_after'))})" if st == "ok" else ""
    line = f"last renewal {when} {st}{fig}, {done_today(project, session or '')} today"
    return line + (f"; renewal trouble: {why}" if why else "")
