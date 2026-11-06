"""The Python side of foreman's hooks module (hooks/mod/foreman.ts): `foreman hook mod OP`.

The module is a thin adapter: each of its hooks runs `<plugin root>/bin/foreman hook mod OP` with the input as one
JSON object on stdin and reads one JSON object from stdout. Everything it decides on (the project, the minimum
Claude Code version, the texts) is here. This docstring is the one description of the protocol: what each op is
sent, what it answers and what the module does with the answer; the module's header keeps only its invariants and
CONTRIBUTING.md «The hooks module» the architecture. The cases both sides must agree on are in tests/mod/contract.ts
(a JSON list after `export default`), which the module's tests (tests/mod/*.test.ts, `claude plugin test`) and the
golden corpus (tests/test_golden.py, tests/golden/mod/) both read. Two answer fields are for the transcript and the
tests only, the module reads neither: `min` (start) and `due` (tick).

- `start` (the module's `session.start`, which fires again after `/reload-plugins`, and its first tick under a new
  session id after a /clear or an in-process resume): stdin {cwd, session, claude_code, root, interactive,
  previous?} (`interactive`: the event's `isInteractive`; `previous`: the id the module ran under until then);
  stdout {"active": false} outside an active project (the module stays inert), else {"active": true, "below": bool,
  "min": CLAUDE_CODE_MIN} plus "grants": true when the project has `[deny.grants]` groups (`grant`), "status" (the
  owner's line, i18n `claude_min_owner`) when `claude_code` is lower than the minimum. In an active project it deletes
  the session in <state root>/install/sessions.json
  (versions.record_session) with what `root` runs, the Claude Code version (`claude_code`) and the session's host
  when its environment reaches this process (`CLAUDE_CODE_ENTRYPOINT`, information only): after `/reload-plugins`
  the record names the new root and version at once, with no transcript read. Unless below the minimum (no tick
  will follow), it also writes the session's heartbeat with `started` and `interactive` (heartbeat.beat;
  `interactive` is information only). With `previous` (carry_mark), in an active project, the old session's
  heartbeat is marked ended when it was not, and when `previous` is the marked orchestrator and this call runs in
  its recorded `claude` process (or none was recorded), the mark moves to `session` (orchestrator.carry) with a
  note; the answer is the same. The module: `active` false leaves it inert (no timer, no other call); `below` shows
  `status` and does nothing else; otherwise it starts its timer. A /clear or a resume goes on under a new id with no
  `session.start`: that `session.end` (reason `clear` or `resume`) drops what the module kept for the old
  conversation (a pass and its retry, notices, the version notice, the reminder, the band, the mark read), and the
  first tick that finds another id runs this op again with the same cwd and `interactive`.
- `measure` (the module's `session.measure`, main loop only, when its `changed` includes `context`): stdin {cwd,
  session, context, rate_limits, cost}, the engine's figures as the event gives them (context {tokens?, window,
  percent?}, rate limits [{kind, percentUsed, resetsAt?}], cost {usd} or null). In an active project it writes
  <state>/measure/<session>.json (MEASURE_KEYS: tokens, window, percent, cost_usd, rate_limits, at) and answers
  {"stored": true}; elsewhere {"stored": false}. `tokens` is the input side of the last response (uncached +
  cache written + cache read), the same figure transcripts.context gives for that response; null until the first
  response of the live window (a fresh or just compacted session). Files untouched for MEASURE_DAYS go.
  read_measure() is the one reader: the `context_rotate` notice, the renewal and `foreman session`. The first
  measure with tokens after a renewal compacted closes it (renew.measured: `ok`, the note) and answers "log" too
  (i18n `renew_done_owner`, or `renew_low_owner` when it freed too little), which the module shows.
- `tick` (the module's timer, every heartbeat.TICK_S seconds in every session of an active project; a tick does not
  start while the last one runs): stdin {cwd, session, busy, claude_code, agents?, hold?}. The module sends `hold`
  while a pass it submitted waits ($.prompt.submit, never awaited, has not settled) or its turn runs, and `agents`
  until a tick answers `marked` false. In an active project it first writes the session's heartbeat
  (<state>/sessions/<session>.json, heartbeat.py: the tick's time, `busy` (a main-loop turn runs), the Claude Code
  version, and in the marked session `agents`, the statuses of `$.agent.list()` the module passes as [{id, status}];
  left out when that call rejected or the module does not know yet that the session is marked, which the renewal
  reads as «cannot tell», renew._live): that is how foreman tells whether the session still runs.
  Every answer in an active project also carries "grants": true|false, whether the project has `[deny.grants]`
  groups, read again at each tick as `start` reads it: the module sets its check of main-loop Bash calls (`grant`)
  from it, so a group added to or taken from the project's foreman.toml while the session runs counts from the next
  tick on.
  With `hold` true (a pass of this session is under way) that is all: {"marked": bool, "grants": bool}. Otherwise
  {"marked": false} outside an active project (no "grants": the module keeps what it had) or when the session is
  not the marked orchestrator (the module does nothing more: the mark is read at every tick, so a relieved session
  stops at its next tick). In the marked session
  {"marked": true, "due": false} while no pass is due: no [loop] prompt, or the last pass (the `loop` heartbeat) is
  newer than [loop] every_min (DUE_SLACK_S early counts) and than the mark; and while a main-loop turn runs (`busy`
  true, as for a renewal): a pass due then is given at the first tick after the turn ends, so the module
  never queues one behind a turn whose end it would take for the pass's. A due pass runs here (loop.loop_pass:
  heartbeat, notices, its line in loop.jsonl) and answers {"marked": true, "due": true} plus "submit", the prompt the
  module submits ([loop] prompt, a blank line and the pass's summary), or, when nothing in it is actionable, "log":
  the one line the module shows with $.ui.log (i18n `loop_idle`), never read by the model. The module submits
  without awaiting (the ticks meanwhile are held); `turn.complete` of the pass's turn ends it. A pass that did not reach
  the model (dropped, or its turn interrupted, as by Esc) is submitted again once, at the next main-loop `turn.complete`
  that does not come while a tick runs; a new pass replaces it. Limit (accepted): in an idle session nobody types into
  there is no such `turn.complete`, so the dropped pass waits for the next due one, up to [loop] every_min later.
  In the marked session, while no pass is due, "notices": the in-between notices whose case was not said yet
  (loop.notices, once per case: the same cases and marks as on a prompt), one text the module keeps and hands the
  model as `context` with the next result of one of its own tool calls (`tool.call` without `agentId`, not denied)
  or its next prompt that is not a slash command, whichever comes first: a notice arrives up to one tick late. A
  pass submitted, or a tick that finds the session no longer marked, drops what was kept (the pass says what is
  there). In any session of an active project, when this foreman's plugin folder is marked `.orphaned_at` (one stat
  per tick) and the session was not told yet (versions.pending), "version" (the text for the model, delivered the
  same way) and "version_owner" (the owner's line, which the module shows with $.ui.log when it delivers the text),
  in the marked session as in any other. Limit (accepted): both are marked said here, when they are handed over,
  and the module keeps them only in memory; a `/reload-plugins`, a worker respawn or a relief before the next tool
  call or prompt loses them (the chat reviewer's `reminder` too), and the next pass repeats what is still pending.
  The renewal (renew.py): in the marked session, before any pass, a tick that finds the session due a renewal
  (renew.due, which writes the attempt first) answers {"marked": true, "due": false, "renew": ID, "instructions":
  the /compact instructions, "log": the owner's line (i18n `renew_owner`), "band"}; the module shows the line and
  runs `/compact <instructions>` from its timer. While the attempt waits for its compaction no
  pass is run; one that failed meanwhile (renew.expire) is said with "log" (i18n `renew_failed_owner`). A pass is
  due right after a renewal compacted (pass_due).
- `renewed` (the module, when its `/compact` call settled): stdin {cwd, session, id, text, error?}. With `error`
  (the call was rejected) the attempt failed: {"recorded": true, "log": i18n `renew_failed_owner`}; resolved, it is
  noted and the compaction's own SessionStart settles it ({"recorded": true}); {"recorded": false} when `id` is not
  open or outside an active project.
- `answer` (the module's `turn.complete`, main loop only, in a session no tick said is unmarked, for a non-empty
  answer): stdin {cwd, session, turn, answer}, the turn's id and its final visible text. In the marked session of
  an active project and a non-empty answer, the chat reviewer checks it
  (plain.review_answer: one line of counts in <state>/plain.jsonl, `message` the turn's id) and answers
  {"reviewed": true} plus "reminder" (i18n `plain_echo`) when something was flagged, which the module adds as
  `context` to the owner's next prompt (a prompt typed by a person, PERSON_ORIGINS: not a notification, another
  session's message or a plugin's); a newer answer replaces it. Elsewhere {"reviewed": false}.
  In the marked session every tick that is not held also answers "band": the orchestrator's band (band.lines),
  {"level": "ok"|"ask"|"bad", "rows": two lists of parts {"text", "role", "prio"} (and "n" for the role "more")}:
  the level's dot, the owner's questions not answered yet, the marked session's memory against [loop]
  context_rotate, branches ready to integrate, the next pass, «foreman»; then the live jobs by the short name of
  their issue and the `more` part. In the project's language, after the pass if one ran. The module draws them above
  the prompt (`AbovePrompt`), the parts that fit its width (its `fit`, the only place that knows the width), until a
  tick says the session is not the marked one, a tick fails or a survey holds the band.
- `status` (the module's answer to `/foreman:status`): stdin {cwd, session}; {"text": the band as plain text
  (band.text: every part joined with « · », one row per line, the dot said as a word)} in an active project, marked
  session or not; outside one {"text": i18n `status_outside`}. A failure is the command's output, not a status
  line; no model turn runs.
- `end` (the module's `session.end`): stdin {cwd, session}; marks the session's heartbeat ended and answers
  {"ended": true}, or {"ended": false} outside an active project or for a session with no heartbeat. The module
  asks beside the engine's own end step (`next(e)` is not kept waiting), within what the event's short bound leaves
  (no call when too little is left), and shows nothing when it fails.
- `grant` (the owner's yes for a `[deny.grants]` group, grants.py): `start` answers "grants": true in a project
  with groups and every tick "grants": true|false (read again, so a group added while the session runs counts from
  the next tick), and only while the last of them said true does the module's `tool.call` hook check a call of the
  main loop (no `agentId`; a subagent's goes straight on and the classic guard refuses it), before the call runs:
  a Bash call, and a call of any other tool but AskUserQuestion (the module's own question) that is not asked about
  as a reserved file (below). {} outside an active project, without a session or without groups. Two phases:
  - stdin {phase: "check", cwd, session, command, tool_use_id}: guard.check with the main loop's ctx. Any other
    tool: stdin {phase: "check", cwd, session, tool, input (the tool's arguments: the event without tool,
    tool_use_id, agentId and requestMeta), tool_use_id}, judged by guard.check_call with the same ctx (the places and
    commands it names, as the classic guard judges it); the question says the tool and the place or command that
    matched (i18n grant_question_tool), and a reserved file it writes asks nothing ({}). When the one
    thing missing is a grant (why=grant), or the command passes on a live grant of grants.json, it answers {group,
    live: bool, question, yes, no, header (i18n grant_*, the project's language), ask_ms ([deny] ask_minutes; 0: no
    limit), waiting (i18n grant_denied_waiting), until (when live)}. After the owner's no, while the group cools down
    in this session (grants.COOLDOWN_S), {group, deny} (i18n grant_denied_cooldown) and no question. A command
    whose every grant the owner gave from foreman's panel (grants.from_panel: via "panel", its signature verified
    with the panel's grant key) answers {}: the module never heard that yes, and the panel's approval stands. Otherwise
    {} (the classic guard decides alone). The module: {} lets the call on; `deny` refuses it; with `live`, a grant whose
    `until` is one the module itself heard granted in this conversation lets it on, and anything else is asked (a
    grants.json written by hand, a grant from before a reload or a /clear). While a question of the group is open
    (another call waits for it, or its time limit refused its call and the engine keeps it drawn), the call is
    refused with `waiting` and nothing is asked: one question per group, so none piles up behind another. Otherwise
    the call waits on `$.ui.ask(question, {options: [yes, no], header})` raced with a `$.clock.after(ask_ms)` timer
    (the engine sets no limit of its own, and the hook's budget stops while the ask is in flight). The answer is
    `yes` only for the exact yes label; any other label or text typed under "Other" is `no`; a rejection is
    `unavailable` when nobody can be asked (`no tool named "AskUserQuestion"`: a -p run, an SDK host without a
    permission callback) and `dismissed` otherwise (Esc, «Chat about this», a host that refuses); the timer is
    `timeout`. Neither the surfaces nor `isInteractive` decide whether to ask: they cannot tell a hosted session
    with a person behind it (the desktop app, an SDK host, the phone) from none.
  - stdin {phase: "answer", cwd, session, group, answer: yes|no|dismissed|timeout|unavailable, surfaces, command (or
    tool and input), tool_use_id, late?}; for another tool the record's `command` is `<tool> <place or command>` (judged
    again) and it carries `tool`. On yes, when the session is still the marked one and this call runs in the mark's
    `claude` process: the grant (grants.give, [deny] grant_minutes), a line in approved.jsonl (verdict grant) and a
    note; {granted: true, until, log}. A yes from another session or process grants nothing ({granted: false, deny}).
    Any other answer: {granted: false, deny} (i18n grant_denied_ANSWER) and a line (verdict deny); a no starts the
    cooldown; a timeout keeps the request pending for the panel (grants.pend, with the mark's pid and start time; the
    owner approves it there: grants.approve). With `late` true (the answer came after the timeout refused the call), a
    line (verdict late) and nothing else: {granted: false, log}; the log says the group is already granted (i18n
    grant_late_granted) when it is, e.g. approved from the panel meanwhile, and nothing is granted twice. The module
    (`surfaces` is $.session.surfaces(), for the record only): `granted` keeps `until` in its memory, shows `log` and
    lets the call on to the classic guard, which finds the grant; otherwise it refuses the call with `deny`. After a
    timeout it keeps listening to the question, still drawn: its late answer (or dismissal) goes here with `late` true
    and the timed-out call's input, `log` is shown, and the group's question is closed. A call that fails (either phase)
    is a status line and lets the call on: the classic guard refuses a group's command with no grant.
  - A reserved file of Claude Code's settings or the registry (guard.ASK_KINDS): the owner's yes for one
    call. The module also checks, in any session of the project (marked or not) and only in its main loop, a
    Write, Edit, MultiEdit or NotebookEdit call whose file (`file_path`, or `notebook_path`) reads as one of those
    by its text, and a Bash call whose command does (`.claude`, `.config/foreman`, `projects.toml`: a loose test;
    a path that reaches one otherwise, a custom $FOREMAN_REGISTRY or $CLAUDE_CONFIG_DIR, is not asked about and the
    classic guard refuses it). stdin {phase: "check", cwd, session, tool, path, tool_use_id} for a file tool (the
    Bash one as above). When the guard, as this session's main thread, refuses the call only for writing such a file
    (guard.check_file, or the Bash rule's "file" verdict), it answers {group: "file:<class>", live: false,
    question (i18n grant_file_question, or grant_file_question_bash with the whole command), yes (grant_file_yes),
    no, header, ask_ms, waiting (grant_file_waiting)}; a Bash command longer than FILE_COMMAND_CHARS is not asked
    about: {group, deny} (grant_file_long). A config, table or state file asks nothing ({}: the classic guard
    refuses it; the config and the table change only through `foreman permissions apply`). The module asks as for a
    group (one question per class at a time, `ask_ms`, a rejection read the same way, so a -p run cannot answer:
    `unavailable`), and never keeps a file's yes in its memory. stdin {phase: "answer", cwd, session, group:
    "file:<class>", answer, surfaces, tool, path | command, tool_use_id, late?}: the call is judged again (nothing to
    grant when it no longer writes such a file: {granted: false}); on yes a one-use grant for that session,
    tool_use_id and file (its real path) or command (grants.give_once), a line in approved.jsonl (verdict grant,
    with `file` or `command`) and a note; {granted: true, log (grant_file_log)}. The classic guard, on that very
    call, spends it (grants.take_once) and lets it through; any other call, an agent's included, finds none. Any
    other answer: {granted: false, deny (grant_file_denied_ANSWER)} and a line (verdict deny); no cooldown, no
    pending request, no panel: the next call that writes such a file asks again. `late`: a line (verdict late) and
    {granted: false, log (grant_file_late)}.

Exit 0 with the answer; 2 for an unknown OP or an input that is not a JSON object; 1 when something fails (one line
on stderr). The module shows a failure as a status line (one for the plugin: the newest op still failing; an op
that works again drops its own) and calls Python again at the next event; only a failed `start` leaves it inert.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

from . import config, heartbeat, i18n, util

if TYPE_CHECKING:
    from pathlib import Path

    from .config import Project
    from .identity import Event
    from .orchestrator import Mark

DUE_SLACK_S = 30  # a pass is due this much before [loop] every_min, so a tick a little early does not wait a minute
MEASURE_KEYS = ("tokens", "window", "percent", "cost_usd", "rate_limits", "at")
MEASURE_DAYS = 7
SESSION_ID = heartbeat.SESSION_ID


def where(ev: Event) -> tuple[Any, Project | None, str | None]:
    """What every op reads first: the event's `cwd` as given, the active project it is in (None outside one, or
    without a cwd) and its `session` (None when it is not a string)."""
    cwd = ev.get("cwd")
    project = config.project_for_cwd(cwd, active_only=True) if isinstance(cwd, str) and cwd else None
    return cwd, project, ev.get("session") if isinstance(ev.get("session"), str) else None


def start(ev: Event) -> dict[str, Any]:
    cwd, project, session = where(ev)
    if project is None:
        return {"active": False}
    from . import versions

    found = ev.get("claude_code") if isinstance(ev.get("claude_code"), str) else None
    root = ev.get("root") if isinstance(ev.get("root"), str) and ev.get("root") else None
    me: dict[str, Any] = {}
    if session:
        me = versions.own(root=os.path.realpath(root) if root else None)
        rec = versions.read_sessions().get(session) or {}
        if rec.get("kind") == "inline" and rec.get("root") == me["root"]:
            me["kind"] = "inline"  # only the classic hooks' environment tells a --plugin-dir session
        entrypoint = os.environ.get(versions.ENTRYPOINT_VAR) or None  # the session's host, when it reaches here
        versions.record_session(session, me, project.name, cwd, claude_code=found, entrypoint=entrypoint)
    previous = ev.get("previous")
    if session and isinstance(previous, str) and SESSION_ID.match(previous):
        carry_mark(project, previous, session)
    out = {"active": True, "below": versions.claude_code_below_min(found), "min": versions.CLAUDE_CODE_MIN}
    if grants_groups(project, internal=False):
        out["grants"] = True
    if session and not out["below"] and heartbeat.beat_file(project, session) is not None:
        interactive = ev.get("interactive") if isinstance(ev.get("interactive"), bool) else None
        heartbeat.beat(project, session, cwd=cwd, claude_code=found, start=True, interactive=interactive)
    if out["below"]:
        out["status"] = i18n.for_project(project)["claude_min_owner"].format(min=versions.CLAUDE_CODE_MIN, found=found)
    return out


def carry_mark(project: Project, previous: str, session: str) -> None:
    """The module started again under a new id (`previous`: the one it ran under, after a /clear or an in-process
    resume): the old heartbeat ends if its session.end did not say so, and the orchestrator's mark moves to the new
    id when the old one was marked in this same `claude` process (orchestrator.carry), with a note for the
    history. Never raises: a start never fails over the mark."""
    from . import notes, orchestrator

    try:
        old = heartbeat.read(project, previous)
        if old is not None and not old.get("ended"):
            heartbeat.end(project, previous)
        moved = orchestrator.carry(project, previous, session)
        if moved is not None:
            notes.note(
                project,
                f"Orchestrator mark moved (session {moved.get('name') or session})",
                f"Session {previous} went on as {session} in the same Claude Code process (/clear or resume): "
                "foreman's hooks module moved the orchestrator's mark to the new id.",
            )
    except Exception:  # noqa: BLE001
        return


def _number(x: object) -> int | float | None:
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def measure_dir(project: Project) -> Path:
    return project.state / "measure"


def measure_file(project: Project, session: object) -> Path | None:
    """<state>/measure/<session>.json, or None for a session id that is not a plain name."""
    return measure_dir(project) / f"{session}.json" if isinstance(session, str) and SESSION_ID.match(session) else None


def measure(ev: Event) -> dict[str, Any]:
    project = where(ev)[1]
    if project is None:
        return {"stored": False}
    f = measure_file(project, ev.get("session"))
    if f is None:
        raise ValueError(f"not a session id: {ev.get('session')!r}")
    ctx: dict[str, Any] = ev["context"] if isinstance(ev.get("context"), dict) else {}
    cost: dict[str, Any] = ev["cost"] if isinstance(ev.get("cost"), dict) else {}
    limits = [
        {"kind": x["kind"], "percent_used": _number(x.get("percentUsed")), "resets_at": x.get("resetsAt")}
        for x in ev.get("rate_limits") or []
        if isinstance(x, dict) and isinstance(x.get("kind"), str)
    ]
    rec = {
        "tokens": _number(ctx.get("tokens")),
        "window": _number(ctx.get("window")),
        "percent": _number(ctx.get("percent")),
        "cost_usd": _number(cost.get("usd")),
        "rate_limits": limits,
        "at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    }
    f.parent.mkdir(parents=True, exist_ok=True)
    util.write_json(f, rec)
    out: dict[str, Any] = {"stored": True}
    out.update(renew_measured(project, ev.get("session"), rec["tokens"]))
    util.prune_older(f.parent, "*.json", MEASURE_DAYS, keep=f)
    return out


def renew_measured(project: Project, session: object, tokens: object) -> dict[str, Any]:
    """{log} when this measure closes a renewal (renew.measured); {} otherwise. Never raises."""
    from . import renew

    try:
        a = renew.measured(project, session if isinstance(session, str) else None, tokens)
    except Exception:  # noqa: BLE001 (a measure never fails over a renewal)
        return {}
    if a is None:
        return {}
    t = i18n.for_project(project)
    key = "renew_low_owner" if renew.low(project, a) else "renew_done_owner"
    force = renew.k(int(renew.FORCE_FACTOR * renew.limit(project)))
    return {
        "log": t[key].format(before=renew.k(a.get("tokens_before")), after=renew.k(a.get("tokens_after")), force=force)
    }


def read_measure(project: Project, session: object) -> dict[str, Any] | None:
    """The last measure of a session ({MEASURE_KEYS}), or None when foreman's module has written none (not loaded, or
    no turn yet). Never raises; nothing is read from the transcript instead."""
    f = measure_file(project, session)
    d = util.read_json(f, None) if f else None
    if not isinstance(d, dict):
        return None
    return {k: d.get(k) for k in MEASURE_KEYS}


def context_text(m: dict[str, Any] | None, limit: int | None = None) -> str:
    """The context of a measure in words: «123456 tokens (123K, 62% of 200K, at T; [loop] context_rotate 150K)»;
    «unknown (…)» without a measure or before the first response of the window. `limit` 0 or None: no threshold."""
    rot = f"; [loop] context_rotate {limit // 1000}K" if limit else ""
    if m is None:
        return f"unknown (no measure from foreman's hooks module for this session yet{rot})"
    n = m.get("tokens")
    if not isinstance(n, int):
        return f"unknown (no response since the session started or compacted, at {m.get('at')}{rot})"
    window = m.get("window")
    of = (
        f", {m.get('percent')}% of {window // 1000}K"
        if isinstance(window, int) and m.get("percent") is not None
        else ""
    )
    return f"{n} tokens ({n // 1000}K{of}, at {m.get('at')}{rot})"


def pass_due(project: Project, o: Mark, now: float | None = None) -> bool:
    """Whether the marked session `o` (orchestrator.current) is due a pass: no `loop` heartbeat, one older than
    [loop] every_min (less DUE_SLACK_S), or one older than the mark (a session marked since the last pass gets its
    first pass at its next tick) or than the session's last renewal (renew.compacted_at: the pass right after it)."""
    from . import orchestrator

    now = time.time() if now is None else now
    last = util.iso_epoch(orchestrator.last_pass(project))
    if last is None:
        return True
    marked = util.iso_epoch(o.get("since"))
    if marked is not None and marked > last:
        return True
    if renewed_since_pass(project, o):
        return True
    return now - last >= orchestrator.every_min(project) * 60 - DUE_SLACK_S


def renewed_since_pass(project: Project, o: Mark) -> bool:
    """Whether the marked session `o` renewed its memory (renew.compacted_at) after the last pass (the `loop`
    heartbeat; none counts as before): the pass due then is sent to the model even with nothing actionable
    (_tick_pass), since after `/compact` the session gets no turn otherwise and its pack says a pass comes.
    Never raises."""
    from . import orchestrator, renew

    try:
        renewed_at = renew.compacted_at(project, o.get("session"))
    except Exception:  # noqa: BLE001 (a renewal record never fails the tick)
        return False
    if renewed_at is None:
        return False
    last = util.iso_epoch(orchestrator.last_pass(project))
    return last is None or renewed_at > last


def tick(ev: Event) -> dict[str, Any]:
    from . import orchestrator

    cwd, project, session = where(ev)
    if project is None:
        return {"marked": False}
    o = orchestrator.current(project)  # the mark, read once per tick
    marked = bool(o and session and o["session"] == session)
    if session and heartbeat.beat_file(project, session) is not None:
        heartbeat.beat(
            project,
            session,
            cwd=cwd,
            busy=ev.get("busy") is True,
            claude_code=ev.get("claude_code"),
            agents=heartbeat.agent_statuses(ev.get("agents")) if marked and "agents" in ev else None,
        )
    grants = bool(
        grants_groups(project, internal=False)
    )  # as start says it, read again: a group added while the session runs
    if ev.get("hold") is True:  # a pass of this session is under way: the heartbeat only
        return {"marked": marked, "grants": grants}
    out: dict[str, Any] = _tick_pass(project, session, o, ev) if marked and o and session else {"marked": False}
    out["grants"] = grants
    told = version_told(project, session, cwd)
    if told:
        out["version"], out["version_owner"] = told
    return out


def _tick_pass(project: Project, session: str, o: Mark, ev: Event) -> dict[str, Any]:
    """The marked session's part of a tick that is not held (`o`: the mark tick read, this session's): a renewal first,
    then the pass when one is due and no main-loop turn runs (`busy`; sent
    even with nothing actionable when it is the first after a renewal, renewed_since_pass), else the in-between
    notices not said yet."""
    from . import band, loop

    prompt = project.section("loop").get("prompt") or ""
    snap = None  # the state the in-between notices and the band share; a pass writes, so the band reads it again
    renewing = renew_due(project, session, ev)
    if renewing is not None:
        out = {"marked": True, "due": False, **renewing}
    elif not prompt or ev.get("busy") is True or not pass_due(project, o):  # never behind a running turn
        out: dict = {"marked": True, "due": False}
        with contextlib.suppress(Exception):  # unreadable: the notices and the band each fail in their own guard
            snap = loop.Snapshot(project, session)
        texts = between_notices(project, session, snap)
        if texts:
            out["notices"] = texts
    else:
        renewed = renewed_since_pass(project, o)  # before loop_pass, which writes the `loop` heartbeat
        text, ns = loop.loop_pass(project, session, send=renewed)
        if loop.skips(project, ns) and not renewed:
            out = {"marked": True, "due": True, "log": loop.idle_line(project, ns)}
        else:
            out = {"marked": True, "due": True, "submit": f"{prompt}\n\n{text}"}
    out["band"] = band.lines(project, snap=snap)  # after the pass, so its time is the new one
    return out


def renew_due(project: Project, session: str, ev: Event) -> dict[str, Any] | None:
    """{renew, instructions, log} when this tick starts a renewal (renew.due wrote the attempt); {} while an attempt
    waits for its compaction (no pass meanwhile); {log} when one just failed (renew.expire); None otherwise. Never
    raises: a renewal never fails the tick."""
    from . import renew

    t = i18n.for_project(project)
    try:
        gone = renew.expire(project, session)
        if renew.pending(project, session) is not None:
            return {}
        a = renew.due(project, session, dict(ev))
        failed = failed_line(project, session, gone) if gone else None
    except Exception:  # noqa: BLE001
        return None
    if a is None:
        return {"log": failed} if failed else None
    m = read_measure(project, session) or {}
    window = m.get("window")
    line = t["renew_owner"].format(tokens=renew.k(a.get("tokens_before")), window=renew.k(window))
    return {"renew": a["id"], "instructions": renew.instructions(project), "log": line}


def failed_line(project: Project, session: str, a: dict[str, Any]) -> str:
    """The owner's line for a renewal that failed: tried again later; after two in a row (renew.trouble), also that
    the owner can type /compact."""
    from . import renew

    t = i18n.for_project(project)
    why = " ".join(str(a.get("error") or "").split())[:120] or "?"
    key = "renew_failed_again_owner" if renew.trouble(project, session) else "renew_failed_owner"
    return t[key].format(why=why, minutes=renew.COOLDOWN_S // 60)


def between_notices(project: Project, session: str | None, snap: Any = None) -> str:
    """The in-between notices not said yet (loop.notices, which marks them said) as one text; '' when none or when
    they cannot be computed (the tick goes on: the next pass shows what is there)."""
    from . import loop

    try:
        return " ".join(n["text"] for n in loop.notices(project, session, snap))
    except Exception:  # noqa: BLE001 (a notice never fails the tick)
        return ""


def version_told(project: Project, session: str | None, cwd: Any) -> tuple[str, str] | None:
    """(text for the model, owner's line) when this foreman's plugin folder is marked `.orphaned_at` (one stat) and
    the session was not told about the installed version yet (versions.pending; marked told here); None otherwise.
    Never raises."""
    from . import hooks, versions

    try:
        me = versions.own()
        if not session or not versions.orphaned(me["root"]):
            return None
        b = versions.pending({"session_id": session, "cwd": cwd}, project.name)
        if not b:
            return None
        versions.mark_told(b["session"], b["key"])
        return hooks.version_texts(project, b)
    except Exception:  # noqa: BLE001 (a version notice never fails the tick)
        return None


def renewed(ev: Event) -> dict[str, Any]:
    from . import renew

    _, project, session = where(ev)
    if project is None or not session:
        return {"recorded": False}
    raw, err = ev.get("text"), ev.get("error")
    text = " ".join(raw.split()) if isinstance(raw, str) else ""
    a = renew.settled(project, session, ev.get("id"), text, err if isinstance(err, str) and err else None)
    if a is None:
        return {"recorded": False}
    if a["state"] == "failed":
        return {"recorded": True, "log": failed_line(project, session, a)}
    return {"recorded": True}


def answer(ev: Event) -> dict[str, Any]:
    from . import orchestrator, plain

    _, project, session = where(ev)
    o = orchestrator.current(project) if project is not None else None
    text = ev.get("answer") if isinstance(ev.get("answer"), str) else ""
    turn = ev["turn"] if isinstance(ev.get("turn"), str) else ""
    if project is None or not o or not session or o["session"] != session:
        return {"reviewed": False}
    reviewed, reminder = plain.review_answer(project, session, turn, text)
    return {"reviewed": reviewed, "reminder": reminder} if reminder else {"reviewed": reviewed}


def end(ev: Event) -> dict[str, Any]:
    _, project, session = where(ev)
    if project is None or not session:
        return {"ended": False}
    return {"ended": heartbeat.end(project, session)}


def grants_groups(project: Project, internal: bool = True) -> list[str]:
    """The [deny.grants] groups, with foreman's own (grants.INTERNAL) unless internal=False: start and the tick say
    `grants` only for the product's groups, and the module also asks for `permissions apply … --opens`."""
    from . import grants

    return list(grants.groups(project, internal))


def grant(ev: Event) -> dict[str, Any]:
    """`hook mod grant` (module doc): the owner's yes for a [deny.grants] group, in two phases."""
    cwd = ev.get("cwd")
    project = config.project_for_cwd(cwd, active_only=True) if isinstance(cwd, str) and cwd else None
    session = ev.get("session") if isinstance(ev.get("session"), str) and ev.get("session") else None
    if project is None or session is None or not grants_groups(project):
        return {}
    phase = ev.get("phase")
    if phase == "check":
        return _grant_check(project, session, ev)
    if phase == "answer":
        group = ev.get("group")
        if isinstance(group, str) and group.startswith(FILE_GROUP):
            return _file_answer(project, session, ev, group[len(FILE_GROUP) :])
        return _grant_answer(project, session, ev)
    raise ValueError(f"phase must be check or answer, not {phase!r}")


FILE_GROUP = "file:"  # the module's key for a question about a reserved file: file:<class>
FILE_COMMAND_CHARS = 300  # a Bash command longer than this is not asked about: the owner must see all of it


def _command(ev: Event) -> str:
    cmd = ev.get("command")
    return cmd if isinstance(cmd, str) else ""


def _tool(ev: Event) -> str | None:
    """The tool of a call that is not Bash and carries its `input` (a group's question for any tool), else None."""
    tool = ev.get("tool")
    return tool if isinstance(tool, str) and tool and tool != "Bash" and isinstance(ev.get("input"), dict) else None


def _tool_call(project: Project, ev: Event, ctx: Any) -> tuple[Any, str]:
    """The guard's verdict on a call of another tool than Bash (_tool) with ctx (guard.check_call: the places and
    commands it names) and the call as the owner reads it, `<tool> <place or command>`: the place or command that
    matched, or that a live grant let through ("" when none)."""
    from . import guard

    tool = str(_tool(ev))
    v = guard.check_call(project, tool, ev["input"], ctx)
    hit = v[1] if v else ctx.get("passed")
    call = getattr(hit, "call", "") if hit is not None else ""
    return v, f"{tool} {call}" if call else ""


def _grant_check(project: Project, session: str, ev: Event) -> dict[str, Any]:
    from . import grants, guard, orchestrator

    if ev.get("tool") in guard.FILE_TOOLS and _path(ev):
        out = _file_check(project, session, ev)
        if out or _tool(ev) is None:
            return out
    t = i18n.for_project(project)
    tool = _tool(ev)
    ctx: guard.Ctx = {"session": session, "agent": None, "cwd": ev.get("cwd")}
    if tool is not None:
        v, cmd = _tool_call(project, ev, ctx)
        if not cmd or (v and v[0] == "file"):
            return {}  # nothing a group covers; a reserved file is asked about by its path (above) or refused
    else:
        cmd = _command(ev)
        if not cmd:
            return {}
        v = guard.check(project, cmd, ctx)
        if v and v[0] == "file":
            return _file_question(project, v[1], cmd) if v[1].kind in guard.ASK_KINDS else {}
    if v and v[0] == "orchestrator" and v[1].why == "grant":
        group, live = v[1].group, False
        until = grants.cooling(project, group, session)
        if until:
            return {
                "group": group,
                "deny": t["grant_denied_cooldown"].format(group=group, owner=project.owner, until=until[11:16]),
            }
    elif v is None and "passed" in ctx and ctx["passed"].grant:
        # a grant the owner gave from the panel is verified here (its signature): the module never heard it given,
        # and asking again would undo the panel's approval; any other live grant goes to the module's memory
        heard = [u["group"] for u in ctx["passed"].grant if not grants.from_panel(project, u["group"])]
        if not heard:
            return {}
        group, live = heard[0], True
    else:
        return {}
    minutes = grants.minutes(project, "grant_minutes", 30)
    o = orchestrator.current(project) or {}
    out = {
        "group": group,
        "live": live,
        "question": t["grant_question_tool" if tool else "grant_question"].format(
            orchestrator=o.get("name") or session[:8],
            project=project.name,
            group=group,
            minutes=minutes,
            tool=tool,
            command=" ".join(cmd[len(tool) + 1 if tool else 0 :].split())[:200],
        ),
        "yes": t["grant_yes"].format(minutes=minutes),
        "no": t["grant_no"],
        "header": t["grant_header"],
        "ask_ms": grants.minutes(project, "ask_minutes", 5) * 60_000,
        "waiting": t["grant_denied_waiting"].format(group=group, owner=project.owner),
    }
    if live:
        out["until"] = (grants.live(project, group) or {}).get("until")
    return out


def _grant_answer(project: Project, session: str, ev: Event) -> dict[str, Any]:
    from . import approved, grants, guard, notes, orchestrator

    group, answer = ev.get("group"), ev.get("answer")
    if group not in grants_groups(project):
        raise ValueError(f"no [deny.grants] group {group!r}")
    if answer not in grants.ANSWERS:
        raise ValueError(f"answer must be one of {', '.join(grants.ANSWERS)}, not {answer!r}")
    surfaces = (
        [x for x in ev.get("surfaces") or [] if isinstance(x, str)] if isinstance(ev.get("surfaces"), list) else []
    )
    tid = ev.get("tool_use_id") if isinstance(ev.get("tool_use_id"), str) else None
    tool = _tool(ev)
    ctx: guard.Ctx = {"session": session, "agent": None, "cwd": ev.get("cwd")}
    cmd = _tool_call(project, ev, ctx)[1] if tool is not None else _command(ev)
    t = i18n.for_project(project)
    o = orchestrator.current(project) or {}
    who = o.get("name") or session[:8]
    rec: dict[str, Any] = {
        "group": group,
        "answer": answer,
        "surfaces": surfaces,
        "session": session,
        "orchestrator": o.get("name") or None,
        **({"tool": tool} if tool is not None else {}),
        "command": cmd[: approved.COMMAND_CHARS],
        "tool_use_id": tid,
    }
    fmt = {"group": group, "owner": project.owner, "orchestrator": who, "answer": answer}
    if ev.get("late") is True:  # the question had timed out and the call was refused: nothing is granted
        approved.granted(project, {"verdict": "late", **rec})
        g = grants.live(project, group)
        if g:  # approved meanwhile (from the panel): the late answer changes nothing, and says so
            return {
                "granted": False,
                "log": t["grant_late_granted"].format(**fmt, until=g["until"][11:16], via=g.get("via") or "?"),
            }
        return {"granted": False, "log": t["grant_late"].format(**fmt)}
    if answer == "yes":
        mark = orchestrator.record(project)
        refused = (
            "session" if mark.get("session") != session else ("process" if not orchestrator.runs_here(project) else "")
        )
        if refused:
            approved.granted(project, {"verdict": "deny", "refused": refused, **rec})
            return {"granted": False, "deny": t[f"grant_refused_{refused}"].format(**fmt)}
        g = grants.give(project, group, mark, "ask", surfaces, tid)
        approved.granted(project, {"verdict": "grant", **rec, "until": g["until"]})
        where = ", ".join(surfaces) or "?"
        notes.note(project, "-", t["grant_note"].format(**fmt, until=g["until"][11:16], surfaces=where))
        return {"granted": True, "until": g["until"], "log": t["grant_log"].format(**fmt, until=g["until"][11:16])}
    extra: dict[str, Any] = {}
    if answer == "no":
        extra["cooldown_until"] = grants.cool(project, group, session)
    if answer == "timeout":  # a no for this call; the request waits for the owner in foreman's panel
        mark = orchestrator.record(project)
        grants.pend(
            project,
            group,
            {
                "session": session,
                "pid": mark.get("pid") if mark.get("session") == session else None,
                "pid_start": mark.get("pid_start") if mark.get("session") == session else None,
                "orchestrator": rec["orchestrator"],
                "command": rec["command"],
                "tool_use_id": tid,
            },
        )
        extra["pending"] = True
    approved.granted(project, {"verdict": "deny", **rec, **extra})
    return {"granted": False, "deny": t[f"grant_denied_{answer}"].format(**fmt, minutes=grants.COOLDOWN_S // 60)}


def _path(ev: Event) -> str:
    path = ev.get("path")
    return path if isinstance(path, str) else ""


def _file_verdict(project: Project, session: str, ev: Event) -> Any:
    """What the guard says of the call as the main thread of this session (no tool_use_id: nothing is spent): the
    Hit of a reserved file of guard.ASK_KINDS, or None."""
    from . import guard

    ctx: guard.Ctx = {"session": session, "agent": None, "cwd": ev.get("cwd")}
    if ev.get("tool") in guard.FILE_TOOLS:
        v = guard.check_file(project, _path(ev), ctx) if _path(ev) else None
    else:
        v = guard.check(project, _command(ev), ctx) if _command(ev) else None
    return v[1] if v and v[0] == "file" and v[1].kind in guard.ASK_KINDS else None


def _file_check(project: Project, session: str, ev: Event) -> dict[str, Any]:
    hit = _file_verdict(project, session, ev)
    return _file_question(project, hit, None) if hit is not None else {}


def _file_what(project: Project, hit: Any) -> dict[str, str]:
    t = i18n.for_project(project)
    fields = {"project": hit.name or project.name, "owner": project.owner}
    return {**fields, "what": t[f"guard_file_what_{hit.kind}"].format(**fields), "path": hit.path or "?"}


def _file_question(project: Project, hit: Any, cmd: str | None) -> dict[str, Any]:
    """The question about one call that writes a file of Claude Code's settings or the registry (module doc)."""
    from . import grants

    t = i18n.for_project(project)
    f = _file_what(project, hit)
    group = FILE_GROUP + hit.kind
    if cmd is not None and len(cmd) > FILE_COMMAND_CHARS:
        return {"group": group, "deny": t["grant_file_long"].format(**f, chars=FILE_COMMAND_CHARS)}
    question = (
        t["grant_file_question_bash"].format(**f, command=" ".join(cmd.split()))
        if cmd is not None
        else t["grant_file_question"].format(**f)
    )
    return {
        "group": group,
        "live": False,
        "question": question,
        "yes": t["grant_file_yes"],
        "no": t["grant_no"],
        "header": t["grant_header"],
        "ask_ms": grants.minutes(project, "ask_minutes", 5) * 60_000,
        "waiting": t["grant_file_waiting"].format(**f),
    }


def _file_answer(project: Project, session: str, ev: Event, kind: str) -> dict[str, Any]:
    """The owner's answer about one call that writes a reserved file (module doc): a one-use grant on yes."""
    from . import approved, grants, guard, notes

    answer = ev.get("answer")
    if kind not in guard.ASK_KINDS:
        raise ValueError(f"no question about the class {kind!r}")
    if answer not in grants.ANSWERS:
        raise ValueError(f"answer must be one of {', '.join(grants.ANSWERS)}, not {answer!r}")
    tid = ev.get("tool_use_id") if isinstance(ev.get("tool_use_id"), str) and ev.get("tool_use_id") else None
    surfaces = (
        [x for x in ev.get("surfaces") or [] if isinstance(x, str)] if isinstance(ev.get("surfaces"), list) else []
    )
    bash = ev.get("tool") not in guard.FILE_TOOLS
    cmd = _command(ev) if bash else None
    t = i18n.for_project(project)
    rec: dict[str, Any] = {
        "group": FILE_GROUP + kind,
        "answer": answer,
        "surfaces": surfaces,
        "session": session,
        **({"command": (cmd or "")[: approved.COMMAND_CHARS]} if bash else {"file": _path(ev)}),
        "tool_use_id": tid,
    }
    hit = _file_verdict(project, session, ev)
    if hit is None or hit.kind != kind:  # the call no longer writes what was asked about: nothing to grant
        return {"granted": False}
    f = _file_what(project, hit)
    if ev.get("late") is True:  # the question had timed out and the call was refused: nothing is granted
        approved.granted(project, {"verdict": "late", **rec})
        return {"granted": False, "log": t["grant_file_late"].format(**f, answer=answer)}
    if answer == "yes":
        if tid is None:
            raise ValueError("a yes about a file needs the call's tool_use_id")
        real = None if bash else guard.real_path(_path(ev), ev.get("cwd") if isinstance(ev.get("cwd"), str) else None)
        grants.give_once(project, session, tid, kind, real, cmd)
        approved.granted(project, {"verdict": "grant", **rec})
        notes.note(
            project, "-", t["grant_file_note"].format(**f, session=session[:8], surfaces=", ".join(surfaces) or "?")
        )
        return {"granted": True, "log": t["grant_file_log"].format(**f)}
    approved.granted(project, {"verdict": "deny", **rec})
    return {"granted": False, "deny": t[f"grant_file_denied_{answer}"].format(**f)}


def status(ev: Event) -> dict[str, Any]:
    from . import band

    project = where(ev)[1]
    if project is None:
        return {"text": i18n.strings()["status_outside"]}
    return {"text": band.text(project)}


OPS = {
    "start": start,
    "measure": measure,
    "tick": tick,
    "renewed": renewed,
    "answer": answer,
    "end": end,
    "status": status,
    "grant": grant,
}


def run(args: list[str]) -> int:
    op = args[0] if args else ""
    if op not in OPS:
        print(f"unknown hook mod op {op!r} ({', '.join(OPS)})", file=sys.stderr)
        return 2
    try:
        ev = json.load(sys.stdin)
    except (ValueError, OSError) as e:
        print(f"hook mod {op}: stdin is not JSON ({e})", file=sys.stderr)
        return 2
    if not isinstance(ev, dict):
        print(f"hook mod {op}: stdin is not a JSON object", file=sys.stderr)
        return 2
    try:
        out = OPS[op](ev)
    except Exception as e:  # noqa: BLE001 (the module shows one status line; the session goes on)
        print(f"hook mod {op}: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    print(json.dumps(out, ensure_ascii=False, sort_keys=True))
    return 0
