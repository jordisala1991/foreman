"""fm's command line: the arguments of each command (the handlers are in cli.py, which sets them per command)."""

import argparse
from collections.abc import Callable

from . import backlog

Command = Callable[[str, str], argparse.ArgumentParser]


def _filing(c: Command) -> None:
    s = c("init", "make a project home and register it")
    s.add_argument("--repo", required=True)
    s.add_argument("--prefix", default="FM")
    s = c("add", "file an issue (in triage until let in; --ready lets it in if it has --criteria)")
    s.add_argument("title")
    s.add_argument("--detail")
    s.add_argument("--claim")
    s.add_argument("--criteria")
    s.add_argument("--ready", action="store_true")
    s.add_argument("--triage", action="store_true")
    s.add_argument("--priority", default="none", choices=backlog.PRIORITIES)
    s = c("list", "the backlog and the open questions")
    s.add_argument("--all", action="store_true")
    s = c("show", "one issue, as stored")
    s.add_argument("id")


def _agent_side(c: Command) -> None:
    s = c("ask", "(agent) ask the owner; then end your turn")
    s.add_argument("text")
    s.add_argument("--action", help="the guard action you ask permission for")
    s = c("done", "(agent) the task is ready to integrate")
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
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--plan", metavar="PATH", help="the written plan (file, not empty)")
    g.add_argument("--no-plan", metavar="WHY", help="no plan, because the task is trivial")
    s.add_argument("--misled", default="")
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
    s = c("accept", "integrate a queued issue")
    s.add_argument("id")
    s = c("reject", "send a queued issue back to its agent")
    s.add_argument("id")
    s.add_argument("note")
    s = c("review", "review a queued issue again from scratch, as round 1")
    s.add_argument("id")
    s = c("go", "let a triage issue in")
    s.add_argument("id")
    s = c("cancel", "cancel an issue (stops its agent)")
    s.add_argument("id")
    s.add_argument("why", nargs="?")
    s = c("unblock", "clear an issue's block")
    s.add_argument("id")
    s.add_argument("--relaunch", action="store_true", help="and launch a new agent for it")


def _operations(c: Command) -> None:
    s = c("pause", "stop launching new agents (--off to resume)")
    s.add_argument("--off", action="store_true")
    s = c("loop", "run the loop (launchd keeps it alive)")
    s.add_argument("--once", action="store_true")
    s = c("health", "the health checks, from outside the loop")
    s = c("metrics", "cost, time, rework, questions, what misled agents")
    s = c("lock", 'use a shared resource: fm lock NAME run "what" command…, or fm lock NAME status')
    s.add_argument("name")
    s.add_argument("action", choices=("run", "status"))
    s.add_argument("what", nargs="?", default="")
    s.add_argument("command", nargs=argparse.REMAINDER)
    c("guard", "(hook) the agents' PreToolUse guard")
    s = c("panel", "serve the panel")
    s.add_argument("--host", default="127.0.0.1", help="0.0.0.0 to reach it from other devices (the key is required)")
    s.add_argument("--port", type=int, default=7788)
    s = c("launchd", "print the two launchd jobs (loop and panel) that start at login and restart if they die")
    s.add_argument("--dir", help="write dev.fm.loop.plist and dev.fm.panel.plist here (e.g. ~/Library/LaunchAgents) instead of printing")
    s.add_argument("--panel-host", default="127.0.0.1")


def parser(description: str | None) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="fm", description=description)
    ap.add_argument("--home", help="the project's home (else FM_HOME, else the folder with fm.toml)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def c(name: str, help_: str) -> argparse.ArgumentParser:
        return sub.add_parser(name, help=help_)

    for group in (_filing, _agent_side, _owner_side, _operations):
        group(c)
    return ap
