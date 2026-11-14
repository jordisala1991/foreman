"""Shared resources: `fm lock NAME run "what" command…` waits its turn, runs the command holding the resource, and
always releases it. The lean version of foreman's lock (lib/foreman/lock.py + sh/lock.sh), keeping what worked:

- a fair queue: first come, first served (a ticket per waiter, by arrival time);
- slots: a resource may have several (`slots = ["A", "B"]`, e.g. two simulators); the command gets FM_SLOT;
- a holder or waiter whose process died is cleaned up when anyone looks, so a crash never leaves it stuck;
- a time limit: the command is stopped after `max_minutes` (default 20), so short jobs pass between long takes;
- a log of every use (who, task, what, waited, held), read by the metrics and the panel.

Dropped from today's: the «busy» detector, short jobs jumping the queue, the turn file, the 5-step «who am I» chain
and the hook that forces its use. Like the guard, it is a convention agents follow because their brief says so.

A resource is declared in fm.toml, `[resources.NAME]` with optional `slots` and `max_minutes`. Its state is common to
the machine (`~/.local/state/fm/locks/NAME/`, or $FM_LOCKS), so two projects declaring the same name share its queue.
"""

import contextlib
import fcntl
import os
import signal
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import util

POLL_S = 1.0


def root() -> Path:
    return Path(os.environ.get("FM_LOCKS") or Path.home() / ".local/state/fm/locks")


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class Resource:
    def __init__(self, name: str, slots: list[str] | None = None, max_minutes: float = 20) -> None:
        self.name, self.slots, self.max_s = name, slots or ["1"], float(max_minutes) * 60
        self.dir = root() / name
        self.dir.mkdir(parents=True, exist_ok=True)

    @contextlib.contextmanager
    def _state(self) -> Any:
        """Exclusive access to the resource's state for one decision (short: never held while a command runs)."""
        with open(self.dir / "state.lock", "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                data = util.read_json(self.dir / "state.json", {"held": {}, "queue": []})
                data["held"] = {s: h for s, h in data["held"].items() if alive(h["pid"])}
                data["queue"] = [t for t in data["queue"] if alive(t["pid"])]
                yield data
                util.write_json(self.dir / "state.json", data)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def status(self) -> dict[str, Any]:
        with self._state() as d:
            return {"name": self.name, "slots": self.slots, "held": dict(d["held"]), "queue": list(d["queue"])}

    def acquire(self, who: str, what: str, task: str = "", say: Callable[[str], object] | None = None) -> tuple[str, float]:
        """Waits for a free slot in arrival order; (slot, seconds waited)."""
        me = {"who": who, "what": what, "task": task, "pid": os.getpid(), "since": util.now(), "t": time.time()}
        with self._state() as d:
            d["queue"].append(me)
        t0, said = time.time(), 0.0
        while True:
            with self._state() as d:
                first = min(d["queue"], key=lambda t: t["t"], default=None)
                free = [s for s in self.slots if s not in d["held"]]
                if first and first["pid"] == me["pid"] and first["t"] == me["t"] and free:
                    d["queue"].remove(first)
                    d["held"][free[0]] = {**me, "since": util.now(), "t": time.time()}
                    return free[0], time.time() - t0
                ahead = [t for t in d["queue"] if t["t"] < me["t"]]
                holders = list(d["held"].values())
            if say and time.time() - said >= 60:
                said = time.time()
                say(
                    f"waiting for {self.name}: held by "
                    + ", ".join(f"{h['who']} ({h['what']})" for h in holders)
                    + (f"; {len(ahead)} ahead in the queue" if ahead else "")
                )
            time.sleep(POLL_S)

    def release(self, slot: str) -> None:
        with self._state() as d:
            if d["held"].get(slot, {}).get("pid") == os.getpid():
                del d["held"][slot]

    def run(self, who: str, what: str, cmd: list[str], task: str = "", say: Callable[[str], object] | None = None) -> int:
        """Holds the resource while `cmd` runs (FM_SLOT set), stops it at the time limit, always releases."""
        slot, waited = self.acquire(who, what, task, say)
        t0, rc, event = time.time(), 1, "use"
        try:
            p = subprocess.Popen(cmd, env={**os.environ, "FM_SLOT": slot, "FM_RESOURCE": self.name}, start_new_session=True)
            try:
                rc = p.wait(timeout=self.max_s)
            except subprocess.TimeoutExpired:
                event = "timeout"
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(p.pid, signal.SIGTERM)
                try:
                    p.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(p.pid, signal.SIGKILL)
                    p.wait()
                rc = 124
                if say:
                    say(f"{self.name}: stopped after {self.max_s / 60:.0f} min; split long work into shorter runs")
        finally:
            self.release(slot)
            util.append_jsonl(
                self.dir / "uses.jsonl",
                {
                    "time": util.now(),
                    "event": event,
                    "who": who,
                    "task": task,
                    "what": what,
                    "slot": slot,
                    "waited_s": round(waited, 1),
                    "held_s": round(time.time() - t0, 1),
                    "rc": rc,
                },
            )
        return rc


def declared(cfg: dict[str, Any]) -> dict[str, Resource]:
    """The project's resources, from `[resources.NAME]` in fm.toml."""
    out: dict[str, Resource] = {}
    for name, item in (util.json_object(cfg.get("resources")) or {}).items():
        r = util.json_object(item) or {}
        out[name] = Resource(name, [str(s) for s in util.json_list(r.get("slots"))] or None, r.get("max_minutes", 20))
    return out


def uses(names: list[str]) -> list[dict[str, Any]]:
    return [u for n in names for u in util.read_jsonl(root() / n / "uses.jsonl")]


def describe(cfg: dict[str, Any]) -> str:
    """The line for the agents' brief: the resources and how to use them."""
    res = declared(cfg)
    if not res:
        return "This project declares no shared resources."
    names = ", ".join(f"`{n}`" + (f" ({len(r.slots)} slots)" if len(r.slots) > 1 else "") for n, r in res.items())
    return (
        f"Shared resources of this project: {names}. Anything that uses one runs inside "
        '`fm lock NAME run "what" command…`: it waits its turn, runs, and always releases (the command gets '
        "FM_SLOT). A run is stopped after its time limit: split long work into shorter runs. Never hold a "
        "resource while you wait for something else."
    )
