"""What the orchestrator should act on: foreman's own notices of the loop pass and of the time between passes.

Forked from an earlier in-house version. The generic skeleton stays here (agents,
pipeline targets, locks, branches ready to integrate, backlog problems, inbox, recurring tasks, hung shells, disk);
everything a project needs on top (models, caches, simulators, containers…) comes from `[loop] checks` in
foreman.toml (notices.run_checks). The notice contract (what a notice is, render, idle, the list of kinds) is
notices.py. The pass is run by foreman's hooks module (hooks/mod/foreman.ts, its timer: hooks_mod.tick), never
by a scheduled task. The kinds of notice and when each is actionable are in the table of docs/orchestrator.md.
While the product is paused from the panel (pause.py) the pass starts with a PAUSED notice and drops the WIP
notices that ask to launch agents (also between passes).

notices_for_pass() builds the pass from one function per group of notices, in the order the summary shows them, and
notices() the cheap part between passes (only actionable notices go out, each case once, the WIP notices once per class
until the next pass, <state>/notices-last).

Who an agent is (name, issue, branch, production) comes from identity.of: what `foreman launch` or `foreman agents
--name` recorded in agents.json first, the old inference from the brief only for what is missing. A worktree branch
the backlog names is not ready to integrate while a live agent has it as its recorded branch, its sub-issue names a
live agent, or (for live agents with no recorded identity only) the agent's brief names the worktree's folder. A branch
whose open sub-issues are all measurements (delivery.branch_kind) is not ready to integrate either: it comes as
`measure_delivered`, to close with `foreman integrate --measure`; one whose sub-issues are all closed without a
commit (closed on purpose) comes in neither. A local branch with commits not in main and no worktree, named by an open
sub-issue with no live agent, is `branch_no_worktree` (recreate the worktree, then integrate), and its issue is not
`orphan_in_progress`. A delivered branch whose review does not let it in yet (review.py, a repo with `review =
"auto"`) is not ready either: it comes as `review_needed` (launch a reviewer) or `review_findings` (back to its agent,
or after round 2 a question for the owner). One that no sub-issue names, that the backlog's text does not mention,
that is not on the remote (as the last fetch left it) and whose content is not in main (cherry-picked or squashed)
is `loose_branches`.

Everything here only reads, except the markers that keep a notice from repeating, the `loop` heartbeat and the
pass's line in `<state>/loop.jsonl` (the history of the passes `foreman catchup` reads).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Collection, Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import (
    approved,
    backlog_check,
    delivery,
    i18n,
    identity,
    lock,
    notes,
    orchestrator,
    pause,
    review,
    util,
    worktree,
)
from . import backlog as backlog_mod
from .backlog import Backlog, BacklogError, Data, Holds, Issue
from .notices import Notice, changed_mark, notice, render, run_checks
from .notices import idle as nothing_actionable

if TYPE_CHECKING:
    from .config import Project
    from .identity import Agent, Identity
    from .util import PathLike
    from .versions import Entry, Found


def agents_file(project: Project) -> Path:
    return project.state / "agents.json"


def agents(project: Project) -> dict[str, Agent]:
    return util.read_json(agents_file(project), {})


WAIT_LIMIT = 3 * 3600  # an agent stopped without delivering counts as live this long (the last resort)
LIVE_STATES = ("working", "waiting")


def agent_state(h: Agent, now: float | None = None) -> str:
    """The one state of an agents.json entry, for the loop, `foreman agents` and the panel:
    `stopped` (marked with `foreman agents --stop`, or stopped without delivering more than WAIT_LIMIT ago),
    `delivered` (it ran `foreman progress --done`: the agents hook sets `delivered`, and clears it when the agent is
    resumed), `waiting` (it stopped without delivering: waiting for a background task of its own) or `working`."""
    if h.get("stopped"):
        return "stopped"
    if h.get("delivered"):
        return "delivered"
    end = h.get("end")
    if end:
        return "waiting" if (now or time.time()) - (util.iso_epoch(end) or 0) <= WAIT_LIMIT else "stopped"
    return "working"


def live_agents(project: Project) -> list[Agent]:
    """Agents working or waiting for a task of their own (agent_state); never delivered or stopped ones."""
    return [{"id": aid, **h} for aid, h in agents(project).items() if agent_state(h) in LIVE_STATES]


def last_sign(h: Agent) -> float | None:
    """Epoch seconds of an agent's last sign of life: the latest of its recorded tool activity (`activity`, else
    `start`) and its transcript's modification time; None if neither is known."""
    times: list[float | None] = [util.iso_epoch(h.get("activity") or h.get("start"))]
    with contextlib.suppress(OSError, TypeError, ValueError):
        times.append(os.path.getmtime(h.get("transcript") or ""))
    found = [t for t in times if t is not None]
    return max(found) if found else None


def quiet_minutes(project: Project, h: Agent, busy: Collection[str] = (), now: float | None = None) -> int | None:
    """Minutes a live agent (agent_state) has been quiet (last_sign) when that is at least [agents] stalled_min
    (default 20) and its name is not in `busy` (lock.people: it holds or waits for a lock), else None. One rule for
    the loop's `stalled_agents` and the panel's «maybe stuck»: usually waiting for a background job that already
    ended."""
    now = now or time.time()
    last = last_sign(h)
    if agent_state(h, now) not in LIVE_STATES or last is None:
        return None
    if now - last < 60 * project.section("agents").get("stalled_min", 20):
        return None
    if busy and h.get("name") in busy:
        return None
    return int((now - last) / 60)


DEAD_STATUSES = ("failed", "killed")  # `$.agent.list()` statuses of a loop that will not come back


def dead_agents(project: Project, session: str | None, live: Iterable[Agent] | None = None) -> dict[str, str]:
    """{agent id: why} of the live agents (live_agents) that are dead, seen from the marked orchestrator's session
    `session`: its last tick lists them as failed or killed (`$.agent.list()`, heartbeat.agents_of), or they run in
    another session whose heartbeat is not alive (gone, or none: a session the module never ran in counts as gone
    here, as an earlier session whose agents died with it). An agent whose session is not known (no transcript) is
    not dead. `why` is the status, or "session gone"."""
    from . import heartbeat, transcripts

    statuses = heartbeat.agents_of(project, session) if session else {}
    states: dict[str, str] = {}
    out: dict[str, str] = {}
    for h in live_agents(project) if live is None else live:
        st = statuses.get(h["id"])
        if st in DEAD_STATUSES:
            out[h["id"]] = st
            continue
        sid = transcripts.session_of_subagent(h.get("transcript")) or h.get("session")
        if not sid or sid == session:
            continue
        if sid not in states:
            states[sid] = orchestrator.liveness(project, sid)["state"]
        if states[sid] != "alive":
            out[h["id"]] = "session gone"
    return out


def stalled(project: Project, session: str | None = None) -> list[str]:
    """`name (N min)` of each live agent quiet_minutes calls stalled, with its `$.agent.list()` status when the
    marked session `session` lists it (`name (N min, idle)`); not the dead ones (dead_agents)."""
    from . import heartbeat

    holders, waiting = lock.people(lock.all_status(project).values())
    live = live_agents(project)
    dead = dead_agents(project, session, live) if session else {}
    statuses = heartbeat.agents_of(project, session) if session else {}
    out: list[str] = []
    for h in live:
        if h["id"] in dead:
            continue
        m = quiet_minutes(project, h, holders | waiting)
        if m is not None:
            name = h.get("name") or Path(h.get("transcript", "")).stem[:8] or h["id"][:8]
            st = statuses.get(h["id"])
            out.append(f"{name} ({m} min{', ' + st if st else ''})")
    return out


def known_names(project: Project) -> set[str]:
    names = {"orchestrator", project.owner.lower(), "owner"}
    for h in agents(project).values():
        if time.time() - (util.iso_epoch(h.get("start")) or 0) < 86400:
            n = h.get("name")
            if n:
                names.add(n)
    return names


def lock_notices(project: Project, mark: bool = True) -> list[Notice]:
    """Foreign names in a lock or its queue (not an agent of this project: another session) and a lock held for more
    than [loop] lock_long_min minutes (60), one notice per case. Each case is announced once (keys in
    <state>/lock-notified), then left out. `lock_foreign` is actionable unless the foreign name holds the lock through
    `run` and that process is alive (the other session uses it as it should); `lock_long` always is.
    mark=False reads the marks without writing them."""
    known = known_names(project)
    f = project.state / "lock-notified"
    try:
        seen = set(f.read_text().split("\n"))
    except OSError:
        seen = set()
    out: list[Notice] = []
    keys: list[str] = []
    hint = ": check it is used well (`foreman lock RESOURCE check`)."
    long_min = project.section("loop").get("lock_long_min", 60)
    for name, st in lock.all_status(project).items():
        for x, holds in [(h, True) for h in st["holders"] if not h["taking"]] + [(q, False) for q in st["queue"]]:
            if x["who"] not in known:
                # a holder by its owner line (what, date, pid): its `since` comes from `meta`, which lock.sh writes
                # after `owner`, so a pass between the two would announce the same case again
                key = f"foreign {name} {x['who']} {x['what'] if holds else x['since']}"
                keys.append(key)
                if key not in seen:
                    running = holds and x.get("alive") is True
                    out.append(
                        notice(
                            "lock_foreign",
                            not running,
                            f"Locks: {name}: {x['who']} ({'holds it' if holds else 'queued'}: {x['what']}) is "
                            "not of this session" + (" (its `run` is alive)." if running else hint),
                            key,
                        )
                    )
            if holds and lock.held_minutes(x) > long_min:
                key = f"long {name} {x['who']} {x['since']}"
                keys.append(key)
                if key not in seen:
                    out.append(
                        notice(
                            "lock_long",
                            True,
                            f"Locks: {name}: {x['who']} has held it for {int(lock.held_minutes(x))} min" + hint,
                            key,
                        )
                    )
    if out and mark:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("\n".join(sorted(seen | set(keys)))[-4000:])
    return out


def lock_queues(project: Project) -> list[Notice]:
    """One `lock_queue` notice per resource with a queue. Information only while every holder took the lock through
    `run` and its process is alive, or is just taking it (lock.status `taking` "writing": the queue moves by itself);
    actionable if a holder is dead, took it without `run` (`acquire`, whose pid ended with the call), gives no pid,
    took it and never wrote its owner line ("stuck"), or nobody holds it."""
    out: list[Notice] = []
    for name, st in lock.all_status(project).items():
        if not st["queue"]:
            continue
        going = lambda h: h.get("alive") is True or h.get("taking") == "writing"  # noqa: E731
        moving = bool(st["holders"]) and all(going(h) for h in st["holders"])
        stuck = [h["who"] for h in st["holders"] if not going(h)]
        text = (
            f"Lock {name}: {', '.join(h['who'] for h in st['holders']) or 'free'}; queue: "
            + ", ".join(q["who"] for q in st["queue"])
            + (
                ""
                if moving
                else (
                    f" (no live `run` behind {', '.join(stuck)}: check it, `foreman lock {name} check`)"
                    if stuck
                    else f" (free with a queue: check it, `foreman lock {name} check`)"
                )
            )
        )
        out.append(
            notice(
                "lock_queue",
                not moving,
                text,
                f"lock_queue:{name}:{','.join(h['who'] for h in st['holders'])}:"
                + ",".join(q["who"] for q in st["queue"]),
            )
        )
    return out


HELD_SHOWN = 3  # issues or sub-issues that wait for a decision, named in the text of wip_production


def held_waits(b: Backlog, i: Issue, rb: dict[str, list[str]], holds: Holds) -> list[str]:
    """What of a not launchable ready issue waits for which decision: «XX-1 waits for decision XX-1 J1» when held
    whole, else «XX-1 T3 waits for decision XX-1 J1» for each sub-issue to do. [] when it is blocked otherwise."""
    if b.is_blocked(i, rb):
        return []
    whole = b.held_by(holds, i["id"])
    if whole:
        return [f"{i['id']} waits for decision {', '.join(whole)}"]
    return [
        f"{i['id']} {s['id']} waits for decision {', '.join(ids)}"
        for s in i["subs"]
        if s["status"] == "todo" and (ids := b.held_by(holds, i["id"], s["id"]))
    ]


HOLD_UNTIL = {  # what new production waits for in pipeline(), by the kind of the notice that holds it
    "context_rotate": "this session renews its memory (context_rotate)",
}


def pipeline(project: Project, rotating: str | bool = False) -> list[Notice]:
    """The WIP notices when production or ready is below target (none otherwise). `wip_production` is actionable only
    if a launchable ready issue is there (Backlog.launchable: unblocked, and not held by a pending decision; an answer
    from the panel resolves its decision) and nothing holds new production (`rotating`: the session renews its memory
    once its agents deliver; True, or the kind of the notice that holds it: context_rotate); its text names what of a
    ready issue waits for which decision (held_waits, up to HELD_SHOWN). `wip_ready` only if something can be planned
    (an unblocked backlog or refining issue) and no planner agent is live. Below target with nothing to do (a backlog
    with only triage, on purpose) is information."""
    cfg = project.section("agents")
    goal_prod, goal_ready = cfg.get("production_slots", 1), cfg.get("ready_target", 2)
    live = live_agents(project)
    prod = sum(1 for h in live if identity.is_production(project, h))
    b = Backlog(project)
    try:
        data = b.load()
        issues = [i for _, i, _ in b.iter_issues(data, archived=False)]
        rb = b.relation_blockers(data)  # blocked: a free-text reason or an open blocker through relations.blocks
        holds = b.decision_holds(data)
    except (BacklogError, OSError, ValueError, KeyError):
        return []
    ready = sum(1 for i in issues if i["status"] == "ready" and b.launchable(i, rb, holds))
    held = [
        w
        for i in issues
        if i["status"] == "ready" and not b.launchable(i, rb, holds)
        for w in held_waits(b, i, rb, holds)
    ]
    refining = sum(1 for i in issues if i["status"] == "refining")
    to_plan = [i["id"] for i in issues if i["status"] in ("backlog", "refining") and not b.is_blocked(i, rb)]
    planner = any(identity.role(project, h.get("type")) == "planner" for h in live)
    out: list[Notice] = []
    if prod < goal_prod:
        waits = (
            " (" + "; ".join(held[:HELD_SHOWN]) + ("; …" if len(held) > HELD_SHOWN else "") + ")"
            if held and not rotating
            else ""
        )
        text = f"WIP (pipeline not full): production {prod}/{goal_prod}: " + (
            "no unblocked `ready` issue to launch"
            if not ready
            else f"launch nothing new until {HOLD_UNTIL.get(str(rotating), HOLD_UNTIL['context_rotate'])}"
            if rotating
            else "launch the next `ready` issue"
        )
        out.append(
            notice(
                "wip_production",
                ready > 0 and not rotating,
                text + waits + ".",
                f"wip_production:{prod}/{goal_prod}:ready{ready}"
                + (f":held{len(held)}" if held else "")
                + (":rotating" if rotating else ""),
            )
        )
    if ready < goal_ready:
        can = bool(to_plan) and not planner
        why = (
            "plans for the next ones (" + ", ".join(to_plan[:6]) + ("…" if len(to_plan) > 6 else "") + ")"
            if can
            else "a planner is working"
            if to_plan
            else "nothing to plan (only triage, blocked or ready issues)"
        )
        out.append(
            notice(
                "wip_ready",
                can,
                f"WIP (pipeline not full): unblocked ready {ready}/{goal_ready} (refining {refining}): {why}.",
                f"wip_ready:{ready}/{goal_ready}:plan{len(to_plan)}:planner{int(planner)}",
            )
        )
    return out


def new_inbox(project: Project) -> list[notes.Message]:
    """Unread owner messages not announced yet (remembers the newest announced id)."""
    mark = project.state / "inbox-notified"
    try:
        seen = int(mark.read_text())
    except (OSError, ValueError):
        seen = 0
    new = [m for m in notes.inbox_new(project) if m["id"] > seen]
    if new:
        mark.parent.mkdir(parents=True, exist_ok=True)
        mark.write_text(str(max(m["id"] for m in new)))
    return new


def hung_shells() -> list[str]:
    # FOREMAN_HUNG_SHELLS_SH replaces the script (the selftest: the machine's real shells must not decide a test)
    sh = os.environ.get("FOREMAN_HUNG_SHELLS_SH") or Path(__file__).resolve().parent / "sh/hung_shells.sh"
    try:
        r = subprocess.run(["bash", str(sh)], capture_output=True, text=True, timeout=10, check=False)
    except subprocess.TimeoutExpired:
        return []
    return [ln.strip() for ln in r.stdout.splitlines() if ln.startswith("pid ")] if r.returncode == 1 else []


def session_agents(project: Project, session: str | None) -> list[Agent]:
    """The live agents that run inside `session` (or whose transcript is not known): they would die with it."""
    return [h for h in live_agents(project) if not h.get("transcript") or f"/{session}/" in h["transcript"]]


def foreman_versions(project: Project, session: str | None) -> tuple[dict[str, Any] | None, list[Entry], Found | None]:
    """(what the orchestrator's session runs: versions.of_session, or None; the live entries of this product's other
    sessions that run an older foreman; the installed {version, root} or None). Never raises: a version that cannot
    be read is no notice."""
    from . import versions

    try:
        inst = versions.installed()
        entries = versions.live_sessions(inst=inst)
        mine = versions.of_session(session, entries) if session else None
        others = [e for e in entries if e["project"] == project.name and e["session"] != session and e["behind"]]
        return mine, others, inst
    except Exception:  # noqa: BLE001 (never fail a pass over a version)
        return None, [], None


def reloads_itself(session: str | None) -> bool:
    """Whether the session's host loads a new version by itself (versions.reloads_itself). Never raises."""
    from . import versions

    try:
        return versions.reloads_itself(session)
    except Exception:  # noqa: BLE001 (never fail a pass over a version)
        return False


def foreman_behind_notice(
    project: Project, session: str | None, mine: Mapping[str, Any] | None, mark: bool = True
) -> Notice | None:
    """The `foreman_behind` notice, or None: the orchestrator's own session runs an older foreman than the installed
    one. Nothing during the grace after the install (versions.settling: some hosts, the desktop app among them, load
    it by themselves). After it, information when the host loads the new version here by itself
    (versions.reloads_itself: the desktop app, once the session is free). Otherwise only the owner can load it,
    typing /reload-plugins in this session (a terminal included): actionable once per session and installed version
    (<state>/foreman-behind-notified holds the case), then information; nothing else waits for it. An estimated
    version («≈», versions.estimate) is always information."""
    b = (mine or {}).get("behind")
    if not b or b.get("settling"):
        changed_mark(project, "foreman-behind-notified", None, mark)
        return None
    inst = b.get("installed") or "a newer install"
    head = f"foreman {'≈' if b.get('estimated') else ''}{b['mine']} in this session, {inst} installed"
    if b.get("estimated"):
        return notice(
            "foreman_behind",
            False,
            f"{head} (estimated: no record of its own): {project.owner} can type /reload-plugins here to load it.",
            f"foreman_behind:{session}:{inst}:estimated",
        )
    if reloads_itself(session):
        key = f"foreman_behind:{session}:{inst}:host"
        changed_mark(project, "foreman-behind-notified", key, mark)
        return notice(
            "foreman_behind",
            False,
            f"{head}: the desktop app loads it into this session by itself once the session is free; nothing to ask "
            "the owner.",
            key,
        )
    key = f"foreman_behind:{session}:{inst}:owner"
    said = changed_mark(project, "foreman-behind-notified", key, mark)
    text = (
        f"{head}: it does not load here by itself. Tell {project.owner}, in plain words, to type /reload-plugins in "
        "this chat to load it. Nothing "
        "else waits for it."
    )
    return notice("foreman_behind", said, text, key)


def sessions_behind_notice(
    project: Project, others: Sequence[Entry], inst: Found | None, mark: bool = True
) -> Notice | None:
    """The `sessions_behind` notice, or None: other live sessions of the product run an older foreman (versions.
    live_sessions; an estimated version with «≈»; not the ones still settling after the install, which may load it
    by themselves). Actionable once per set (<state>/sessions-behind-notified), then information."""
    from . import versions

    if not others:
        changed_mark(project, "sessions-behind-notified", None, mark)
        return None
    others = sorted(others, key=lambda e: (e.get("name") or "", e["session"]))
    names = ", ".join(f"{e.get('name') or e['session'][:8]} {versions.label(e)}" for e in others)
    est = any(e.get("estimated") for e in others)
    installed_v = (inst or {}).get("version") or "?"
    key = f"sessions_behind:{installed_v}:" + ",".join(e["session"] for e in others)
    text = (
        f"Sessions of this product on an older foreman ({installed_v} installed): {names}"
        + (" (≈: estimated, no record of its own)" if est else "")
        + f": tell {project.owner} which ones need /reload-plugins (only they can type it there); a session "
        "you launched can be relaunched instead."
    )
    return notice("sessions_behind", changed_mark(project, "sessions-behind-notified", key, mark), text, key)


def nameless_notice(
    project: Project, live: Iterable[Agent], ids: Mapping[str, Identity], mark: bool = True
) -> Notice | None:
    """The `nameless_agents` notice, or None: live production agents (identity.of) with no name by any path (no
    launch line, not named by hand). The pass cannot tie them to their branch, their issue or their progress.
    Actionable once per set of agents (<state>/nameless-notified, key with their ids), then information."""
    bare = sorted((h for h in live if ids[h["id"]]["production"] and not ids[h["id"]]["name"]), key=lambda h: h["id"])
    if not bare:
        changed_mark(project, "nameless-notified", None, mark)
        return None
    key = "nameless_agents:" + ",".join(h["id"] for h in bare)
    who = ", ".join(f"{h.get('type') or 'agent'} {h['id'][:6]}" for h in bare)
    text = (
        f"Live production agents without a name: {who}: launched without `foreman launch` (no launch line in their "
        "brief): name them with `foreman agents --name ID NAME --issue ID --sub Tn --branch BRANCH`, or "
        "the pass cannot tie them to their branch."
    )
    return notice("nameless_agents", changed_mark(project, "nameless-notified", key, mark), text, key)


def context_notice(project: Project, session: str | None, mark: bool = True) -> Notice | None:
    """The `context_rotate` notice, or None: the context of the orchestrator's session, as foreman's hooks module
    measured it last (hooks_mod.read_measure: Claude Code's own figure after each turn), is at or above [loop]
    context_rotate (0 turns it off). No measure (the module not loaded, no turn yet), or one older than the last
    renewal's compaction (renew.measure), is «unknown»: no notice. Otherwise renewal_notice.
    Actionable when it crosses the threshold and again when its case changes (the agents of the session no longer
    live, the renewal forced); otherwise information. The case is in <state>/context-rotate-notified, cleared when
    the context goes back below (after a renewal or a /compact)."""
    from . import renew

    limit = project.section("loop").get("context_rotate", 200000)
    if not session:
        return None
    m = (renew.measure(project, session) if limit else None) or {}
    n, window, percent = m.get("tokens"), m.get("window"), m.get("percent")
    if not isinstance(n, int) or n < limit:
        changed_mark(project, "context-rotate-notified", None, mark)
        return None
    busy = session_agents(project, session)
    head = (
        f"Context {n // 1000}K"
        + (f" ({percent}% of {window // 1000}K)" if percent is not None and isinstance(window, int) and window else "")
        + f" >= {limit // 1000}K ([loop] context_rotate)"
    )
    return renewal_notice(project, session, n, limit, head, len(busy), mark)


def renewal_notice(
    project: Project, session: str, n: int, limit: int, head: str, busy: int, mark: bool = True
) -> Notice:
    """The `context_rotate` notice (context_notice): it asks the session to renew its memory in place (put in the
    board what only its memory holds, then `foreman renew`), to wait while agents of the session are live (unless the
    context is at renew.FORCE_FACTOR × the threshold, when foreman renews it anyway at its next idle minute), and is
    information while a renewal is asked or under way. After a renewal that freed too little (renew.low_last) it is
    information (`low`): the next one waits for the safety trigger, and the text says what weighs. After two failures
    in a row (renew.trouble) it still asks, and says so: foreman tries again, at most every renew.COOLDOWN_S."""
    from . import renew

    forced = n >= renew.FORCE_FACTOR * limit
    asked = renew.requested(project, session) or renew.pending(project, session)
    why = renew.trouble(project, session)
    force_k = renew.k(int(renew.FORCE_FACTOR * limit))
    again = (
        f" ({why}: foreman tries again, at most every {renew.COOLDOWN_S // 60} min; {project.owner} can also type "
        "/compact in this session, and foreman gives it back its pack after it)"
        if why and not renew.low_last(project, session)
        else ""
    )
    keep = (
        "put in the board (or `foreman note`) whatever only your memory holds: what the owner asked that is still "
        "open, what was agreed with them, what you were about to do"
    )
    if asked:
        state = "renewing"
        text = (
            f"{head}: the renewal of this session's memory is asked (at {str(asked.get('at'))[11:16]}): foreman's "
            "hooks module compacts this session at its next idle minute and gives it back its pack "
            "(docs/orchestrator.md, «Renewal»); nothing else to do, no new session."
        )
    elif forced:
        state = "forced"
        text = (
            f"{head}, past {force_k}: foreman renews this session's memory in place "
            "when this turn ends, live agents or not (docs/orchestrator.md, «Renewal»): " + keep + ", now. "
            "No new session, nothing to tell the owner."
        )
    elif renew.low_last(project, session):
        state = "low"
        text = (
            f"{head}: {why}, so foreman renews this session's memory again only at {force_k}, by itself, live "
            "agents or not (docs/orchestrator.md, «Renewal»). What comes back after every renewal weighs: the rules "
            "file, the board, the active backlog, the long outputs you keep. Thin out what you can; nothing to tell "
            "the owner."
        )
    elif busy:
        state = "busy"
        text = (
            f"{head}: renew this session's memory in place (docs/orchestrator.md, «Renewal») once the {busy} live "
            f"agents deliver; launch no new production. At {force_k} foreman renews it anyway{again}."
        )
    else:
        state = "free"
        text = (
            f"{head}: renew this session's memory in place (docs/orchestrator.md, «Renewal»): " + keep + "; then "
            "run `foreman renew`. foreman compacts this session at its next idle minute and gives it back its pack "
            "(rules, board, backlog, agents, the owner's last messages); no new session, nothing to tell the owner"
            f"{again}."
        )
    key = f"context_rotate:{session}:{state}"
    said = changed_mark(project, "context-rotate-notified", key, mark)
    return notice("context_rotate", said and state not in ("renewing", "low"), text, key)


def holds_production(n: Notice) -> bool:
    """Whether a `context_rotate` notice holds new production (pipeline): not after a renewal that freed too little,
    when the next one waits for the safety trigger, live agents or not."""
    return n["kind"] == "context_rotate" and not n["key"].endswith(":low")


def paused_notices(project: Project, paused_at: str | None) -> list[Notice]:
    """The `paused` notice while the product is paused (pause.since), information only."""
    if not paused_at:
        return []
    return [
        notice("paused", False, i18n.for_project(project)["pause_line"].format(since=paused_at, owner=project.owner))
    ]


def config_notices(project: Project, mark: bool = True) -> list[Notice]:
    """The `config_warnings` notice: the product's config warnings and the shared resources the registry's projects
    declare differently, actionable when they appear or change."""
    shared = lock.shared_warnings(project)
    if not (project.warnings or shared):
        changed_mark(project, "config-warnings-notified", None, mark)
        return []
    joined = "; ".join(project.warnings + shared)
    parts: list[str] = []
    if project.warnings:
        parts.append(
            "Config warnings (the product loads, ignoring them; `foreman check` fails until they are "
            "fixed or foreman is updated): " + "; ".join(project.warnings)
        )
    if shared:
        parts.append(
            "Shared resources declared differently by the registry's projects (`foreman check` warns "
            "but passes; declare the resource once in the registry, align the declarations or say why "
            "not): " + "; ".join(shared)
        )
    return [
        notice("config_warnings", changed_mark(project, "config-warnings-notified", joined, mark), "\n".join(parts))
    ]


def session_notices(project: Project, session: str | None, mark: bool = True) -> list[Notice]:
    """The notices about the orchestrator's own session: its context (`context_rotate`)."""
    ctx = context_notice(project, session, mark)
    return [ctx] if ctx else []


def stale_notice(project: Project, session: str | None, now: float | None = None) -> Notice | None:
    """The `loop_stale` notice between passes, or None: the marked orchestrator (this session) is alive but no pass
    came for more than orchestrator.loop_limit_min since the latest of the last pass, the mark and the start of its
    process (orchestrator.health: no_loop, or silent when its process cannot be checked): foreman's hooks module is not
    running its timer in this session. Cheap first: the session's heartbeat is only read once the `loop` heartbeat
    and the mark are past the limit. Actionable; said once per case (key with the last pass) by notices()."""
    o = orchestrator.current(project) if session else None
    if not o or o["session"] != session or not orchestrator.watches_loop(project):
        return None
    now = util.now().timestamp() if now is None else now
    limit = orchestrator.loop_limit_min(project)
    _, window = orchestrator.pass_window(project, o)
    if window is not None and now - window <= limit * 60:
        return None
    h = orchestrator.health(project, o, now=now)
    if not h or h["verdict"] not in ("no_loop", "silent"):
        return None
    last = h["last_pass"]
    since = f"since {last[11:16]}" if last else "yet"
    text = (
        f"No loop pass {since} (> {limit} min, [loop] every_min {project.section('loop').get('every_min', 60)}): "
        "foreman's hooks module is not running its timer in this session (Claude Code below foreman's minimum, "
        f"plugin mods turned off, or a foreman installed after the session started): ask {project.owner} to run "
        "`/reload-plugins` here; until then run the pass by hand with `foreman loop`."
    )
    return notice("loop_stale", True, text, f"loop_stale:{session}:{last or '-'}")


def incomplete_notice(project: Project) -> Notice | None:
    """`delivery_incomplete`: agents the delivery check let stop after delivery.MAX_BLOCKS tries with their delivery
    still incomplete (`delivery_incomplete` in their entry; it goes when the agent is resumed or delivers complete);
    None when there are none. Not for agents marked stopped."""
    bad: list[tuple[str, str]] = []
    for aid, h in agents(project).items():
        x = h.get("delivery_incomplete")
        if isinstance(x, dict) and not h.get("stopped"):
            name = h.get("name") or aid[:8]
            missing = ", ".join(x.get("missing") or []) or "?"
            bad.append((name, f"{name}" + (f" ({h['branch']})" if h.get("branch") else "") + f": missing {missing}"))
    if not bad:
        return None
    return notice(
        "delivery_incomplete",
        True,
        f"Stopped with an incomplete delivery (the delivery check let them go "
        f"after {delivery.MAX_BLOCKS} tries): "
        + "; ".join(t for _, t in bad)
        + ": resume the agent (SendMessage) with what is missing, or finish or drop it yourself.",
        "delivery_incomplete:" + ",".join(sorted(n for n, _ in bad)),
    )


def agent_notices(
    project: Project, session: str | None, live: Iterable[Agent], ids: Mapping[str, Identity], mark: bool = True
) -> list[Notice]:
    """The notices about the agents: dead ones of an earlier session, incomplete deliveries, nameless production ones,
    idle ones."""
    out: list[Notice] = []
    if session:
        why = dead_agents(project, session, live)
        dead = [ids[aid]["name"] or aid[:8] for aid in why]
        if dead:
            out.append(
                notice(
                    "dead_agents",
                    True,
                    "Agents still marked live that are dead (their session is gone, or Claude Code reports them "
                    "failed or killed): "
                    + ", ".join(f"{n} ({why[aid]})" for n, aid in zip(dead, why))
                    + ": relaunch them or mark them stopped (`foreman agents --stop ID`).",
                    "dead_agents:" + ",".join(sorted(dead)),
                )
            )
    incomplete = incomplete_notice(project)
    if incomplete:
        out.append(incomplete)
    nameless = nameless_notice(project, live, ids, mark)
    if nameless:
        out.append(nameless)
    idle = stalled(project, session)
    if idle:
        out.append(
            notice(
                "stalled_agents",
                True,
                "Agents idle without a lock: " + ", ".join(idle) + ": wake them (SendMessage) or stop them.",
                "stalled_agents:" + ",".join(x.split(" (")[0] for x in idle),
            )
        )
    return out


def ready_branches(
    b: Backlog,
    data: Data,
    wts: Iterable[tuple[str, str, str]],
    live: Iterable[Agent],
    heads: Mapping[str, str],
    ids: Mapping[str, Identity],
    kind: str = "code",
    held: list[tuple[str, str, str, review.State]] | None = None,
) -> list[tuple[str, str, str]]:
    """The worktrees ready to integrate, [(repo, branch, worktree path)]: a branch the backlog names (`b`, its loaded
    `data`) whose worktree (`wts`, worktree.all_worktrees) no live agent (`live`, with `heads` and `ids` as in
    notices_for_pass) has, by its recorded branch, by the agent the sub-issue names, or by its brief citing the
    worktree's path, and whose `delivery.branch_kind` is KIND: "code" (to integrate) or "measure" (measurements to
    close with `integrate --measure`); a branch whose sub-issues are all closed without a commit ("closed") is in
    neither. A code branch whose review does not let it in yet (review.state: a repo with `review = "auto"`, the
    branch needs a review and has none clean on its tip) is not ready either: it goes to HELD, when given, with its
    state (the pass's `review_needed` and `review_findings`). The one rule of the pass's `ready_to_integrate` and
    `measure_delivered` and the orchestrator's band (band.py)."""
    live_names = {i["name"] for i in ids.values()} - {None}
    live_branches = {h["branch"] for h in live if h.get("branch")}  # recorded at launch or by hand
    guessed = [h for h in live if not ids[h["id"]]["registered"]]  # no recorded identity: the fallbacks apply
    named_branches = {s.get("branch") for _, i, _ in b.iter_issues(data) for s in i["subs"] if s.get("branch")}
    agent_of = {
        s.get("branch"): s.get("agent")
        for _, i, _ in b.iter_issues(data, archived=False)
        for s in i["subs"]
        if s.get("branch")
    }
    out: list[tuple[str, str, str]] = []
    for repo, br, path in wts:
        if br not in named_branches or br in live_branches or agent_of.get(br) in live_names:
            continue
        if any(identity.cites_path(heads[h["id"]], path) for h in guessed):
            continue  # a live agent with no recorded identity names its worktree
        if delivery.branch_kind(b.project, br, data) != kind:
            continue
        if kind == "code":
            st = review_state(b.project, repo, br, agent_of.get(br))
            if st is not None and not st.passes:
                if held is not None:
                    held.append((repo, br, path, st))
                continue
        out.append((repo, br, path))
    return out


def review_state(project: Project, repo: str, br: str, agent: str | None) -> review.State | None:
    """review.state of a delivered branch (its outcome from its delivery); None when the repo's review is off or
    git cannot tell (then `integrate` says it)."""
    tab = project.repo(repo)
    if review.mode(tab) != "auto":
        return None
    try:
        return review.state(project, tab, br, delivery.outcome_of(project, branch=br, name=agent))
    except RuntimeError:
        return None


def unworktreed_branches(project: Project) -> list[tuple[str, str]]:
    """[(repo key, branch)]: the local branches of every configured repo that are not merged into its main (`git
    branch --no-merged`, so a branch whose tip is an ancestor of main is out) and have no worktree (the main checkout
    counts as one). Local refs only: no fetch, no network."""
    out: list[tuple[str, str]] = []
    for key in project.repos:
        repo = project.repo(key)
        if not repo["path"] or not Path(repo["path"]).exists():
            continue
        main = repo.get("main") or "main"
        listed = util.git(repo["path"], "branch", "--no-merged", main, "--format=%(refname:short)", check=False)
        if not listed:
            continue
        held = worktree.worktrees(repo["path"])
        out += [(key, br) for br in listed.splitlines() if br and br != main and br not in held]
    return out


def commits_ahead(project: Project, repo: str, br: str) -> int:
    """How many commits of BR are not in the repo's main (0 when git cannot tell)."""
    tab = project.repo(repo)
    n = util.git(tab["path"], "rev-list", "--count", f"{tab.get('main') or 'main'}..{br}", check=False)
    return int(n) if n.isdigit() else 0


SQUASH_DEPTH = 200  # how many first-parent commits of main `in_main` compares a branch's whole diff with


def _patch_ids(repo: PathLike, diff: str) -> list[str]:
    """`git patch-id --stable` of a diff (or of `git log -p` output): the ids, in order."""
    try:
        r = subprocess.run(
            ["git", "-C", str(repo), "patch-id", "--stable"],
            input=diff,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return [ln.split()[0] for ln in r.stdout.splitlines() if ln.strip()]


def on_remote(path: PathLike, remote: str | None, br: str) -> bool:
    """Whether BR's tip is in the remote branch of the same name as the last fetch left it (no network)."""
    if not remote or remote == "none":
        return False
    try:
        util.git(path, "merge-base", "--is-ancestor", br, f"refs/remotes/{remote}/{br}")
    except RuntimeError:
        return False
    return True


def in_main(path: PathLike, main: str, br: str) -> bool:
    """Whether BR's content is already in MAIN though BR is not merged: every commit has an equivalent in main (`git
    cherry`: cherry-picked or rebased), or its whole diff since the common base is the patch of one of main's
    first-parent commits since then (squashed), looking at most SQUASH_DEPTH commits back."""
    cherry = util.git(path, "cherry", main, br, check=False)
    if cherry and not any(ln.startswith("+") for ln in cherry.splitlines()):
        return True
    base = util.git(path, "merge-base", main, br, check=False)
    if not base:
        return False
    whole = _patch_ids(path, util.git(path, "diff", "--no-color", "--no-ext-diff", base, br, check=False) + "\n")
    if not whole:
        return False
    log = util.git(
        path,
        "log",
        "-p",
        "--no-color",
        "--no-ext-diff",
        "--first-parent",
        f"-n{SQUASH_DEPTH}",
        f"{base}..{main}",
        check=False,
    )
    return bool(log) and whole[0] in _patch_ids(path, log + "\n")


def tip_line(path: PathLike, br: str, n: int) -> str:
    """`N commits not in main, D days, "subject"` of a branch's tip."""
    when, _, subject = util.git(path, "log", "-1", "--format=%ct%x00%s", br, check=False).partition("\0")
    parts = [plural(n, "commit") + " not in main"]
    if when.isdigit():
        parts.append(plural(int((time.time() - int(when)) // 86400), "day"))
    if subject:
        parts.append('"' + (subject if len(subject) <= 60 else subject[:59] + "…") + '"')
    return ", ".join(parts)


def plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def report_part(project: Project, data: Data, repo: str, br: str, path: str | None, agent: str | None) -> str:
    """What `ready_line` says about a branch's report and outcome, without the parentheses ("" when nothing). With no
    worktree (`path` None) the branch is read from the repo's main checkout (git refs are shared)."""
    tab = project.repo(repo)
    rep = delivery.branch_report(
        project,
        br,
        wt=path or tab.get("path"),
        main=tab.get("main") or "main",
        main_path=tab.get("path"),
        data=data,
        delivered=delivery.report_of(project, branch=br, name=agent),
        since=delivery.started(project, br),
    )
    oc = delivery.outcome_of(project, branch=br, name=agent)
    oc = f"; outcome {oc}" if oc else ""
    if rep.path:
        whose = "the sub-issue's" if rep.stale_how == "sub-issue" else "the delivered"
        stale = f"; {whose} report {rep.stale} not found" if rep.stale else ""
        return f"{rep.path}{stale}{oc}"
    if rep.stale:
        return f"report {rep.stale} not found: fix the sub-issue's --report or ask for it{oc}"
    if rep.required:
        return f"no report: ask for it first{oc}"
    return oc[2:]


def ready_line(project: Project, data: Data, repo: str, br: str, path: str | None, agent: str | None) -> str:
    """One branch of `ready_to_integrate`: `repo:br (report[; outcome X])`, with the report `delivery.branch_report`
    finds (the same rule as the delivery check, with the report the agent named at its delivery), «report X not
    found» when the sub-issue or the delivery names one that exists nowhere, «no report» when there is none, and
    nothing about a report when the project keeps none."""
    part = report_part(project, data, repo, br, path, agent)
    return f"{repo}:{br} ({part})" if part else f"{repo}:{br}"


def no_worktree_line(
    project: Project, data: Data, repo: str, br: str, subs: Sequence[tuple[str, str]], agent: str | None
) -> str:
    """One branch of `branch_no_worktree`: `repo:br (ID Tn[, measurement]; report…; N commits not in main)`."""
    kind = ", measurement" if delivery.branch_kind(project, br, data) == "measure" else ""
    parts = [", ".join(f"{iid} {sid}" for iid, sid in subs) + kind]
    part = report_part(project, data, repo, br, None, agent)
    if part:
        parts.append(part)
    parts.append(plural(commits_ahead(project, repo, br), "commit") + " not in main")
    return f"{repo}:{br} ({'; '.join(parts)})"


def review_target(data: Data, b: Backlog, br: str) -> tuple[str, str]:
    """(issue, sub-issue) of the open sub-issue that names BR, else of any that does; ("ID", "Tn") when none."""
    subs = [(i["id"], s) for _, i, _ in b.iter_issues(data) for s in i["subs"] if s.get("branch") == br]
    subs.sort(key=lambda x: x[1].get("status") in delivery.SUB_CLOSED)
    return (subs[0][0], subs[0][1]["id"]) if subs else ("ID", "Tn")


def agent_entry(project: Project, name: str | None, br: str) -> Agent | None:
    """The latest agents.json entry (with its `id`) of the agent NAME that made BR (not a reviewer); None without
    one."""
    mine = [
        {"id": aid, **h}
        for aid, h in agents(project).items()
        if not h.get("review") and ((name and h.get("name") == name) or h.get("branch") == br)
    ]
    return max(mine, key=lambda h: str(h.get("start") or "")) if mine else None


def who(h: Mapping[str, Any] | None) -> str:
    """`name (id 1234abcd)` of an agents.json entry with its `id`."""
    if not h:
        return "?"
    return f"{h.get('name') or h['id'][:8]} (id {h['id'][:8]})"


def review_notices(
    project: Project,
    b: Backlog,
    data: Data,
    held: Sequence[tuple[str, str, str, review.State]],
    agent_of: Mapping[str, str | None],
    act: bool,
) -> list[Notice]:
    """`review_needed` and `review_findings`: the delivered branches whose review does not let them in yet
    (ready_branches' HELD), each with its next step. Needed (round 1, or round 2 once the fixes moved the tip):
    launch a reviewer with `foreman launch NAME --review REPO:BRANCH`, or resume the one of round 1. Findings (round
    1): send them back to the agent that made the branch (SendMessage), or launch a new one on its worktree (plan B).
    For the owner (round 2 left H or M open; no third round): a question for the owner (`decision add` with the
    review as its page), then nothing while it is pending (one notice, not actionable), then what to do with their
    answer. Not actionable while the product is paused (ACT)."""
    needed: list[str] = []
    findings: list[str] = []
    waiting: list[str] = []
    keys: dict[str, list[str]] = {"needed": [], "findings": [], "waiting": []}  # the case of each: branch@state
    everyone = [{"id": aid, **h} for aid, h in agents(project).items()]
    owner = project.owner
    for repo, br, _, st in held:
        ref = f"{repo}:{br}"
        iid, sid = review_target(data, b, br)
        name = agent_of.get(br)
        dev = agent_entry(project, name, br)
        base = name or (dev or {}).get("name") or re.sub(r"[^\w.-]", "-", br)
        why = "; ".join(r.text for r in st.reasons)
        rv = st.review
        case = f"{ref}@{st.status}" + (f"@{rv.path}" if rv else "")
        plan_b = (
            f"`foreman launch {base}-fix --repo {repo} --branch {br} --issue {iid} --sub {sid}` (it reuses the "
            "branch's worktree), brief: the plan, its report and the review"
        )
        if st.status == review.NEEDED or (st.status == review.FIXED and rv):
            launch = f"`foreman launch {base}-rv{st.round} --review {ref} --issue {iid} --sub {sid}`"
            keys["needed"].append(case)
            if st.status == review.FIXED and rv:
                prev = review.reviewer_of(everyone, repo, br)
                resume = f"resume the reviewer {who(prev)} with SendMessage, or launch one: " if prev else "launch: "
                needed.append(
                    f"{ref} ({iid} {sid}; round {st.round}: the fixes are in, {rv.path} left {rv.counts()} open): "
                    f"{resume}{launch}, brief: the previous review, the plan and the report; it checks each finding "
                    "and only what the fixes changed"
                )
            else:
                again = f"; {rv.path} reviewed another diff" if rv else ""
                bad = f"; header unreadable: {', '.join(st.problems)}" if st.problems else ""
                needed.append(
                    f"{ref} ({iid} {sid}; {why}{again}{bad}; round {st.round}): {launch}, brief: the plan, the "
                    f"report and «round {st.round}»"
                )
        elif st.status == review.FINDINGS and rv:
            keys["findings"].append(case)
            back = (
                f"send it back to {who(dev)} with SendMessage"
                if dev
                else "no agent of it is recorded to send it back to"
            )
            findings.append(
                f"{ref}: {rv.counts()} open ({rv.path}, round {rv.round} of {review.MAX_ROUNDS}): {back}: fix or "
                f"refute each H and M with proof (a test, or the line that disproves it), a «Review, round "
                f"{rv.round}» section in its report, commit, `foreman progress --done` again; if it cannot be "
                f"resumed: {plan_b}"
            )
        elif st.status == review.OWNER and rv:
            page = os.path.realpath(project.home / rv.path)
            asked = [d for d in data["decisions"] if d.get("page") and os.path.realpath(b.page_path(d["page"])) == page]
            answered = [
                d
                for d in data.get("decisions_resolved") or []
                if d.get("page") and os.path.realpath(b.page_path(d["page"])) == page
            ]
            if asked:
                keys["waiting"].append(f"{case}@{asked[-1]['id']}")
                waiting.append(f"{ref} waits for {owner}'s answer to {asked[-1]['id']} «{asked[-1].get('title', '')}»")
            elif answered:
                d = answered[-1]
                keys["findings"].append(f"{case}@{d['id']}")
                findings.append(
                    f"{ref}: {owner} answered {d['id']}: «{d.get('answer', '')}»: act on it: in as it is, "
                    f'`foreman integrate {repo} {br} "{iid} {sid}" --approved "{owner}, {d.get("resolved", "")} '
                    f'({d["id"]})"`; fixed by another agent, {plan_b}, then a review with «Round: '
                    f"{rv.round + 1}»; dropped, cancel the sub-issue and remove the branch with a note giving its tip"
                )
            else:
                keys["findings"].append(case)
                findings.append(
                    f"{ref}: round {rv.round} of {review.MAX_ROUNDS} left {rv.counts()} open ({rv.path}): no third "
                    f"round: ask {owner} with a question written for them (docs/owner.md), the review as its page: "
                    f"`foreman backlog decision add --issue {iid} --blocks {sid} --page {rv.path} --title …` with "
                    "the options in as it is with the problem noted, fixed by another agent then reviewed again, or "
                    "dropped, each with its --effect; the branch waits for the answer"
                )
    out: list[Notice] = []
    if needed:
        out.append(
            notice(
                "review_needed",
                act,
                "Need a review by an agent that did not write them before integrating: "
                + "; ".join(needed)
                + ". The review starts with «Branch: REPO:BRANCH», «Tip: SHA», «Round: N», «Open: H n, M n»; the "
                "reviewer holds the branch while it lives.",
                "review_needed:" + ";".join(keys["needed"]),
            )
        )
    if findings:
        out.append(
            notice(
                "review_findings",
                act,
                "Review findings before integrating: " + "; ".join(findings) + ".",
                "review_findings:" + ";".join(keys["findings"]),
            )
        )
    if waiting:
        out.append(
            notice(
                "review_findings",
                False,
                "Waiting for the owner after round 2: " + "; ".join(waiting) + ".",
                "review_findings:waiting:" + ";".join(keys["waiting"]),
            )
        )
    return out


def branch_notices(
    project: Project, live: Iterable[Agent], heads: Mapping[str, str], ids: Mapping[str, Identity]
) -> list[Notice]:
    """The notices about the backlog and the worktrees: an unreadable backlog, branches ready to integrate,
    measurements delivered (nothing to merge; a branch closed without a commit is in neither), worktrees
    the backlog does not name, `in progress` issues with no agent, backlog problems."""
    out: list[Notice] = []
    live_names = {i["name"] for i in ids.values()} - {None}
    live_issues = {i["issue"] for i in ids.values() if i["issue"]}  # recorded, or «Issue: ID» in the brief
    guessed = [h for h in live if not ids[h["id"]]["registered"]]  # no recorded identity: the fallbacks apply
    guessed_text = "\n".join(heads[h["id"]][:4000] for h in guessed)
    b = Backlog(project)
    try:
        data = b.load()
        text = b.data_path.read_text()
    except (BacklogError, OSError, ValueError) as e:
        data, text = None, ""
        out.append(notice("backlog_unreadable", True, f"backlog could not be read: {e}"))
    wts = worktree.all_worktrees(project)
    if data is None:
        return out
    agent_of = {
        s.get("branch"): s.get("agent")
        for _, i, _ in b.iter_issues(data, archived=False)
        for s in i["subs"]
        if s.get("branch")
    }
    ready: list[str] = []
    withheld: list[tuple[str, str, str, review.State]] = []
    for repo, br, path in ready_branches(b, data, wts, live, heads, ids, held=withheld):
        ready.append(ready_line(project, data, repo, br, path, agent_of.get(br)))
    # paused: integrate is blocked, so a delivered branch only informs until the owner resumes the product
    act = not pause.since(project)
    if ready:
        out.append(
            notice(
                "ready_to_integrate",
                act,
                "Ready to integrate (no live agent): " + "; ".join(ready),
                "ready_to_integrate:" + ";".join(r.split(" (")[0] for r in ready),
            )
        )
    out += review_notices(project, b, data, withheld, agent_of, act)
    measured = [
        ready_line(project, data, repo, br, path, agent_of.get(br))
        for repo, br, path in ready_branches(b, data, wts, live, heads, ids, kind="measure")
    ]
    if measured:
        out.append(
            notice(
                "measure_delivered",
                act,
                "Measurements delivered (nothing to merge): "
                + "; ".join(measured)
                + ': read the report, then close it with `foreman integrate REPO BRANCH "ID Tn" --measure --result '
                '"…"` (no CI, no merge; removes the worktree and the branch; `--keep-branch` keeps the branch for a '
                "later sub-issue).",
                "measure_delivered:" + ";".join(r.split(" (")[0] for r in measured),
            )
        )
    loose = [f"{repo}:{br}" for repo, br, _ in wts if br not in text]
    if loose:
        out.append(
            notice(
                "loose_worktrees",
                True,
                "Worktrees the backlog does not name: "
                + ", ".join(loose)
                + ": note them in their sub-issue (`foreman backlog sub … --branch`) or remove them.",
                "loose_worktrees:" + ",".join(loose),
            )
        )
    live_branches = {h["branch"] for h in live if h.get("branch")}
    unworktreed = unworktreed_branches(project)
    subs_of: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for _, i, _ in b.iter_issues(data):  # archived included, as ready_branches
        for s in i["subs"]:
            if s.get("branch"):
                subs_of.setdefault(s["branch"], []).append((i["id"], s))
    stranded: list[tuple[str, str, list[tuple[str, str]]]] = []  # named by an open sub-issue, no live agent
    for repo, br in unworktreed:
        open_ = [(iid, s) for iid, s in subs_of.get(br, []) if s.get("status") not in delivery.SUB_CLOSED]
        if not open_ or br in live_branches or any(s.get("agent") in live_names for _, s in open_):
            continue  # all closed (on purpose, or integrated by squash), or an agent is on it
        stranded.append((repo, br, [(iid, s["id"]) for iid, s in open_]))
    if stranded:
        lines = [no_worktree_line(project, data, repo, br, subs, agent_of.get(br)) for repo, br, subs in stranded]
        out.append(
            notice(
                "branch_no_worktree",
                True,
                "Unmerged branches of open sub-issues with no worktree: "
                + "; ".join(lines)
                + ": integrate needs a worktree: `foreman worktree REPO NAME BRANCH` (it reuses the branch), then "
                '`foreman integrate REPO BRANCH "ID Tn" --check` (`--measure` for a measurement); not delivered: '
                "relaunch the sub-issue on it (`foreman launch … --repo REPO --branch BRANCH`); dead: cancel the "
                "sub-issue (`foreman backlog sub ID Tn --status canceled`) and delete the branch with a note giving "
                "its tip.",
                "branch_no_worktree:" + ";".join(f"{repo}:{br}" for repo, br, _ in stranded),
            )
        )
    stranded_branches = {br for _, br, _ in stranded}
    stray: list[str] = []  # named by no sub-issue nor the backlog's text, only here, with work not in main
    for repo, br in unworktreed:
        if br in subs_of or br in text:
            continue  # the backlog keeps it (a closed sub-issue, or noted in an issue: as loose_worktrees)
        tab = project.repo(repo)
        path, main = tab["path"], tab.get("main") or "main"
        if on_remote(path, tab.get("remote", "origin"), br) or in_main(path, main, br):
            continue  # safe on the remote (a PR, a pushed branch), or already in main (squashed, cherry-picked)
        stray.append(f"{repo}:{br} ({tip_line(path, br, commits_ahead(project, repo, br))})")
    if stray:
        out.append(
            notice(
                "loose_branches",
                True,
                "Unmerged branches with no worktree that the backlog does not name: "
                + "; ".join(stray)
                + ": note them in their sub-issue (`foreman backlog sub … --branch`) or in the issue that keeps them. "
                f"Ask {project.owner} before deleting one (it may be their own work); once agreed, delete it with a "
                "note giving its tip (`git branch -D`; back with `git branch NAME SHA` until git prunes it, ~2 "
                "weeks).",
                "loose_branches:" + ";".join(s.split(" (")[0] for s in stray),
            )
        )
    orphans: list[str] = []
    for _, i, _ in b.iter_issues(data, archived=False):
        if i["status"] != "in progress":
            continue
        if i["id"] in live_issues:
            continue
        held = {br for _, br, _ in wts} | stranded_branches  # a branch with work: branch_no_worktree says it
        if any(s.get("agent") in live_names or s.get("branch") in held for s in i["subs"]):
            continue
        if re.search(rf"Issue:\s*{re.escape(i['id'])}(?![\w-])", guessed_text):
            continue
        orphans.append(i["id"])
    if orphans:
        out.append(
            notice(
                "orphan_in_progress",
                True,
                "`in progress` with no live agent or branch: "
                + ", ".join(orphans)
                + ": back to `ready` (or relaunch).",
                "orphan_in_progress:" + ",".join(orphans),
            )
        )
    problems = [p for p in backlog_check.validate(b, data) if not p.startswith("warning: ")]
    if problems:
        out.append(
            notice(
                "backlog_problems",
                True,
                "foreman check, backlog: " + "; ".join(problems[:6]) + (" …" if len(problems) > 6 else ""),
            )
        )
    return out


def orchestrator_only_notices(project: Project, mark: bool = True) -> list[Notice]:
    """`orchestrator_only_denied`: an agent, another session or another process of the marked one (a mark with a
    process) tried a command of a `[deny.grants]` group, or an agent or another session a write of a reserved file
    (guard.check_file), and the guard refused it (approved.jsonl), each refusal
    once (<state>/orchestrator-denied-notified: the time of the last one said; mark=False reads it without
    writing)."""
    f = project.state / "orchestrator-denied-notified"
    try:
        since = f.read_text().strip()
    except OSError:
        since = ""
    new = approved.refusals_since(project, since)
    if not new:
        return []
    last = max(r.get("at", "") for r in new)
    if mark:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(last + "\n")
    tried = []
    for r in new[-5:]:
        aid = r.get("agent_id")
        who = r.get("agent") or (f"agent-{aid[:6]}" if aid else f"session {str(r.get('session') or '?')[:8]}")
        if r.get("why") == "process":
            who = (
                f"another Claude Code process of session {str(r.get('session') or '?')[:8]} (a copy of it; if it is"
                " you after a restart, mark yourself again)"
            )
        at = str(r.get("at", ""))[11:16]
        if r.get("file"):  # a reserved file (guard.check_file)
            tried.append(f"{who} tried to write `{str(r['file'])[-80:]}` at {at}")
            continue
        cmd = " ".join(str(r.get("command") or r.get("place") or "").split())
        tool = f" with {r['tool']}" if r.get("tool") else ""  # another tool than Bash (guard.check_tool)
        tried.append(f"{who} tried `{cmd[:80]}`{tool} at {at}")
    files = any(r.get("file") for r in new)
    return [
        notice(
            "orchestrator_only_denied",
            True,
            ("Orchestrator-only commands and reserved files refused" if files else "Orchestrator-only commands refused")
            + f" ({len(new)}): "
            + "; ".join(tried)
            + ". Only you run them, after the owner's yes ([deny.grants]): see"
            " whether that task needs it, and tell the agent."
            + (
                " A reserved file (foreman.toml, the guard table, the state, the registry, Claude Code's settings) is"
                " written by nobody: a change of permissions goes through `foreman permissions apply`."
                if files
                else ""
            ),
            "orchestrator_only_denied:" + last,
        )
    ]


def machine_notices(project: Project, mark: bool = True) -> list[Notice]:
    """The notices about the machine and the owner: hung shells, recurring tasks overdue, the inbox, low disk."""
    out: list[Notice] = []
    hung = hung_shells()
    if hung:
        out.append(notice("hung_shells", True, "Hung shells (foreman hung-shells --kill): " + "; ".join(hung)))
    late = [
        t["name"] for t in notes.recurring(project) if t["late"] and t["name"] != project.section("loop").get("prompt")
    ]
    if late:
        out.append(
            notice(
                "recurring_late",
                True,
                "Recurring tasks overdue (pass skill, «Closing a batch and recurring tasks»): " + ", ".join(late),
                "recurring_late:" + ",".join(late),
            )
        )
    unread = notes.inbox_new(project)
    if unread:
        out.append(
            notice(
                "inbox",
                True,
                f"Inbox: {len(unread)} unread from {project.owner}{notes.inbox_counts(unread)} (ids "
                f"{', '.join(str(m['id']) for m in unread[-8:])}): `foreman inbox new`, answer and mark them "
                "read.",
                "inbox:" + ",".join(str(m["id"]) for m in unread),
            )
        )
        if mark:
            new_inbox(project)
    free_gb = shutil.disk_usage(Path.home()).free / 2**30
    if free_gb < project.section("loop").get("disk_low_gb", 30):
        out.append(
            notice("disk_low", changed_mark(project, "disk-low-notified", "low", mark), f"Disk: {free_gb:.0f} GB free.")
        )
    else:
        changed_mark(project, "disk-low-notified", None, mark)
    return out


def decision_notices(project: Project, mark: bool = True) -> list[Notice]:
    """`decision_due`: a pending question reached its reminder day (`unanswered.by`) without an answer: said once
    per question (<state>/decision-due-notified: `ID|day`; mark=False reads it without writing). Only a reminder:
    foreman never answers for the owner."""
    try:
        due = backlog_mod.due_decisions(Backlog(project).load())
    except (BacklogError, OSError, ValueError):
        return []  # backlog_unreadable says it
    f = project.state / "decision-due-notified"
    try:
        seen = set(f.read_text().split("\n"))
    except OSError:
        seen = set()
    out: list[Notice] = []
    for d in due:
        case = f"{d['id']}|{d['by']}"
        if case in seen:
            continue
        out.append(
            notice(
                "decision_due",
                True,
                f"Question {d['id']} reaches its date ({d['by']}) without an answer: remind {project.owner} in the "
                f"chat; it says what waits: {d['waits']}",
                "decision_due:" + case,
            )
        )
        seen.add(case)
    if out and mark:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("\n".join(sorted(seen))[-4000:])
    return out


def with_versions(
    project: Project,
    session: str | None,
    out: Iterable[Notice],
    at_version: int,
    found: tuple[dict[str, Any] | None, list[Entry], Found | None],
    mark: bool = True,
) -> list[Notice]:
    """`out` with the version notices: `sessions_behind` at the end and `foreman_behind` at `at_version` (the
    orchestrator's own session goes right after its context and loop notices). `found` is foreman_versions()."""
    mine, others, inst = found
    out = list(out)
    others_n = sessions_behind_notice(project, others, inst, mark)
    if others_n:
        out.append(others_n)
    own_n = foreman_behind_notice(project, session, mine, mark)
    if own_n:
        out.insert(at_version, own_n)
    return out


def notices_for_pass(project: Project, session: str | None = None, mark: bool = True) -> list[Notice]:
    """The notices of a loop pass, in the order the summary shows them; each check decides its `actionable` (table in
    docs/orchestrator.md). mark=False (`foreman loop`) reads the markers that keep a notice from repeating without
    writing them, so looking does not use up a pass's notices. Each group of notices is one function."""
    paused_at = pause.since(project)
    out = paused_notices(project, paused_at) + config_notices(project, mark)
    out += session_notices(project, session, mark)
    at_version = len(out)  # where foreman_behind goes (with_versions)
    found = foreman_versions(project, session)
    live = live_agents(project)
    heads = {h["id"]: identity.transcript_head(h) for h in live}
    ids = {h["id"]: identity.of(project, h, heads[h["id"]]) for h in live}
    if not paused_at:  # paused: nothing to launch, so no WIP notice asking for it
        ctx = any(holds_production(n) for n in out)
        out += pipeline(project, rotating="context_rotate" if ctx else False)
    out += agent_notices(project, session, live, ids, mark)
    out += orchestrator_only_notices(project, mark)
    out += lock_notices(project, mark)
    out += lock_queues(project)
    out += branch_notices(project, live, heads, ids)
    out += machine_notices(project, mark)
    out += decision_notices(project, mark)
    out += run_checks(project)
    return with_versions(project, session, out, at_version, found, mark)


def summary(project: Project, session: str | None = None, mark: bool = True) -> list[str]:
    """The text of each notice of a loop pass (what `foreman loop` lists)."""
    return [n["text"] for n in notices_for_pass(project, session, mark)]


def skips(project: Project, ns: Iterable[Notice]) -> bool:
    """Whether the pass is not sent to the model (hooks_mod.tick): no notice asks for action."""
    return nothing_actionable(ns)


def idle_line(project: Project, ns: Iterable[Notice]) -> str:
    """The one line an idle pass leaves on screen (the module's $.ui.log; the model never reads it): the time and,
    if any, the short text of each notice."""
    s = i18n.for_project(project)
    short = [n["text"] if len(n["text"]) <= 140 else n["text"][:139] + "…" for n in ns]
    return s["loop_idle"].format(time=time.strftime("%H:%M")) + (
        " " + s["loop_idle_info"].format(items="; ".join(short)) if short else ""
    )


LOOP_LOG_DAYS = 30
LOOP_LOG_TRIM_BYTES = 512 * 1024  # the file is cut to its last LOOP_LOG_DAYS days when it grows past this


def loop_log_file(project: Project) -> Path:
    return project.state / "loop.jsonl"


def log_pass(project: Project, session: str | None, ns: Sequence[Notice], seconds: float, send: bool = False) -> None:
    """One line in <state>/loop.jsonl for a pass: {time, session, skipped, notices (kinds, in order), actionable
    (kinds), duration_s}; `send`: the pass goes to the model whatever skips() says (not skipped). Past
    LOOP_LOG_TRIM_BYTES it keeps only the last LOOP_LOG_DAYS days. Never raises."""
    line = {
        "time": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "session": session or None,
        "skipped": not send and skips(project, ns),
        "notices": [n["kind"] for n in ns],
        "actionable": [n["kind"] for n in ns if n["actionable"]],
        "duration_s": round(seconds, 2),
    }
    f = loop_log_file(project)
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        with notes.file_lock(f):
            with open(f, "a") as out:
                out.write(json.dumps(line, ensure_ascii=False) + "\n")
            if f.stat().st_size > LOOP_LOG_TRIM_BYTES:
                cut = (datetime.now() - timedelta(days=LOOP_LOG_DAYS)).strftime("%Y-%m-%dT%H:%M:%S")
                keep = [
                    ln
                    for ln in f.read_text(encoding="utf-8", errors="replace").splitlines(True)
                    if ln.startswith('{"time": "') and ln[10:29] >= cut
                ]  # a broken line goes too
                tmp = f.with_name(f.name + ".tmp")
                tmp.write_text("".join(keep), encoding="utf-8")
                os.replace(tmp, f)
    except OSError:
        pass


def loop_pass(project: Project, session: str | None, send: bool = False) -> tuple[str, list[Notice]]:
    """Heartbeat and the pass: (text for the model, notices), and the pass's line in loop.jsonl (`send`: the caller
    sends it whatever skips() says, log_pass). Any failure is a `summary_failed` notice, never raised."""
    t0 = time.time()
    try:
        notes.beat(project, "loop")
        ns = notices_for_pass(project, session)
    except Exception as e:  # noqa: BLE001 (a hook never fails the call)
        ns = [
            notice(
                "summary_failed",
                True,
                f"the summary failed ({type(e).__name__}: {e}); do the pass by hand (docs/orchestrator.md).",
            )
        ]
        log_pass(project, session, ns, time.time() - t0, send)
        return render(ns, "Loop pass"), ns
    remember(project, ns)
    log_pass(project, session, ns, time.time() - t0, send)
    return render(ns, f"Loop pass {datetime.now():%H:%M} (heartbeat written, {time.time() - t0:.1f} s)"), ns


def notices(project: Project, session: str | None = None) -> list[Notice]:
    """Between passes (UserPromptSubmit outside the loop, PostToolUse): the actionable notices whose `key` was not
    emitted yet. It computes the cheap part of a pass (new inbox messages, locks, the session's context, pipeline,
    idle agents, a loop with no pass (loop_stale); never the project checks, the checks of the backlog or git) and
    remembers every actionable key it sees in <state>/notices-last, replaced on each call: a case that stays is said
    once, one that goes away and comes back is said again.
    Information-only notices never go out here (the pass shows them). The inbox (inbox-notified) and the locks
    (lock-notified) keep their own once-only marks, shared with the pass: a lock case said here is marked there, so
    the pass does not repeat it; one that is not actionable is left for the pass. The context (context-rotate-notified)
    too: crossing the threshold said here is not actionable again in the pass."""
    cand: list[Notice] = []
    new = new_inbox(project)
    if new:
        cand.append(
            notice(
                "inbox",
                True,
                f"Inbox: {len(new)} unread from {project.owner}{notes.inbox_counts(new)}"
                + ": `foreman inbox new`, attend to them and mark them read.",
                f"inbox_new:{max(m['id'] for m in new)}",
            )
        )
    locks = [n for n in lock_notices(project, mark=False) if n["actionable"]]
    cand += locks
    ctx = context_notice(project, session)
    if ctx:
        cand.append(ctx)
    stale = stale_notice(project, session)
    if stale:
        cand.append(stale)
    if not pause.since(project):  # paused: nothing to launch
        cand += pipeline(project, rotating=ctx is not None and holds_production(ctx))
    incomplete = incomplete_notice(project)
    if incomplete:
        cand.append(incomplete)
    quiet = stalled(project, session)
    if quiet:
        cand.append(
            notice(
                "stalled_agents",
                True,
                "Agents idle without a lock: "
                + ", ".join(quiet)
                + ": see whether they wait for something that already ended and wake them with SendMessage.",
                "stalled_agents:" + ",".join(x.split(" (")[0] for x in quiet),
            )
        )
    cand = [n for n in cand if n["actionable"]]
    f = project.state / "notices-last"
    try:
        last = set(f.read_text().split("\n"))
    except OSError:
        last = set()
    keys = sorted({between_key(n) for n in cand})
    if set(keys) != last - {""}:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("\n".join(keys))
    out = merge_wip([n for n in cand if between_key(n) not in last])
    if locks:
        mark_locks(project, [n["key"] for n in out if n in locks])
    return out


def between_key(n: Notice) -> str:
    """The dedupe key of a notice between passes: the WIP notices are one class each, said once until the
    next pass whatever their counts (the pass gives the detail); every other kind keeps its own key."""
    return n["kind"] if n["kind"] in ("wip_production", "wip_ready") else n["key"]


def merge_wip(ns: list[Notice]) -> list[Notice]:
    """Two WIP notices in the same text become one line: the second's text joins the first's without its prefix."""
    wip = [n for n in ns if n["kind"] in ("wip_production", "wip_ready")]
    if len(wip) < 2:
        return ns
    prefix = "WIP (pipeline not full): "
    first: Notice = {**wip[0], "text": wip[0]["text"] + " " + wip[1]["text"].removeprefix(prefix)}
    return [first if n is wip[0] else n for n in ns if n is not wip[1]]


def remember(project: Project, ns: Iterable[Notice]) -> None:
    """After a pass: its actionable keys become <state>/notices-last, so the notices between passes do not repeat
    what the pass just said (the next notices() call replaces the file with what it sees)."""
    f = project.state / "notices-last"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("\n".join(sorted({between_key(n) for n in ns if n["actionable"]})))


def mark_locks(project: Project, keys: Collection[str]) -> None:
    """Add lock cases to <state>/lock-notified (lock_notices), so that what was said between passes is not repeated."""
    if not keys:
        return
    f = project.state / "lock-notified"
    try:
        seen = set(f.read_text().split("\n"))
    except OSError:
        seen = set()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("\n".join(sorted(seen | set(keys)))[-4000:])


def stop_agent(project: Project, aid: str) -> list[str]:
    f = agents_file(project)
    with notes.file_lock(f):
        d = util.read_json(f, {})
        hits = [k for k in d if k == aid or k.startswith(aid) or d[k].get("name") == aid]
        for k in hits:
            d[k]["stopped"] = True
        util.write_json(f, d)
    return hits


def agents_json(project: Project) -> str:
    """agents.json with each agent's computed state (agent_state) as `state` and its identity (identity.of: the
    recorded one, with the fallback for what is missing) as `identity`."""
    b = Backlog(project)
    try:
        data = b.load()
        branch_of = {
            s["agent"]: s["branch"]
            for _, i, _ in b.iter_issues(data, archived=False)
            for s in i["subs"]
            if s.get("agent") and s.get("branch") and s.get("status") == "in progress"
        }
    except (BacklogError, OSError, ValueError, KeyError):
        branch_of = {}
    return json.dumps(
        {
            k: {**h, "state": agent_state(h), "identity": identity.of(project, h, branch_of=branch_of)}
            for k, h in agents(project).items()
        },
        ensure_ascii=False,
        indent=1,
    )


def agents_compact(project: Project) -> str:
    """`foreman agents` by default: one line per agent: id (8), name, state, issue/sub, branch, minutes
    (from its start to its end, or to now), model and, once delivered, its outcome. `--json` gives agents_json."""
    full = json.loads(agents_json(project))
    now = time.time()
    lines: list[str] = []
    for aid, h in full.items():
        ident = h.get("identity") or {}
        start = util.iso_epoch(h.get("start"))
        end = util.iso_epoch(h.get("end")) if h.get("end") else None
        mins = f"{max(0, round(((end or now) - start) / 60))}min" if start else "-"
        issue = ident.get("issue") or "-"
        if ident.get("sub"):
            issue += "/" + ident["sub"]
        parts = [
            aid[:8],
            ident.get("name") or "-",
            h["state"],
            issue,
            ident.get("branch") or "-",
            mins,
            h.get("model") or h.get("model_asked") or "-",
        ]
        if h.get("delivered") and h.get("outcome"):
            parts.append(h["outcome"])
        lines.append(" ".join(parts))
    return "\n".join(lines) if lines else "no agents"
