"""A project: a home folder holding `fm.toml` (the owner's settings), `backlog.json` and `state/`; the code lives in
the repo `fm.toml` points to (`[project] repo`, or several `[repos.<key>]` tables: tasks run in the first). The
registry (`~/.config/fm/projects.json`, or $FM_REGISTRY) lists the homes the loop and the panel serve. A project owns
its home, its repos, its worktrees folder and the umbrella folder set in `[project] umbrella`: `fm` run in any of
them works on it (the deepest folder wins)."""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self

from . import recurring_table, repos

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


class ProjectError(Exception):
    """An fm.toml that cannot be read as a project."""


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
        try:
            cfg["repos"] = repos.parse(raw, cfg)
            cfg["recurring"] = recurring_table.parse(raw)
        except ValueError as e:
            raise ProjectError(str(e)) from None
        if cfg["repos"]:  # several repos: tasks run in the first one listed
            first = next(iter(cfg["repos"].values()))
            cfg["project"]["repo"], cfg["project"]["main"] = first["path"], first["main"]
        if not cfg["project"]["name"]:
            cfg["project"]["name"] = home.name
        p = cls(home, cfg)
        if len(cfg["repos"]) > 1:
            p._check_layout()
        return p

    def _check_layout(self) -> None:
        """Several repos: the home, state/ and every worktrees folder stay outside every repo (the umbrella is fine)."""
        project_wt = self.get("project", "worktrees")
        folders = [("the home", self.home), ("state/", self.state), ("the worktrees folder", self.path("worktrees"))]
        for key, r in self.cfg["repos"].items():
            if r["worktrees"] != project_wt:
                folders.append((f"the worktrees folder of repo {key}", self._abs(r["worktrees"])))
        paths = {key: self._abs(r["path"]).resolve() for key, r in self.cfg["repos"].items()}
        if msg := repos.layout_error([(what, f.resolve()) for what, f in folders], paths):
            raise ProjectError(msg)

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


@dataclass
class Unreadable:
    """A registered home whose fm.toml cannot be loaded: it still claims its home and the folders it names
    ([project] repo and umbrella, [repos.<key>] path) when those can be read, so fm refuses there with its reason."""

    home: Path
    reason: str
    folders: list[Path]

    @property
    def name(self) -> str:
        return str(self.home)
