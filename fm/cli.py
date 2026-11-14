"""`fm`: the one command. Agents use `ask`, `done`, `ready`, `block`, `add` (their issue comes from FM_ISSUE);
the owner and the panel use the rest; launchd runs `fm loop`; Claude Code runs `fm guard` as the agents' hook."""

import argparse
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path

from . import agents, backlog, editing, guard, health, integrate, lock, loop, metrics, plan_gate, providers, sessions, subtasks, token, util
from .cli_agent import cmd_ask, cmd_block, cmd_done, cmd_ready, cmd_verdict
from .cli_common import home
from .cli_parser import parser
from .config import Project, register


def cmd_init(a: argparse.Namespace) -> None:
    h = Path(a.home or ".").resolve()
    h.mkdir(parents=True, exist_ok=True)
    if not (h / "fm.toml").exists():
        (h / "fm.toml").write_text(
            f'[project]\nname = "{h.name}"\nprefix = "{a.prefix}"\nrepo = "{a.repo}"\nmain = "main"\n\n'
            '[agents]\nslots = 2\nmodel = "sonnet"\neffort = "medium"\n'
            'define_model = "sonnet"\ndefine_effort = "high"\nreview_model = "sonnet"\nreview_effort = "high"\n\n'
            '[integrate]\nautonomy = "manual"  # manual | low | medium\nprovider = "local"\nmerge = "squash"  # squash | merge\ntest = ""\n',
            encoding="utf-8",
        )
    p = Project.load(h)
    if not p.backlog_file.exists():
        util.write_json(p.backlog_file, backlog.empty())
    register(h)
    print(f"project {p.name} at {h} (registered)")
    print(token.SETUP)


def cmd_add(a: argparse.Namespace) -> None:
    p = home(a)
    status = "triage" if a.triage else "backlog"
    if a.parent:
        if os.environ.get("FM_ISSUE"):
            sys.exit("fm: subtasks are filed by the owner, not from an agent's session")
        if why := subtasks.refusal(backlog.issues(backlog.load(p)), a.parent):
            sys.exit(f"fm: {why}")
    iid = backlog.add(
        p, a.title, detail=a.detail or "", status=status, criteria=a.criteria or "", claim=a.claim or "", priority=a.priority, parent=a.parent or ""
    )
    print(iid)


def cmd_edit(a: argparse.Namespace) -> None:
    if os.environ.get("FM_ISSUE"):
        sys.exit("fm: tasks are edited by the owner, not from an agent's session")
    p = home(a)
    fields = {f: v for f in editing.FIELDS if (v := getattr(a, f))}
    with backlog.edit(p) as d:
        i = backlog.get(d, a.id)
        if why := editing.refusal(i, fields):
            sys.exit(f"fm: {why}")
        old = dict(i)
        new = editing.edited(i, fields)
        i.clear()
        i.update(new)
    reset = old["status"] != new["status"] or ("plan_file" in old and "plan_file" not in new)
    backlog.event(p, a.id, "edited by the owner: " + ", ".join(fields) + ("; plan dropped, back to backlog" if reset else ""))
    after = "; it stays blocked until fm unblock" if new.get("blocked") else "; it will be planned again"
    print(f"{a.id} edited" + (after if reset else ""))


def cmd_list(a: argparse.Namespace) -> None:
    p = home(a)
    d = backlog.load(p)
    status = {i["id"]: i["status"] for i in backlog.issues(d)}
    for i in subtasks.ordered(backlog.issues(d)):
        if i["status"] in backlog.CLOSED and not a.all:
            continue
        agent: backlog.Agent = i.get("agent") or {}
        ag = agent.get("name", "")
        shown = i.get("parent") in status and (a.all or status[i["parent"]] not in backlog.CLOSED)
        title = ("  " if shown else "") + i["title"]
        print(f"{i['id']:8} {i['status']:12} {ag:14} {title}" + (f"  [blocked: {i['blocked']}]" if i.get("blocked") else ""))
    for q in backlog.open_questions(d):
        print(f"? {q['id']} ({q.get('agent')}): {q['text']}")


def cmd_show(a: argparse.Namespace) -> None:
    p = home(a)
    d = backlog.load(p)
    i = backlog.get(d, a.id)
    if subs := subtasks.of(backlog.issues(d), a.id):
        i = {**i, "subtasks": [{"id": s["id"], "status": s["status"], "title": s["title"]} for s in subs]}
    print(json.dumps(i, indent=2, ensure_ascii=False))


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
        integrate.accept(p, a.id)
        print(f"{a.id} accepted: the loop merges it next")
    except (integrate.IntegrateError, providers.ConfigError) as e:
        sys.exit(f"fm: {e}")


def cmd_reject(a: argparse.Namespace) -> None:
    p = home(a)
    if backlog.get(backlog.load(p), a.id)["status"] != "in review":
        sys.exit(f"fm: {a.id} is not in the integration queue")
    loop.send_back(p, a.id, "rejection", a.note)


def cmd_approve_plan(a: argparse.Namespace) -> None:
    plan_gate.approve(home(a), a.id)


def cmd_reject_plan(a: argparse.Namespace) -> None:
    plan_gate.reject(home(a), a.id, a.note)


def cmd_review(a: argparse.Namespace) -> None:
    p = home(a)
    with backlog.edit(p) as d:
        i = backlog.get(d, a.id)
        if i["status"] != "in review":
            sys.exit(f"fm: {a.id} is not in the integration queue")
        if i.get("accepted"):
            sys.exit(f"fm: {a.id} is accepted: the loop merges it next")
        if agents.review_running(i):
            sys.exit(f"fm: {a.id} has a review running: wait for its verdict")
        for k in ("review", "hunter", "reviewer", "check"):
            i.pop(k, None)
        i["blocked"] = ""
        i["review_from"] = len(i.get("reviews", []))
    backlog.event(p, a.id, "owner asked for a fresh review from round 1")
    print(f"{a.id}: the loop reviews it again from scratch")


def cmd_go(a: argparse.Namespace) -> None:
    p = home(a)
    i = backlog.get(backlog.load(p), a.id)
    acceptance: backlog.Acceptance = i.get("acceptance") or {}
    if not acceptance.get("criteria"):
        sys.exit(f"fm: {a.id}: define its acceptance criteria with the owner first")
    backlog.set_status(p, a.id, "backlog", "let in by the owner", blocked="")


def cmd_cancel(a: argparse.Namespace) -> None:
    try:
        integrate.close(home(a), a.id, "canceled", a.why or "by the owner")
    except integrate.IntegrateError as e:
        sys.exit(f"fm: {e}")


def cmd_unblock(a: argparse.Namespace) -> None:
    p = home(a)
    i = backlog.update(p, a.id, blocked="", pr_error="", retry_error="", retry_count=0)
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


def cmd_metrics(a: argparse.Namespace) -> None:
    rows = metrics.per_issue(home(a))
    print(metrics.text(rows))


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
    fm_bin = str(Path(__file__).resolve().parent.parent / "bin/fm")
    jobs = {"loop": (), "panel": ("--host", a.panel_host, "--port", "7788")}
    for job, args in jobs.items():
        text = loop.launchd_plist(fm_bin, job, args)
        if a.dir:
            out = Path(a.dir).expanduser() / f"dev.fm.{job}.plist"
            out.write_text(text, encoding="utf-8")
            print(f"wrote {out}: launchctl bootstrap gui/$(id -u) {out}")
        else:
            print(f"<!-- dev.fm.{job}.plist -->\n{text}")


HANDLERS: dict[str, Callable[[argparse.Namespace], object]] = {
    "init": cmd_init,
    "add": cmd_add,
    "edit": cmd_edit,
    "list": cmd_list,
    "show": cmd_show,
    "ask": cmd_ask,
    "done": cmd_done,
    "verdict": cmd_verdict,
    "ready": cmd_ready,
    "block": cmd_block,
    "answer": cmd_answer,
    "allow": lambda a: cmd_allow(a, True),
    "deny": lambda a: cmd_allow(a, False),
    "accept": cmd_accept,
    "reject": cmd_reject,
    "approve-plan": cmd_approve_plan,
    "reject-plan": cmd_reject_plan,
    "review": cmd_review,
    "go": cmd_go,
    "cancel": cmd_cancel,
    "unblock": cmd_unblock,
    "pause": cmd_pause,
    "loop": cmd_loop,
    "health": cmd_health,
    "metrics": cmd_metrics,
    "lock": cmd_lock,
    "guard": cmd_guard,
    "panel": cmd_panel,
    "launchd": cmd_launchd,
}


def main(argv: list[str] | None = None) -> None:
    a = parser(__doc__).parse_args(argv)
    try:
        HANDLERS[a.cmd](a)
    except backlog.BacklogError as e:
        sys.exit(f"fm: {e}")
