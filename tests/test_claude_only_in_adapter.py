"""Only the claude adapter builds a claude command line (foreman/providers/claude/sessions.py); callers go through the Runner
port. A module that names one of claude's flags or the program "claude" is one that builds it."""

import ast

from helpers import PLUGIN

FLAGS = {"--bg", "--permission-mode", "--settings", "bypassPermissions", "--resume"}
NAMES = FLAGS | {"claude"}
# The last three name the `claude` config key / runner name, not a command line.
ALLOWED = {"providers/claude/sessions.py", "core/project/config.py", "providers/registry.py", "steps/health/checks.py"}


def names_claude(source: str) -> bool:
    return any(isinstance(n, ast.Constant) and n.value in NAMES for n in ast.walk(ast.parse(source)))


def test_only_the_adapter_builds_a_claude_command_line():
    root = PLUGIN / "foreman"
    found = {f.relative_to(root).as_posix() for f in root.rglob("*.py") if names_claude(f.read_text())}
    assert found == ALLOWED


def test_a_module_that_runs_the_program_is_found():
    assert names_claude('from foreman import proc\nproc.run(["claude", "--model", "opus", "-p", "hi"])\n')


def test_a_module_that_takes_the_program_from_config_is_found():
    assert names_claude('proc.run([p.get("agents", "claude"), "-p", "hi"])\n')


def test_a_module_without_claude_is_not_found():
    assert not names_claude('proc.run(["git", "status"])\n')
