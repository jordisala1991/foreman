"""foreman: one Claude Code session runs a project's backlog with short-lived agents (backlog, locks, worktrees, hooks).

foreman [--project NAME] COMMAND …
backlog …                          the project's backlog (foreman backlog --help)
note "Title" "text" | note - "text"   today's notes (text may come from stdin)
progress [--done [--outcome ok|partial|failed] [--report PATH] [--kind measure]] "text"
                                   a progress line for the owner's panel;
                                   --done: the agent's delivery (its last command), so it stops counting as live;
                                   --outcome: how it went (default ok; failed: delivered without commits or report);
                                   --report: names its report (relative to the home, under [ids] report_prefixes);
                                   --kind measure: it only measured (no code for main: needs no commits)
inbox new | read [ID…] | reply "text" | add "text"   the owner's inbox
recurring [done NAME]              recurring tasks and which are late; done NAME: the task NAME was just done
lock RESOURCE run "what" cmd… | acquire "what" [s] | release | check
wait [-t S] [-i S] pid|file|line|lock …
time-limit SECONDS cmd…
hung-shells [--kill]
worktree REPO NAME BRANCH [BASE] | worktree --list   a worktree on a new branch from BASE (default main), or on
                                   BRANCH if it exists (local, or only on the remote: tracked; BASE ignored);
                                   a branch checked out in another worktree is an error; post_create runs after
                                   it also records a launch and prints its line (as `launch`, without issue)
launch NAME [--issue ID [--sub Tn [--kind measure|code]]] [--repo REPO --branch BRANCH [--base BASE]] [--type T]
       [--model M] [--production | --no-production] [--no-backlog] [--despite DID --why TEXT] [--json]
       | launch NAME --review REPO:BRANCH [--issue ID [--sub Tn]] | launch NAME --cancel
                                   records an agent's identity before launching it (<state>/launches.json) and
                                   prints the launch line, the first line of its brief: `[foreman launch TOKEN]
                                   NAME · ID Tn · branch B · worktree W · production`; with --repo/--branch it makes
                                   the worktree first (as `worktree`), or reuses the one foreman made for BRANCH
                                   when no live agent has it; --review: a reviewer of that branch, no worktree nor
                                   backlog change, that holds the branch (not offered to integrate) while it
                                   lives; with --issue/--sub it marks the sub-issue ▶
                                   with agent and branch (and --kind) and the issue in progress (unless
                                   --no-backlog); a NAME a live agent or a pending launch has is exit 2 (--cancel
                                   drops a pending launch). A production launch of an issue or sub-issue that a
                                   pending decision holds (decision add --blocks; a panel answer resolves it)
                                   is exit 2, nothing made: answer it, or --despite DID (each one that holds it)
                                   --why TEXT, recorded in the launch and the notes, with a line for the owner.
                                   Production work of an issue (a production type, --production, or a mechanic)
                                   without --repo/--branch is exit 2: give it its worktree, or --no-production if
                                   it changes no code
integrate REPO BRANCH "ID [Tn]" … [--title T] [--body B | --body-file F] [--sub Tn] [--note N] [--report PATH]
          [--result R] [--verdict V] [--rebase-from REF] [--approved A] [--ci ARGS]… [--no-ci] [--no-review WHY]
          [--opens] [--check] [--measure] [--keep-branch]   (foreman integrate --help says what each does)
holdout seal ID [--no-baseline] | run REPO BRANCH ID [--full] | status [ID]   an issue's hidden acceptance check
                                   (the orchestrator's; foreman holdout --help): seal hashes its folder and runs
                                   the cases on main; run runs them on a branch as integrate does
guard "command" [--as agent|planner|orchestrator] | guard --table FILE   what the guard hook would say about a command
guard --file PATH [--as agent|planner|orchestrator] [--cwd DIR]   …and about a Write or Edit call that writes PATH
                                   (exit 2 = rejected; --as: who runs it, default an agent), or check a table of
                                   commands with their expected verdict
permissions [--table T] [--json] | permissions --candidate FILE [--table T] [--json]   the product's permissions in
                                   bands (never, only the orchestrator after the owner's yes, waits its turn), each
                                   rule with the table rows that check it (none: a warning), and the commands the
                                   orchestrator must keep; --candidate: what a new foreman.toml would change (rules,
                                   rows whose verdict changes, rows that fail; exit 1 if a row fails or the
                                   orchestrator loses a command, 2 if it does not load); only reads
permissions --machine-candidate FILE [--json]   what a changed [machine] section of the registry would do to
                                   each product (FILE: a copy of the registry; only reads)
permissions --refused [--since 7d] [--json]   the guard's refusals by rule and who ran into them (<state>/guard.jsonl,
                                   without the commands); the view shows each rule's count of the last 7 days
permissions apply FILE [--table T] [--base HASH] [--opens]   tests the candidate as --candidate and, only with 0,
                                   keeps a copy of foreman.toml (and the table) in <state>/permissions/, replaces
                                   them atomically, loads again and writes a note; exit 2, nothing written, if
                                   foreman.toml changed since the copy (its base hash) or the change allows
                                   something new without --opens (which asks the owner yes/no). Only the marked
                                   orchestrator runs it
permissions grants [off [GROUP]] [--json]   the owner's grants for the [deny.grants] groups (granted until, cooling
                                   down after a no, a request waiting for the owner, closed); off: remove them
                                   (every group, or GROUP) and the requests that wait; anyone may, as it only takes
                                   away
check [--run-checks]               validates foreman.toml, the backlog and the [check] guard table; --run-checks:
                                   also runs each [loop] checks command once and validates its output lines
config [--json]                    the project's configuration as foreman resolved it: every key with its default
                                   filled in, the paths absolute, and where each value comes from (default,
                                   foreman.toml, registry, environment); read-only, for a product's own commands
config --projects                  the registry: name, mode and home of each project; no --project
loop [--json]                      the loop pass notices, without the heartbeat or marking them seen
                                   (--json: kind, actionable, text, key)
start [--json]                     what a new orchestrator session needs, in one short summary: its own session id
                                   and the `session` line to run, who is marked and whether it lives, the loop to
                                   create, the board, the active backlog, what waits for the owner, locks and
                                   agents, and what to read next only if needed
renew [--now] [--note TEXT]        the marked orchestrator session asks to renew its memory in place: foreman's
                                   hooks module compacts it at its next idle minute and foreman gives it back its
                                   pack (rules, board, backlog, agents, the owner's last messages); --now: below
                                   [loop] context_rotate too, without waiting for live agents
session SESSION_ID [--name NAME]   marks the orchestrator's session (resume and loop hooks act only there) and the
                                   name other sessions write to (default: its Claude Code name, if found), and
                                   says whom it relieves; `foreman session` alone shows who orchestrates and
                                   its context (as foreman's hooks module last measured it)
session --all [--behind] [--json]  the live Claude Code sessions of every registered product with the foreman
                                   each runs (recorded, or «≈» estimated) and whether it is behind the installed
                                   one or settling (just installed: it may load it by itself); --behind: only those
                                   on an older foreman (behind or settling); no --project
agents [--json] [--stop ID]        one line per agent: id, name, state (working, waiting, delivered, stopped),
                                   issue/sub, branch, minutes, model, outcome; --json (--full): the full records
agents --name ID NAME [--issue ID] [--sub Tn] [--repo R] [--branch B] [--worktree W]   the identity of an agent
                                   launched without a launch line, by hand (ID: its id or a unique prefix)
agents --misled [--since YYYY-MM-DD] [--type T] [--model M] [--json]
                                   the `Misled:` lines of the reports (what misled each agent) since the day
                                   (default: the last `*-review-harness*.md`, else 7 days), with date, agent,
                                   type, model and report; the footer counts the reports with something, `none`
                                   and without the line
agents --cost [--by issue|sub|type|model|week|day] [--since D] [--until D] [--backfill D] [--json]
                                   what the agents cost at API list prices, from their transcripts (recorded at
                                   `progress --done`, or when the pass sees one stopped; --backfill D records the
                                   agents of the machine since D): runs, rework, minutes, MTok, $, share of tokens
                                   on Sonnet or Haiku
init HOME --name NAME [--owner O] [--language en|es] [--repo KEY=PATH]… [--local-ci [KEY=]CMD]… [--slots N]
     [--every-min N] [--sample [--prefix PX]] [--mode active|inactive] [--no-register] [--dry-run]
                                   a new project's home: a minimal foreman.toml, starting rules, notes/, reviews/,
                                   the backlog (--sample: with a sample issue in triage) and its registry entry
                                   (appended; --no-register: none); each step says «already there» when it is,
                                   and anything different already there is exit 2 with nothing written;
                                   --dry-run: prints what it would write; no --project
panel [--port 8766] [--host H] [--token T] [--follow-installed]   the web panel of every registered project
                                   (foreman panel --help); --follow-installed moves to a new installed version
panel service install|uninstall|status|print [--label L] [--host H] [--port P] [--replace]   the panel as a
                                   user service (LaunchAgent or systemd user unit) that follows the installed
                                   version; without --label, the only panel service there is
plain [TEXT…|-] [--json]           what a text for the owner names without explaining (bare ids, internal names,
                                   this project's agent and branch names; docs/owner.md): exit 0 nothing, 1
                                   something to explain, 2 no text; - or no TEXT reads stdin
plain --stats [--since 7d] [--session ID] [--json]   the share of the orchestrator's messages to the owner that
                                   the chat reviewer flagged, per day
catchup [--since 2h|HH:MM|DATE] [--json]   what the owner missed, in four parts: done, running, waiting for them,
                                   went wrong (filtered: no planners stopped, no CI that passed later); only reads;
                                   without --since it counts from the last catchup (else 24 h, at most 7 days) and
                                   moves that mark
docs [orchestrator|agent|owner|config|extending|…]   prints a page of docs/ (agents need no path; alone: the list)
selftest [-v] [-j N] [--python PATH] [--quality auto|require|off] [--mods auto|require|off] [--quality-only] [--fix]
         [--coverage] [--update-golden] [NAME]
                                   the test suite on fake projects in a temporary directory; -j: test classes run
                                   in N processes (default: the cores shared by the selftests running on the
                                   machine; -j 1 serial); --python: with that interpreter (e.g. /usr/bin/python3);
                                   NAME filters; --quality: the code checks
                                   (ruff, pyright) at the versions of pyproject.toml, skipped without them in auto;
                                   --mods: the hooks module (claude plugin validate/test), skipped without Claude
                                   Code in auto; --quality-only: no tests; --fix: ruff's safe fixes first;
                                   --coverage: the suite under coverage.py, a table per module and the total;
                                   --update-golden: only the golden corpus, rewriting its files under tests/golden/
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import __version__, config, util

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from .config import Project


def doc() -> str:
    """The help text, the same on every Python (3.13+ dedents docstrings itself); inspect only loads for it."""
    import inspect

    return inspect.cleandoc(__doc__ or "")


HERE = Path(__file__).resolve().parent


def need_project(args: argparse.Namespace) -> Project:
    try:
        return config.resolve(args.global_project)
    except config.ConfigError as e:
        print(f"foreman: {e}", file=sys.stderr)
        sys.exit(2)


def guard_table(p: Project, path: Path, indent: str = "") -> int:
    """Runs a guard table (foreman guard --table, [check] guard_table): 0 if every row gets its expected verdict."""
    from . import guard

    try:
        counts, failures = guard.run_table(p, path)
    except (OSError, ValueError) as e:
        print(f"{indent}guard table {path}: {e}")
        return 1
    summary = ", ".join(f"{counts.get(k, 0)} {k}" for k in ("deny", "lock", "allow"))
    if counts.get("orchestrator"):
        summary += f", {counts['orchestrator']} orchestrator"
    print(f"{indent}guard table {path}: " + ("ok" if not failures else f"{len(failures)} wrong") + f" ({summary})")
    for f in failures:
        print(f"{indent}  {f}")
    return 1 if failures else 0


# The commands main() dispatches before argparse, with their line in `foreman -h` (test_help_lists_every_command),
# but `hook` (internal: Claude Code's hooks call it), which `foreman -h` leaves out.
PASS_THROUGH = {
    "lock": 'a shared resource\'s queue: RESOURCE run "what" cmd… | acquire | release | check',
    "wait": "wait in the background for a pid, a file, a line or a lock: [-t S] [-i S] pid|file|line|lock …",
    "time-limit": "run a command with a time limit: SECONDS cmd…",
    "hung-shells": "list (or --kill) shells left hanging by a command",
}

INBOX_DOC = """\
The owner's inbox: what the owner sends from the panel (messages, decision answers, backlog moves) and the
orchestrator's replies, shown under them.

  new            the owner's messages not read yet, one per line (id, time, kind: text)
  read [ID…]     mark the owner's messages read (all of them, or these ids)
  reply "text"   the orchestrator's reply, shown to the owner in the panel
  add "text"     add a message as if the owner had sent it (what the owner said in the chat, say)"""

# The README's table of commands, by group: every command of build_parser() once (tests/test_docs_tied.py).
COMMAND_GROUPS = {
    "Set up and check": ["init", "check", "config", "permissions", "guard", "docs", "selftest"],
    "The orchestrator's session": ["start", "session", "loop", "renew", "catchup", "recurring"],
    "Agents and branches": ["launch", "worktree", "agents", "integrate", "holdout"],
    "Backlog, notes and the owner": ["backlog", "note", "progress", "inbox", "plain", "panel"],
    "Shared resources and waiting": ["lock", "wait", "time-limit", "hung-shells"],
}


def command_table(ap: argparse.ArgumentParser | None = None) -> str:
    """The README's table of commands, from the parser's help texts, grouped by COMMAND_GROUPS."""
    sp = next(a for a in (ap or build_parser())._actions if isinstance(a, argparse._SubParsersAction))
    helps = {a.dest: a.help or "" for a in sp._choices_actions}
    out = []
    for group, names in COMMAND_GROUPS.items():
        out += [f"**{group}**", "", "| Command | What it does |", "|---|---|"]
        out += [f"| `{n}` | {helps[n].replace('|', chr(92) + '|')} |" for n in names] + [""]
    return "\n".join(out).rstrip() + "\n"


# Commands that act (write state, take a lock, merge, mark): run from a replaced plugin folder they say so on stderr.
ACTING = {
    "lock",
    "integrate",
    "holdout",
    "worktree",
    "launch",
    "session",
    "check",
    "note",
    "progress",
    "inbox",
    "permissions",
    "recurring",
    "renew",
    "agents",
    "backlog",
}
BACKLOG_READS = {"show", "list", "get", "render"}


def acts(argv: list[str]) -> bool:
    """Whether this command line acts (ACTING; not with --json, `--help` or a read-only form of it)."""
    if argv[:1] == ["launch"] and not {"-h", "--help"} & set(argv):
        return True  # it acts with --json too
    if not argv or argv[0] not in ACTING or "--json" in argv or "-h" in argv or "--help" in argv:
        return False
    rest = argv[1:]
    if argv[0] == "backlog":
        return bool(rest) and rest[0] not in BACKLOG_READS
    if argv[0] == "session":
        return bool(rest) and not rest[0].startswith("-")
    if argv[0] == "worktree":
        return "--list" not in rest
    if argv[0] == "agents":
        return "--stop" in rest or "--name" in rest
    if argv[0] == "inbox":
        return rest[:1] != ["new"]
    if argv[0] == "permissions":
        return rest[:2] == ["grants", "off"]
    if argv[0] == "recurring":
        return rest[:1] == ["done"]
    return True


def launch_backlog(
    p: Project,
    issue: str,
    sub: str | None,
    name: str,
    branch: str | None,
    check_only: bool = False,
    kind: str | None = None,
) -> str | None:
    """`launch --issue/--sub`: the sub-issue ▶ with agent and branch (and `kind`, from --kind), the issue in
    progress. check_only: only that the issue and the sub-issue exist (before anything is made); returns the
    sub-issue's `type` (TYPE[/MODEL]) if it has one. Raises backlog.BacklogError."""
    from . import backlog

    b = backlog.Backlog(p)
    if check_only:
        data = b.load()
        i = b.issue(data, b.resolve(data, issue))
        k = backlog.find_sub(i, sub) if sub else None
        if sub and k is None:
            raise backlog.BacklogError(
                f"{i['id']} has no sub-issue {sub} (foreman backlog sub {i['id']} {sub} --title …)"
            )
        return i["subs"][k].get("type") if k is not None else None
    with b.mutate() as data:
        iid = b.resolve(data, issue)
        if sub:
            b.set_sub(data, iid, sub, status="▶", agent=name, branch=branch, kind=kind)
        if b.issue(data, iid).get("status") not in ("in progress", *backlog.CLOSED):
            b.set_status(data, iid, "in progress")
    return None


def launch_holds(p: Project, issue: str, sub: str | None) -> tuple[list[dict[str, Any]], str]:
    """(the pending decisions that hold a production launch of `issue` / `sub`, what is launched «XX-1 T3»): those
    whose `blocks` name the issue as a whole or the sub-issue (Backlog.held_by); an answer from the panel resolves
    its decision, so it holds nothing more. Without a backlog, or an issue it does not know, nothing holds it."""
    from . import backlog

    b = backlog.Backlog(p)
    if not b.exists():
        return [], ""
    try:
        data = b.load()
        iid = b.resolve(data, issue)
        i = b.issue(data, iid)
    except backlog.BacklogError:
        return [], ""
    k = backlog.find_sub(i, sub) if sub else None
    sid = i["subs"][k]["id"] if k is not None else sub
    ids = b.held_by(b.decision_holds(data), iid, sid)
    by_id = {d["id"]: d for d in data["decisions"]}
    return [by_id[x] for x in ids if x in by_id], f"{iid} {sid}" if sid else iid


def launch_held_text(name: str, held: list[dict[str, Any]], what: str) -> str:
    """What `launch` says when pending decisions hold it: each decision, and the two ways out."""
    decs = "; ".join(f"{d['id']} «{d['title']}» (pending since {d['date']})" for d in held)
    resolve = " and ".join(f'foreman backlog decision resolve "{d["id"]}" --answer …' for d in held)
    despite = " ".join(f'--despite "{d["id"]}"' for d in held)
    return (
        f"{what} waits for the owner's decision {decs}: nothing made. Either the owner answers it and you run "
        f'{resolve}, or launch {name} anyway with {despite} --why "…" (recorded, and told to the owner)'
    )


def launch_command(p: Project, args: argparse.Namespace) -> int:
    """`foreman launch`: exit 0 recorded (a backlog that could not be updated is a warning), 1 the worktree could
    not be made, 2 bad arguments or a NAME already in use."""
    from . import backlog, identity, worktree

    def fail(msg: object, rc: int = 2) -> int:
        print(f"foreman launch: {msg}", file=sys.stderr)
        return rc

    if args.cancel:
        hits = identity.cancel(p, args.name)
        print(f"dropped: {', '.join(hits)}" if hits else f"no pending launch of {args.name}")
        return 0 if hits else 1
    if not re.fullmatch(r"[\w.-]+", args.name):
        return fail(f"bad name {args.name!r}: letters, digits, '.', '_' and '-'")
    if bool(args.repo) != bool(args.branch):
        return fail("--repo and --branch go together")
    if args.base and not args.repo:
        return fail("--base needs --repo and --branch")
    if args.sub and not args.issue:
        return fail("--sub needs --issue")
    if args.kind and not args.sub:
        return fail("--kind needs --issue and --sub (it marks the sub-issue)")
    rec_repo, rec_branch = args.repo, args.branch  # what the launch records (a reviewer's: --review)
    if args.review is not None:
        rkey, _, rbranch = args.review.partition(":")
        if args.repo or args.branch or args.base or args.kind:
            return fail("--review REPO:BRANCH goes without --repo, --branch, --base and --kind (no worktree)")
        if args.production:
            return fail("--review launches a reviewer, never a production agent")
        if not rkey or not rbranch:
            return fail(f"--review {args.review!r}: REPO:BRANCH")
        if rkey not in p.repos:
            return fail(f"--review: no repo {rkey!r} ({', '.join(p.repos) or 'none configured'})")
        rpath = p.repo(rkey)["path"]
        if not rpath or not worktree.exists(rpath, f"refs/heads/{rbranch}"):
            return fail(f"--review: {rkey} has no local branch {rbranch!r}")
        args.agent_type = args.agent_type or "reviewer"
        args.no_backlog = True
        rec_repo, rec_branch = rkey, rbranch
    elif args.issue and not args.repo and identity.needs_worktree(p, args.production, args.agent_type):
        return fail(
            "a production agent needs --repo REPO --branch BRANCH (its worktree); --no-production if it changes no code"
        )
    taken = identity.name_taken(p, args.name)
    if taken:
        return fail(f"{taken}: choose another name")
    update = bool(args.issue) and not args.no_backlog and backlog.Backlog(p).exists()
    planned = None
    if update:
        try:
            planned = launch_backlog(p, args.issue, args.sub, args.name, args.branch, check_only=True)
        except backlog.BacklogError as e:
            return fail(f"{e} (or --no-backlog)")
    if planned:  # the sub-issue's type and model are the defaults; a dearer one is refused
        ptype, pmodel = identity.split_type(planned)
        args.agent_type = args.agent_type or ptype
        args.model = args.model or (pmodel if args.agent_type == ptype else None)
        if identity.price_rank(args.agent_type, args.model) > identity.price_rank(ptype, pmodel):
            return fail(
                f"{args.agent_type}/{args.model or 'inherit'} costs more than the plan's {planned} for "
                f"{args.issue} {args.sub}: launch it as planned, or change the plan first "
                f"(foreman backlog sub {args.issue} {args.sub} --type {args.agent_type}/{args.model or 'opus'}) "
                "and say why in a note"
            )
    despite = [" ".join(x.split()) for x in args.despite or []]
    why = " ".join((args.why or "").split())
    if args.why is not None and not despite:
        return fail("--why goes with --despite DID")
    if despite and not why:
        return fail("--despite needs --why TEXT: why it is launched before the owner answers (it is recorded)")
    held: list[dict[str, Any]] = []
    what = ""
    if args.issue and identity.production_at_launch(p, args.production, bool(args.repo), args.agent_type):
        held, what = launch_holds(p, args.issue, args.sub)  # also with --no-backlog: that only leaves it unwritten
    held_ids = {d["id"].casefold() for d in held}
    wrong = [x for x in despite if x.casefold() not in held_ids]
    if wrong:
        holding = f"; it waits for {', '.join(d['id'] for d in held)}" if held else ""
        return fail(f"--despite {', '.join(wrong)}: no pending decision of that id holds this launch{holding}")
    if [d for d in held if d["id"].casefold() not in {x.casefold() for x in despite}]:
        return fail(launch_held_text(args.name, held, what))
    wt = None
    if args.repo:
        try:
            wt, how, warnings = worktree.create(p, args.repo, args.name, args.branch, args.base, reuse=True)
        except (worktree.WorktreeError, config.ConfigError) as e:
            return fail(e, 1)
        for w in warnings:
            print(f"foreman launch: warning: {w}", file=sys.stderr)
        if not args.json:
            print(f"{wt} ({args.branch} {how})")
    token, rec = identity.new_launch(
        p,
        args.name,
        issue=args.issue,
        sub=args.sub,
        repo=rec_repo,
        branch=rec_branch,
        worktree=wt,
        agent_type=args.agent_type,
        production=args.production,
        model=args.model,
        despite=[{"decision": d["id"], "why": why} for d in held],
        review=args.review is not None,
        planned=planned,
    )
    if update:
        try:
            launch_backlog(p, args.issue, args.sub, args.name, args.branch, kind=args.kind)
        except backlog.BacklogError as e:
            print(f"foreman launch: warning: the backlog was not updated: {e}", file=sys.stderr)
    tell = launch_despite_note(p, args.name, held, what, why) if held else None
    line = identity.launch_line(token, rec)
    if args.json:
        out = {"token": token, "line": line, **rec, **({"tell_owner": tell} if tell else {})}
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        if tell:
            print(f"tell the owner in the chat: {tell}")
        print(line)
    return 0


def launch_despite_note(p: Project, name: str, held: list[dict[str, Any]], what: str, why: str) -> str:
    """A launch let through with --despite: the note of the day («launch despite») and the sentence the orchestrator
    tells the owner in the chat (returned). A note that cannot be written is a warning."""
    from . import notes

    decs = " and ".join(f"«{d['title']}» ({d['id']})" for d in held)
    text = f"{name} was launched for {what} before the owner answered {decs}: {why}"
    try:
        notes.note(p, "launch despite", text)
    except (OSError, ValueError) as e:
        print(f"foreman launch: warning: no note written: {e}", file=sys.stderr)
    return text


def config_text(d: Mapping[str, Any]) -> str:
    """`foreman config` without --json: one `section.key = value  (source)` line per key, the TOML way."""
    out = [
        f"{d['name']}: {d['home']} ({d['mode']}{', registry' if d['registered'] else ''}); "
        f"config {d['config_file']}; foreman {d['foreman']}, FOREMAN_API {d['api']}"
    ]

    def lines(prefix: str, values: Mapping[str, Any], sources: Mapping[str, Any]) -> None:
        for k, v in values.items():
            out.append(f"{prefix}{k} = {json.dumps(v, ensure_ascii=False, default=str)}  ({sources[k]})")

    for sec, values in d["config"].items():
        src = d["sources"][sec]
        if sec in ("repos", "resources"):
            for name, table in values.items():
                lines(f"{sec}.{name}.", table, src[name])
        elif sec == "recurring":
            out += [f"recurring = {json.dumps(t, ensure_ascii=False, default=str)}  ({src})" for t in values]
        else:
            lines(f"{sec}.", values, src)
    out += [f"warning: {w}" for w in d["warnings"]]
    return "\n".join(out)


def run_checks_report(p: Project) -> int:
    """`foreman check --run-checks`: each [loop] checks command run once (notices.try_checks), its notices and what is
    wrong with each line. 1 if a check would be a check_failed notice in the loop (an exit code other than 0 or 1,
    or cut at its time limit); a line the loop reads otherwise than it says (bad JSON, foreman's own kind) is a
    warning and does not fail."""
    from . import notices

    runs = notices.try_checks(p)
    if not runs:
        print("  loop checks: none ([loop] checks)")
        return 0
    rc = 0
    for r in runs:
        print(
            f"  loop check `{r['cmd']}`: "
            + (f"FAILED, {r['failed']}" if r["failed"] else f"rc {r['rc']}")
            + f", {util.plural(len(r['lines']), 'notice')}"
        )
        if r["failed"]:
            rc = 1
            err = r["stderr"].strip()
            if err:
                print(f"    stderr: {err[:300]}")
        for i, (n, problems) in enumerate(r["lines"], 1):
            print(f"    {i}. {n['kind']}{'' if n['actionable'] else ' (info)'}: {n['text']}")
            for w in problems:
                print(f"       warning: {w}")
    return rc


def check(p: Project, run_checks: bool = False) -> int:
    """`foreman check`: the project's configuration (strict), Claude Code, the registry, repos, resources, rules,
    guard table and backlog; also printed by `foreman init` for the project it made. Returns the exit code."""
    print(f"{p.name}: {p.home} ({p.mode}); state {p.state}")
    rc = 0
    # strict: what the tolerant read only warned about (unknown keys, a newer config schema) fails the check
    schema = p.section("project").get("config_schema")
    print(
        f"  config schema {schema} (this foreman knows {config.CONFIG_SCHEMA}): "
        + ("ok" if not p.warnings else f"{len(p.warnings)} problems")
    )
    for w in p.warnings:
        print(f"    {w}")
    rc = max(rc, 1 if p.warnings else 0)
    keys, rules, dropped = p.inherited()
    print(
        f"  inherited from the machine (registry [machine]): {util.plural(keys, 'setting')}, "
        f"{util.plural(rules, 'deny rule')}" + (f"; [deny] drop removes {dropped} here" if dropped else "")
    )
    from . import versions

    cc = versions.claude_code_version()
    print(
        f"  claude code {cc or 'not found'} (foreman needs >= {versions.CLAUDE_CODE_MIN}): "
        + (
            "version unknown"
            if not cc
            else "below the minimum, run `claude update`"
            if versions.claude_code_below_min(cc)
            else "ok"
        )
    )
    try:
        broken, reg_warnings = [], []
        config.registered_projects(broken=broken, warnings=reg_warnings)
        for x in reg_warnings:  # the registry's own (machine resources too): they fail only the products using them
            print(f"  (registry) {x}")
        for x in [f"{b['name']} does not load: {b['error']}" for b in broken if b["name"] != p.name]:
            print(f"  (registry, other products) {x}")
    except config.ConfigError as e:
        print(f"  (registry) {e}")
    for key in p.repos:
        r = p.repo(key)
        ok = r["path"] and (Path(r["path"]) / ".git").exists()
        print(f"  repo {key}: {r['path']} merge={r['merge']}" + ("" if ok else "  (not a git checkout)"))
    for name, res in p.resources.items():
        origin = "machine (registry)" if p.resource_origin.get(name) == "machine" else "this project"
        print(
            f"  resource {name}: {origin}, foreman lock"
            + (f", busy {res['busy']}" if res.get("busy") else "")
            + (f", slots {', '.join(res['slots'])}" if res.get("slots") else "")
            + f", guard {len(res.get('guard') or [])}"
        )
    from . import lock

    for w in lock.shared_warnings(p):  # a warning: it never fails the check (it is not this project's error)
        print(f"  warning: {w}")
    from . import identity

    for name, f in identity.shadowed_types(p):  # a warning: a choice of the project, but one that ages silently
        print(
            f"  warning: {f} shadows foreman's agent type {name} (Claude Code runs it instead, whole, and it does "
            "not follow foreman's updates; prefer a type with its own name, docs/extending.md)"
        )
    rules = p.path(p.raw["project"]["rules"])
    print(f"  rules {rules}: " + ("ok" if rules.exists() else "missing"))
    table = p.section("check").get("guard_table")
    if table:
        rc = max(rc, guard_table(p, p.path(table), indent="  "))
    from . import backlog, backlog_check

    b = backlog.Backlog(p)
    try:
        data = b.load() if b.exists() else None
    except backlog.BacklogError as e:
        print(f"  backlog {b.data_path}: {e}")
        return 1
    if data is not None:
        found = backlog_check.validate(b, data)
        errors = [e for e in found if not e.startswith("warning: ")]
        print(f"  backlog {b.data_path}: " + ("ok" if not errors else f"{len(errors)} problems"))
        for e in errors + [e for e in found if e.startswith("warning: ")]:  # a warning never fails the check
            print(f"    {e}")
        rc = max(rc, 1 if errors else 0)
    else:
        print(f"  backlog {b.data_path}: missing (foreman init {p.home} --name {p.name})")
    if run_checks:
        rc = max(rc, run_checks_report(p))
    return rc


def build_parser(only: str | None = None) -> argparse.ArgumentParser:
    """foreman's argument parser (COMMAND_GROUPS orders its commands for the README's table). `only`: the command
    to be run; the commands that live in their own modules are then registered by name only (nothing to parse
    for them), so their modules stay unloaded."""

    def module_parser(name: str, module: str | None = None) -> None:
        if only is None or only == name:
            import_module(f".{module or name}", __package__).add_parser(sp)
        else:
            sp.add_parser(name)

    d = doc()
    ap = argparse.ArgumentParser(
        prog="foreman",
        description=d.split("\n\n")[0],
        epilog=d.split("\n\n", 1)[1],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--version", action="version", version=f"foreman {__version__}")
    sp = ap.add_subparsers(dest="cmd", required=True)
    module_parser("backlog", "backlog_cli")
    module_parser("integrate")
    module_parser("holdout")
    a = sp.add_parser("note", help="append an entry to today's notes (what must last: figures, corrections)")
    a.add_argument("title")
    a.add_argument("text", nargs="*")
    a = sp.add_parser("progress", help="a progress line for the owner's panel; --done: an agent's delivery")
    a.add_argument("--done", action="store_true")
    a.add_argument("text", nargs="+")
    a.add_argument("--outcome", choices=("ok", "partial", "failed"))
    a.add_argument("--report", metavar="PATH")
    a.add_argument("--kind", choices=("measure", "code"))
    a = sp.add_parser(
        "inbox",
        help="the owner's messages from the panel and the orchestrator's replies",
        description=INBOX_DOC,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    a.add_argument("action", choices=("new", "read", "reply", "add"), help="what to do (above)")
    a.add_argument("rest", nargs="*", help="read: message ids; reply, add: the text")
    a = sp.add_parser("recurring", help="the recurring tasks and which are late; done NAME: the task was just done")
    a.add_argument("action", nargs="?", choices=("done",))
    a.add_argument("name", nargs="?")
    a = sp.add_parser("worktree", help="a worktree on a new or an existing branch (local or only on the remote)")
    a.add_argument("repo", nargs="?")
    a.add_argument("name", nargs="?")
    a.add_argument("branch", nargs="?")
    a.add_argument("base", nargs="?")
    a.add_argument("--list", action="store_true")
    a = sp.add_parser("launch", help="record an agent's identity (and make its worktree) before launching it")
    a.add_argument("name")
    a.add_argument("--issue")
    a.add_argument("--sub")
    a.add_argument("--kind", choices=("measure", "code"), help="the sub-issue's kind (measure: no code for main)")
    a.add_argument("--repo")
    a.add_argument("--branch")
    a.add_argument("--base")
    a.add_argument("--type", dest="agent_type")
    a.add_argument(
        "--review",
        metavar="REPO:BRANCH",
        help="a reviewer of that branch: no worktree, no backlog change, --type reviewer unless given; the branch is "
        "not offered to integrate while it lives",
    )
    a.add_argument(
        "--model", help="the model the agent is launched with (recorded; the one that ran is read at its stop)"
    )
    a.add_argument("--production", action="store_true", default=None)
    a.add_argument("--no-production", dest="production", action="store_false")
    a.add_argument("--no-backlog", action="store_true", help="leave the backlog alone")
    a.add_argument(
        "--despite",
        action="append",
        metavar="DID",
        help="launch although pending decision DID holds it (repeat for each one; needs --why)",
    )
    a.add_argument("--why", help="why it is launched despite the pending decisions (recorded, and told to the owner)")
    a.add_argument("--json", action="store_true", help="{token, line, name, issue, …}")
    a.add_argument("--cancel", action="store_true", help="drop the pending launches of NAME (no agent took them)")
    a = sp.add_parser(
        "guard", help="what the command guard would say about a command or a file write, or check a table of commands"
    )
    a.add_argument("command", nargs="?")
    a.add_argument("--table")
    a.add_argument(
        "--file",
        metavar="PATH",
        help="what the guard says of a Write or Edit call that writes PATH (a reserved file; relative to --cwd)",
    )
    a.add_argument(
        "--as",
        dest="role",
        choices=("agent", "planner", "orchestrator"),
        default="agent",
        help="check the command as an agent (default), a planner agent or the marked orchestrator's main thread",
    )
    a.add_argument(
        "--cwd",
        metavar="DIR",
        help="the folder the command runs in, for what a `git push` would do (default: the current folder)",
    )
    a = sp.add_parser("loop", help="the notices a loop pass would get (markers read, not written)")
    a.add_argument("--json", action="store_true", help="the notices with kind, actionable, text and key")
    a = sp.add_parser("renew", help="renew the orchestrator session's memory in place (docs/orchestrator.md)")
    a.add_argument(
        "--now", action="store_true", help="at the next idle minute: below the threshold too, live agents too"
    )
    a.add_argument("--note", help="what only your memory holds, into the pack")
    a = sp.add_parser(
        "session", help="mark the orchestrator's session, or show who orchestrates; --all: every product's sessions"
    )
    a.add_argument("session_id", nargs="?")
    a.add_argument("--name")
    a.add_argument("--all", action="store_true", help="the live sessions of every product with the foreman each runs")
    a.add_argument(
        "--behind", action="store_true", help="with --all: only those on an older foreman (behind or settling)"
    )
    a.add_argument("--json", action="store_true", help="with --all: {installed, sessions: [...]}")
    a = sp.add_parser("agents", help="the agents the hooks saw, with their state; name or stop one")
    a.add_argument("--stop")
    a.add_argument(
        "--json",
        "--full",
        dest="json",
        action="store_true",
        help="every agent's full record (identity, transcript, state) as JSON; default: one line each",
    )
    a.add_argument("--name", nargs=2, metavar=("ID", "NAME"), help="give an agent its identity by hand")
    for k in ("--issue", "--sub", "--repo", "--branch", "--worktree"):
        a.add_argument(k)
    a.add_argument(
        "--misled", action="store_true", help="the `Misled:` lines of the reports, with a footer that counts them"
    )
    a.add_argument(
        "--since", metavar="YYYY-MM-DD", help="with --misled: from this day (default: the last harness review)"
    )
    a.add_argument("--type", dest="agent_type", metavar="T", help="with --misled: only this type of agent")
    a.add_argument("--model", metavar="M", help="with --misled: only the models whose id has M")
    a.add_argument(
        "--cost",
        action="store_true",
        help="what the agents cost (list prices, from their transcripts), by --by; --since/--until by day; --json",
    )
    a.add_argument(
        "--by",
        choices=("issue", "sub", "type", "model", "week", "day"),
        default="issue",
        help="with --cost: group by (default issue)",
    )
    a.add_argument("--until", metavar="YYYY-MM-DD", help="with --cost: before this day")
    a.add_argument(
        "--backfill", metavar="YYYY-MM-DD", help="with --cost: first record the agents of the machine since this day"
    )
    a = sp.add_parser("check", help="validate foreman.toml, the backlog and the guard table")
    a.add_argument(
        "--run-checks",
        action="store_true",
        help="also run each [loop] checks command once, as the loop does, and validate its output lines (nothing is "
        "marked)",
    )
    a = sp.add_parser("config", help="the resolved configuration, with the source of each value (read-only)")
    a.add_argument("--json", action="store_true", help="{foreman, api, name, home, …, config, sources, warnings}")
    a.add_argument("--projects", action="store_true", help="the registry: name, mode and home of each project")
    module_parser("init")
    a = sp.add_parser(
        "panel", help="the web panel of every registered project; panel service: run it as a user service"
    )
    a.add_argument("--port", type=int, default=8766)
    a.add_argument("--host", default="127.0.0.1", help="0.0.0.0 to reach it from the phone (the key still applies)")
    a.add_argument("--token", help="the key (default: FOREMAN_PANEL_TOKEN or <state root>/panel/token)")
    a.add_argument(
        "--follow-installed",
        action="store_true",
        help="re-execute into the installed foreman when its version changes (what the hooks record)",
    )
    # Dispatched above, before argparse (their arguments are a command line): registered only so `foreman -h` lists
    # them.
    for name, text in PASS_THROUGH.items():
        sp.add_parser(name, help=text)  # `hook` is dispatched above too, and left out of `foreman -h`
    a = sp.add_parser("plain", help="check a text for the owner: bare ids and internal names (docs/owner.md)")
    a.add_argument("text", nargs="*", help="the text (- or nothing: stdin)")
    a.add_argument("--json", action="store_true", help="{words, findings: [{kind, what, text, start, end, reason}]}")
    a.add_argument(
        "--stats",
        action="store_true",
        help="the share of the orchestrator's messages to the owner with findings, per day (the chat reviewer)",
    )
    a.add_argument("--since", help="with --stats: 7d, 12h, 30m or a date (2026-10-31)")
    a.add_argument("--session", help="with --stats: one session id only")
    module_parser("start")
    module_parser("catchup")
    module_parser("permissions")
    a = sp.add_parser("docs", help="print a page of foreman's docs (alone: the list of pages)")
    a.add_argument("name", nargs="?")
    a = sp.add_parser(
        "selftest", help="foreman's own checks: static checks, the test suite on fake projects, code quality"
    )
    a.add_argument("-v", action="store_true")
    a.add_argument("pattern", nargs="?")
    a.add_argument(
        "-j",
        type=int,
        metavar="N",
        help="worker processes (default: the cores shared by the selftests running on the machine, at least 2; "
        "1: serial, in-process)",
    )
    a.add_argument(
        "--python",
        metavar="PATH",
        help="run the selftest with this Python (e.g. /usr/bin/python3, the "
        "oldest foreman supports) instead of the one running foreman",
    )
    a.add_argument(
        "--update-golden",
        action="store_true",
        help="run only the golden corpus (tests/test_golden.py) "
        "and rewrite its files under tests/golden/ as the outputs are now; says which changed",
    )
    a.add_argument(
        "--quality",
        choices=("auto", "require", "off"),
        help="the code quality checks (ruff, ruff format, "
        "pyright at the versions pinned in pyproject.toml): auto (default) skips a tool it cannot get, "
        "require fails, off does not run them (the default with --python)",
    )
    a.add_argument(
        "--mods",
        choices=("auto", "require", "off"),
        help="the hooks module's check (claude plugin validate --strict and "
        "claude plugin test with the installed Claude Code): auto (default) skips without Claude Code or below its "
        "minimum, require fails, off does not run it (the default with --python)",
    )
    a.add_argument(
        "--quality-only", action="store_true", help="only the static, code quality and mods checks, no tests"
    )
    a.add_argument(
        "--fix",
        action="store_true",
        help="apply ruff's safe fixes (and ruff format once it is on), say "
        "which files changed, then run --quality-only",
    )
    a.add_argument(
        "--coverage",
        action="store_true",
        help="run the suite under coverage.py (subprocesses included; version pinned in pyproject.toml) and print "
        "the total and a table per module, least covered first; --quality=require fails when coverage cannot be had",
    )
    return ap


def cmd_lock(project_name: str | None, argv: list[str]) -> int:
    from . import identity, lock

    if len(argv) < 3:
        print('usage: foreman lock RESOURCE run "what" cmd… | acquire "what" [s] | release | check', file=sys.stderr)
        return 2
    p = config.maybe_resolve(project_name)
    who, _, default = identity.who("agent", "lock", [project_name, *argv])
    try:
        return lock.run(p, argv[1], argv[2:], who=who, default=default)
    except lock.LockError as e:
        print(f"foreman lock: {e}", file=sys.stderr)
        return 2


def cmd_wait(project_name: str | None, argv: list[str]) -> int:
    from . import waits

    rest, timeout, every = argv[1:], 0, 10
    while rest[:1] in (["-t"], ["-i"]) and len(rest) > 1:
        if rest[0] == "-t":
            timeout = float(rest[1])
        else:
            every = float(rest[1])
        rest = rest[2:]
    if not rest:
        print(waits.DOC, file=sys.stderr)
        return 2
    return waits.run(config.maybe_resolve(project_name), rest[0], rest[1:], timeout, every)


def cmd_selftest(args: argparse.Namespace) -> int:
    from . import selftest

    same = ("pattern", "python", "update_golden", "quality", "quality_only", "fix", "mods", "coverage")
    return selftest.run(verbose=args.v, jobs=args.j, **{k: getattr(args, k) for k in same})


def cmd_docs(args: argparse.Namespace) -> int:
    docs = HERE.parents[1] / "docs"
    if not args.name or not (docs / f"{args.name}.md").exists():
        print("docs: " + ", ".join(sorted(f.stem for f in docs.glob("*.md"))))
        return 0 if not args.name else 2
    print((docs / f"{args.name}.md").read_text())
    return 0


def cmd_panel(args: argparse.Namespace) -> int:
    from . import panel

    follow = args.argv[args.argv.index("panel") + 1 :] if args.follow_installed else None
    return panel.serve(args.host, args.port, args.token, follow=follow)


def cmd_config(args: argparse.Namespace) -> int:
    if not args.projects:
        d = need_project(args).as_dict()
        print(json.dumps(d, ensure_ascii=False, indent=1, default=str) if args.json else config_text(d))
        return 0
    if args.json:
        print("foreman config: --projects goes alone", file=sys.stderr)
        return 2
    try:
        broken = []
        for p in config.registered_projects(broken=broken):
            print(
                f"{p.name}\t{p.mode}\t{p.home}"
                + (f"\t{len(p.warnings)} config warnings (foreman check)" if p.warnings else "")
            )
        for b in broken:
            print(f"{b['name']}\t{b.get('mode') or '?'}\t{b.get('home') or '?'}\tdoes not load: {b['error']}")
    except config.ConfigError as e:
        print(f"foreman: {e}", file=sys.stderr)
        return 2
    return 0


def cmd_plain(args: argparse.Namespace) -> int:
    from . import plain

    p = config.maybe_resolve(args.global_project)
    return plain.run_stats(p, args.since, args.session, args.json) if args.stats else plain.run(p, args.text, args.json)


def cmd_backlog(args: argparse.Namespace) -> int:
    from . import backlog_cli

    if args.bcmd == "list" and args.all_products:
        return backlog_cli.run_list(None, args)
    return backlog_cli.run(need_project(args), args)


def module_command(module: str, function: str = "run") -> Callable[[Project, argparse.Namespace], int]:
    """A command its module runs whole, as `function(project=, args=)`."""
    return lambda p, args: getattr(import_module(f".{module}", __package__), function)(project=p, args=args)


def cmd_note(p: Project, args: argparse.Namespace) -> int:
    from . import notes

    body = " ".join(args.text) or notes.read_stdin_briefly()
    try:
        print(notes.note(p, args.title, body))
    except ValueError as e:
        print(f"foreman note: {e}", file=sys.stderr)
        return 2
    return 0


def cmd_progress(p: Project, args: argparse.Namespace) -> int:
    from . import identity, notes, plain

    for flag, what in (
        ("outcome", "the delivery's outcome"),
        ("report", "the delivery's report"),
        ("kind", "what the delivery brings"),
    ):
        if getattr(args, flag) is not None and not args.done:
            print(f"foreman progress: --{flag} goes with --done ({what})", file=sys.stderr)
            return 2
    who, aid, _ = identity.who("orchestrator", "progress", [args.global_project, *args.argv])
    report = agent_type = model = None
    if args.report is not None or args.done:
        from . import delivery

        if args.report is not None:
            try:
                report = delivery.named_report(p, args.report, [*delivery.worktrees_of(p, aid, who), os.getcwd()])
            except ValueError as e:
                print(f"foreman progress: --report {args.report}: {e}; nothing written", file=sys.stderr)
                return 2
        if args.done:
            agent_type, model = delivery.type_and_model(p, aid, who)
    notes.progress(
        p,
        " ".join(args.text),
        who=who,
        done=args.done,
        agent_id=aid,
        outcome=(args.outcome or "ok") if args.done else None,
        report=report,
        kind=args.kind if args.done else None,
        agent_type=agent_type,
        model=model,
    )
    if args.done:
        from . import costs

        costs.record(p, aid, who, args.outcome or "ok")
    plain.warn(p, "progress line", " ".join(args.text))  # the owner reads it (docs/owner.md): never blocks
    return 0


def agents_cost(p: Project, args: argparse.Namespace) -> int:
    from . import costs

    for flag in ("since", "until", "backfill"):
        v = getattr(args, flag)
        if v and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
            print(f"foreman agents: --{flag} {v}: a day as YYYY-MM-DD", file=sys.stderr)
            return 2
    if args.backfill:
        print(f"recorded {costs.backfill(p, args.backfill)} agents since {args.backfill}", file=sys.stderr)
    res = costs.rows(p, args.by, args.since, args.until)
    print(json.dumps(res, ensure_ascii=False, indent=1) if args.json else costs.text(res))
    return 0


def cmd_inbox(p: Project, args: argparse.Namespace) -> int:
    from . import notes

    if args.action == "new":
        for m in notes.inbox_new(p):
            print(notes.inbox_line(m))
    elif args.action == "read":
        notes.inbox_read(p, args.rest)
    elif args.action == "reply":
        notes.inbox_add(p, " ".join(args.rest), sender="orchestrator")
        from . import plain

        plain.warn(p, "inbox reply", " ".join(args.rest))  # the owner reads it (docs/owner.md): never blocks
    else:
        notes.inbox_add(p, " ".join(args.rest), sender="owner")
    return 0


def cmd_recurring(p: Project, args: argparse.Namespace) -> int:
    from . import notes

    if args.action == "done" or args.name:
        if args.action != "done" or not args.name:
            print("usage: foreman recurring [done NAME]", file=sys.stderr)
            return 2
        print(notes.beat(p, args.name))
        return 0
    for t in notes.recurring(p):
        parts = ", ".join(
            f"{x['label'] or 'last'} {x['last'] or 'never'}"
            + (f" (daily at {x['at']})" if x.get("at") else "")
            + (f" (new model: {', '.join(x['new'])})" if x.get("new") else "")
            + (" LATE" if x["late"] else "")
            for x in t["parts"]
        )
        print(f"{'LATE ' if t['late'] else 'ok   '}{t['name']} ({t['every']}): {parts}")
    return 0


def cmd_worktree(p: Project, args: argparse.Namespace) -> int:
    from . import identity, worktree

    if args.list:
        for repo, br, path in worktree.all_worktrees(p):
            print(f"{repo}\t{br}\t{path}")
        return 0
    if not (args.repo and args.name and args.branch):
        print("usage: foreman worktree REPO NAME BRANCH [BASE] | foreman worktree --list", file=sys.stderr)
        return 2
    try:
        wt, how, warnings = worktree.create(p, args.repo, args.name, args.branch, args.base)
    except (worktree.WorktreeError, config.ConfigError) as e:
        print(f"foreman worktree: {e}", file=sys.stderr)
        return 1
    for w in warnings:
        print(f"foreman worktree: warning: {w}", file=sys.stderr)
    print(f"{wt} ({args.branch} {how})")
    taken = identity.name_taken(p, args.name)
    if taken:
        print(f"foreman worktree: warning: no launch recorded: {taken}", file=sys.stderr)
        return 0
    token, rec = identity.new_launch(p, args.name, repo=args.repo, branch=args.branch, worktree=wt)
    print(identity.launch_line(token, rec))
    return 0


def cmd_loop(p: Project, args: argparse.Namespace) -> int:
    from . import loop, notices, orchestrator

    o = orchestrator.current(p)  # the marked session: dead agents, context
    ns = loop.notices_for_pass(p, o["session"] if o else None, mark=False)
    if args.json:
        print(json.dumps(ns, ensure_ascii=False, indent=1))
    else:
        print(
            "\n".join(f"- {'' if n['actionable'] else notices.INFO}{n['text']}" for n in ns)
            if ns
            else "nothing asks for action"
        )
    return 0


def cmd_session(args: argparse.Namespace) -> int:
    from . import versions

    if args.all or args.behind or args.json:
        if not args.all or args.session_id or args.name:
            print("usage: foreman session --all [--behind] [--json]", file=sys.stderr)
            return 2
        return versions.sessions_command(args.behind, args.json)
    from . import grants, hooks_mod, orchestrator, renew

    p = need_project(args)
    before = None
    if args.session_id:
        before = orchestrator.current(p)
        try:
            o = orchestrator.mark(p, args.session_id, args.name, os.environ.get("CLAUDE_CODE_SESSION_ID"))
        except orchestrator.Refused as e:
            print(
                f"foreman session: session {args.session_id} is marked by another Claude Code process that "
                f"still runs (pid {e.args[0]}): a copy of the session (`--resume`, `--session-id`) does not "
                "take its mark. Nothing changed; once that process ends, mark it again.",
                file=sys.stderr,
            )
            return 1
    else:
        o = orchestrator.current(p)
    if not o:
        print(f"{p.name}: no orchestrator now")
        return 1
    print(f"{p.name}: orchestrated by {o['name'] or '(no name)'} (session {o['session']}, since {o['since']})")
    if before and before["session"] != o["session"]:  # a new session took the mark: say whom it relieves
        dead = orchestrator.liveness(p, before["session"])["state"] == "gone"
        print(
            f"  relieves {before['name'] or '(no name)'} (session {before['session']}) since {before['since']}"
            + (" (not alive)" if dead else "")
        )
    line = orchestrator.health_line(p, orchestrator.health(p, o))
    if line:
        print("  " + line)
    if grants.patterns(p):
        print("  " + orchestrator.process_line(p))
    print("  " + versions.session_version_line(o["session"]))
    print("  context " + hooks_mod.context_text(hooks_mod.read_measure(p, o["session"])))
    print("  " + renew.status_line(p, o["session"]))
    return 0


def cmd_renew(p: Project, args: argparse.Namespace) -> int:
    from . import identity, renew

    sid = (os.environ.get("CLAUDE_CODE_SESSION_ID") or "").strip() or None
    aid, _ = identity.recorded_caller("renew", [args.global_project, *args.argv])
    if aid is not None:  # a subagent shares the session id: only the orchestrator's own thread renews it
        print(
            "foreman renew: run by an agent of the orchestrator's session"
            + (" (or one of several calls at once)" if aid == "ambiguous" else "")
            + ": only the orchestrator itself renews its session",
            file=sys.stderr,
        )
        return 1
    try:
        rec = renew.request(p, sid, note=args.note, now=args.now)
    except renew.RenewError as e:
        print(f"foreman renew: {e}", file=sys.stderr)
        return 1
    print(
        f"{p.name}: renewal asked for session {rec['session']}"
        + (" (--now)" if rec["now"] else "")
        + ": foreman's hooks module compacts this session at its next idle minute (after this turn"
        + ("" if rec["now"] else ", once no agent of the session is live")
        + ") and gives it back foreman's pack; nothing else to do."
    )
    return 0


def cmd_agents(p: Project, args: argparse.Namespace) -> int:
    from . import loop

    if args.name:
        from . import identity

        try:
            fields = {k: getattr(args, k) for k in ("issue", "sub", "repo", "branch", "worktree")}
            aid = identity.name_hand(p, args.name[0], args.name[1], **fields)
        except identity.IdentityError as e:
            print(f"foreman agents: {e}", file=sys.stderr)
            return 2
        print(f"named: {aid} is {args.name[1]}")
        return 0
    if args.misled:
        from . import delivery

        if args.since and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.since):
            print(f"foreman agents: --since {args.since}: a day as YYYY-MM-DD", file=sys.stderr)
            return 2
        res = delivery.misled(p, since=args.since, type_=args.agent_type, model=args.model)
        print(json.dumps(res, ensure_ascii=False, indent=1) if args.json else delivery.misled_text(res))
        return 0
    if args.cost:
        return agents_cost(p, args)
    if args.since or args.agent_type or args.model or args.until or args.backfill:
        print(
            "foreman agents: --since, --type and --model go with --misled; --until, --by and --backfill with --cost",
            file=sys.stderr,
        )
        return 2
    if args.stop:
        hits = loop.stop_agent(p, args.stop)
        print(f"stopped: {', '.join(hits)}" if hits else f"no agent {args.stop}")
        return 0 if hits else 1
    print(loop.agents_json(p) if args.json else loop.agents_compact(p))
    return 0


# Each command of build_parser() but `guard` (main() keeps its branch) -> its function. Those that may run without a
# project take the parsed arguments (with `global_project` and `argv`) and resolve it themselves when they need it;
# the others, (project, arguments).
NO_PROJECT_COMMANDS = {
    "selftest": cmd_selftest,
    "docs": cmd_docs,
    "panel": cmd_panel,
    "config": cmd_config,
    "plain": cmd_plain,
    "init": lambda args: import_module(".init", __package__).run(args),
    "backlog": cmd_backlog,
    "session": cmd_session,
}
COMMANDS = {
    "catchup": module_command("catchup"),
    "start": module_command("start"),
    "integrate": module_command("integrate"),
    "holdout": module_command("holdout", "run_command"),
    "permissions": module_command("permissions"),
    "note": cmd_note,
    "progress": cmd_progress,
    "inbox": cmd_inbox,
    "recurring": cmd_recurring,
    "worktree": cmd_worktree,
    "launch": launch_command,
    "loop": cmd_loop,
    "renew": cmd_renew,
    "agents": cmd_agents,
    "check": lambda p, args: check(p, args.run_checks),
}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    project_name = None
    if argv[:1] == ["--project"] and len(argv) > 1:
        project_name, argv = argv[1], argv[2:]
    if acts(argv):
        try:
            from . import versions

            line = versions.orphan_line(once=True)
        except Exception:  # noqa: BLE001 (never block a command over this line)
            line = None
        if line:
            print(line, file=sys.stderr)
    if argv == ["--version"]:
        print(f"foreman {__version__}")
        return 0
    if argv[:2] == ["hook", "mod"]:
        from . import hooks_mod

        return hooks_mod.run(argv[2:])
    if argv[:1] == ["hook"]:
        from . import hooks

        return hooks.run(argv[1] if len(argv) > 1 else "")
    if argv[:2] == ["panel", "service"]:
        from . import install

        return install.service_main(argv[2:])
    if argv[:1] == ["time-limit"]:
        os.execvp("bash", ["bash", str(HERE / "sh/time_limit.sh"), *argv[1:]])
    if argv[:1] == ["hung-shells"]:
        return subprocess.run(["bash", str(HERE / "sh/hung_shells.sh"), *argv[1:]], check=False).returncode
    if argv[:1] == ["lock"]:
        return cmd_lock(project_name, argv)
    if argv[:1] == ["wait"]:
        return cmd_wait(project_name, argv)

    lazy = ("backlog", "integrate", "holdout", "init", "start", "catchup", "permissions")
    ap = build_parser(argv[0] if argv[:1] and argv[0] in lazy else None)
    args = util.no_abbrev(ap).parse_args(argv)
    args.global_project, args.argv = project_name, argv
    c = args.cmd
    if c in NO_PROJECT_COMMANDS:
        return NO_PROJECT_COMMANDS[c](args)
    p = need_project(args)
    if c in COMMANDS:
        return COMMANDS[c](p, args)
    if c == "guard":
        from . import guard

        if args.table:
            return guard_table(p, Path(args.table))
        if (args.command is None) == (args.file is None):
            print('usage: foreman guard "command" | guard --file PATH | guard --table FILE', file=sys.stderr)
            return 2
        ctx = guard.as_role(args.role, os.path.abspath(os.path.expanduser(args.cwd or os.getcwd())))
        if args.file is not None:
            v = guard.check_file(p, args.file, ctx)
            print(guard.message(p, v) if v else "allowed")
            return 2 if v else 0
        v = guard.check(p, args.command, ctx)
        if v:
            print(guard.message(p, v))
            return 2
        from . import pause

        held = pause.guard_check(p, args.command)
        if held:
            print(pause.guard_message(p, held))
            return 2
        print("allowed" + (f" (orchestrator only: «{ctx['passed']}»)" if ctx and "passed" in ctx else ""))
        return 0
    return 2
