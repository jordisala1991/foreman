"""fm's command line: the arguments of each command (the handlers are in main.py, which sets them per command)."""

import argparse
from collections.abc import Callable

from ...core.project import config
from ...core.tasks import backlog, tasktypes

Command = Callable[[str, str], argparse.ArgumentParser]


def _whole(text: str) -> int:
    if not text.isdigit() or int(text) < 1:
        raise argparse.ArgumentTypeError("a whole number of 1 or more")
    return int(text)


def _model_effort(s: argparse.ArgumentParser) -> None:
    s.add_argument("--model", choices=config.MODELS, help="the model of the task's responsible agent (default: the project's)")
    s.add_argument("--effort", choices=config.EFFORTS, help="the effort of the task's responsible agent (default: the project's)")


def _type(s: argparse.ArgumentParser) -> None:
    s.add_argument("--type", choices=tasktypes.TYPES, help="change (default), bug, mechanical or definition: the instructions of the task's agents")


def _repo(s: argparse.ArgumentParser) -> None:
    s.add_argument("--repo", metavar="KEY", help="the task's repo: a [repos.K] key (projects with several repos)")


def _json(s: argparse.ArgumentParser, required: bool = False) -> None:
    s.add_argument("--json", action="store_true", required=required, help="print JSON: a contract for other programs (DESIGN.md «The JSON reads»)")


def _filing(c: Command) -> None:
    s = c("init", "make a project home and register it")
    s.add_argument("--repo", required=True)
    s.add_argument("--prefix", default="FM")
    s = c("add", "file a task (in triage until let in; --ready lets it in if it has --criteria)")
    s.add_argument("title")
    s.add_argument("--detail")
    s.add_argument("--goal")
    s.add_argument("--criteria")
    s.add_argument("--parent", help="file it as a subtask of this task (the owner, or the parent's own agent; before it starts)")
    blocked_by(s)
    s.add_argument("--ready", action="store_true")
    s.add_argument("--triage", action="store_true")
    s.add_argument("--priority", default="none", choices=backlog.PRIORITIES)
    _model_effort(s)
    _type(s)
    _repo(s)
    s = c("list", "the backlog and the open questions")
    s.add_argument("--all", action="store_true")
    _json(s)
    s = c("show", "one task, as stored")
    s.add_argument("id")


def _agent_side(c: Command) -> None:
    s = c("ask", "(agent) ask the owner; then end your turn")
    s.add_argument("text")
    s.add_argument("--action", help="the guard action you ask permission for")
    s = c("done", "(responsible agent) deliver the task: it goes to review")
    s.add_argument("--summary", required=True)
    s.add_argument("--report", help="your report file")
    s.add_argument("--misled", default="", help="what was wrong or unclear in what you were given, or none")
    s = c("verdict", "(reviewer) the open findings of your review")
    s.add_argument("--open", type=int, required=True, help="findings to fix")
    s.add_argument("--file", required=True)
    s.add_argument("--tip", default="", help="the tip you reviewed (the brief gives it)")
    s.add_argument("--misled", default="")
    s = c("ready", "(planning agent) the plan is written")
    s.add_argument("--detail")
    _model_effort(s)
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--plan", metavar="PATH", help="the written plan (file, not empty)")
    g.add_argument("--no-plan", metavar="WHY", help="no plan, because the task is trivial")
    s.add_argument("--misled", default="")
    s = c("diagnose", "(mechanic) your diagnosis of the task")
    s.add_argument("--file", required=True, help="the diagnosis (markdown with the required ## sections)")
    s = c("block", "(agent) it cannot go on without the owner")
    s.add_argument("text")


def _owner_side(c: Command) -> None:
    s = c("answer", "answer an agent's question")
    s.add_argument("qid")
    s.add_argument("text")
    for name in ("allow", "deny"):
        s = c(name, f"{name} a guarded action once")
        s.add_argument("qid")
        s.add_argument("text", nargs="?")
    s = c("accept", "accept a task in review: it may merge")
    s.add_argument("id")
    s = c("reject", "reject a task in review: return it to its agent with a note")
    s.add_argument("id")
    s.add_argument("note")
    s = c("review", "review a task in review again from scratch, as round 1")
    s.add_argument("id")
    s = c("go", "let a triage task in")
    s.add_argument("id")
    s = c("edit", "change a task before its agent starts (triage, backlog, ready)")
    s.add_argument("id")
    s.add_argument("--title")
    s.add_argument("--goal")
    s.add_argument("--detail")
    s.add_argument("--criteria", help="one criterion per line, as in foreman add")
    s.add_argument("--max-steps", type=_whole, metavar="N", help="a chain parent's cap of steps (renew it; not below the steps filed)")
    blocked_by(s)
    _model_effort(s)
    _type(s)
    _repo(s)
    s = c("cancel", "cancel a task (stops its agent)")
    s.add_argument("id")
    s.add_argument("why", nargs="?")
    s = c("unblock", "clear a task's block")
    s.add_argument("id")
    s.add_argument("--relaunch", action="store_true", help="and launch a new agent for it")
    s = c("relaunch", "restart a task's live agent on the current foreman, keeping its conversation")
    s.add_argument("id")


def _operations(c: Command) -> None:
    s = c("pause", "stop launching new agents (--off to resume)")
    s.add_argument("--off", action="store_true")
    s = c("loop", "run the loop (launchd keeps it alive)")
    s.add_argument("--once", action="store_true")
    s = c("watch", "print one line each time something starts needing the owner (--once: exit after the first)")
    s.add_argument("--once", action="store_true")
    s = c("health", "the health checks, from outside the loop")
    _json(s)
    _json(c("needs", "what waits for the owner now, as JSON"), required=True)
    _json(c("agents", "the agents the loop has live now, as JSON"), required=True)
    s = c("runs", "every agent session the loop started, as JSON")
    _json(s, required=True)
    s.add_argument("--task", metavar="ID", help="only this task's runs")
    s.add_argument("--active", action="store_true", help="only the runs that have not ended")
    s = c("events", "a task's events in time order, as JSON")
    _json(s, required=True)
    s.add_argument("--task", metavar="ID", required=True)
    s = c("reviews", "a task's review rounds with their text, as JSON")
    _json(s, required=True)
    s.add_argument("--task", metavar="ID", required=True)
    s = c(
        "backlog",
        "the project's Board on GitHub: `setup` makes its fields and the label and deletes its workflows;"
        " `cutover --repo OWNER/NAME --project N` moves the open tasks of the local file there",
    )
    s.add_argument("action", choices=("setup", "cutover"))
    s.add_argument("--repo", help="cutover: the repo whose issues become the tasks (owner/name)")
    s.add_argument("--project", type=int, dest="board", metavar="N", help="cutover: the Board's number (not the global --project NAME)")
    s.add_argument("--owner", help="cutover: the Board owner's login (default: the repo's owner)")
    s.add_argument("--dry-run", action="store_true", help="cutover: print what would be filed and write nothing")
    s = c("metrics", "cost, time, rework, questions, what misled agents")
    s = c("lock", 'use a shared resource: foreman lock NAME run "what" command…, or foreman lock NAME status')
    s.add_argument("name")
    s.add_argument("action", choices=("run", "status"))
    s.add_argument("what", nargs="?", default="")
    s.add_argument("command", nargs=argparse.REMAINDER)
    c("guard", "(hook) the agents' PreToolUse guard")
    c("project", "this project's values: name, prefix, home, repos, main, slots")
    _json(c("projects", "every registered project, as JSON"), required=True)
    c("session-context", "(hook) Claude Code's SessionStart: the project's instructions for a session in one of its folders")
    s = c("panel", "serve the panel")
    s.add_argument("--host", default="127.0.0.1", help="0.0.0.0 to reach it from other devices (the key is required)")
    s.add_argument("--port", type=int, default=7788)
    s = c("launchd", "print the two launchd jobs (loop and panel) that start at login and restart if they die")
    s.add_argument("--dir", help="write dev.foreman.loop.plist and dev.foreman.panel.plist here (e.g. ~/Library/LaunchAgents) instead of printing")
    s.add_argument("--panel-host", default="127.0.0.1")


def blocked_by(s: argparse.ArgumentParser) -> None:
    s.add_argument(
        "--blocked-by", nargs="+", action="extend", metavar="FN-n", help="wait for these tasks to be done; on edit it replaces the list, `none` clears it"
    )


def parser(description: str | None) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="foreman", description=description)
    ap.add_argument("--project", metavar="NAME", help="the project's name (else FOREMAN_PROJECT, else the project that owns the current folder)")
    ap.add_argument("--home", help="the project's home (else FOREMAN_HOME)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def c(name: str, help_: str) -> argparse.ArgumentParser:
        return sub.add_parser(name, help=help_)

    for group in (_filing, _agent_side, _owner_side, _operations):
        group(c)
    return ap
