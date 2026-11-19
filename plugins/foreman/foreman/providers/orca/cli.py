"""Orca's command line, `orca … --json`: every call the adapter makes goes through `orca`. Orca answers
`{"ok": true, "result": …}` or `{"ok": false, "error": {"code", "message"}}`, and sometimes exits 0 on an error, so the
answer's `ok` decides, never the exit code alone."""

import json
import os
import shutil
from typing import Any

from ... import proc, util
from ..runner import RunnerError, RunnerUnavailable

APP_BIN = "/Applications/Orca.app/Contents/Resources/bin/orca"  # launchd's PATH is short
OPEN_TIMEOUT = 120


class OrcaError(RunnerError):
    """Orca answered a call with an error; `code` is Orca's own (`selector_not_found`, `dispatch_not_found`…)."""

    def __init__(self, message: str, code: str = "") -> None:
        super().__init__(message)
        self.code = code


def binary() -> str:
    return os.environ.get("ORCA_BIN") or shutil.which("orca") or APP_BIN


def orca(args: list[str], timeout: float = 60) -> dict[str, Any]:
    """One call's `result`. Orca not running, or the binary missing → RunnerUnavailable; any other error → OrcaError."""
    try:
        r = proc.run([binary(), *args, "--json"], timeout=timeout)
    except proc.ProcError as e:
        raise RunnerUnavailable(f"orca {args[0]}: {e}") from e
    answer = util.json_object(_json(r.out)) or {}
    if answer.get("ok") is True:
        return util.json_object(answer.get("result")) or {}
    err = util.json_object(answer.get("error")) or {}
    code = str(err.get("code") or "")
    why = " ".join(str(err.get("message") or r.err or r.out or f"exit {r.code}").split())[:300]
    if code == "runtime_unavailable":
        raise RunnerUnavailable(f"Orca is not running: {why}")
    raise OrcaError(f"orca {' '.join(args[:2])}: {why}", code)


def _json(out: str) -> object:
    try:
        return json.loads(out)
    except ValueError:
        return None


def running() -> bool:
    """Orca's app is up (`orca status` answers even when it is not)."""
    try:
        app = util.json_object(orca(["status"]).get("app")) or {}
    except RunnerError:
        return False
    return app.get("running") is True


def ensure_running() -> None:
    """Opens Orca when it is not running (the owner allows it, Q3) and waits for it; RunnerUnavailable when it stays down."""
    if running():
        return
    try:
        orca(["open"], timeout=OPEN_TIMEOUT)
    except RunnerError as e:
        raise RunnerUnavailable(f"Orca is not running and could not be opened: {e}") from e
    if not running():
        raise RunnerUnavailable("Orca is not running and could not be opened")
