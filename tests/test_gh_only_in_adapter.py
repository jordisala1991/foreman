"""Only the GitHub adapter runs gh (foreman/providers/github/provider.py and review.py); services orchestrate through the
port. The guard (core/oversight/guard.py) names it to parse command lines and runs nothing. A module that names the program
"gh" is one that runs it."""

import ast

from helpers import PLUGIN

ALLOWED = {"providers/github/provider.py", "providers/github/review.py", "core/oversight/guard.py"}


def names_gh(source: str) -> bool:
    return any(isinstance(n, ast.Constant) and n.value == "gh" for n in ast.walk(ast.parse(source)))


def test_only_the_github_adapter_names_gh():
    root = PLUGIN / "foreman"
    found = {f.relative_to(root).as_posix() for f in root.rglob("*.py") if names_gh(f.read_text())}
    assert found == ALLOWED


def test_a_module_that_runs_the_program_is_found():
    assert names_gh('from foreman import proc\nproc.run(["gh", "pr", "list"])\n')


def test_a_module_without_gh_is_not_found():
    assert not names_gh('proc.run(["git", "status"])\n')


def test_a_longer_name_is_not_found():
    assert not names_gh('x = "ghost"\n')
