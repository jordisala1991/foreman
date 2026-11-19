"""The `[repos.<key>]` tables of foreman.toml (DESIGN.md `## Several repos`): read strictly, each repo's settings merged over
the project's key by key, and the layout check. Pure: raises ValueError, `config` turns it into a ProjectError."""

import string
from pathlib import Path
from typing import Any

SETTINGS = ("path", "main", "remote", "worktrees", "post_create", "publish_on_merge", "merge", "risk")
SUB_SETTINGS = {"merge": ("provider", "strategy", "pr", "test", "autonomy"), "risk": ("large_lines", "high", "medium")}
PLACEHOLDERS = ("main", "wt")
KEPT_OUT = (".claude", "CLAUDE.md", "CLAUDE.local.md", "foreman.toml")  # the files a repo of several must stay free of


def parse(raw: dict[str, Any], cfg: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Each repo's settings: the table's own, else the project's (`cfg`, defaults applied), else fm's."""
    if not isinstance(raw.get("repos", {}), dict):
        raise ValueError("[repos] holds [repos.<key>] tables (path, main)")
    tables: dict[str, Any] = raw.get("repos", {})
    if tables and "repo" in raw.get("project", {}):
        raise ValueError("foreman.toml has both [project] repo and [repos.<key>] tables: use one of the two forms")
    if tables and not isinstance(cfg["project"]["worktrees"], str):
        raise ValueError("[project] worktrees is a folder path")
    out: dict[str, dict[str, Any]] = {}
    for key in tables:
        if not isinstance(tables[key], dict) or not isinstance(tables[key].get("path"), str):
            raise ValueError(f"[repos.{key}] needs a path")
        out[key] = _repo(key, tables[key], cfg)
    return out


def _repo(key: str, t: dict[str, Any], cfg: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """One `[repos.<key>]` table (a dict with a path) read strictly, over the project's settings."""
    _check_keys(f"[repos.{key}]", t, SETTINGS)
    repo: dict[str, Any] = {
        "main": str(t.get("main", "main")),
        "remote": "origin",
        "worktrees": cfg["project"]["worktrees"],
        "post_create": [],
        "publish_on_merge": False,
        **{k: v for k, v in t.items() if k not in SUB_SETTINGS and k != "main"},
    }
    if not isinstance(repo["remote"], str):
        raise ValueError(f'[repos.{key}] remote is a remote name, or "none"')
    if not isinstance(repo["worktrees"], str):
        raise ValueError(f"[repos.{key}] worktrees is a folder path")
    for name, allowed in SUB_SETTINGS.items():
        if not isinstance(t.get(name, {}), dict):
            raise ValueError(f"[repos.{key}.{name}] must be a table")
        _check_keys(f"[repos.{key}.{name}]", t.get(name, {}), allowed)
        repo[name] = {**cfg[name], **t.get(name, {})}  # shallow: a repo's high or medium replaces the project's whole
    if not isinstance(t.get("post_create", []), list) or not all(isinstance(c, str) for c in t.get("post_create", [])):
        raise ValueError(f"[repos.{key}] post_create is a list of commands")
    _check_placeholders(key, repo["post_create"])
    return repo


def _check_keys(where: str, table: dict[str, Any], allowed: tuple[str, ...]) -> None:
    for k in table:
        if k not in allowed:
            raise ValueError(f"{where} has no setting '{k}'")


def _check_placeholders(key: str, commands: list[str]) -> None:
    for cmd in commands:
        try:
            names = [name for _, name, _, _ in string.Formatter().parse(cmd)]
        except ValueError as e:
            raise ValueError(f"[repos.{key}] post_create: {e}") from None
        for name in names:
            if name is not None and name not in PLACEHOLDERS:
                raise ValueError(f"[repos.{key}] post_create: unknown placeholder {{{name}}}")


def layout_error(folders: list[tuple[str, Path]], repos: dict[str, Path]) -> str:
    """The first folder (a name and a resolved path) equal to or under a repo, as a message; "" if none is."""
    for what, folder in folders:
        for key, repo in repos.items():
            if folder == repo or repo in folder.parents:
                return f"{what} is inside repo {key}: {folder} is in {repo}"
    return ""


def refusal(keys: list[str], key: str) -> str:
    """Why `--repo KEY` is refused; empty if `key` is one of the project's repo keys."""
    if key in keys:
        return ""
    if not keys:
        return "this project has one repo"
    return f"unknown repo {key!r}: choose one of {', '.join(keys)}"


def stray(paths: list[str]) -> list[str]:
    """The kept-out files among `paths` (as git prints them): each path with a KEPT_OUT component, cut after that
    component, once, in order of first appearance."""
    out: list[str] = []
    for path in paths:
        parts = path.rstrip("/").split("/")
        for i, name in enumerate(parts):
            if name in KEPT_OUT:
                cut = "/".join(parts[: i + 1])
                if cut not in out:
                    out.append(cut)
                break
    return out
