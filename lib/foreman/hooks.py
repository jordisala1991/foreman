"""Claude Code's classic hooks (hooks/classic.json, named by .claude-plugin/plugin.json `hooks`, runs `foreman hook
NAME` with the event JSON on stdin). The hooks module (hooks/hooks.json `modules`) calls `foreman hook mod OP`
instead: hooks_mod.py.

Every hook first finds the project of the session by the event's `cwd` in the registry and does nothing (exit 0,
no output) unless it is an `active` project: with the plugin enabled for a user, sessions elsewhere are untouched.
No hook ever fails the call: any error ends in exit 0, except the guard's deliberate rejection (exit 2) and, in the
guard, a missing foreman module that this module imports where it uses it (exit 2 too: run). One imported at the top
(config, guard, i18n, identity, pause, util) or this module itself, missing, fails as Python does in lib/main.py
(exit 1, which Claude Code does not block on): there no registry is read yet, so refusing would block every
session of the plugin. lib/main.py runs this module without foreman.cli; it imports at the top only what the guard
needs (each tool call starts it).
The registry is read tolerantly (config.py): a project with warnings (unknown keys, a newer config schema) still gets
every hook, and `who` and the loop pass name the warnings. A project whose config does not load gets no hook at all:
the guard fails open; `who` and the loop pass say so (config.broken_for_cwd), the others keep quiet.
- agents  (SubagentStart, SubagentStop, PostToolUse, Stop): agent state in <state>/agents.json (forked from an earlier
          in-house version);
          keys start, type, transcript, end, final, final_chars, tool, activity, stopped, name,
          delivered. SubagentStop sets `end` (the agent delivered, or only stopped to wait for a task of its own);
          a PostToolUse of a Bash that runs `foreman progress --done` sets `delivered` (the delivery is that datum,
          by the event's agent_id); a PostToolUse with `end` set means the agent was resumed: `end` and `delivered`
          go. agents.agent_state reads them. The identity (identity.py): the PostToolUse of an Agent/Task
          call (the orchestrator's, without agent_id, or a subagent's) whose prompt carries a launch line
          (`[foreman launch TOKEN]`, from `foreman launch`/`foreman worktree`) and whose tool_response has the new
          `agentId` ties that launch to it: name, issue, sub, repo, branch, worktree, production, launch and
          named_by=launch go into its entry (created if SubagentStart did not come yet). An agent not tied that way
          is tied by the token in the head of its transcript (a foreground launch); without either it has no name
          (the `nameless_agents` notice; `foreman agents --name`). A production type launched without
          the line: `launch_line_missing` back to the launcher as additionalContext (a warning, never a block).
          The delivery (delivery.py): the `--outcome` of `progress --done` goes into `outcome` and its `--report`
          into `report` (both go when the agent is resumed or its stop is blocked); at the
          SubagentStop of a production agent tied to a launch with a branch or worktree (and no background tasks of
          its own) the delivery check runs, outside the agents.json lock: if something is missing the stop is blocked
          ({"decision": "block", "reason": …}; `end`, `delivered` and `outcome` go, `delivery_blocks` +1), at most
          delivery.MAX_BLOCKS times, then `delivery_incomplete` is recorded for the loop. Any error: no block.
          A session agent (identity.session_agent: its own Claude Code session, events with session_id and no
          agent_id) is the same entry keyed by its session id (SESSION_MARK; created at its first event by the launch
          token in its environment when `launch --session` could not tie it): its PostToolUse sets activity and
          `delivered`, its `Stop` is the SubagentStop above (same delivery check and block), its transcript is the
          event's. Subagent events of a session agent's session (Claude Code's compaction makes some) are ignored,
          and so is a `Stop` of any other session.
- resume  (SessionStart compact|resume): only in the marked orchestrator's session. After a compaction (`compact`:
          a renewal foreman asked for, Claude Code's automatic one or the owner's /compact) it settles an open
          renewal and prints foreman's pack (renew.resume_text: who the session still is, the owner's last
          messages verbatim, the board, the active backlog, open decisions, live agents, the rules); on `resume`,
          the board alone, as before.
- who     (SessionStart startup|resume|compact): in every other session of the project, two or three lines on who
          orchestrates it (name and since when, from `foreman session ID --name NAME`), that this session is not the
          orchestrator, and how to coordinate (SendMessage, foreman lock); «no orchestrator now» when none is marked.
          The orchestrator's own subagents share its session id, so they get nothing; nor does a session agent
          (identity.session_agent: a session foreman launched as an agent). When the marked session is
          not alive (orchestrator.health with this session as the caller: `gone`), or its loop is past its limit and
          its process cannot be checked (`silent`), `who_gone` / `who_silent` instead: mark yourself if you relieve
          it, otherwise tell the owner. A live orchestrator, or an unknown one within the limit, changes
          nothing.
- loop    (UserPromptSubmit): the in-between notices, only in the orchestrator's session.
          The pass itself does not come through here: foreman's hooks module runs it on its timer (hooks_mod.tick,
          hooks/mod/foreman.ts) and submits it as a prompt that starts with [loop] prompt and carries the summary;
          that prompt gets nothing more here. Any other prompt gets only the actionable notices whose case was not
          said yet (loop.notices, <state>/notices-last). Between prompts the module's tick computes the same
          notices and hands them to the model with the result of the orchestrator's next tool call; the chat
          reviewer runs in the module too (`hook mod answer`, plain.review_answer).
          In any other session of the product (not its subagents, nor a session agent) a prompt gets `who_gone` /
          `who_silent` once per
          case (sessions already open, which SessionStart never reached; from the marked session's heartbeat, which
          foreman's hooks module writes each minute, and the `loop` heartbeat); the pause changes nothing.
- guard   (PreToolUse on every tool, with no matcher in hooks/classic.json; guard.check_call): on
          any other tool, the places and commands its call names (guard.check_tool: a path as `cat PATH`, a command
          as in Bash, the pause too; a call that names neither leaves at once). On a write tool first the reserved
          files (guard.check_file, by the call's file_path or notebook_path: no caller record, no pause), then the
          same rule (writing a secret is refused as reading it); a refusal of an
          agent or another session goes to <state>/approved.jsonl with the file (notice orchestrator_only_denied),
          every one to <state>/guard.jsonl. The ctx carries the event's tool_use_id: a main-thread write of Claude
          Code's settings or the registry passes on the owner's one-use yes for that call, which it spends
          (grants.take_once; foreman's hooks module asks for it, `hook mod grant`). On Bash: resource prefixes and
          the deny list; while the product is paused from the panel (<state>/pause), also `foreman integrate` and
          `foreman worktree`.
          First it writes down who runs the command's `foreman progress` / `foreman lock` / `foreman renew` calls
          (identity.record_call:
          <state root>/callers.json, keyed by tool_use_id, with the session, the agent_id or null for the main
          thread, and the calls' words), so the CLI knows its caller without FOREMAN_WHO; the agents hook drops the
          entry on that Bash's PostToolUse (a background Bash's stays until the 2 h pruning). Only commands with
          such a call write; it never fails the call. The guard gets who calls (guard.Ctx: the event's session_id
          and agent_id, and identity.LAUNCH_ENV of this hook's environment: a session agent's main thread is an
          agent, and its refusals are logged as an agent's): a command of a `[deny.grants]` group passes only for
          the marked orchestrator's main thread, from its own process, with the owner's yes; each decision on one
          goes into <state>/approved.jsonl, and one that passes into today's notes too (approved.py). Every refusal
          of the
          guard is also a line in <state>/guard.jsonl (permissions.log_refusal: the rule, never the command's text);
          the allowed path does not import it.
- pause-agents (PreToolUse on Agent|Task): while the product is paused, the orchestrator's session may not launch a
          production subagent (`dev`, `foreman:dev`, [agents] production_types); the decision goes out as JSON
          (permissionDecision deny) with exit 0. Other types, other sessions and unpaused products: nothing.
`who` and `loop` on UserPromptSubmit also record the plugin root and version they run from (install.record: the
trace `foreman panel --follow-installed` follows); silently, and only when something changed.
The session's own version (versions.py): `who` (SessionStart) and `loop` on UserPromptSubmit also record the
session in <state root>/install/sessions.json and, when it runs an installed foreman older than the installed one
(or its plugin folder is marked `.orphaned_at`), tell it once per installed version: `version_behind` (or
`version_replaced` when the new version is not known yet) for the model, joined to the hook's own additionalContext,
and the owner's one line (`version_behind_owner`) as `systemMessage`. A session that gets no other prompt (an agent
launched with a single one) hears it from the module's tick, which stats `.orphaned_at` once a minute
(hooks_mod.version_told). Never for a subagent (`agent_id`). `run` collects the stdout of `who` and `loop` so that
one JSON object goes out (version_notice).
"""

from __future__ import annotations

import contextlib
import glob
import io
import json
import sys
import time
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

# Only what the guard needs to reach its verdict is imported here: `hook guard` runs on every tool call, so the rest
# (approved, delivery, loop, orchestrator, transcripts, …) is imported where it is used (lib/main.py runs this module
# without foreman.cli). In the guard, one of those missing refuses the call (run); for one of these, see the
# module doc.
from . import config, guard, i18n, identity, pause, util

if TYPE_CHECKING:
    from pathlib import Path

    from .config import BrokenEntry, Project
    from .identity import Agent, Event
    from .orchestrator import Health, Mark
    from .versions import Behind


def read_event() -> dict[str, Any] | None:
    try:
        ev = json.load(sys.stdin)
        return ev if isinstance(ev, dict) else None
    except (ValueError, OSError):
        return None


def registry(ev: Event) -> list[Project] | None:
    """The registry's projects, read once for the whole hook (the guard's rules read them too); None when the event
    has no cwd or the registry does not read (no project then, as project_for_cwd says)."""
    if not ev.get("cwd"):
        return None
    try:
        return config.registered_projects()
    except config.ConfigError:
        return None


def project_of(ev: Event, projects: Sequence[Project] | None = None) -> Project | None:
    cwd = ev.get("cwd")
    return config.project_for_cwd(cwd, active_only=True, projects=projects) if cwd else None


def out(obj: dict[str, Any]) -> None:
    """The hook's JSON answer to Claude Code, on stdout."""
    print(json.dumps(obj, ensure_ascii=False))


def emit(event: str, text: str) -> None:
    out({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}})


SESSION_SOURCES = ("startup", "resume", "compact")


def session_start(ev: Event) -> bool:
    """Whether a SessionStart event is one `who` speaks at (no source counts as startup)."""
    return ev.get("source", "startup") in SESSION_SOURCES


def is_orchestrator(project: Project, ev: Event) -> bool:
    from . import orchestrator

    return orchestrator.is_orchestrator(project, ev.get("session_id"))


def done_words(ev: Event) -> list[str] | None:
    """The words after `progress` of the `foreman progress --done` a PostToolUse Bash runs, or None."""
    ti = ev.get("tool_input")
    cmd = ti.get("command") if ev.get("tool_name") == "Bash" and isinstance(ti, dict) else None
    if not isinstance(cmd, str):
        return None
    for _, sub, rest in pause.foreman_calls(cmd):
        if sub == "progress" and "--done" in rest:
            return list(rest)
    return None


def hook_agents(project: Project, ev: Event) -> None:
    aid = ev.get("agent_id")
    name = ev.get("hook_event_name", "")
    if name not in ("SubagentStart", "SubagentStop", "PostToolUse", "Stop"):
        return
    if name == "PostToolUse" and ev.get("tool_name") == "Bash":
        identity.drop_call(ev)  # the call is over: the guard's entry goes (the main thread's too)
    launch_tool = name == "PostToolUse" and ev.get("tool_name") in identity.AGENT_TOOLS
    sa = identity.session_agent(project.state, ev.get("session_id"), identity.launch_env())
    if sa is not None and aid:
        return  # Claude Code's own subagents of a session agent (a compaction's): not agents of the product
    if name == "Stop":  # a session's turn ends: only a session agent's is an agent's stop
        if sa is None or not ev.get("session_id"):
            return
        aid = None
    sid = ev.get("session_id") if sa is not None and not aid and name in ("PostToolUse", "Stop") else None
    if not aid and not sid and not launch_tool:
        return
    from . import agents as agents_mod

    f = agents_mod.agents_file(project)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    warning: str | None = None
    entry: Agent | None = None
    with util.locked_json(f, {}) as d:
        if launch_tool:  # the launcher's call (orchestrator or a subagent): the new agent's identity
            warning = identity.launched(project, d, ev)[1]
        if aid:
            agent_event(project, d, aid, name, ev, now)
            if name == "SubagentStop" and aid in d and wants_check(project, d[aid], ev):
                entry = dict(d[aid])
        elif sid and session_entry(project, d, sid, ev):
            agent_event(project, d, sid, "SubagentStop" if name == "Stop" else name, ev, now)
            if name == "Stop" and wants_check(project, d[sid], ev):
                entry = dict(d[sid])
        cutoff = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 86400))
        util.write_json(f, {k: v for k, v in d.items() if v.get("end", "9") > cutoff})
    if warning:
        emit("PostToolUse", warning)
    if entry is not None and (aid or sid):  # outside the lock: the check runs git
        delivery_stop(project, f, str(aid or sid), entry)


def session_entry(project: Project, d: dict[str, Agent], sid: str, ev: Event) -> bool:
    """Makes sure session agent `sid` has its agents.json entry in `d` (held by the caller): the one `foreman launch
    --session` tied, else tied now by the launch token in the session's environment (the id could not be read at
    launch); its transcript is the event's. Whether the entry is there (a token foreman does not know leaves none)."""
    if sid not in d:
        token = identity.launch_env()
        if not token or not identity.link(project, d, sid, token, "launch"):
            return False
    a = d[sid]
    a[identity.SESSION_MARK] = True
    if not a.get("transcript") and isinstance(ev.get("transcript_path"), str):
        a["transcript"] = ev["transcript_path"]
    if ev.get("hook_event_name") == "PostToolUse":
        a.pop("stopped", None)  # it works again (woken after a stop)
    return True


def wants_check(project: Project, h: Agent, ev: Event) -> bool:
    """Whether this SubagentStop goes through the delivery check (delivery.py): a production agent tied to a launch
    record, with a branch or worktree, that does not stop to wait for background tasks of its own."""
    from . import delivery

    return delivery.applies(project, h) and not ev.get("background_tasks")


def delivery_stop(project: Project, f: Path, aid: str, entry: Agent) -> None:
    """The delivery check at an agent's stop (delivery.py): blocks it (prints Claude Code's SubagentStop block) at
    most delivery.MAX_BLOCKS times, then lets it go with `delivery_incomplete` in its entry (the loop's notice). Any
    error: no block."""
    from . import delivery

    try:
        res = {"items": delivery.missing(project, aid, entry)}
    except Exception:  # noqa: BLE001 (a delivery check never fails the hook)
        return
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    block: str | None = None
    with util.locked_json(f, {}) as d:
        a = d.get(aid)
        if not isinstance(a, dict):
            return
        if not res["items"]:
            a.pop("delivery_incomplete", None)
        else:
            n = int(a.get("delivery_blocks") or 0)
            missing = [k for k, _ in res["items"]]
            if n < delivery.MAX_BLOCKS:
                a["delivery_blocks"] = n + 1
                a["blocked_at"] = now
                for k in ("end", "delivered", "outcome", "report", "kind"):  # back to work: working, not delivered
                    a.pop(k, None)
                block = delivery.block_reason(project, res, n + 1)
            else:
                a["delivery_incomplete"] = {"at": now, "missing": missing}
        util.write_json(f, d)
    if block:
        out({"decision": "block", "reason": block})


def agent_event(project: Project, d: dict[str, Agent], aid: str, name: str, ev: Event, now: str) -> None:
    """One event of agent `aid` into the agents.json dict `d`."""
    from . import delivery, transcripts

    # Claude Code's progress summaries arrive as untyped subagents and live for an instant: skip them.
    if aid not in d and not ev.get("agent_type"):
        return
    a = d.setdefault(aid, {"start": now, "type": ev.get("agent_type")})
    if name == "SubagentStart" and ev.get("agent_type"):
        a["type"] = ev["agent_type"]
    if not a.get("transcript"):
        p = ev.get("agent_transcript_path") or next(
            iter(glob.glob(f"/private/tmp/claude-*/*/*/tasks/{aid}.output")), None
        )
        if p:
            a["transcript"] = p
    if a.get("transcript") and not a.get("launch") and not a.get("name"):
        # a foreground launch (its PostToolUse comes only at the end) or a lost one: the token in the brief
        token = identity.token_in(identity.transcript_head(a))
        if token:
            identity.link(project, d, aid, token, "launch", relink=False)
    if name == "SubagentStop":
        a["end"] = now
        msg = ev.get("last_assistant_message")
        if isinstance(msg, str):
            a["final"] = msg[:600]
        # the length of the whole final message (the orchestrator's context pays for it)
        tp = a.get("transcript")  # no transcript: final_chars reads nothing, None
        chars = len(msg) if isinstance(msg, str) else (transcripts.final_chars(tp) if tp else None)
        if chars is not None:
            a["final_chars"] = chars
        model = identity.transcript_model(a)
        if model:
            a["model"] = model
    elif name == "PostToolUse":
        a["tool"] = ev.get("tool_name")
        a["activity"] = now
        if a.pop("end", None):  # a resumed agent is running again, until it delivers again
            for k in ("delivered", "outcome", "report", "kind", "delivery_incomplete"):
                a.pop(k, None)
        # its delivery (delivery.py): outcome (an invalid one was refused by the CLI), report relative to the home
        # (not checked here: the delivery check and the pass do), `--kind measure`
        words = done_words(ev)
        outcome = delivery.outcome_in(words) if words else None
        if words and outcome:
            a["delivered"] = now
            a["outcome"] = outcome
            path = delivery.report_in(words)
            report = path and (delivery.report_rel(project, path, [a.get("worktree"), ev.get("cwd")]) or path)
            kind = delivery.kind_in(words)
            for k, v in (("report", report), ("kind", kind)):
                if v:
                    a[k] = v
                else:
                    a.pop(k, None)


def hook_resume(project: Project, ev: Event) -> None:
    if not is_orchestrator(project, ev) or ev.get("source") not in ("compact", "resume"):
        return
    if ev.get("source") == "compact":
        from . import renew

        try:
            text = renew.resume_text(project, ev)
        except Exception as e:  # noqa: BLE001 (the board below still comes back)
            text = None
            print(f"foreman's renewal pack failed ({type(e).__name__}: {e}); the board alone follows.")
        if text:
            print(text)
            return
    s = i18n.for_project(project)
    board = project.board.read_text() if project.board.exists() else s["no_board"]
    print(
        s["resume"].format(procedure="docs/orchestrator.md + " + project.section("project").get("rules", "rules.md"))
        + "\n\n"
        + board
    )


def short_time(project: Project, iso: str | None) -> str:
    """hh:mm of a local ISO time if it is today, otherwise YYYY-MM-DD hh:mm; «none seen» without one."""
    if not iso:
        return i18n.for_project(project)["time_none"]
    t = str(iso).replace("T", " ")[:16]
    return t[11:] if t[:10] == time.strftime("%Y-%m-%d") else t


def marked_name(o: Mark) -> str:
    return o["name"] or o["session"][:8] + "…"


def notified(project: Project, fname: str, key: str, value: str) -> bool:
    """Whether `key` already has `value` in <state>/fname (a once-per-case marker); records it if not. The file keeps
    the last 50 keys."""
    f = project.state / fname
    with util.locked_json(f, {}) as raw:
        d = raw if isinstance(raw, dict) else {}
        if d.get(key) == value:
            return True
        d.pop(key, None)
        d[key] = value
        util.write_json(f, dict(list(d.items())[-50:]))
    return False


def not_alive_text(project: Project, o: Mark, session_id: str | None, h: Health | None) -> str:
    """`who_gone` or `who_silent` for a session that is not the marked orchestrator, from its health; '' otherwise."""
    if not h or h["verdict"] not in ("gone", "silent"):
        return ""
    return i18n.for_project(project)["who_" + h["verdict"]].format(
        project=project.name,
        name=marked_name(o),
        since=short_time(project, o["since"]),
        last=short_time(project, h["last_pass"] if h["verdict"] == "gone" else h["window_from"]),
        limit=h["limit_min"],
        session=session_id or "<your session id>",
        owner=project.owner,
    )


def case_key(o: Mark, h: Health) -> str:
    """The case a who_gone/who_silent notice is about: the marked session, the verdict and its last pass."""
    return f"{o['session']}:{h['verdict']}:{h['last_pass'] or '-'}"


def gone_notice(project: Project, o: Mark, session_id: str | None) -> tuple[str, bool]:
    """(who_gone / who_silent for this session when the marked session `o` is gone or silent, from its health
    (orchestrator.health), '' otherwise; whether that case was already said to this session). The case is recorded
    as said (<state>/who-gone-notified.json {session: case})."""
    from . import orchestrator

    h = orchestrator.health(project, o)
    text = not_alive_text(project, o, session_id, h)
    if not text or h is None or not session_id:  # a text only comes from a health
        return text, False
    return text, notified(project, "who-gone-notified.json", session_id, case_key(o, h))


def who_text(project: Project, session_id: str | None) -> str:
    """The notice for a session that is not the project's orchestrator ('' for the orchestrator itself and for a
    session agent, is_session_agent). When the
    marked session is gone or silent, who_gone / who_silent instead of who_other (gone_notice: open_session_notice
    does not repeat it)."""
    from . import orchestrator

    o = orchestrator.current(project)
    if o and session_id and session_id == o["session"]:
        return ""
    if is_session_agent(project, session_id):
        return ""  # its brief says who it is and whom it answers to
    s = i18n.for_project(project)
    if not o:
        return s["who_none"].format(project=project.name)
    text = gone_notice(project, o, session_id)[0]
    if text:
        return text
    since = short_time(project, o["since"])
    if o["name"]:
        return s["who_other"].format(project=project.name, name=o["name"], since=since)
    return s["who_other_unnamed"].format(project=project.name, session=o["session"][:8], since=since)


def is_session_agent(project: Project, session_id: str | None) -> bool:
    """Whether a session is a session agent foreman launched (identity.session_agent, with this hook's
    LAUNCH_ENV)."""
    return identity.session_agent(project.state, session_id, identity.launch_env()) is not None


def open_session_notice(project: Project, session_id: str | None) -> str:
    """who_gone / who_silent for a session already open (SessionStart never reached it with that
    case), once per case and session (<state>/who-gone-notified.json {session: case}); '' otherwise. Reads the mark,
    the `loop` heartbeat and the marked session's heartbeat (orchestrator.health), no process."""
    from . import orchestrator

    o = orchestrator.current(project)
    if not o or not session_id or session_id == o["session"] or is_session_agent(project, session_id):
        return ""
    text, said = gone_notice(project, o, session_id)
    return "" if said else text


def warnings_text(project: Project) -> str:
    """The line on the config warnings of a project that loads ('' if none)."""
    if not project.warnings:
        return ""
    return i18n.for_project(project)["config_warnings"].format(
        project=project.name, warnings="; ".join(project.warnings)
    )


def broken_text(entry: BrokenEntry, project: Project) -> str:
    """The line for a session in a product whose config does not load (the guard is off there)."""
    return i18n.for_project(project)["config_broken"].format(project=entry["name"], error=entry["error"])


def claude_min_texts(project: Project, session_id: str | None) -> tuple[str, str] | None:
    """(text for the model, line for the owner) when the installed Claude Code is below the minimum and this session
    was not told yet (once per session, <state>/claude-min-notified.json); None otherwise, and for a version that
    cannot be read."""
    from . import versions

    found = versions.claude_code_version()
    if not found or not versions.claude_code_below_min(found):
        return None
    if session_id and notified(project, "claude-min-notified.json", session_id, versions.CLAUDE_CODE_MIN):
        return None
    s = i18n.for_project(project)
    fields = {"min": versions.CLAUDE_CODE_MIN, "found": found}
    return s["claude_min"].format(**fields), s["claude_min_owner"].format(**fields)


def hook_who(project: Project, ev: Event) -> None:
    if not session_start(ev):
        return
    text = "\n".join(x for x in (who_text(project, ev.get("session_id")), warnings_text(project)) if x)
    old = claude_min_texts(project, ev.get("session_id"))
    if not old:
        if text:
            emit("SessionStart", text)
        return
    context = "\n".join(x for x in (old[0], text) if x)
    out(
        {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context}, "systemMessage": old[1]}
    )


def hook_broken(name: str, ev: Event) -> None:
    """`who` and the loop pass in a product whose config does not load: say so (nothing else runs there)."""
    event = ev.get("hook_event_name", "")
    if name == "loop" and event != "UserPromptSubmit":
        return
    hit = config.broken_for_cwd(ev["cwd"]) if ev.get("cwd") else None
    if not hit:
        return
    entry, project = hit
    if name == "who" and session_start(ev):
        emit("SessionStart", broken_text(entry, project))


def hook_loop(project: Project, ev: Event) -> None:
    event = ev.get("hook_event_name", "")
    if not is_orchestrator(project, ev):
        if event == "UserPromptSubmit" and not ev.get("agent_id"):
            text = open_session_notice(project, ev.get("session_id"))
            if text:
                emit(event, text)
        return
    if event != "UserPromptSubmit" or ev.get("agent_id"):
        return
    prompt = project.section("loop").get("prompt") or ""
    if prompt and ev.get("prompt", "").lstrip().startswith(prompt):
        return  # the pass the hooks module submitted: it carries its own summary
    from . import loop

    texts = [n["text"] for n in loop.notices(project, ev.get("session_id"))]
    if texts:
        emit(event, " ".join(texts))


def hook_guard(project: Project, ev: Event, projects: Sequence[Project] | None = None) -> int:
    """`projects`: the registry's, if already loaded (run reads it once to find the project)."""
    tool = ev.get("tool_name")
    if tool not in (None, "Bash"):
        return guard_tool(project, ev, tool, projects)
    cmd = (ev.get("tool_input") or {}).get("command", "")
    if cmd:
        identity.record_call(project, ev)  # who runs its foreman calls (never fails)
    ctx = _guard_ctx(ev)
    verdict = guard.check(project, cmd, ctx, projects) if cmd else None
    if verdict:
        return _refuse(project, ev, verdict)
    held = pause.guard_check(project, cmd) if cmd else None
    if held:
        print(pause.guard_message(project, held), file=sys.stderr)
        return 2
    _passed(project, ev, ctx)
    return 0


def _guard_ctx(ev: Event) -> guard.Ctx:
    """Who calls, for the guard: the event's session, agent and folder, and the launch token of a session agent's
    session (identity.LAUNCH_ENV, in this hook's environment)."""
    return {
        "session": ev.get("session_id"),
        "agent": ev.get("agent_id") or None,
        "agent_type": ev.get("agent_type") or None,
        "cwd": ev.get("cwd"),
        "tool_use_id": ev.get("tool_use_id") or None,
        "launch": identity.launch_env(),
    }


def _passed(project: Project, ev: Event, ctx: guard.Ctx) -> None:
    """A [deny.grants] command that passed with the owner's yes goes to approved.jsonl and today's notes. The call
    passed already: a failure here (its import too) does not turn it into a refusal."""
    if "passed" not in ctx:
        return
    try:
        from . import approved

        approved.decided(project, ev, ctx["passed"], allowed=True)
    except Exception as e:  # noqa: BLE001 (a pass stays a pass)
        print(f"foreman: the guard could not record this pass ({type(e).__name__}: {e})", file=sys.stderr)


def _refuse(project: Project, ev: Event, verdict: guard.Verdict) -> int:
    """Exit 2 with the reason; the refusal of a [deny.grants] rule or a reserved file for an agent or another session
    goes to approved.jsonl (the orchestrator's notice), every refusal to guard.jsonl. The verdict is already taken:
    nothing that fails here (those records, their imports, the message) turns it into a pass."""
    try:
        if verdict[0] == "orchestrator" or (verdict[0] == "file" and verdict[1].why):
            from . import approved

            approved.decided(project, ev, verdict[1], allowed=False)
        from . import permissions  # only on a refusal: the allowed path stays as it was

        permissions.log_refusal(project, ev, verdict)
    except Exception as e:  # noqa: BLE001 (a refusal stays a refusal)
        print(f"foreman: the guard could not record this refusal ({type(e).__name__}: {e})", file=sys.stderr)
    try:
        text = guard.message(project, verdict)
    except Exception as e:  # noqa: BLE001 (a refusal stays a refusal)
        text = f"foreman's guard refused this call ({verdict[0]}); its message failed ({type(e).__name__}: {e})"
    print(text, file=sys.stderr)
    return 2


def guard_tool(project: Project, ev: Event, tool: str, projects: Sequence[Project] | None = None) -> int:
    """The guard on a call of any tool but Bash (guard.check_call): a FILE_TOOLS tool by the file it writes
    (check_file), then like any other, by the places and commands it names (check_tool); a command it runs also
    waits while the product is paused, as in Bash. A call that names nothing leaves before anything is built."""
    ti = ev.get("tool_input")
    path = guard.tool_path(tool, ti if isinstance(ti, dict) else None)
    places, commands = guard.tool_texts(tool, ti)
    if path is None and not places and not commands:
        return 0
    ctx = _guard_ctx(ev)
    verdict = guard.check_call(project, tool, ti, ctx, projects)
    if verdict:
        return _refuse(project, ev, verdict)
    for cmd in commands:
        held = pause.guard_check(project, cmd)
        if held:
            print(pause.guard_message(project, held), file=sys.stderr)
            return 2
    _passed(project, ev, ctx)
    return 0


def hook_pause_agents(project: Project, ev: Event) -> None:
    if ev.get("tool_name") not in ("Agent", "Task") or not is_orchestrator(project, ev):
        return
    at = pause.since(project)
    ti = identity.tool_input(ev)
    kind = ti.get("subagent_type")
    reason = ""
    if at and isinstance(kind, str) and identity.role(project, kind) == "production":
        reason = i18n.for_project(project)["pause_agents"].format(
            project=project.name, since=at, type=kind, owner=project.owner
        )
    dear = identity.call_too_dear(project, ti)
    if dear and not reason:
        reason = i18n.for_project(project)["launch_dearer"].format(
            plan=dear[0], type=identity.split_type(dear[0])[0], model=dear[1]
        )
    if not reason:
        return
    out(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }
    )


HOOKS: dict[str, Callable[[Project, Event], int | None]] = {
    "agents": hook_agents,
    "resume": hook_resume,
    "who": hook_who,
    "loop": hook_loop,
    "guard": hook_guard,
    "pause-agents": hook_pause_agents,
}


def version_texts(project: Project, b: Behind) -> tuple[str, str]:
    """(text for the model, line for the owner) of a session left behind (versions.behind)."""
    s = i18n.for_project(project)
    fields = {
        "mine": b["mine"],
        "root": b["root"],
        "installed": b["installed"] or "",
        "installed_root": b["installed_root"] or "",
        "owner": project.owner,
    }
    which = "version_behind" if b["installed"] else "version_replaced"
    return s[which].format(**fields), s[which + "_owner"].format(**fields)


def merge_notice(out: str, event: str, text: str, owner: str) -> tuple[str, bool] | None:
    """The hook's stdout `out` with the version notice added: (new stdout, whether the model hears it). One JSON
    object only: the text joins the hook's own additionalContext (or goes alone), the owner's line goes in
    `systemMessage` (neither `who` nor `loop` blocks a prompt). Output that is not one JSON object is left alone
    (None)."""
    raw = out.strip()
    obj: Any = {}  # a JSON value until the isinstance below
    if raw:
        try:
            obj = json.loads(raw)
        except ValueError:
            if raw.startswith("{"):
                return None
            obj = {"hookSpecificOutput": {"hookEventName": event, "additionalContext": raw}}  # plain text = context
        if not isinstance(obj, dict):
            return None
    hso = obj.setdefault("hookSpecificOutput", {"hookEventName": event})
    hso["additionalContext"] = "\n\n".join(x for x in (hso.get("additionalContext"), text) if x)
    obj["systemMessage"] = " ".join(x for x in (obj.get("systemMessage"), owner) if x)
    return json.dumps(obj, ensure_ascii=False) + "\n", True


def version_notice(name: str, project: Project, ev: Event, best: str | None, out: str) -> str:
    """`out` (the hook's stdout) plus, when this session runs a foreman older than the installed one and was not
    told about this installed version yet, the notice to the model and the owner (versions.pending). Only in
    `who` (SessionStart) and `loop` on UserPromptSubmit (the module's tick covers sessions with no prompt:
    hooks_mod.version_told). Never for a subagent. Never raises."""
    try:
        event = ev.get("hook_event_name") or ""
        if ev.get("agent_id") or not ev.get("session_id"):
            return out
        if name == "who":
            if not session_start(ev):
                return out
            event = "SessionStart"
        elif name != "loop" or event != "UserPromptSubmit":
            return out
        from . import versions

        b = versions.pending(ev, project.name, best=best)
        if not b:
            return out
        merged = merge_notice(out, event, *version_texts(project, b))
        if not merged:
            return out
        if merged[1]:
            versions.mark_told(b["session"], b["key"])
        return merged[0]
    except Exception:  # noqa: BLE001 (a version notice never fails the hook)
        return out


def run(name: str) -> int:
    if name not in HOOKS:
        print(f"unknown hook {name} ({', '.join(HOOKS)})", file=sys.stderr)
        return 0
    try:
        ev = read_event()
        projects = registry(ev) if ev else None
        project = project_of(ev, projects) if ev and projects is not None else None
        if project is None or ev is None:
            if ev and name in ("who", "loop"):
                hook_broken(name, ev)
            return 0
        best: str | None = None
        if name == "who" or (name == "loop" and ev.get("hook_event_name") == "UserPromptSubmit"):
            from . import install

            best = install.record()  # where the sessions run foreman from (the panel follows it); never raises
        if name not in ("who", "loop"):
            try:
                rc = hook_guard(project, ev, projects) if name == "guard" else HOOKS[name](project, ev)
            except ImportError as e:
                if name != "guard" or (e.name or "").split(".")[0] != "foreman":
                    raise
                # a module of foreman that the guard imports where it is used is missing: the guard cannot judge
                # this call, and it is refused rather than let through (the launcher does the same without a Python)
                print(f"foreman's guard cannot run ({e}); the call is refused. Reinstall foreman.", file=sys.stderr)
                return 2
            return rc if name == "guard" and rc else 0
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                HOOKS[name](project, ev)
        finally:
            sys.stdout.write(version_notice(name, project, ev, best, buf.getvalue()))
        return 0
    except Exception:  # noqa: BLE001 (a hook never fails the call)
        return 0
