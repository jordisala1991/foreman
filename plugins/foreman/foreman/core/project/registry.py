"""The registry of projects (`~/.config/foreman/projects.json`, or $FOREMAN_REGISTRY): the homes the loop and the panel serve, and
who owns a folder."""

import os
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ... import util
from .config import Project, Unreadable


def registry_file() -> Path:
    return Path(os.environ.get("FOREMAN_REGISTRY") or Path.home() / ".config/foreman/projects.json")


def python_note_file() -> Path:
    return Path(str(registry_file()) + ".python")


def registered() -> list[Path]:
    return [Path(p) for p in util.read_json(registry_file(), [])]


def register(home: Path) -> None:
    with util.locked_json(registry_file(), []) as homes:
        if str(home.resolve()) not in homes:
            homes.append(str(home.resolve()))


def loaded() -> list[Project]:
    """Every registered project; one whose foreman.toml cannot be read is left out so the others are served."""
    return load_all()[0]


def _named_folders(home: Path) -> list[Path]:
    try:
        raw: dict[str, Any] = tomllib.loads((home / "foreman.toml").read_text(encoding="utf-8"))
    except Exception:  # an foreman.toml that cannot be read names no folder
        return [home]
    try:
        keys: list[Any] = list(raw["repos"])
    except Exception:  # no [repos], or not a table
        keys = []
    named = [("project", "repo"), ("project", "umbrella"), *(("repos", k, "path") for k in keys)]
    return list(dict.fromkeys([home, *(f for at in named for f in _folder_at(home, raw, at))]))


def _folder_at(home: Path, raw: dict[str, Any], at: tuple[Any, ...]) -> list[Path]:
    """The folder at that place of foreman.toml, or none when it is missing or cannot be read as a path."""
    try:
        v: Any = raw
        for k in at:
            v = v[k]
        p = Path(v).expanduser()
        return [(p if p.is_absolute() else home / p).resolve()]
    except Exception:  # a bad entry is skipped; the others are kept
        return []


def load_all() -> tuple[list[Project], list[Unreadable]]:
    """Every registered project, and every home whose foreman.toml cannot be loaded."""
    out: list[Project] = []
    broken: list[Unreadable] = []
    for home in registered():
        try:
            out.append(Project.load(home))
        except Exception as e:  # a broken foreman.toml leaves the others served
            broken.append(Unreadable(home.resolve(), str(e), _named_folders(home.resolve())))
    return out, broken


def owners(projects: Sequence[Project | Unreadable], here: Path) -> list[tuple[Project | Unreadable, Path]]:
    """The projects owning `here` through their deepest folder, only the deepest of all: none, one, or several
    claiming the same folder."""
    here = here.resolve()
    found: list[tuple[Project | Unreadable, Path]] = []
    for p in projects:
        mine = [f for f in p.folders if f == here or f in here.parents]
        if mine:
            found.append((p, max(mine, key=lambda f: len(f.parts))))
    depth = max((len(f.parts) for _, f in found), default=0)
    return [(p, f) for p, f in found if len(f.parts) == depth]


def shared(projects: list[Project]) -> dict[Path, list[str]]:
    """Folders claimed by two or more projects, with their names."""
    claims: dict[Path, list[str]] = {}
    for p in projects:
        for f in p.folders:
            claims.setdefault(f, []).append(p.name)
    return {f: names for f, names in claims.items() if len(names) > 1}
