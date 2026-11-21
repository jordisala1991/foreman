"""The `[backlog]` table of foreman.toml (DESIGN.md «The backlog on GitHub»): where a project keeps its backlog when it is not
the local file. Pure: raises ValueError, `config` turns it into a ProjectError."""

import re
from dataclasses import dataclass
from typing import Any

from ... import util

SETTINGS = ("kind", "repo", "project", "owner")


@dataclass(frozen=True)
class Board:
    """The repo whose issues are the tasks, and the Board (a GitHub Project) that holds their fields."""

    repo: str  # owner/name
    owner: str  # the Board owner's login
    number: int


def table(b: Board) -> str:
    """The `[backlog]` table the cutover appends to foreman.toml (`parse` reads it back as `b`); `owner` only when it is
    not the repo's owner."""
    owner = f'owner = "{b.owner}"\n' if b.owner != b.repo.split("/")[0] else ""
    return f'\n# cutover: delete this table to point fm back at its local file\n[backlog]\nkind = "github"\nrepo = "{b.repo}"\nproject = {b.number}\n{owner}'


def parse(raw: dict[str, Any]) -> Board | None:
    """The project's Board; None without a `[backlog]` table."""
    return board(raw["backlog"]) if "backlog" in raw else None


def board(raw: object) -> Board:
    """The Board a `[backlog]` table names (also `foreman backlog cutover`'s options)."""
    table = util.json_object(raw)
    if table is None:
        raise ValueError("[backlog] is a table")
    for k in table:
        if k not in SETTINGS:
            raise ValueError(f"[backlog] has no setting '{k}'")
    if table.get("kind") != "github":
        raise ValueError('[backlog] kind = "github" is the only kind')
    repo = table.get("repo")
    if not isinstance(repo, str) or not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise ValueError('[backlog] repo is "owner/name"')
    number = table.get("project")
    if not isinstance(number, int) or isinstance(number, bool) or number < 1:
        raise ValueError("[backlog] project is the Board's number (a whole number of 1 or more)")
    owner = table.get("owner", repo.split("/")[0])
    if not isinstance(owner, str) or not owner:
        raise ValueError("[backlog] owner is the Board owner's login (a non-empty text)")
    return Board(repo, owner, number)
