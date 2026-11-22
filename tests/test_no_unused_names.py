"""A function, class, method or module constant under foreman/ is named somewhere else in foreman/ or bin/foreman: code left
behind by a removed feature fails (FN-186, 8378b3a). Tests do not count: a test that keeps dead code alive does not save it."""

import ast
import re
from collections import Counter
from pathlib import Path

import pytest
from helpers import ROOT

# BaseHTTPRequestHandler's hooks: the standard library calls them by name.
ALLOWED = {"log_message", "do_GET", "do_POST"}


def defined(module: ast.Module) -> list[tuple[int, str]]:
    """The module's functions, classes, methods and constants (a top-level name assigned), with their line."""
    found: list[tuple[int, str]] = []
    for node in module.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            found.append((node.lineno, node.name))
        elif isinstance(node, ast.ClassDef):
            found.append((node.lineno, node.name))
            found += [(m.lineno, m.name) for m in node.body if isinstance(m, ast.FunctionDef | ast.AsyncFunctionDef)]
        elif isinstance(node, ast.Assign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            found += [(t.lineno, t.id) for t in targets if isinstance(t, ast.Name)]
    return found


def unused(root: Path) -> list[str]:
    pkg = root / "plugins/foreman/foreman"
    modules = sorted(pkg.rglob("*.py"))
    texts = [f.read_text() for f in [*modules, *sorted(pkg.rglob("*.md")), root / "plugins/foreman/bin/foreman"]]
    words = Counter(w for text in texts for w in re.findall(r"\w+", text))
    found: list[str] = []
    for f in modules:
        for line, name in defined(ast.parse(f.read_text())):
            if words[name] == 1 and name not in ALLOWED and not (name.startswith("__") and name.endswith("__")):
                found.append(f"{f.relative_to(root).as_posix()}:{line} {name}")
    return found


def test_main_has_no_unused_name():
    assert unused(ROOT) == []


def tree(root: Path, files: dict[str, str]) -> None:
    for rel, text in {"plugins/foreman/bin/foreman": "", **files}.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)


PKG = "plugins/foreman/foreman"
DEAD = "def send_old() -> None:\n    pass\n"


@pytest.mark.parametrize(
    ("rel", "text", "expected"),
    [
        ("steps/notify.py", "def send_old(line: str) -> None:\n    pass\n", "steps/notify.py:1 send_old"),
        ("steps/a.py", "class Gone:\n    pass\n", "steps/a.py:1 Gone"),
        ("steps/a.py", "class Box:\n    def keep(self) -> None: ...\n\n    def gone(self) -> None: ...\n\n\nBox().keep()\n", "steps/a.py:4 gone"),
        ("steps/a.py", "LIMIT = 3\nSIZE: int = 4\nprint(SIZE)\n", "steps/a.py:1 LIMIT"),
        ("steps/a.py", "async def later() -> None:\n    pass\n", "steps/a.py:1 later"),
    ],
)
def test_an_unreferenced_name_is_reported(tmp_path: Path, rel: str, text: str, expected: str):
    tree(tmp_path, {f"{PKG}/{rel}": text})
    assert unused(tmp_path) == [f"{PKG}/{expected}"]


@pytest.mark.parametrize(
    ("rel", "text"),
    [(f"{PKG}/steps/b.py", "send_old()\n"), (f"{PKG}/briefs/x.md", "call `send_old`\n"), ("plugins/foreman/bin/foreman", "send_old()\n")],
)
def test_a_reference_outside_tests_keeps_a_name(tmp_path: Path, rel: str, text: str):
    tree(tmp_path, {f"{PKG}/steps/notify.py": DEAD, rel: text})
    assert unused(tmp_path) == []


def test_a_test_does_not_keep_a_name(tmp_path: Path):
    tree(tmp_path, {f"{PKG}/steps/notify.py": DEAD, "tests/test_x.py": "from foreman.steps.notify import send_old\nsend_old()\n"})
    assert unused(tmp_path) == [f"{PKG}/steps/notify.py:1 send_old"]


def test_the_stdlib_hooks_and_dunders_are_allowed(tmp_path: Path):
    text = "class H:\n    def do_GET(self) -> None:\n        pass\n\n    def __repr__(self) -> str:\n        return ''\n\n\nH()\n"
    tree(tmp_path, {f"{PKG}/ui/panel/server.py": text})
    assert unused(tmp_path) == []
