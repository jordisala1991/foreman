"""The `[backlog]` table of foreman.toml (core/project/backlog_table.py): a project without it keeps its local file."""

import pytest
from helpers import Env

from foreman.core.project.backlog_table import Board
from foreman.core.project.config import Project, ProjectError


def with_table(e: Env, table: str) -> Project:
    with (e.home / "foreman.toml").open("a") as f:
        f.write(f"[backlog]\n{table}\n")
    return Project.load(e.home)


def test_no_table_no_board():
    assert Env().p.board is None


def test_the_board():
    assert with_table(Env(), 'kind = "github"\nrepo = "o/r"\nproject = 3').board == Board("o/r", "o", 3)
    assert with_table(Env(), 'kind = "github"\nrepo = "o/r"\nproject = 3\nowner = "me"').board == Board("o/r", "me", 3)


@pytest.mark.parametrize(
    ("table", "named"),
    [
        ('kind = "gitlab"\nrepo = "o/r"\nproject = 1', "kind"),
        ('kind = "github"\nrepo = "x"\nproject = 1', "repo"),
        ('kind = "github"\nrepo = "o/r"\nproject = 0', "project"),
        ('kind = "github"\nrepo = "o/r"\nproject = true', "project"),
        ('kind = "github"\nrepo = "o/r"\nproject = 1\nowner = ""', "owner"),
        ('kind = "github"\nrepo = "o/r"\nproject = 1\nfoo = 1', "foo"),
    ],
)
def test_a_bad_table_is_refused(table: str, named: str):
    with pytest.raises(ProjectError, match=named):
        with_table(Env(), table)
