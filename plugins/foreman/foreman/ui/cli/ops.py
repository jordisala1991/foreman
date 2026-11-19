"""The operator's commands: loop, health, metrics, guard, lock, launchd."""

import argparse
import os
import sys
from pathlib import Path

from ... import providers
from ...core.oversight import guard
from ...core.project import registry
from ...loop import tick
from ...providers import registry as provider_registry
from ...services import lock
from ...steps import metrics, schedule, watch
from ...steps.health import health, notes
from .common import home


def cmd_loop(a: argparse.Namespace) -> None:
    tick.forever([a.home] if a.home else None, once=a.once)


def cmd_health(a: argparse.Namespace) -> None:
    for line in [*notes.python_note(), *notes.shared_folders(registry.loaded())]:
        print(line)
    p = home(a)
    for r in schedule.rows(p):
        print(schedule.line(r))
    for line in [*notes.versions(p), *notes.old_links(p)]:
        print(line)
    try:
        rows = provider_registry.rows(p)
    except providers.ConfigError as e:
        sys.exit(f"foreman: {e}")
    for f in health.findings(p, rows, outside=True):
        print(f"! {f['title']}: {(f['evidence'].splitlines() or [''])[0]}")


def cmd_metrics(a: argparse.Namespace) -> None:
    rows = metrics.per_task(home(a))
    print(metrics.text(rows))


def cmd_watch(a: argparse.Namespace) -> None:
    p = home(a)
    try:
        rows = provider_registry.runner(p).rows()
    except providers.ConfigError as e:
        sys.exit(f"foreman: {e}")
    if rows is None:
        sys.exit("foreman: claude agents is unreadable: foreman watch needs the session listing")
    watch.run(p, a.once, rows)


def cmd_guard(a: argparse.Namespace) -> None:
    out = guard.hook(sys.stdin.read())
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
