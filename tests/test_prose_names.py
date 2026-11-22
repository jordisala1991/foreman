"""A `foreman/…` path or a `foreman.x.y` module named in prose (the docs, the briefs, a docstring or comment under
foreman/) points at something in the tree: after a move, the prose that still names the old place fails (FN-178)."""

import ast
import io
import re
import tokenize
from pathlib import Path

import pytest
from helpers import ROOT

# A token like a/b.py or a/b/c.md, not part of a longer path, a home (~/), or a `ref:` path into another project.
PATH = re.compile(r"(?<![\w./~:-])(?:[\w.-]+/)+[\w.-]+\.(?:py|md)\b")
DOTTED = re.compile(r"(?<![\w./-])foreman(?:\.\w+)+")
# The one name of that shape that is a file, not a module.
OUTSIDE = {"foreman.toml"}


def docs(root: Path) -> list[Path]:
    pkg = root / "plugins/foreman/foreman"
    return [root / "README.md", root / "DESIGN.md", root / "GLOSSARY.md", *(pkg / "briefs").glob("*.md"), *(pkg / "shared").rglob("*.md")]


def prose(source: str) -> list[tuple[int, str]]:
    """The comments and docstrings of a module, line by line; code strings are runtime text, not prose."""
    lines = [(t.start[0], t.string) for t in tokenize.generate_tokens(io.StringIO(source).readline) if t.type == tokenize.COMMENT]
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                lines += [(first.lineno + i, line) for i, line in enumerate(first.value.value.splitlines())]
    return lines


def module_exists(pkg: Path, name: str) -> bool:
    """foreman.a.b… resolves to a package, a module (the rest are its attributes) or a name its package's __init__ re-exports."""
    here = pkg
    for part in name.split(".")[1:]:
        if (here / part).is_dir():
            here = here / part
        elif (here / f"{part}.py").is_file():
            return True
        else:
            init = here / "__init__.py"
            return init.is_file() and re.search(rf"\b{part}\b", init.read_text()) is not None
    return True


def stale(root: Path) -> list[str]:
    pkg = root / "plugins/foreman/foreman"
    areas = {"foreman", "plugins", "tests", *(d.name for d in pkg.iterdir() if d.is_dir())}
    bases = (root, root / "plugins/foreman", pkg)
    texts = [(d, list(enumerate(d.read_text().splitlines(), 1))) for d in docs(root)]
    texts += [(f, prose(f.read_text())) for f in sorted(pkg.rglob("*.py"))]
    found: list[str] = []
    for f, lines in texts:
        where = f.relative_to(root).as_posix()
        for n, line in lines:
            for path in PATH.findall(line):
                if path.split("/")[0] in areas and not any((b / path).is_file() for b in bases):
                    found.append(f"{where}:{n} {path}")
            for name in DOTTED.findall(line):
                if name not in OUTSIDE and not module_exists(pkg, name):
                    found.append(f"{where}:{n} {name}")
    return found


def test_main_has_no_stale_name():
    assert stale(ROOT) == []


def tree(root: Path, rel: str, text: str) -> None:
    for f, body in [
        ("README.md", ""),
        ("DESIGN.md", ""),
        ("GLOSSARY.md", ""),
        ("plugins/foreman/foreman/__init__.py", ""),
        ("plugins/foreman/foreman/services/__init__.py", ""),
        ("plugins/foreman/foreman/services/lock.py", "def acquire() -> None: ...\n"),
        ("plugins/foreman/foreman/briefs/a.md", ""),
        (rel, text),
    ]:
        (root / f).parent.mkdir(parents=True, exist_ok=True)
        (root / f).write_text(body)


@pytest.mark.parametrize(
    ("rel", "text", "expected"),
    [
        (
            "plugins/foreman/foreman/steps/metrics.py",
            '"""See `foreman/services/agents/gone.py` (moved)."""\n',
            ["plugins/foreman/foreman/steps/metrics.py:1 foreman/services/agents/gone.py"],
        ),
        (
            "DESIGN.md",
            "x\nOld: `foreman/services/gone/old.py` and `foreman.services.gone`.\n",
            ["DESIGN.md:2 foreman/services/gone/old.py", "DESIGN.md:2 foreman.services.gone"],
        ),
        (
            "plugins/foreman/foreman/services/x.py",
            "def f() -> None:\n    pass  # see foreman.services.gone\n",
            ["plugins/foreman/foreman/services/x.py:2 foreman.services.gone"],
        ),
        (
            "plugins/foreman/foreman/briefs/b.md",
            "From `services/claude/sessions.py`.\n",
            ["plugins/foreman/foreman/briefs/b.md:1 services/claude/sessions.py"],
        ),
    ],
)
def test_a_stale_name_is_reported(tmp_path: Path, rel: str, text: str, expected: list[str]):
    tree(tmp_path, rel, text)
    assert stale(tmp_path) == expected


def test_a_live_or_outside_name_is_not_reported(tmp_path: Path):
    text = (
        '"""`foreman/services/lock.py`, `foreman.services.lock.acquire`, `foreman.toml`, `ref:services/orca/call.py`,\n'
        '`.claude/settings.local.json`, `~/.config/foreman/x.md`, `state/loop.json`, `reports/FN-1.md`."""\n'
        'MESSAGE = "see foreman/services/gone.py and foreman.services.gone"\n'
    )
    tree(tmp_path, "plugins/foreman/foreman/services/y.py", text)
    assert stale(tmp_path) == []
