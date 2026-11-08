"""Signals from tools (CI, a PR review, an error log) become backlog through small plug-in sources: each executable
in `<home>/sources/` is run every tick with FM_CURSOR (the last cursor it printed, or empty) and prints JSON lines,
`{"key": "...", "title": "...", "detail": "..."}` for a signal, `{"cursor": "..."}` to move its cursor. A signal
becomes a `triage` issue (the owner lets it in), deduplicated by source and key. None is shipped yet: the contract is
the point; the health checks (health.py) are the first producer of findings."""

from __future__ import annotations

import json
import os
import subprocess

from . import backlog, util
from .config import Project


def run(p: Project) -> list[str]:
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
            r = subprocess.run([str(exe)], env={**os.environ, "FM_CURSOR": cursor}, capture_output=True, text=True,
                               timeout=60, check=False)
        except (OSError, subprocess.SubprocessError) as e:
            util.append_jsonl(p.state / "errors.jsonl", {"time": util.now(), "step": f"source {exe.name}",
                                                         "error": repr(e)})
            continue
        for ln in r.stdout.splitlines():
            try:
                s = json.loads(ln)
            except ValueError:
                continue
            if "cursor" in s:
                util.write_text_atomic(cursor_file, str(s["cursor"]))
            elif s.get("key") and s.get("title"):
                iid = backlog.add(p, s["title"], detail=s.get("detail", ""), status="triage",
                                  source=f"{exe.name}:{s['key']}")
                if iid:
                    filed.append(iid)
    return filed
