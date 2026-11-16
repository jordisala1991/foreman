"""Only the repo adapter runs git on a repo (foreman/providers/gitrepo.py); the platform calls stay in providers/github and the
guard parses git commands without running them. A module that names the program "git" is one that runs it."""

import ast

from helpers import PLUGIN

ALLOWED = {"providers/gitrepo.py", "providers/github/provider.py", "core/oversight/guard.py"}


def names_git(source: str) -> bool:
    return any(isinstance(n, ast.Constant) and n.value == "git" for n in ast.walk(ast.parse(source)))


def test_only_the_adapter_names_git():
    root = PLUGIN / "foreman"
    found = {f.relative_to(root).as_posix() for f in root.rglob("*.py") if names_git(f.read_text())}
    assert found == ALLOWED


PLATFORM_GIT = {"ls-remote", "fetch", "push"}


def git_commands(source: str) -> list[str]:
    """The first string after each `"git"` constant that is not an option (`-q`, `-C`...): the git command."""
    strings = [n.value for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    return [next(s for s in strings[k + 1 :] if not s.startswith("-")) for k, s in enumerate(strings) if s == "git"]


def test_the_github_provider_runs_only_platform_git():
    source = (PLUGIN / "foreman" / "providers" / "github" / "provider.py").read_text()
    assert set(git_commands(source)) == PLATFORM_GIT
