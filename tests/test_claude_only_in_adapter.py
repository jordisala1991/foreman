"""Only the claude adapter builds a claude command line (foreman/providers/claude/sessions.py); callers go through the Runner
port. A module that names one of claude's flags is one that builds it."""

import ast

from helpers import PLUGIN

FLAGS = {"--bg", "--permission-mode", "--settings", "bypassPermissions", "--resume"}
ALLOWED = {"providers/claude/sessions.py"}


def names_a_flag(source: str) -> bool:
    return any(isinstance(n, ast.Constant) and n.value in FLAGS for n in ast.walk(ast.parse(source)))


def test_only_the_adapter_builds_a_claude_command_line():
    root = PLUGIN / "foreman"
    found = {f.relative_to(root).as_posix() for f in root.rglob("*.py") if names_a_flag(f.read_text())}
    assert found == ALLOWED
