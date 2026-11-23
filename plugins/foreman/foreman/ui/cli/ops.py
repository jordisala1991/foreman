"""The operator's commands: loop, health, metrics, watch, needs, agents, runs, events, reviews, guard, lock, launchd."""

import argparse
import os
import sys
from pathlib import Path

from ... import providers
from ...core.oversight import guard
from ...core.project import registry
from ...core.project.config import Project
from ...core.review import reviews
from ...core.tasks import backlog, runs
from ...loop import tick
from ...providers import registry as provider_registry
from ...providers.runner import Rows, unreadable
from ...services import lock
from ...steps import live, metrics, schedule, watch
from ...steps.health import health, notes, orca, report
from .common import home, print_json


def cmd_loop(a: argparse.Namespace) -> None:
    tick.forever([a.home] if a.home else None, once=a.once)


def cmd_health(a: argparse.Namespace) -> None:
    if a.json:
        p = home(a)
        print_json(report.build(p, _rows(p)))
        return
    for line in [*notes.python_note(), *notes.shared_folders(registry.loaded())]:
        print(line)
    p = home(a)
    for r in schedule.rows(p):
        print(schedule.line(r))
    for line in [*notes.versions(p), *notes.old_links(p), *orca.warnings(p)]:
        print(line)
    for f in health.findings(p, _rows(p), outside=True):
        print(f"! {f['title']}: {(f['evidence'].splitlines() or [''])[0]}")


def _rows(p: Project) -> Rows:
    try:
        return provider_registry.rows(p)
    except providers.ConfigError as e:
        sys.exit(f"foreman: {e}")


def cmd_metrics(a: argparse.Namespace) -> None:
    rows = metrics.per_task(home(a))
    print(metrics.text(rows))


def _listing(p: Project, command: str) -> Rows:
    """The session listing, or the command exits: `watch` and `needs` decide parents from it."""
    try:
        rows = provider_registry.runner(p).rows()
    except providers.ConfigError as e:
        sys.exit(f"foreman: {e}")
    if rows is None:
        sys.exit(f"foreman: {unreadable(p)}: foreman {command} needs the session listing")
    return rows


def cmd_watch(a: argparse.Namespace) -> None:
    p = home(a)
    watch.run(p, a.once, _listing(p, "watch"))


def cmd_needs(a: argparse.Namespace) -> None:
    """`watch`'s set of needs now, as JSON (DESIGN.md «The JSON reads»)."""
    p = home(a)
    print_json([{"kind": n.kind, "task": n.task, "since": n.since, "text": n.text, "key": n.key} for n in watch.found(p, _listing(p, "needs"))])


def cmd_agents(a: argparse.Namespace) -> None:
    """The agents live now, as JSON (DESIGN.md «The JSON reads»)."""
    p = home(a)
    out = [
        {"task": x.task, "kind": x.kind, "model": x.model, "effort": x.effort, "started": x.started, "round": x.round, "session": x.session, "handle": x.handle}
        for x in live.agents(p, _listing(p, "agents"))
    ]
    print_json(out)


def cmd_runs(a: argparse.Namespace) -> None:
    """Every run in the ledger, as JSON (DESIGN.md «The JSON reads»); `--task` and `--active` narrow it."""
    print_json([r.json() for r in runs.load(home(a)) if (not a.task or r.task == a.task) and (not a.active or r.active)])


def cmd_events(a: argparse.Namespace) -> None:
    """A task's events in time order, as JSON (DESIGN.md «The JSON reads»)."""
    print_json(backlog.history(home(a), a.task))


def cmd_reviews(a: argparse.Namespace) -> None:
    """A task's review rounds with their text, as JSON (DESIGN.md «The JSON reads»); unknown task: `[]`."""
    try:
        i = provider_registry.backlog(home(a)).find(a.task)
    except backlog.BacklogError:
        i = {}
    print_json(reviews.rounds(i))


def cmd_guard(a: argparse.Namespace) -> None:
    out = guard.hook(sys.stdin.read(), lambda p, tid: provider_registry.backlog(p).local(tid))
    if out:
        print(out)


def cmd_lock(a: argparse.Namespace) -> None:
    p = home(a)
    res = lock.declared(p.cfg)
    if a.name not in res:
        sys.exit(f"foreman: unknown resource {a.name!r} (declared: {', '.join(res) or 'none'}; [resources.NAME] in foreman.toml)")
    r = res[a.name]
    if a.action == "status":
        s = r.status()
        for slot, h in s["held"].items():
            print(f"{slot}: {h['who']} ({h['task'] or '-'}): {h['what']} since {h['since']}")
        for t in s["queue"]:
            print(f"queued: {t['who']} ({t['task'] or '-'}): {t['what']} since {t['since']}")
        if not s["held"] and not s["queue"]:
            print("free")
        return
    if not a.command:
        sys.exit('foreman: foreman lock NAME run "what" command…')
    who = os.environ.get("FOREMAN_AGENT") or "owner"
    sys.exit(r.run(who, a.what, a.command, os.environ.get("FOREMAN_TASK", ""), say=lambda m: print(m, file=sys.stderr)))


def cmd_launchd(a: argparse.Namespace) -> None:
    from ...services import launchd

    jobs = {"loop": (), "panel": ("--host", a.panel_host, "--port", "7788")}
    for job, args in jobs.items():
        text = launchd.plist(job, args)
        if a.dir:
            out = Path(a.dir).expanduser() / f"dev.foreman.{job}.plist"
            out.write_text(text, encoding="utf-8")
            print(f"wrote {out}: launchctl bootstrap gui/$(id -u) {out}")
        else:
            print(f"<!-- dev.foreman.{job}.plist -->\n{text}")
