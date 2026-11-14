"""The project's LESSONS.md: what misled an agent, kept for the next agents."""

import re
from datetime import date
from pathlib import Path

from . import util
from .config import Project


def lesson(p: Project, tid: str, who: str, text: str) -> None:
    """What misled an agent, kept in the project's LESSONS.md for the next agents (the owner prunes it)."""
    t = " ".join((text or "").split())
    if not t or re.match(r"(?i)(none|nothing|n/a|-)\b", t):
        return
    f = ensure_lessons(p)
    with util.file_lock(f):
        util.write_text_atomic(f, f.read_text(encoding="utf-8") + f"- {date.today().isoformat()} {tid} ({who}): {t}\n")


def ensure_lessons(p: Project) -> Path:
    if not p.lessons_file.exists():
        util.write_text_atomic(
            p.lessons_file, "# Lessons\n\nWhat misled earlier agents in this project, in their words. The owner prunes it; agents read it before starting.\n\n"
        )
    return p.lessons_file
