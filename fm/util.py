"""Small file helpers: an exclusive lock, atomic writes, JSON and JSON-lines (adapted from foreman's util.py)."""

import contextlib
import fcntl
import json
import os
import tempfile
import threading
import time
from collections.abc import Callable, Generator
from datetime import datetime
from pathlib import Path
from typing import Any, cast


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def age_s(stamp: str | None) -> float | None:
    """Seconds since a now() stamp; None when there is none or it does not parse."""
    try:
        return time.time() - datetime.strptime(stamp or "", "%Y-%m-%d %H:%M:%S").timestamp()
    except ValueError:
        return None


@contextlib.contextmanager
def file_lock(path: Path) -> Generator[None, None, None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path) + ".lock", "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, obj: Any) -> None:
    write_text_atomic(path, json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


@contextlib.contextmanager
def locked_json(path: Path, default: Any) -> Generator[Any, None, None]:
    """Read under the file's lock; whatever the caller leaves in the yielded object is written back."""
    with file_lock(path):
        data = read_json(path, default)
        yield data
        write_json(path, data)


def read_jsonl(path: Path) -> list[Any]:
    out: list[Any] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for ln in lines:
        with contextlib.suppress(ValueError):
            out.append(json.loads(ln))
    return out


def append_jsonl(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path), open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def json_object(v: object) -> dict[str, Any] | None:
    """The JSON value as an object, or None when it is anything else: the one place untyped JSON gets its type."""
    return cast(dict[str, Any], v) if isinstance(v, dict) else None


def json_list(v: object) -> list[Any]:
    """The JSON value as a list; [] when it is anything else."""
    return cast(list[Any], v) if isinstance(v, list) else []


BACKGROUND_WAIT_S = 20  # how long a tick waits for slow work (minutes of tests) before going on without it
BACKGROUND: dict[str, threading.Thread] = {}
STARTED: dict[str, int] = {}  # how many times each key was started


def start_background(key: str, fn: Callable[[], object]) -> threading.Thread | None:
    """Runs `fn` in a thread, one at a time per key; None while the previous one is still running."""
    old = BACKGROUND.get(key)
    if old and old.is_alive():
        return None
    t = threading.Thread(target=fn, daemon=True)
    BACKGROUND[key] = t
    STARTED[key] = STARTED.get(key, 0) + 1
    t.start()
    return t
