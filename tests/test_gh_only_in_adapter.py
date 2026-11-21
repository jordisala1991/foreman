"""Only the GitHub adapter runs gh (foreman/providers/github/provider.py and review.py); services orchestrate through the
port. The guard (core/oversight/guard.py) names it to parse command lines and runs nothing. A module that names the program
"gh" is one that runs it."""

from helpers import PLUGIN, names_program

ALLOWED = {"providers/github/provider.py", "providers/github/review.py", "providers/github/api.py", "core/oversight/guard.py"}


def names_gh(source: str) -> bool:
    return names_program(source, "gh")


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


def test_a_program_given_by_its_path_is_found():
    assert names_gh('proc.run(["/opt/homebrew/bin/gh", "pr", "list"])\n')


def test_a_path_to_another_name_is_not_found():
    assert not names_gh('x = "/usr/bin/ghost"\ny = "/usr/bin/gh/"\n')
