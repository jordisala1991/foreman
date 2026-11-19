"""Only the claude adapter builds a claude command line (foreman/providers/claude/sessions.py); callers go through the Runner
port. A module that names one of claude's flags or the program "claude" is one that builds it."""

import ast

from helpers import PLUGIN

FLAGS = {"--bg", "--permission-mode", "--settings", "bypassPermissions", "--resume"}
ALLOWED = {"providers/claude/sessions.py"}
# These three name "claude" as the config key / runner name, not in a command line; they may name no flag.
NAMING_CLAUDE = ALLOWED | {
    "core/project/config.py",
    "providers/registry.py",
    "steps/health/checks.py",
    "providers/orca/sessions.py",  # names Orca's agent id claude, not a command line
}


def names_a_flag(source: str) -> bool:
    return any(isinstance(n, ast.Constant) and n.value in FLAGS for n in ast.walk(ast.parse(source)))


def names_claude(source: str) -> bool:
    return any(isinstance(n, ast.Constant) and n.value == "claude" for n in ast.walk(ast.parse(source)))


def test_only_the_adapter_builds_a_claude_command_line():
    root = PLUGIN / "foreman"
    found = {f.relative_to(root).as_posix() for f in root.rglob("*.py") if names_a_flag(f.read_text())}
    assert found == ALLOWED


def test_only_the_adapter_and_the_config_name_claude():
    root = PLUGIN / "foreman"
    found = {f.relative_to(root).as_posix() for f in root.rglob("*.py") if names_claude(f.read_text())}
    assert found == NAMING_CLAUDE


def test_a_module_that_runs_the_program_is_found():
    assert names_claude('from foreman import proc\nproc.run(["claude", "--model", "opus", "-p", "hi"])\n')


def test_a_module_that_takes_the_program_from_config_is_found():
    assert names_claude('proc.run([p.get("agents", "claude"), "-p", "hi"])\n')


def test_a_module_without_claude_is_not_found():
    assert not names_claude('proc.run(["git", "status"])\n')


def test_a_flag_next_to_the_config_key_is_found():
    assert names_a_flag('AGENTS = {"claude": Claude}\nSNEAK = ["--bg", "--permission-mode", "bypassPermissions"]\n')
