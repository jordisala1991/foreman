"""`fm`: the one command. Agents use `ask`, `note`, `done`, `ready`, `block`, `add` (their issue comes from FM_ISSUE);
the owner and the panel use the rest; launchd runs `fm loop`; Claude Code runs `fm guard` as the agents' hook."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import backlog, guard, health, integrate, lock, loop, metrics, sessions, util
from .config import Project, register


def home(args: argparse.Namespace) -> Project:
    h = args.home or os.environ.get("FM_HOME")
    if not h:
        here = Path.cwd()
        h = next((str(d) for d in (here, *here.parents) if (d / "fm.toml").exists()), None)
    if not h:
        sys.exit("fm: no project: pass --home, set FM_HOME, or run inside a folder with fm.toml")
    return Project.load(h)


def mine(args: argparse.Namespace) -> str:
    iid = getattr(args, "issue", None) or os.environ.get("FM_ISSUE")
    if not iid:
        sys.exit("fm: which issue? (--issue, or FM_ISSUE in an agent's session)")
    return iid


def cmd_init(a: argparse.Namespace) -> None:
    h = Path(a.home or ".").resolve()
    h.mkdir(parents=True, exist_ok=True)
    if not (h / "fm.toml").exists():
        (h / "fm.toml").write_text(
            f'[project]\nname = "{h.name}"\nprefix = "{a.prefix}"\nrepo = "{a.repo}"\nmain = "main"\n\n'
            '[agents]\nslots = 2\nmodel = "sonnet"\ndefine_model = "sonnet"\n\n'
            '[integrate]\nautonomy = "manual"  # manual | low | medium\ntest = ""\n', encoding="utf-8")
    p = Project.load(h)
    if not p.backlog_file.exists():
        util.write_json(p.backlog_file, backlog.EMPTY)
    register(h)
    print(f"project {p.name} at {h} (registered)")


def cmd_add(a: argparse.Namespace) -> None:
    p = home(a)
    status = "ready" if a.ready else "triage" if a.triage else "backlog"
    iid = backlog.add(p, a.title, detail=a.detail or "", status=status, criterion=a.criterion or "",
                      claim=a.claim or "", priority=a.priority)
    print(iid)


def cmd_list(a: argparse.Namespace) -> None:
    p = home(a)
    d = backlog.load(p)
    for i in backlog.issues(d):
        if i["status"] in backlog.CLOSED and not a.all:
            continue
        ag = (i.get("agent") or {}).get("name", "")
        print(f"{i['id']:8} {i['status']:12} {ag:14} {i['title']}" + (f"  [blocked: {i['blocked']}]" if i.get(
            "blocked") else ""))
    for q in backlog.open_questions(d):
        print(f"? {q['id']} ({q.get('agent')}): {q['text']}")


def cmd_show(a: argparse.Namespace) -> None:
    p = home(a)
    print(json.dumps(backlog.get(backlog.load(p), a.id), indent=2, ensure_ascii=False))


# --- the agent's side ---------------------------------------------------------------------------------------------

def cmd_ask(a: argparse.Namespace) -> None:
    p = home(a)
    kind = "permission" if a.action else "question"
    qid = backlog.ask(p, mine(a), os.environ.get("FM_AGENT", "?"), a.text, kind, a.action or "")
    print(f"{qid} is with the owner. End your turn now: his answer will come back to this conversation.")


def cmd_note(a: argparse.Namespace) -> None:
    p = home(a)
    backlog.event(p, mine(a), f"note: {a.text}")


def cmd_done(a: argparse.Namespace) -> None:
    p = home(a)
    iid = mine(a)
    report = {"summary": a.summary, "file": a.report or "", "confused": a.misled or "", "at": util.now()}
    backlog.set_status(p, iid, "in review", a.summary, report=report, blocked="")
    backlog.lesson(p, iid, os.environ.get("FM_AGENT", "agent"), a.misled)
    print(f"{iid} is in the integration queue: an independent reviewer reads it, then the owner. End your turn.")


def cmd_verdict(a: argparse.Namespace) -> None:
    p = home(a)
    iid = mine(a)
    with backlog.edit(p) as d:
        i = backlog.get(d, iid)
        r = i.get("reviewer") or {}
        v = {"round": r.get("round", 1), "tip": r.get("tip", ""), "high": a.high, "medium": a.medium,
             "low": a.low, "file": a.file, "at": util.now()}
        i["review"] = v
        i["reviews"] = [*i.get("reviews", []), v]
        i["report"] = {**(i.get("report") or {}),
                       "review": "clean" if a.high + a.medium == 0 else f"open: H {a.high}, M {a.medium}"}
    backlog.lesson(p, iid, os.environ.get("FM_AGENT", "reviewer"), a.misled)
    backlog.event(p, iid, f"review round {v['round']}: H {a.high}, M {a.medium}, L {a.low} ({a.file})")
    print("Verdict recorded. End your turn.")


def cmd_ready(a: argparse.Namespace) -> None:
    p = home(a)
    iid = mine(a)
    with backlog.edit(p) as d:
        i = backlog.get(d, iid)
        i["acceptance"] = {**(i.get("acceptance") or {}), "claim": a.claim, "criterion": a.criterion}
        if a.detail:
            i["detail"] = (i.get("detail", "") + "\n\n" + a.detail).strip()
        i["status"], i["blocked"] = "ready", ""
        i["defined_by"] = (i.get("agent") or {}).get("name")
    backlog.event(p, iid, f"→ ready: {a.criterion}")
    backlog.lesson(p, iid, os.environ.get("FM_AGENT", "definer"), a.misled)
    print(f"{iid} is ready; the loop will launch its responsible agent. End your turn.")


def cmd_block(a: argparse.Namespace) -> None:
    p = home(a)
    backlog.update(p, mine(a), blocked=a.text)
    backlog.event(p, mine(a), f"blocked by its agent: {a.text}")
    print("Blocked: the owner sees it in the panel. End your turn.")


# --- the owner's side ---------------------------------------------------------------------------------------------

def cmd_answer(a: argparse.Namespace) -> None:
    p = home(a)
    q = backlog.answer(p, a.qid, a.text)
    print(f"answered {q['id']}; the loop gives it to {q.get('agent')}")


def cmd_allow(a: argparse.Namespace, allowed: bool = True) -> None:
    p = home(a)
    q = next((q for q in backlog.load(p)["decisions"] if q["id"] == a.qid), None)
    if not q or q.get("kind") != "permission":
        sys.exit(f"fm: {a.qid} is not an open permission request")
    if allowed:
        guard.grant(p, q["issue"], q["action"])
    backlog.answer(p, a.qid, a.text or ("allowed once" if allowed else "not allowed"))
    with backlog.edit(p) as d:
        next(x for x in d["decisions_resolved"] if x["id"] == a.qid)["allowed"] = allowed


def cmd_accept(a: argparse.Namespace) -> None:
    p = home(a)
    try:
        print(integrate.accept(p, a.id))
    except integrate.IntegrateError as e:
        sys.exit(f"fm: {e}")


def cmd_reject(a: argparse.Namespace) -> None:
    p = home(a)
    if backlog.get(backlog.load(p), a.id)["status"] != "in review":
        sys.exit(f"fm: {a.id} is not in the integration queue")
    loop.send_back(p, a.id, "rejection", a.note)


def cmd_go(a: argparse.Namespace) -> None:
    p = home(a)
    i = backlog.get(backlog.load(p), a.id)
    status = "ready" if (i.get("acceptance") or {}).get("criterion") else "backlog"
    backlog.set_status(p, a.id, status, "let in by the owner", blocked="")


def cmd_cancel(a: argparse.Namespace) -> None:
    integrate.close(home(a), a.id, "canceled", a.why or "by the owner")


def cmd_unblock(a: argparse.Namespace) -> None:
    p = home(a)
    i = backlog.update(p, a.id, blocked="")
    if i["status"] in backlog.RUNNING and a.relaunch:
        backlog.set_status(p, a.id, "backlog" if i["status"] == "refining" else "ready", "relaunch")


def cmd_pause(a: argparse.Namespace) -> None:
    p = home(a)
    f = p.state / "paused"
    if a.off:
        f.unlink(missing_ok=True)
    else:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(util.now())
    print("launching paused" if not a.off else "launching resumed")


def cmd_loop(a: argparse.Namespace) -> None:
    loop.forever([a.home] if a.home else None, once=a.once)


def cmd_health(a: argparse.Namespace) -> None:
    p = home(a)
    rows = sessions.listing(p)
    for f in health.findings(p, rows, outside=True):
        print(f"! {f['title']}: {f['detail'].splitlines()[0] if f['detail'] else ''}")
    if a.file:
        print("filed:", " ".join(health.run(p, rows, outside=True)) or "nothing new")


def cmd_metrics(a: argparse.Namespace) -> None:
    rows = metrics.per_issue(home(a))
    print(json.dumps(rows, indent=2, ensure_ascii=False) if a.json else metrics.text(rows))


def cmd_check(a: argparse.Namespace) -> None:
    print(json.dumps(integrate.check(home(a), a.id), indent=2))


def cmd_guard(a: argparse.Namespace) -> None:
    out = guard.hook(sys.stdin.read())
    if out:
        print(out)


def cmd_lock(a: argparse.Namespace) -> None:
    p = home(a)
    res = lock.declared(p.cfg)
    if a.name not in res:
        sys.exit(f"fm: unknown resource {a.name!r} (declared: {', '.join(res) or 'none'}; [resources.NAME] in fm.toml)")
    r = res[a.name]
    if a.action == "status":
        s = r.status()
        for slot, h in s["held"].items():
            print(f"{slot}: {h['who']} ({h['issue'] or '-'}): {h['what']} since {h['since']}")
        for t in s["queue"]:
            print(f"queued: {t['who']} ({t['issue'] or '-'}): {t['what']} since {t['since']}")
        if not s["held"] and not s["queue"]:
            print("free")
        return
    if not a.command:
        sys.exit('fm: fm lock NAME run "what" command…')
    who = os.environ.get("FM_AGENT") or "owner"
    sys.exit(r.run(who, a.what, a.command, os.environ.get("FM_ISSUE", ""), say=lambda m: print(m, file=sys.stderr)))


def cmd_panel(a: argparse.Namespace) -> None:
    from . import panel

    panel.serve(a.port, a.host)


def cmd_launchd(a: argparse.Namespace) -> None:
    print(loop.launchd_plist(str(Path(__file__).resolve().parent.parent / "bin/fm")))


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="fm", description=__doc__)
    ap.add_argument("--home", help="the project's home (else FM_HOME, else the folder with fm.toml)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def c(name: str, fn, help_: str):  # noqa: ANN001, ANN202
        s = sub.add_parser(name, help=help_)
        s.set_defaults(fn=fn)
        return s

    s = c("init", cmd_init, "make a project home and register it")
    s.add_argument("--repo", required=True)
    s.add_argument("--prefix", default="FM")
    s = c("add", cmd_add, "file an issue (to define, unless --ready)")
    s.add_argument("title")
    s.add_argument("--detail")
    s.add_argument("--claim")
    s.add_argument("--criterion")
    s.add_argument("--ready", action="store_true")
    s.add_argument("--triage", action="store_true")
    s.add_argument("--priority", default="none", choices=backlog.PRIORITIES)
    s = c("list", cmd_list, "the backlog and the open questions")
    s.add_argument("--all", action="store_true")
    s = c("show", cmd_show, "one issue, as stored")
    s.add_argument("id")
    s = c("ask", cmd_ask, "(agent) ask the owner; then end your turn")
    s.add_argument("text")
    s.add_argument("--action", help="the guard action you ask permission for")
    s.add_argument("--issue")
    s = c("note", cmd_note, "(agent) a progress note")
    s.add_argument("text")
    s.add_argument("--issue")
    s = c("done", cmd_done, "(agent) the task is ready to integrate")
    s.add_argument("--summary", required=True)
    s.add_argument("--report", help="your report file")
    s.add_argument("--misled", default="", help="what was wrong or unclear in what you were given, or none")
    s.add_argument("--issue")
    s = c("verdict", cmd_verdict, "(reviewer) the open findings of your review")
    s.add_argument("--high", type=int, required=True)
    s.add_argument("--medium", type=int, required=True)
    s.add_argument("--low", type=int, default=0)
    s.add_argument("--file", required=True)
    s.add_argument("--misled", default="")
    s.add_argument("--issue")
    s = c("ready", cmd_ready, "(defining agent) the task is defined")
    s.add_argument("--claim", required=True)
    s.add_argument("--criterion", required=True)
    s.add_argument("--detail")
    s.add_argument("--misled", default="")
    s.add_argument("--issue")
    s = c("block", cmd_block, "(agent) it cannot go on without the owner")
    s.add_argument("text")
    s.add_argument("--issue")
    s = c("answer", cmd_answer, "answer an agent's question")
    s.add_argument("qid")
    s.add_argument("text")
    for name, allowed in (("allow", True), ("deny", False)):
        s = c(name, lambda a, al=allowed: cmd_allow(a, al), f"{name} a guarded action once")
        s.add_argument("qid")
        s.add_argument("text", nargs="?")
    s = c("accept", cmd_accept, "integrate a queued issue")
    s.add_argument("id")
    s = c("reject", cmd_reject, "send a queued issue back to its agent")
    s.add_argument("id")
    s.add_argument("note")
    s = c("go", cmd_go, "let a triage issue in")
    s.add_argument("id")
    s = c("cancel", cmd_cancel, "cancel an issue (stops its agent)")
    s.add_argument("id")
    s.add_argument("why", nargs="?")
    s = c("unblock", cmd_unblock, "clear an issue's block")
    s.add_argument("id")
    s.add_argument("--relaunch", action="store_true", help="and launch a new agent for it")
    s = c("pause", cmd_pause, "stop launching new agents (--off to resume)")
    s.add_argument("--off", action="store_true")
    s = c("loop", cmd_loop, "run the loop (launchd keeps it alive)")
    s.add_argument("--once", action="store_true")
    s = c("health", cmd_health, "the health checks, from outside the loop")
    s.add_argument("--file", action="store_true", help="file the new findings as triage issues")
    s = c("metrics", cmd_metrics, "cost, time, rework, questions, what misled agents")
    s.add_argument("--json", action="store_true")
    s = c("check", cmd_check, "check a queued branch now")
    s.add_argument("id")
    s = c("lock", cmd_lock, "use a shared resource: fm lock NAME run \"what\" command…, or fm lock NAME status")
    s.add_argument("name")
    s.add_argument("action", choices=("run", "status"))
    s.add_argument("what", nargs="?", default="")
    s.add_argument("command", nargs=argparse.REMAINDER)
    c("guard", cmd_guard, "(hook) the agents' PreToolUse guard")
    s = c("panel", cmd_panel, "serve the panel")
    s.add_argument("--host", default="127.0.0.1", help="0.0.0.0 to reach it from other devices (the key is required)")
    s.add_argument("--port", type=int, default=7788)
    c("launchd", cmd_launchd, "print the launchd job that keeps the loop alive")
    return ap


def main(argv: list[str] | None = None) -> None:
    a = parser().parse_args(argv)
    try:
        a.fn(a)
    except backlog.BacklogError as e:
        sys.exit(f"fm: {e}")
