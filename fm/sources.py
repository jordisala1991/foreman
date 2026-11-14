"""Signals from tools (CI, an error log, a pull request's activity) become backlog or reach it through small plug-in
sources: each executable in `<home>/sources/` is run every tick with FM_CURSOR (the last cursor it printed, or empty)
and prints JSON lines. `{"cursor": "..."}` moves its cursor. A signal always has a "key" (deduplication):
  - without "task": `{"key", "title", "detail"}` files a `triage` task (the owner lets it in), deduplicated by
    source and key. For what has no task yet: CI failing on main, an error log.
  - with "task" (a task id): `{"key", "task", "action", "text"}` acts on that open task, with the owner's own
    commands: note (text reaches its agent like an answer), reject (text; `fm reject`), accept (`fm accept`),
    done (closed as done: merged outside fm), cancel (`fm cancel`; text is the reason). A review on a task's own
    change targets that task; it is not a new task. Applied once per source and key (`state/applied/`); an unknown
    or closed task, an unknown action, a missing key or one not valid for the task's state changes nothing and is logged
    in `state/errors.jsonl` (and is not retried); a transient failure (accept before its check, a busy checkout, a
    hung stop) is logged and the same key is tried again next tick. None is shipped yet: the contract is the point; the health checks (health.py) are the
    first producer of findings."""

import json
import os
from pathlib import Path
from typing import Any

from . import backlog, delivery, proc, util
from .config import Project
from .talk import return_to_agent


def run(p: Project) -> list[str]:
    folder = p.home / "sources"
    filed: list[str] = []
    if not folder.is_dir():
        return filed
    for exe in sorted(folder.iterdir()):
        if exe.is_file() and os.access(exe, os.X_OK):
            filed += _poll(p, exe)
    return filed


def _poll(p: Project, exe: Path) -> list[str]:
    """Runs one source once: its signals are handled in order; the ids of the triage tasks it filed."""
    cursor_file = p.state / "cursors" / exe.name
    cursor = cursor_file.read_text().strip() if cursor_file.exists() else ""
    try:
        r = proc.run([str(exe)], env={**os.environ, "FM_CURSOR": cursor})
    except proc.ProcError as e:
        util.append_jsonl(p.state / "errors.jsonl", {"time": util.now(), "step": f"source {exe.name}", "error": str(e)})
        return []
    ids = (_signal(p, exe.name, ln, cursor_file) for ln in r.out.splitlines())
    return [tid for tid in ids if tid]


def _signal(p: Project, name: str, ln: str, cursor_file: Path) -> str | None:
    """One line of a source's output; the id of the task it filed, if it filed one."""
    try:
        s = util.json_object(json.loads(ln))
    except ValueError:
        return None
    if s is None:
        fail(p, name, "a signal must be a JSON object", ln)
        return None
    return _handle(p, name, s, cursor_file)


def _handle(p: Project, name: str, s: dict[str, Any], cursor_file: Path) -> str | None:
    if "cursor" in s:
        util.write_text_atomic(cursor_file, str(s["cursor"]))
    elif s.get("task") and not s.get("key"):
        fail(p, name, "a signal with a task needs a key", s)
    elif s.get("task"):
        _on_task(p, name, s)
    elif s.get("key") and s.get("title"):
        return backlog.add(p, s["title"], detail=s.get("detail", ""), status="triage", source=f"{name}:{s['key']}") or None
    return None


def _on_task(p: Project, name: str, s: dict[str, Any]) -> None:
    """A signal that targets an open task: applied once per key."""
    key = str(s["key"])
    applied = p.state / "applied" / name
    if key in {x.get("key") for x in util.read_jsonl(applied)}:
        return
    try:
        apply(p, f"{name}:{key}", s)
    except (backlog.BacklogError, ValueError) as e:  # permanent: it will not become valid by waiting
        fail(p, name, str(e), s)
    except Exception as e:  # transient (a busy checkout, a hung stop): tried again next tick
        logged = p.state / "retrying" / name  # one line per key, not one per tick
        if key not in {x.get("key") for x in util.read_jsonl(logged)}:
            util.append_jsonl(logged, {"key": key, "at": util.now()})
            fail(p, name, f"{e!r} (will be tried again)", s)
        return
    util.append_jsonl(applied, {"key": key, "at": util.now()})


def fail(p: Project, name: str, error: str, signal: object) -> None:
    util.append_jsonl(p.state / "errors.jsonl", {"time": util.now(), "step": f"source {name}", "error": error, "signal": signal})


ACTIONS = ("note", "reject", "accept", "done", "cancel")


def apply(p: Project, source: str, s: dict[str, Any]) -> None:
    """One signal on a task, through the owner's command path. Raises when it is not valid: nothing changed."""
    tid, action, text = str(s["task"]), s.get("action"), str(s.get("text") or "")
    i = backlog.get(backlog.load(p), tid)
    status = i["status"]
    if action not in ACTIONS:
        raise ValueError(f"{tid}: unknown action {action!r}")
    if status in backlog.CLOSED:
        raise ValueError(f"{tid} is {status}: closed")
    if action in ("note", "reject") and not text:
        raise ValueError(f"{tid}: {action} needs text")
    if action == "note":
        if status not in (*backlog.RUNNING, "in review"):
            raise ValueError(f"{tid} is {status}: no agent to tell")
        backlog.note(p, tid, text, source)
    elif action in ("reject", "accept"):
        if status != "in review":
            raise ValueError(f"{tid} is {status}, not in review")
        if action == "reject":
            return_to_agent(p, tid, "rejection", text)
        else:
            delivery.accept(p, tid, by=source)
    elif action == "done":
        delivery.close(p, tid, "done", text or f"merged outside fm ({source})")
    else:
        delivery.close(p, tid, "canceled", text or f"by {source}")
