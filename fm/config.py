"""A project: a home folder holding `fm.toml` (the owner's settings), `backlog.json` and `state/`; the code lives in
the repo `fm.toml` points to. The registry (`~/.config/fm/projects.json`, or $FM_REGISTRY) lists the homes the loop
and the panel serve."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import util

DEFAULTS: dict[str, dict[str, Any]] = {
    "project": {"name": "", "prefix": "FM", "repo": "repo", "main": "main", "worktrees": "worktrees",
                "branch_prefix": "fm/"},
    # slots: how many agents of this project run at once (defining and responsible agents alike)
    "agents": {"slots": 2, "model": "sonnet", "define_model": "sonnet", "review_model": "sonnet", "settings": {},
               "claude": "claude"},
    # autonomy: which merge risks go in without the owner's accept: manual (none), low, medium. high never does.
    "integrate": {
        "autonomy": "manual",
        "test": "",
        "sensitive": ["fm.toml", ".github/", "*.lock", "*secret*", "*.env"],
        "small_lines": 80,
        "medium_lines": 300,
    },
    "guard": {"ask": []},  # extra rules: [{"name": "deploy", "pattern": "regex on a Bash command"}]
    "health": {"stuck_minutes": 60, "loop_seconds": 30, "ci_every_minutes": 30},
}


def registry_file() -> Path:
    return Path(os.environ.get("FM_REGISTRY") or Path.home() / ".config/fm/projects.json")


def registered() -> list[Path]:
    return [Path(p) for p in util.read_json(registry_file(), [])]


def register(home: Path) -> None:
    with util.locked_json(registry_file(), []) as homes:
        if str(home.resolve()) not in homes:
            homes.append(str(home.resolve()))


@dataclass
class Project:
    home: Path
    cfg: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def load(cls, home: str | Path) -> Project:
        home = Path(home).resolve()
        raw: dict[str, Any] = {}
        f = home / "fm.toml"
        if f.exists():
            raw = tomllib.loads(f.read_text(encoding="utf-8"))
        cfg = {k: {**v, **raw.get(k, {})} for k, v in DEFAULTS.items()}
        cfg["resources"] = raw.get("resources", {})  # [resources.NAME]: fm/lock.py
        if not cfg["project"]["name"]:
            cfg["project"]["name"] = home.name
        return cls(home, cfg)

    def get(self, section: str, key: str) -> Any:
        return self.cfg[section][key]

    def path(self, key: str) -> Path:
        p = Path(self.get("project", key)).expanduser()
        return p if p.is_absolute() else self.home / p

    @property
    def name(self) -> str:
        return str(self.get("project", "name"))

    @property
    def repo(self) -> Path:
        return self.path("repo")

    @property
    def state(self) -> Path:
        return self.home / "state"

    @property
    def config_file(self) -> Path:
        return self.home / "fm.toml"

    @property
    def lessons_file(self) -> Path:
        return self.home / "LESSONS.md"

    @property
    def backlog_file(self) -> Path:
        return self.home / "backlog.json"
