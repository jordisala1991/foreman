"""Locks common to every project in the panel: the owner taking one (OwnerLock), what a resource's `busy` command
prints (BusyProbe, through commands.run) and the list of every resource (`locks`).
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import commands, lock, util
from .panel_cache import iso_ts
from .panel_registry import REG

if TYPE_CHECKING:
    from .config import Table


class OwnerLock:
    """The owner taking a lock from the panel (only resources without slots), as the panel it was forked from did:
    the turn file names the owner so nobody else goes first; a thread takes the lock directory once it is free and
    releases it when the time runs out. State per resource: no | waiting | holds."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.st: dict[str, dict[str, Any]] = {}

    def who(self) -> str:
        return REG.settings.get("who") or "owner"

    def state(self, name: str) -> dict[str, Any]:
        return self.st.get(name, {"state": "no", "expires": None})

    def take(self, name: str, res: Table, minutes: float) -> None:
        p = lock.paths(name, res)
        with self.lock:
            if self.state(name)["state"] != "no":
                return
            prev = p["next"].read_text().strip() if p["next"].exists() else None
            p["next"].parent.mkdir(parents=True, exist_ok=True)
            p["next"].write_text(f"{self.who()}: panel ({util.now():%H:%M})\n")
            self.st[name] = {
                "state": "waiting",
                "expires": None,
                "minutes": minutes,
                "res": res,
                "prev": None if prev and prev.startswith(self.who()) else prev,
            }

    def release(self, name: str, res: Table) -> None:
        p = lock.paths(name, res)
        with self.lock:
            s = self.st.get(name, {})
            if self._mine(p):
                shutil.rmtree(p["dir"], ignore_errors=True)
            if p["next"].exists() and p["next"].read_text().startswith(self.who()):
                p["next"].unlink()
                if s.get("prev"):
                    p["next"].write_text(s["prev"] + "\n")
            self.st[name] = {"state": "no", "expires": None}

    def extend(self, name: str, res: Table, minutes: float) -> bool:
        """+minutes to the owner's lock: to its expiry if he holds it (written in the owner line too), to the time he
        asked for if he is still waiting. A lock without expiry stays without one."""
        p = lock.paths(name, res)
        with self.lock:
            s = self.st.get(name)
            if not s or s["state"] == "no":
                return False
            if s["state"] == "waiting":
                if s.get("minutes"):
                    s["minutes"] += minutes
                return True
            if not s.get("expires") or not self._mine(p):
                return False
            s["expires"] += timedelta(minutes=minutes)
            try:
                o = (p["dir"] / "owner").read_text()
                util.write_text_atomic(
                    p["dir"] / "owner", re.sub(r"expires \S+ \S+\)", f"expires {s['expires']:%Y-%m-%d %H:%M:%S})", o)
                )
            except OSError:
                pass
            return True

    def recover(self) -> None:
        """On startup, the locks the owner still holds from an earlier run of the panel (with the expiry of their
        owner line), so they are released on time and the buttons say they are his."""
        for name, (res, _users, _machine) in REG.resources().items():
            if res.get("slots"):
                continue
            p = lock.paths(name, res)
            if not self._mine(p):
                continue
            o = (p["dir"] / "owner").read_text()
            m = re.search(r"expires (\S+ \S+)\)", o)
            try:
                exp = util.fromiso(m.group(1)) if m else None
            except ValueError:
                exp = None
            with self.lock:
                self.st[name] = {"state": "holds", "expires": exp, "res": res}

    def _mine(self, p: dict[str, Path]) -> bool:
        try:
            return (p["dir"] / "owner").read_text().startswith(self.who() + ":")
        except OSError:
            return False

    def tick(self) -> None:
        with self.lock:
            for name, s in list(self.st.items()):
                if s["state"] == "no":
                    continue
                p = lock.paths(name, s.get("res"))
                if s["state"] == "waiting":
                    try:
                        p["dir"].mkdir()
                    except FileExistsError:
                        continue
                    except OSError:
                        continue
                    mins = s.get("minutes") or 0
                    exp = (util.now() + timedelta(minutes=mins)).replace(microsecond=0) if mins else None
                    tail = f", expires {exp:%Y-%m-%d %H:%M:%S}" if exp else ", no expiry"
                    now = util.now().strftime("%Y-%m-%dT%H:%M:%S")
                    # whole files, as lock.sh writes them: a reader never sees an empty owner line
                    util.write_text_atomic(
                        p["dir"] / "owner", f"{self.who()}: manual from the panel ({util.now():%F %T}{tail})\n"
                    )
                    util.write_text_atomic(p["dir"] / "meta", f"{now}|{now}|0|manual from the panel\n")
                    if s.get("prev"):
                        p["next"].write_text(s["prev"] + "\n")
                    elif p["next"].exists():
                        p["next"].unlink()
                    s.update(state="holds", expires=exp, prev=None)
                elif not self._mine(p):
                    s.update(state="no", expires=None)
                elif s["expires"] and util.now() >= s["expires"]:
                    shutil.rmtree(p["dir"], ignore_errors=True)
                    s.update(state="no", expires=None)

    def watch(self) -> None:
        with contextlib.suppress(Exception):
            self.recover()
        while True:
            time.sleep(3)
            with contextlib.suppress(Exception):
                self.tick()


OWNER = OwnerLock()


class BusyProbe:
    """What a resource's `busy` command prints (its users outside the lock), for the resource card. The command runs
    in a background thread, never in the request: a request gets the last result (state `pending` until the first one
    ends) and starts a new run once it is older than `ttl` seconds. A run is killed (with its process group) after
    `timeout` seconds and shows as `timeout`; a failure to start, or a non-zero exit with nothing printed, as `error`
    (with its stderr). The output (stdout, what `foreman lock` looks at) is cut to `limit` bytes (`truncated`). It runs
    like `foreman lock` runs it (bash -c, with the
    FOREMAN_LOCK_* environment of the resource), in Registry.busy_cwd: the registry's folder for a machine resource,
    else the home of the project whose config the panel uses."""

    def __init__(self, ttl: float = 5.0, timeout: float = 5.0, limit: int = 4000) -> None:
        self.ttl, self.timeout, self.limit = ttl, timeout, limit
        self.lock = threading.Lock()
        self.st: dict[Any, dict[str, Any]] = {}
        self.runs = 0  # commands started (tests: a second request within the ttl starts none)

    def get(self, name: str, res: Table, cwd: str | Path | None) -> dict[str, Any] | None:
        cmd = str(res.get("busy") or "").strip()
        if not cmd:
            return None
        key = (name, cmd)
        with self.lock:
            st = self.st.get(key) or {"state": "pending", "output": "", "truncated": False, "at": 0.0}
            if not st.get("running") and time.time() - st["at"] >= self.ttl:
                st = {**st, "running": True}
                self.runs += 1
                env = lock.environment(name, res, OWNER.who(), mkdirs=False)
                st["thread"] = threading.Thread(target=self._run, args=(key, cmd, env, cwd), daemon=True)
                st["thread"].start()
            self.st[key] = st
            out = {k: st[k] for k in ("state", "output", "truncated")}
        if st.get("since"):
            out["since"] = datetime.fromtimestamp(st["since"]).isoformat(timespec="seconds")
        return out

    def wait(self, timeout: float | None = None) -> None:
        """Wait for the runs in flight (tests)."""
        for st in list(self.st.values()):
            if st.get("thread"):
                st["thread"].join(timeout)

    def _run(self, key: tuple[str, str], cmd: str, env: dict[str, str], cwd: str | Path | None) -> None:
        r = commands.run(
            "resource.busy",
            cmd,
            env=env,
            cwd=cwd if cwd and os.path.isdir(cwd) else None,
            timeout=self.timeout,
            limit=self.limit + 1,
        )
        state, cut = "ok", len(r.stdout.encode()) > self.limit
        out = r.stdout.encode()[: self.limit].decode(errors="replace").rstrip()
        if r.rc is None:
            state, out = "error", r.stderr.strip()
        elif r.timed_out:
            state = "timeout"
        elif r.rc and not out:  # like `foreman lock`, what counts is what it prints
            state, out = "error", r.stderr.strip()[: self.limit] or f"exit {r.rc}"
        with self.lock:
            now, old = time.time(), self.st.get(key) or {}
            same = old.get("since") and (old["state"], old["output"], old["truncated"]) == (state, out, cut)
            # since: when what it prints last changed (not when it last ran, which changes every few seconds)
            self.st[key] = {
                "state": state,
                "output": out,
                "truncated": cut,
                "at": now,
                "running": False,
                "since": old["since"] if same else now,
            }


BUSY = BusyProbe()


def locks() -> list[dict[str, Any]]:
    """Every resource of every project: holders (with since when, `since_ts`), queue (`since_ts`), turn, who declares
    it, whether the owner can
    take it from here and, for a resource with `busy`, what that command prints (BusyProbe)."""
    out = []
    for name, (res, users, machine) in sorted(REG.resources().items()):
        st = lock.status(name, res)
        p = lock.paths(name, res)
        for h in st["holders"]:
            try:
                h["since_ts"] = int(
                    (p["dir"] / "owner").stat().st_mtime
                    if not h.get("slot")
                    else (p["dir"].parent / f"{p['dir'].name}-{h['slot']}" / "owner").stat().st_mtime
                )
            except OSError:
                h["since_ts"] = iso_ts(h.get("since"))
        for q in st["queue"]:
            q["since_ts"] = iso_ts(q.get("since"))
        own = OWNER.state(name)
        takers = [u for u in users if REG.projects[u].active] if not res.get("slots") else []
        out.append(
            {
                "name": name,
                **st,
                "projects": users,
                "machine": machine,
                "me": OWNER.who(),
                "slots": res.get("slots") or [],
                # only an active product takes a lock from the panel: an inactive one is set up but
                # switched off; `takers` are the products shown with the action
                "takers": takers,
                "takeable": bool(takers),
                "owner_state": own["state"],
                "owner_expires": own["expires"].strftime("%H:%M") if own.get("expires") else None,
            }
        )
        busy = BUSY.get(name, res, REG.busy_cwd(users, machine))
        if busy is not None:
            out[-1]["busy"] = busy
    return out
