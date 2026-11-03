"""What the panel reads of a project's transient state: foreman's own state folder (<state>/agents.json,
progress.jsonl, inbox.json, heartbeats.json, pause, report.md, session), written by the hooks and the CLI.
ProjectState only opens files for reading.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from . import util

if TYPE_CHECKING:
    from .config import Project


def read_jsonl_tail(path: util.PathLike, n: int) -> list[Any]:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()[-n:]
    except OSError:
        return []
    out: list[Any] = []
    for ln in lines:
        with contextlib.suppress(ValueError):
            out.append(json.loads(ln))
    return out


class ProjectState:
    FILES: ClassVar[dict[str, str]] = {
        "agents": "agents.json",
        "progress": "progress.jsonl",
        "inbox": "inbox.json",
        "heartbeats": "heartbeats.json",
        "pause": "pause",
        "report": "report.md",
        "session": "session",
    }

    def __init__(self, project: Project) -> None:
        self.project = project
        self.state = project.state

    def path(self, key: str) -> Path:
        return self.state / self.FILES[key]

    def agents(self) -> dict[str, Any]:
        """{agent id: {start, end, type, transcript, tool, activity, stopped, final, name}}."""
        d = util.read_json(self.path("agents"), {})
        return d if isinstance(d, dict) else {}

    def progress(self, n: int = 40) -> list[Any]:
        """[{time, agent, text}], newest first."""
        return read_jsonl_tail(self.path("progress"), n)[::-1]

    def inbox(self) -> list[Any]:
        """[{id, time, from: owner|orchestrator, text, read, decision?, move?, approve?}]."""
        d = util.read_json(self.path("inbox"), [])
        return d if isinstance(d, list) else []

    def heartbeats(self) -> dict[str, Any]:
        d = util.read_json(self.path("heartbeats"), {})
        return d if isinstance(d, dict) else {}

    def _text(self, key: str) -> str | None:
        try:
            with open(self.path(key), encoding="utf-8", errors="replace") as f:
                return f.read()
        except OSError:
            return None

    def paused(self) -> str | None:
        t = self._text("pause")
        return t.strip() or "paused" if t is not None else None

    def orchestrator(self) -> dict[str, Any] | None:
        """{session, name, since} of the marked orchestrator session, or None (foreman session)."""
        from . import orchestrator

        return orchestrator.current(self.project)

    def report(self) -> dict[str, Any] | None:
        t = self._text("report")
        if t is None:
            return None
        # when it was written, not its age: the panel's polled state keeps its ETag while nothing changes
        try:
            return {"text": t, "ts": int(self.path("report").stat().st_mtime)}
        except OSError:
            return None
