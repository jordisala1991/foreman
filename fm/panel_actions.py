"""The panel's write side: the owner's actions. They call what the CLI calls (fm.cli's functions, fm.integrate.accept), so the panel and `fm`
cannot disagree. `act` routes a POST path to one small handler."""

import argparse
import contextlib
import io
import json
import threading
from collections.abc import Callable
from typing import Any

from . import backlog, cli, integrate, providers
from .config import Project
from .panel_views import Obj, project

Query = dict[str, list[str]]
OK: Obj = {"ok": True}


class Refused(Exception):
    pass


_cli_lock = threading.Lock()  # one at a time: what a command prints for the terminal is dropped


def run_cli(fn: Callable[[argparse.Namespace], Any], p: Project, **kw: Any) -> None:
    """A command of fm.cli as `fm --home HOME ...` would run it; its sys.exit(message) becomes a refusal."""
    try:
        with _cli_lock, contextlib.redirect_stdout(io.StringIO()):
            fn(argparse.Namespace(home=str(p.home), **kw))
    except SystemExit as e:
        raise Refused(str(e.code)) from None


def open_permission(p: Project, qid: str) -> None:
    q = next((q for q in backlog.load(p)["decisions"] if q["id"] == qid), None)
    if not q or q.get("kind") != "permission":
        raise Refused(f"{qid} is not an open permission request")


def add(p: Project, body: str) -> Obj:
    f = json.loads(body or "{}")
    if not str(f.get("title", "")).strip():
        raise Refused("a task needs a title")
    crit = str(f.get("criteria", "")).strip()
    new = backlog.add(
        p,
        f["title"].strip(),
        detail=str(f.get("detail", "")).strip(),
        status="backlog",
        criteria=crit,
        claim=str(f.get("claim", "")).strip(),
    )
    return {"id": new}


def answer(p: Project, iid: str, text: str, q: Query) -> Obj:
    if not text:
        raise Refused("the answer is empty")
    open_q = next((x for x in backlog.load(p)["decisions"] if x["id"] == iid), None)
    if open_q and open_q.get("kind") == "permission":
        raise Refused(f"{iid} is a permission request: allow or deny it")
    run_cli(cli.cmd_answer, p, qid=iid, text=text)
    return OK


def permission(allowed: bool) -> Callable[[Project, str, str, Query], Obj]:
    def handle(p: Project, iid: str, text: str, q: Query) -> Obj:
        open_permission(p, iid)
        run_cli(lambda a: cli.cmd_allow(a, allowed), p, qid=iid, text=text or None)
        return OK

    return handle


def accept(p: Project, iid: str, text: str, q: Query) -> Obj:
    try:
        return {"accepted": integrate.accept(p, iid, by="owner (panel)")}
    except (integrate.IntegrateError, providers.ConfigError) as e:
        raise Refused(str(e)) from None


def reject(p: Project, iid: str, text: str, q: Query) -> Obj:
    if not text:
        raise Refused("say what to change: the note goes back to its agent")
    run_cli(cli.cmd_reject, p, id=iid, note=text)
    return OK


def go(p: Project, iid: str, text: str, q: Query) -> Obj:
    if backlog.get(backlog.load(p), iid)["status"] != "triage":
        raise Refused(f"{iid} is not in triage")
    run_cli(cli.cmd_go, p, id=iid)
    return OK


def cancel(p: Project, iid: str, text: str, q: Query) -> Obj:
    run_cli(cli.cmd_cancel, p, id=iid, why=text or None)
    return OK


def unblock(p: Project, iid: str, text: str, q: Query) -> Obj:
    run_cli(cli.cmd_unblock, p, id=iid, relaunch=q.get("relaunch", [""])[0] == "1")
    return OK


def pause(p: Project, iid: str, text: str, q: Query) -> Obj:
    run_cli(cli.cmd_pause, p, off=q.get("on", ["1"])[0] == "0")
    return OK


# the actions on one task or question (they need `id=`), by path
BY_ID: dict[str, Callable[[Project, str, str, Query], Obj]] = {
    "/api/answer": answer,
    "/api/allow": permission(True),
    "/api/deny": permission(False),
    "/api/accept": accept,
    "/api/reject": reject,
    "/api/go": go,
    "/api/cancel": cancel,
    "/api/unblock": unblock,
}


def act(path: str, q: Query, body: str) -> Obj:
    p = project(q)
    if path == "/api/add":
        return add(p, body)
    if path == "/api/pause":
        return pause(p, "", "", q)
    iid = q.get("id", [""])[0]
    if not iid:
        raise KeyError("id is missing")
    handler = BY_ID.get(path)
    if handler is None:
        raise LookupError(path)
    return handler(p, iid, body.strip(), q)
