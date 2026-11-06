"""Resource locks: `foreman lock RESOURCE run|acquire|release|check …`.

A resource is declared once for the machine in the registry ([resources.NAME]: where the lock lives, busy, busy_wait,
short_max, slots) and each product that uses it declares [resources.NAME] in its foreman.toml with its own guard and
allow; or, without a machine table, in each product's foreman.toml. Either way it is common to every project: its
state lives in ~/.local/state/foreman/locks/NAME/ (held, queue/, next, jumped, uses.jsonl) unless the declaration
says where, so two projects that declare the same name share one queue. The algorithm is sh/lock.sh (a fork of
an earlier in-house version); foreman runs every lock itself.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

from . import config, util
from .config import state_root

if TYPE_CHECKING:
    from collections.abc import Iterable

    from .config import Project, Table

Status = dict[str, Any]  # what status() returns: holders, queue and next (see its docstring)
Holder = dict[str, Any]  # a holder or a queue entry of a Status; the keys are in status()'s docstring

HERE = Path(__file__).resolve().parent
LOCK_SH = HERE / "sh/lock.sh"


class LockError(Exception):
    pass


def who_am_i() -> str:
    """The holder's name from the environment only (identity.who, the same order as notes), else `agent`. The CLI
    also looks up the call the guard wrote down (cli.py passes `who` to run)."""
    from . import identity

    return identity.who("agent")[0]


def machine() -> dict[str, Table]:
    """The registry's machine resources ({} when the registry does not read: `foreman check` says why)."""
    try:
        return config.machine_resources()[0]
    except config.ConfigError:
        return {}


def resource_config(project: Project | None, name: str, tolerant: bool = False) -> Table:
    """The table of resource `name`: the project's (already merged with the machine's at load), else the machine's
    (a machine resource works from a project that does not declare it, and outside any project). Unknown anywhere:
    LockError, or {} with tolerant=True (the default paths)."""
    res = (project.resources if project else {}).get(name)
    if res is not None:
        return res
    mres = machine()
    if name in mres:
        return mres[name]
    if tolerant:
        return {}
    known = ", ".join(sorted({*(project.resources if project else {}), *mres})) or "none"
    raise LockError(
        f"unknown resource {name!r} (known: {known}); declare it in foreman.toml [resources.{name}], or "
        f"once for the machine in the registry ({config.registry_path()}) [resources.{name}]"
    )


def paths(name: str, res: Table | None = None) -> dict[str, Path]:
    """Where the lock of resource `name` lives: dir (held), queue, next, jumped, log."""
    res = res or {}
    base = state_root() / "locks" / name
    pick = lambda key, default: Path(os.path.expanduser(res[key])) if res.get(key) else default  # noqa: E731
    return {
        "dir": pick("dir", base / "held"),
        "queue": pick("queue", base / "queue"),
        "next": pick("next", base / "next"),
        "jumped": pick("jumped", base / "jumped"),
        "log": pick("log", base / "uses.jsonl"),
    }


def environment(
    name: str,
    res: Table,
    who: str | None = None,
    mkdirs: bool = True,
    default: bool = False,
    project: Project | None = None,
) -> dict[str, str]:
    """The environment lock.sh runs with. FOREMAN_LOCK_WHO is the holder's name, which the children of `run`
    inherit (a `foreman progress` inside a `run` is that holder's); `default` (the name is only the default) marks
    it with FOREMAN_LOCK_WHO_DEFAULT so the children do not take it as a name. The `busy` command gets the contract
    of commands.py from lock.sh (its time limit, FOREMAN_BIN and, with `project`, FOREMAN_PROJECT) through
    FOREMAN_LOCK_* names, so the command run under the lock does not inherit them. `busy` does not see an outer
    point's variables either (FOREMAN_LOCK_POINT_VARS: commands.POINT_VARIABLES, which lock.sh unsets for it); the
    command under the lock keeps them (it is part of that outer command)."""
    from . import commands, identity

    p = paths(name, res)
    env = dict(os.environ)
    env.pop(identity.LOCK_DEFAULT, None)
    if default or (not who and identity.who("agent")[2]):
        env[identity.LOCK_DEFAULT] = "1"
    env.update(
        {
            "FOREMAN_LOCK_WHO": str(who or who_am_i()),
            "FOREMAN_LOCK_DIR": str(p["dir"]),
            "FOREMAN_LOCK_QUEUE": str(p["queue"]),
            "FOREMAN_LOCK_NEXT": str(p["next"]),
            "FOREMAN_LOCK_JUMPED": str(p["jumped"]),
            "FOREMAN_LOCK_LOG": str(p["log"]),
            "FOREMAN_LOCK_BUSY": res.get("busy", ""),
            "FOREMAN_LOCK_BUSY_WAIT": str(res.get("busy_wait", 600)),
            "FOREMAN_LOCK_SHORT_MAX": str(res.get("short_max", 300)),
            "FOREMAN_LOCK_SLOTS": " ".join(res.get("slots", [])),
            "FOREMAN_LOCK_BUSY_TIMEOUT": str(int(commands.POINTS["resource.busy"].timeout or 5)),
            "FOREMAN_LOCK_BIN": str(commands.BIN),
            "FOREMAN_LOCK_POINT_VARS": " ".join(commands.POINT_VARIABLES),
        }
    )
    if project is not None:
        env["FOREMAN_LOCK_PROJECT"] = project.name
    for d in (p["dir"].parent, p["queue"].parent, p["log"].parent) if mkdirs else ():
        d.mkdir(parents=True, exist_ok=True)
    return env


def command(
    name: str,
    res: Table,
    args: Iterable[str],
    who: str | None = None,
    default: bool = False,
    project: Project | None = None,
) -> tuple[list[str], dict[str, str]]:
    """(argv, env) to run for `foreman lock NAME args…`."""
    return ["bash", str(LOCK_SH), *args], environment(name, res, who, default=default, project=project)


def run(
    project: Project | None, name: str, args: Iterable[str], who: str | None = None, default: bool = False
) -> NoReturn:
    """`foreman lock NAME …`: replaces this process (run keeps the lock for the command's life)."""
    res = resource_config(project, name)
    argv, env = command(name, res, args, who, default, project)
    os.execvpe(argv[0], argv, env)


# ---------- readers (no writes): check output, wait_for, the loop pass ----------

# A lock directory without its owner line (lock.sh takes the lock with mkdir and writes `owner` right after) is being
# taken for this long; older, the taker died between the two (the lock stays taken, by nobody known).
WRITING_S = 60


def status(name: str, res: Table | None = None) -> Status:
    """{"holders": [{who, what, since, slot, pid, alive, taking}], "queue": [{who, what, since, pid, short}],
    "next": str|None}.

    A holder's pid is the one lock.sh wrote in `owner` («who: what (date, pid N)»): the shell of `run`, alive while
    the command runs, or the one of `acquire`, gone as soon as the call returned. So `alive` is True only for a live
    `run`; False for a dead `run` or any `acquire`; None if the owner line gives no pid (one written by hand or by
    an older script).

    A lock directory whose owner line is not there (being taken, or being released; or empty: an older lock.sh wrote
    it in two steps) is a holder `?` with `taking` "writing" while the directory is younger than WRITING_S, "stuck"
    after that; every complete
    holder has `taking` None. A ticket of a holder's own pid is not in the queue (the taker removes it right after
    writing `owner`), nor is an empty one (still being written) or a dot-file (lock.sh's temporary)."""
    p = paths(name, res)
    holders: list[Holder] = []
    queue: list[Holder] = []
    slots = (res or {}).get("slots") or []
    dirs = [p["dir"].parent / f"{p['dir'].name}-{s}" for s in slots] if slots else [p["dir"]]
    for d in dirs:
        try:
            owner = (d / "owner").read_text().strip()
        except FileNotFoundError:
            owner = ""
        except OSError:
            continue
        try:
            slot = (d / "slot").read_text().strip()
        except OSError:
            slot = ""
        if not owner:
            try:
                age = time.time() - d.stat().st_mtime
            except OSError:
                continue  # not taken
            holders.append(
                {
                    "who": "?",
                    "what": "",
                    "since": "",
                    "slot": slot,
                    "pid": "",
                    "alive": None,
                    "taking": "writing" if age < WRITING_S else "stuck",
                }
            )
            continue
        who, _, rest = owner.partition(": ")
        m = re.search(r", pid (\d+)\)$", rest)
        pid = m.group(1) if m else ""
        try:
            since = (d / "meta").read_text().split("|")[1]
        except (OSError, IndexError):
            since = ""
        holders.append(
            {
                "who": who,
                "what": rest,
                "since": since,
                "slot": slot,
                "pid": pid,
                "alive": util.pid_alive(pid),
                "taking": None,
            }
        )
    holding = {int(h["pid"]) for h in holders if h["pid"]}
    try:
        tickets = sorted(f for f in os.listdir(p["queue"]) if not f.startswith("."))
    except OSError:
        tickets = []
    for f in tickets:
        tpid = f.rsplit("-", 1)[-1]
        if not util.pid_alive(tpid) or (tpid.isdigit() and int(tpid) in holding):
            continue
        try:
            text = (p["queue"] / f).read_text().strip()
        except OSError:
            continue
        if not text:
            continue
        who, since, pid, what, short = (text.split("|") + ["", ""])[:5]
        queue.append({"who": who, "what": what, "since": since, "pid": pid, "short": short == "short"})
    nxt = p["next"].read_text().strip() if p["next"].exists() else None
    return {"holders": holders, "queue": queue, "next": nxt}


def held_minutes(h: Holder) -> float:
    t = util.iso_epoch(h.get("since"))
    return 0 if t is None else (time.time() - t) / 60


def involved(name: str, res: Table, who: str) -> bool:
    """True if `who` holds the lock or waits for it."""
    st = status(name, res)
    return any(x["who"] == who for x in st["holders"] + st["queue"])


def people(status_list: Iterable[Status]) -> tuple[set[str], set[str]]:
    """(holders, waiting) of these status() results: the names holding any of the locks, and those waiting for one
    (in its queue, or named by its turn file `next`, «who: …»)."""
    holders = {h["who"] for st in status_list for h in st["holders"]}
    waiting = {q["who"] for st in status_list for q in st["queue"]} | {
        (st.get("next") or "").split(":")[0] for st in status_list
    }
    return holders, waiting - {""}


def all_status(project: Project | None) -> dict[str, Status]:
    return {name: status(name, res) for name, res in (project.resources if project else {}).items()}


# The keys of [resources.NAME] that every project declaring NAME should agree on: where the one shared lock lives and
# how lock.sh behaves for its users; the keys a machine resource owns (config.MACHINE_KEYS). The others (guard and
# allow) are each project's own business.
SHARED_KEYS = config.MACHINE_KEYS


def effective(name: str, res: Table) -> dict[str, Any]:
    """{key: value} of SHARED_KEYS as lock.sh will get them (after defaults: an omitted key and its default agree)."""
    p = paths(name, res)
    out: dict[str, Any] = {k: str(p[k]) for k in ("dir", "queue", "next", "jumped", "log")}
    out["busy"] = str(res.get("busy") or "")
    slots = res.get("slots") or []
    out["slots"] = " ".join(map(str, slots)) if isinstance(slots, list) else str(slots)
    for key, default in (("busy_wait", 600), ("short_max", 300)):
        v = res.get(key, default)
        try:
            out[key] = int(v)
        except (TypeError, ValueError):
            out[key] = str(v)
    return out


def shared_conflicts(projects: Iterable[Project], only: str | None = None) -> list[str]:
    """Warnings for resources that several projects declare with different SHARED_KEYS (a resource is shared by
    name: one lock, but each project's commands run lock.sh with its own declaration). One line per resource naming
    the projects and, per differing key, each project's value. `only`: just the resources that project declares.
    A machine resource (declared in the registry) is never one: every product gets the machine's keys."""
    users: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for p in projects:
        for name, res in p.resources.items():
            if getattr(p, "resource_origin", {}).get(name) == "machine":
                continue
            users.setdefault(name, []).append((p.name, effective(name, res)))
    out: list[str] = []
    for name, decl in users.items():
        names = [n for n, _ in decl]
        if len(decl) < 2 or (only is not None and only not in names):
            continue
        diffs: list[str] = []
        for key in SHARED_KEYS:
            values = [v[key] for _, v in decl]
            if len(set(values)) > 1:
                diffs.append(f"{key} " + ", ".join(f"{n}={v[key]!r}" for n, v in decl))
        if diffs:
            out.append(
                f"[resources.{name}] is one lock shared by {', '.join(names)}, declared differently "
                f"(each project's commands use its own): "
                + "; ".join(diffs)
                + f"; declare it once in the registry ([resources.{name}] in {config.registry_path()}) "
                "or align them"
            )
    return out


def shared_warnings(project: Project) -> list[str]:
    """shared_conflicts of `project` against the registry's projects (it stands in for its own registry entry).
    Never raises: a registry that does not read gives no warning here (`foreman check` reports it)."""
    from . import config

    try:
        projects = [project if p.name == project.name else p for p in config.registered_projects()]
    except Exception:  # noqa: BLE001 (a warning must never break the caller)
        return []
    if project not in projects:
        projects.append(project)
    return shared_conflicts(projects, only=project.name)
