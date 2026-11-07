"""The panel's in-memory cache (`memo`, `file_sig`, per-key locks) and its small shared helpers.
Imports nothing of foreman, so every other panel module can use it.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from collections.abc import Callable, Hashable, Iterable, Mapping
from pathlib import Path
from typing import Any, Optional, TypeVar

from . import util

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "panel"


def page_version() -> int:
    """The page's version: the newest mtime of its files."""
    return int(max((STATIC / n).stat().st_mtime for n in ["index.html", "app.js", "app.css"]))


NEXT = STATIC / "next"  # the new panel (/next/): its own files, its own version


def next_version() -> int:
    """The new panel's version: the newest mtime of the files under panel/next/ (0 without them). The old page's
    version does not move with them."""
    return int(max((f.stat().st_mtime for f in NEXT.rglob("*") if f.is_file()), default=0))


def sh(*cmd: str, cwd: util.PathLike | None = None, timeout: float = 10, env: Mapping[str, str] | None = None) -> str:
    try:
        return subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env, check=False
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def age(ts: float) -> int:
    return int(time.time() - ts)


def iso_ts(s: object) -> int | None:
    """A local ISO time as whole epoch seconds (util.iso_epoch), None if it is not one. The polled state carries
    times, never ages, so an unchanged state keeps its ETag; the page counts the ages from the response's Date
    header."""
    t = util.iso_epoch(s)
    return None if t is None else int(t)


def iso_age(s: object) -> int | None:
    t = iso_ts(s)
    return None if t is None else age(t)


# ---------- in-memory cache ----------
# _memo_lock only guards the bookkeeping (the per-key locks and the keys being refreshed); a value is computed under
# its own key's lock, so a slow key (gh, a cold git) never makes a request for another key wait
_memo: dict[Hashable, tuple[Sig, float, Any]] = {}
_memo_lock = threading.Lock()
_refreshing: set[Hashable] = set()
_key_locks: dict[Hashable, threading.Lock] = {}
_NONE = object()
T = TypeVar("T")
Sig = tuple[Optional[tuple[int, int]], ...]  # file_sig: (mtime_ns, size) per path, None for one that cannot be read


def file_sig(paths: Iterable[util.PathLike]) -> Sig:
    out = []
    for p in paths:
        try:
            st = os.stat(p)
            out.append((st.st_mtime_ns, st.st_size))
        except OSError:
            out.append(None)
    return tuple(out)


def memo(
    key: Hashable,
    fn: Callable[[], T],
    ttl: float | None = None,
    deps: Iterable[util.PathLike] = (),
    initial: Any = _NONE,
) -> T:
    """fn() cached until one of `deps` changes; with only a ttl (git, gh), an expired value is still returned while a
    background thread recomputes it, so a poll never waits for git or the network. With `initial` (and no deps), the
    first call does not wait either: it starts fn() in the background and returns `initial` until the value arrives.
    Two callers only wait for each other when they ask for the same key."""
    sig = file_sig(deps)
    hit = _memo.get(key)
    if hit and hit[0] == sig:
        if ttl is None or time.time() < hit[1]:
            return hit[2]
        if not deps:
            _background(key, fn, ttl)
            return hit[2]
    if hit is None and initial is not _NONE and not deps:
        _background(key, fn, ttl)
        return initial
    with _key_lock(key):
        hit = _memo.get(key)
        if hit and hit[0] == sig and (ttl is None or time.time() < hit[1]):
            return hit[2]
        v = fn()
        _memo[key] = (sig, time.time() + (ttl or 0), v)
        return v


def _key_lock(key: Hashable) -> threading.Lock:
    with _memo_lock:
        lk = _key_locks.get(key)
        if lk is None:
            lk = _key_locks[key] = threading.Lock()
        return lk


def _background(key: Hashable, fn: Callable[[], Any], ttl: float | None) -> None:
    """Recompute `key` in a thread of its own, unless one is already at it."""
    with _memo_lock:
        if key in _refreshing:
            return
        _refreshing.add(key)
    threading.Thread(target=_refresh, args=(key, fn, ttl), daemon=True).start()


def _refresh(key: Hashable, fn: Callable[[], Any], ttl: float | None) -> None:
    try:
        _memo[key] = ((), time.time() + (ttl or 0), fn())
    except Exception:  # noqa: BLE001 (a refresh failure keeps the stale value and the panel up)
        pass
    finally:
        with _memo_lock:
            _refreshing.discard(key)
