"""A project: a home folder holding `foreman.toml` (the owner's settings), `backlog.json` and `state/`; the code lives in
the repo `foreman.toml` points to (`[project] repo`, or several `[repos.<key>]` tables: a task runs in its own, `for_task`). The
registry (`~/.config/foreman/projects.json`, or $FOREMAN_REGISTRY) lists the homes the loop and the panel serve. A project owns
its home, its repos, its worktrees folder and the umbrella folder set in `[project] umbrella`: `foreman` run in any of
them works on it (the deepest folder wins)."""

import dataclasses
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self

from ... import util
from . import backlog_table, recurring_table, repos

MODELS = ("haiku", "sonnet", "opus", "fable")
EFFORTS = ("low", "medium", "high", "xhigh", "max")

DEFAULTS: dict[str, dict[str, Any]] = {
    "project": {"name": "", "prefix": "FM", "repo": "repo", "main": "main", "worktrees": "worktrees", "branch_prefix": "foreman/", "umbrella": ""},
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
        "runner": "claude",  # which runs the agents' sessions: claude or orca (foreman/providers/runner.py)
    },
    # autonomy: which merge risks go in without the owner's accept: manual (none), low, medium. high never does.
    "merge": {
        "autonomy": "manual",
        "provider": "auto",  # auto (github if the repo's remote is on GitHub, else local) | local | github (foreman/providers/)
        "strategy": "squash",  # squash | merge (--no-ff) | rebase (github only)
        "pr": "draft",  # github: a draft PR from the first pushed commit | done: a PR only at `foreman done`
        "test": "",
    },
    # risk: label -> path patterns; the label is what the reason says. [risk.high] replaces the default table whole.
    # large_lines: a change over this many counted lines (tests, generated and lock files excluded) is at least medium
    "risk": {
        "large_lines": 400,
        "high": {"config, secrets or CI": ["foreman.toml", ".github/", "*secret*", "*.env"]},
        "medium": {"dependencies": ["requirements*.txt", "pyproject.toml", "package.json", "package-lock.json", "*.lock", "go.mod", "Cargo.toml"]},
    },
    "guard": {"ask": []},  # extra rules: [{"name": "deploy", "pattern": "regex on a Bash command"}]
    "owner": {"notify": ""},  # a command the loop runs, with the line as its last argument, for each need that starts (steps/notify.py)
    "health": {"stuck_minutes": 60, "loop_seconds": 30, "ci_every_minutes": 30},
}


class ProjectError(Exception):
    """A foreman.toml that cannot be read as a project."""


@dataclass
class Project:
    home: Path
    cfg: dict[str, dict[str, Any]] = field(default_factory=dict[str, dict[str, Any]])
    board: backlog_table.Board | None = None  # [backlog]: the tasks are the issues of a repo on a Board; None: the local file

    @classmethod
    def load(cls, home: str | Path) -> Self:
        home = Path(home).resolve()
        raw: dict[str, Any] = {}
        f = home / "foreman.toml"
        if f.exists():
            raw = tomllib.loads(f.read_text(encoding="utf-8"))
        cfg: dict[str, dict[str, Any]] = {k: {**v, **raw.get(k, {})} for k, v in DEFAULTS.items()}
        if not isinstance(cfg["owner"]["notify"], str):
            raise ProjectError("[owner] notify is a text: the command to run")
        cfg["resources"] = raw.get("resources", {})  # [resources.NAME]: foreman/services/lock.py
        try:
            cfg["repos"] = repos.parse(raw, cfg)
            cfg["recurring"] = recurring_table.parse(raw)
            board = backlog_table.parse(raw)
        except ValueError as e:
            raise ProjectError(str(e)) from None
        if cfg["repos"]:  # several repos: tasks run in the first one listed
            first = next(iter(cfg["repos"].values()))
            cfg["project"]["repo"], cfg["project"]["main"] = first["path"], first["main"]
        if not cfg["project"]["name"]:
            cfg["project"]["name"] = home.name
        p = cls(home, cfg, board)
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

    def for_task(self, i: Mapping[str, Any]) -> Self:
        """The project as the task sees it (DESIGN.md "One task, one PR, one repo"): `[project]` repo, main, worktrees and
        post_create, `[merge]` and `[risk]` are its repo's; with no `[repos.*]` tables, the project itself."""
        if not self.cfg["repos"]:
            return self
        key = i.get("repo") or next(iter(self.cfg["repos"]))
        if key not in self.cfg["repos"]:
            raise ProjectError(f"repo {key} is not in foreman.toml")
        r = self.cfg["repos"][key]
        own = {
            "repo": r["path"],
            "main": r["main"],
            "worktrees": r["worktrees"],
            "post_create": r["post_create"],
            "remote": r["remote"],
            "repo_key": key,
            "publish_on_merge": r["publish_on_merge"],
        }
        return dataclasses.replace(self, cfg={**self.cfg, "project": {**self.cfg["project"], **own}, "merge": r["merge"], "risk": r["risk"]})

    @property
    def remote(self) -> str:
        """The remote of the repo, "none" for a repo without one (the local provider); the `[project] repo` form says origin."""
        return self.cfg["project"].get("remote", "origin")

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
    def post_create(self) -> list[str]:
        """The commands run once in each new worktree; only `[repos.K]` tables have them."""
        return list(self.cfg["project"].get("post_create", [])) if self.cfg["repos"] else []

    @property
    def repo_keys(self) -> list[str]:
        """The `[repos.K]` keys; none in the `[project] repo` form."""
        return list(self.cfg["repos"])

    @property
    def repo_key(self) -> str:
        """The key of the task's repo in a `for_task` view; empty otherwise."""
        return str(self.cfg["project"].get("repo_key", ""))

    @property
    def publishes(self) -> str:
        """The repo's key when merging into its main publishes it, else empty."""
        return self.repo_key if self.cfg["project"].get("publish_on_merge") else ""

    @property
    def several(self) -> bool:
        """The project has more than one repo."""
        return len(self.cfg["repos"]) > 1

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
    def paused(self) -> str:
        """Why launching is paused: the flag file's text (`yes` when empty); empty when it is not."""
        f = self.state / "paused"
        return f.read_text().strip() or "yes" if f.exists() else ""

    def set_paused(self, on: bool) -> None:
        f = self.state / "paused"
        if on:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(util.now())
        else:
            f.unlink(missing_ok=True)

    @property
    def config_file(self) -> Path:
        return self.home / "foreman.toml"


@dataclass
class Unreadable:
    """A registered home whose foreman.toml cannot be loaded: it still claims its home and the folders it names
    ([project] repo and umbrella, [repos.<key>] path) when those can be read, so fm refuses there with its reason."""

    home: Path
    reason: str
    folders: list[Path]

    @property
    def name(self) -> str:
        return str(self.home)
