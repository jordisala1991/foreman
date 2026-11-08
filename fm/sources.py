"""Signals from tools (CI, an error log, a pull request's activity) become backlog or reach it through small plug-in
sources: each executable in `<home>/sources/` is run every tick with FM_CURSOR (the last cursor it printed, or empty)
and prints JSON lines. `{"cursor": "..."}` moves its cursor. A signal always has a "key" (deduplication):
  - without "task": `{"key", "title", "detail"}` files a `triage` issue (the owner lets it in), deduplicated by
    source and key. For what has no task yet: CI failing on main, an error log.
  - with "task" (an issue id): `{"key", "task", "action", "text"}` acts on that open task, with the owner's own
    commands: note (text reaches its agent like an answer), reject (text; `fm reject`), accept (`fm accept`),
    done (closed as done: merged outside fm), cancel (`fm cancel`; text is the reason). A review on a task's own
    change targets that task; it is not a new issue. Applied once per source and key (`state/applied/`); an unknown
    or closed task, an unknown action, a missing key or one not valid for the task's state changes nothing and is logged
    in `state/errors.jsonl` (and is not retried); a transient failure (accept before its check, a busy checkout, a
    hung stop) is logged and the same key is tried again next tick. None is shipped yet: the contract is the point; the health checks (health.py) are the
    first producer of findings."""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any

from . import backlog, integrate, util
from .config import Project


def run(p: Project) -> list[str]:  # noqa: C901, PLR0912
    folder = p.home / "sources"
    filed: list[str] = []
    if not folder.is_dir():
        return filed
    for exe in sorted(folder.iterdir()):
        if not exe.is_file() or not os.access(exe, os.X_OK):
            continue
        cursor_file = p.state / "cursors" / exe.name
        cursor = cursor_file.read_text().strip() if cursor_file.exists() else ""
        try:
            r = subprocess.run([str(exe)], env={**os.environ, "FM_CURSOR": cursor}, capture_output=True, text=True, timeout=60, check=False)
        except (OSError, subprocess.SubprocessError) as e:
            util.append_jsonl(p.state / "errors.jsonl", {"time": util.now(), "step": f"source {exe.name}", "error": repr(e)})
            continue
        for ln in r.stdout.splitlines():
            try:
                raw = json.loads(ln)
            except ValueError:
                continue
            s = util.sub({"s": raw}, "s")
            if not isinstance(raw, dict):
                fail(p, exe.name, "a signal must be a JSON object", ln)
                continue
            if "cursor" in s:
                util.write_text_atomic(cursor_file, str(s["cursor"]))
            elif s.get("task") and not s.get("key"):
                fail(p, exe.name, "a signal with a task needs a key", s)
            elif s.get("task"):
                key = str(s["key"])
                applied = p.state / "applied" / exe.name
                if key in {x.get("key") for x in util.read_jsonl(applied)}:
                    continue
                try:
                    apply(p, f"{exe.name}:{key}", s)
                except (backlog.BacklogError, ValueError) as e:  # permanent: it will not become valid by waiting
                    fail(p, exe.name, str(e), s)
                except Exception as e:  # noqa: BLE001 - transient (a busy checkout, a hung stop): tried again next tick
                    logged = p.state / "retrying" / exe.name  # one line per key, not one per tick
                    if key not in {x.get("key") for x in util.read_jsonl(logged)}:
                        util.append_jsonl(logged, {"key": key, "at": util.now()})
                        fail(p, exe.name, f"{e!r} (will be tried again)", s)
                    continue
                util.append_jsonl(applied, {"key": key, "at": util.now()})
            elif s.get("key") and s.get("title"):
                iid = backlog.add(p, s["title"], detail=s.get("detail", ""), status="triage", source=f"{exe.name}:{s['key']}")
                if iid:
                    filed.append(iid)
    return filed


def fail(p: Project, name: str, error: str, signal: object) -> None:
    util.append_jsonl(p.state / "errors.jsonl", {"time": util.now(), "step": f"source {name}", "error": error, "signal": signal})


ACTIONS = ("note", "reject", "accept", "done", "cancel")


def apply(p: Project, source: str, s: dict[str, Any]) -> None:
    """One signal on a task, through the owner's command path. Raises when it is not valid: nothing changed."""
    from . import loop  # late: loop imports this module

    iid, action, text = str(s["task"]), s.get("action"), str(s.get("text") or "")
    i = backlog.get(backlog.load(p), iid)
    status = i["status"]
    if action not in ACTIONS:
        raise ValueError(f"{iid}: unknown action {action!r}")
    if status in backlog.CLOSED:
        raise ValueError(f"{iid} is {status}: closed")
    if action in ("note", "reject") and not text:
        raise ValueError(f"{iid}: {action} needs text")
    if action == "note":
        if status not in (*backlog.RUNNING, "in review"):
            raise ValueError(f"{iid} is {status}: no agent to tell")
        backlog.note(p, iid, text, source)
    elif action in ("reject", "accept"):
        if status != "in review":
            raise ValueError(f"{iid} is {status}, not in the integration queue")
        if action == "reject":
            loop.send_back(p, iid, "rejection", text)
        else:
            integrate.accept(p, iid, by=source)
    elif action == "done":
        integrate.close(p, iid, "done", text or f"merged outside fm ({source})")
    else:
        integrate.close(p, iid, "canceled", text or f"by {source}")
