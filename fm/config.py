"""A project: a home folder holding `fm.toml` (the owner's settings), `backlog.json` and `state/`; the code lives in
the repo `fm.toml` points to (`[project] repo`, or several `[repos.<key>]` tables: tasks run in the first). The
registry (`~/.config/fm/projects.json`, or $FM_REGISTRY) lists the homes the loop and the panel serve. A project owns
its home, its repos, its worktrees folder and the umbrella folder set in `[project] umbrella`: `fm` run in any of
them works on it (the deepest folder wins)."""

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self

from . import util

MODELS = ("haiku", "sonnet", "opus", "fable")
EFFORTS = ("low", "medium", "high", "xhigh", "max")

DEFAULTS: dict[str, dict[str, Any]] = {
    "project": {"name": "", "prefix": "FM", "repo": "repo", "main": "main", "worktrees": "worktrees", "branch_prefix": "fm/", "umbrella": ""},
    # slots: how many agents of this project run at once (planning and responsible agents alike)
    # every agent kind gets an explicit model and effort (claude --effort), never the machine's Claude settings
    "agents": {
        "slots": 2,
        "model": "sonnet",
        "planning_model": "sonnet",
        "planning_effort": "high",
        "effort": "medium",
        "review_model": "sonnet",
        "review_effort": "high",
        "settings": {},
        "claude": "claude",
    },
    # autonomy: which merge risks go in without the owner's accept: manual (none), low, medium. high never does.
    "merge": {
        "autonomy": "manual",
        "provider": "auto",  # auto (github if origin is on GitHub, else local) | local | github (fm/providers/)
        "strategy": "squash",  # squash | merge (--no-ff) | rebase (github only)
        "pr": "draft",  # github: a draft PR from the first pushed commit | done: a PR only at `fm done`
        "test": "",
    },
    # risk: label -> path patterns; the label is what the reason says. [risk.high] replaces the default table whole.
    # large_lines: a change over this many counted lines (tests, generated and lock files excluded) is at least medium
    "risk": {
        "large_lines": 400,
        "high": {"config, secrets or CI": ["fm.toml", ".github/", "*secret*", "*.env"]},
        "medium": {"dependencies": ["requirements*.txt", "pyproject.toml", "package.json", "package-lock.json", "*.lock", "go.mod", "Cargo.toml"]},
    },
    "guard": {"ask": []},  # extra rules: [{"name": "deploy", "pattern": "regex on a Bash command"}]
    "health": {"stuck_minutes": 60, "loop_seconds": 30, "ci_every_minutes": 30},
}


def registry_file() -> Path:
    return Path(os.environ.get("FM_REGISTRY") or Path.home() / ".config/fm/projects.json")


def registered() -> list[Path]:
    return [Path(p) for p in util.read_json(registry_file(), [])]


class ProjectError(Exception):
    """An fm.toml that cannot be read as a project."""


def register(home: Path) -> None:
    with util.locked_json(registry_file(), []) as homes:
        if str(home.resolve()) not in homes:
            homes.append(str(home.resolve()))


@dataclass
class Project:
    home: Path
    cfg: dict[str, dict[str, Any]] = field(default_factory=dict[str, dict[str, Any]])

    @classmethod
    def load(cls, home: str | Path) -> Self:
        home = Path(home).resolve()
        raw: dict[str, Any] = {}
        f = home / "fm.toml"
        if f.exists():
            raw = tomllib.loads(f.read_text(encoding="utf-8"))
        cfg: dict[str, dict[str, Any]] = {k: {**v, **raw.get(k, {})} for k, v in DEFAULTS.items()}
        cfg["resources"] = raw.get("resources", {})  # [resources.NAME]: fm/lock.py
        cfg["repos"] = _repos(raw)
        if cfg["repos"]:  # several repos: tasks run in the first one listed
            first = next(iter(cfg["repos"].values()))
            cfg["project"]["repo"], cfg["project"]["main"] = first["path"], first["main"]
        if not cfg["project"]["name"]:
            cfg["project"]["name"] = home.name
        return cls(home, cfg)

    def get(self, section: str, key: str) -> Any:
        return self.cfg[section][key]

    def path(self, key: str) -> Path:
        return self._abs(self.get("project", key))

    def _abs(self, s: str) -> Path:
        p = Path(s).expanduser()
        return p if p.is_absolute() else self.home / p

    @property
    def name(self) -> str:
        return str(self.get("project", "name"))

    @property
    def repo(self) -> Path:
        return self.path("repo")

    @property
    def repos(self) -> list[Path]:
        return [self._abs(r["path"]) for r in self.cfg["repos"].values()] or [self.repo]

    @property
    def folders(self) -> list[Path]:
        """The folders this project owns, resolved: home, repos, worktrees folder, umbrella."""
        umbrella = [self.path("umbrella")] if self.get("project", "umbrella") else []
        return list(dict.fromkeys(f.resolve() for f in [self.home, *self.repos, self.path("worktrees"), *umbrella]))

    @property
    def state(self) -> Path:
        return self.home / "state"

    @property
    def config_file(self) -> Path:
        return self.home / "fm.toml"

    @property
    def backlog_file(self) -> Path:
        return self.home / "backlog.json"


def _repos(raw: dict[str, Any]) -> dict[str, dict[str, str]]:
    if not isinstance(raw.get("repos", {}), dict):
        raise ProjectError("[repos] holds [repos.<key>] tables (path, main)")
    tables: dict[str, Any] = raw.get("repos", {})
    if tables and "repo" in raw.get("project", {}):
        raise ProjectError("fm.toml has both [project] repo and [repos.<key>] tables: use one of the two forms")
    out: dict[str, dict[str, str]] = {}
    for key in tables:
        if not isinstance(tables[key], dict) or not isinstance(tables[key].get("path"), str):
            raise ProjectError(f"[repos.{key}] needs a path")
        out[key] = {"path": tables[key]["path"], "main": str(tables[key].get("main", "main"))}
    return out


def loaded() -> list[Project]:
    """Every registered project; one whose fm.toml cannot be read is left out so the others are served."""
    return load_all()[0]


def load_all() -> tuple[list[Project], list[str]]:
    """Every registered project, and one line per home whose fm.toml cannot be read (its home and why)."""
    out: list[Project] = []
    broken: list[str] = []
    for home in registered():
        try:
            out.append(Project.load(home))
        except Exception as e:  # a broken fm.toml leaves the others served
            broken.append(f"{home}: {e}")
    return out, broken


def owners(projects: list[Project], here: Path) -> list[tuple[Project, Path]]:
    """The projects owning `here` through their deepest folder, only the deepest of all: none, one, or several
    claiming the same folder."""
    here = here.resolve()
    found: list[tuple[Project, Path]] = []
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
