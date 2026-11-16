"""Only the repo adapter runs git on a repo (fm/providers/gitrepo.py); the platform calls stay in providers/github and the
guard parses git commands without running them. A module that names the program "git" is one that runs it."""

import ast

from helpers import PLUGIN

ALLOWED = {"providers/gitrepo.py", "providers/github/provider.py", "core/oversight/guard.py"}


def names_git(source: str) -> bool:
    return any(isinstance(n, ast.Constant) and n.value == "git" for n in ast.walk(ast.parse(source)))


def test_only_the_adapter_names_git():
    root = PLUGIN / "fm"
    found = {f.relative_to(root).as_posix() for f in root.rglob("*.py") if names_git(f.read_text())}
    assert found == ALLOWED
