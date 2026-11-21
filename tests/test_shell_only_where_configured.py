"""Only services/agents/worktree.py and services/delivery/check.py run a shell (sh -c on a command the project configures).
Any other module that names a shell can hide git, gh or claude in a string the adapter-only tests cannot see."""

import ast

import pytest
from helpers import PLUGIN

ALLOWED = {"services/agents/worktree.py", "services/delivery/check.py"}
SHELLS = {"sh", "bash", "zsh"}


def names_shell(source: str) -> bool:
    return any(isinstance(n, ast.Constant) and n.value in SHELLS for n in ast.walk(ast.parse(source)))


def test_only_the_two_configured_command_modules_name_a_shell():
    root = PLUGIN / "foreman"
    found = {f.relative_to(root).as_posix() for f in root.rglob("*.py") if names_shell(f.read_text())}
    assert found == ALLOWED


@pytest.mark.parametrize("shell", sorted(SHELLS))
def test_a_shell_string_is_found(shell: str):
    assert names_shell(f'proc.run(["{shell}", "-c", "git push && gh pr list"])\n')


def test_a_module_without_a_shell_is_not_found():
    assert not names_shell('proc.run(["git", "status"])\n')


def test_a_longer_name_is_not_found():
    assert not names_shell('x = "shell"\ny = "bashful"\n')
