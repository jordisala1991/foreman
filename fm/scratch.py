"""A review's own scratch folder, `<home>/scratch/ID-rN-<tip9>-ROLE`: made at launch, removed by the loop's sweep."""

import shutil
from pathlib import Path

from .core.project.config import Project


def folder(p: Project, tid: str, rnd: int, tip: str, role: str) -> Path:
    return p.home / "scratch" / f"{tid}-r{rnd}-{tip[:9]}-{role}"


def make(p: Project, tid: str, rnd: int, tip: str, role: str) -> Path:
    """The folder, empty (a relaunch of the same review starts clean)."""
    f = folder(p, tid, rnd, tip, role)
    shutil.rmtree(f, ignore_errors=True)
    f.mkdir(parents=True)
    return f


def sweep(p: Project, keep: set[Path]) -> None:
    """Removes every scratch folder not in `keep`."""
    root = p.home / "scratch"
    if not root.is_dir():
        return
    for d in root.iterdir():
        if d.is_dir() and d not in keep:
            shutil.rmtree(d, ignore_errors=True)
