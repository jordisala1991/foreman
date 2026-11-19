"""Every .py under foreman/ is covered by a [[modules]] path of tach.toml: root_module = "ignore" leaves an undeclared file
unchecked, so a new file must be declared."""

import ast
import tomllib
from pathlib import Path

from helpers import PLUGIN, ROOT


def uncovered(root: Path, declared: list[str]) -> list[str]:
    found: list[str] = []
    for f in sorted((root / "foreman").rglob("*.py")):
        name = f.relative_to(root).with_suffix("").as_posix().replace("/", ".").removesuffix(".__init__")
        imports = any(isinstance(n, ast.Import | ast.ImportFrom) for n in ast.walk(ast.parse(f.read_text())))
        if f.name == "__init__.py" and not imports:
            continue  # an empty or docstring-only package file imports nothing, so it needs no layer
        if not any(name == p or name.startswith(p + ".") for p in declared):
            found.append(name)
    return found


def declared_paths() -> list[str]:
    with (ROOT / "tach.toml").open("rb") as f:
        modules: list[dict[str, str]] = tomllib.load(f)["modules"]
    return [m["path"] for m in modules]


def test_every_module_under_foreman_is_declared():
    assert uncovered(PLUGIN, declared_paths()) == []


def write(root: Path, rel: str, text: str = "") -> None:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text)


def test_a_file_no_declared_path_covers_is_reported(tmp_path: Path):
    for rel in ("foreman/services/p4b.py", "foreman/services/agents/brief.py", "foreman/services/agentsx.py"):
        write(tmp_path, rel)
    assert uncovered(tmp_path, ["foreman.services.agents"]) == ["foreman.services.agentsx", "foreman.services.p4b"]


def test_a_package_file_without_imports_needs_no_module(tmp_path: Path):
    write(tmp_path, "foreman/core/__init__.py")
    assert uncovered(tmp_path, []) == []
    write(tmp_path, "foreman/core/__init__.py", "from . import x\n")
    assert uncovered(tmp_path, []) == ["foreman.core"]
