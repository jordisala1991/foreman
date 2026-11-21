"""Only the backlog port's local adapter (foreman/providers/backlogfile.py) names the backlog's file: every other module
reads and writes the backlog through the port (`registry.backlog(p)`). A docstring may mention the file."""

import ast

from helpers import PLUGIN


def names_the_file(source: str) -> bool:
    tree = ast.parse(source)
    docs = {
        id(n.body[0].value)
        for n in ast.walk(tree)
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and n.body
        and isinstance(n.body[0], ast.Expr)
        and isinstance(n.body[0].value, ast.Constant)
    }
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and "backlog.json" in n.value and id(n) not in docs:
            return True
        if (isinstance(n, ast.Name) and n.id == "backlog_file") or (isinstance(n, ast.Attribute) and n.attr == "backlog_file"):
            return True
    return False


def test_only_the_adapter_names_the_file():
    root = PLUGIN / "foreman"
    found = {f.relative_to(root).as_posix() for f in root.rglob("*.py") if names_the_file(f.read_text())}
    assert found == {"providers/backlogfile.py"}


def test_a_docstring_is_not_naming_the_file():
    assert names_the_file('x = home / "backlog.json"')
    assert names_the_file("x = p.backlog_file")
    assert not names_the_file('"""The backlog: one `backlog.json`."""\n')
