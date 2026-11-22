"""The briefs and shared texts serve every project, so they name no file, tool or command of foreman's own repo (owner's FN-24
«foreman's briefs must stay generic; they run in other projects that have no DESIGN.md, tach or make check»; FN-319, FN-346).
The names come from the repo root and pyproject's dev group, so a new root file or dev tool is covered with no edit here.
The one allowance, the Vocabulary paragraph that names GLOSSARY.md only when a project has one, lives in brief.py."""

import re
import tomllib
from pathlib import Path

import pytest
from helpers import ROOT

GENERIC = {"README.md", "CLAUDE.md", "AGENTS.md"}  # files any project may have
TEXTS = ("plugins/foreman/foreman/briefs", "plugins/foreman/foreman/shared")


def own_names(root: Path) -> list[str]:
    with (root / "pyproject.toml").open("rb") as f:
        groups: dict[str, list[str]] = tomllib.load(f)["dependency-groups"]
    tools = [re.split(r"[=<>~!;\[ ]", dep, maxsplit=1)[0] for dep in groups["dev"]]
    files = [p.name for p in root.iterdir() if p.is_file() and p.name not in GENERIC]
    return [*files, *tools, "make check"]


def leaks(root: Path) -> list[str]:
    names = sorted(own_names(root), key=len, reverse=True)  # longest first: tach.toml before tach, pytest-xdist before pytest
    escaped = [re.escape(n).replace(r"\ ", r"\s+") for n in names]  # `make check` also when wrapped or spaced twice
    pattern = re.compile(r"(?<![\w-])(?:" + "|".join(escaped) + r")(?![\w-])")
    found: list[str] = []
    for f in sorted(p for d in TEXTS for p in (root / d).rglob("*.md")):
        text = f.read_text()
        for m in pattern.finditer(text):
            found.append(f"{f.relative_to(root).as_posix()}:{text.count(chr(10), 0, m.start()) + 1} {' '.join(m.group().split())}")
    return found


def test_main_names_no_file_of_our_repo():
    assert leaks(ROOT) == []


def tree(root: Path, rel: str, text: str) -> None:
    for name in ("README.md", "CLAUDE.md", "AGENTS.md", "DESIGN.md", "GLOSSARY.md", "Makefile", "tach.toml"):
        (root / name).write_text("")
    deps = '"ruff==0.17.4", "tach==0.35.3", "pytest==9.2.0", "pytest-xdist==3.8.0"'
    (root / "pyproject.toml").write_text(f"[dependency-groups]\ndev = [{deps}]\n")
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text)


B = "plugins/foreman/foreman/briefs"
S = "plugins/foreman/foreman/shared"


@pytest.mark.parametrize(
    ("rel", "text", "expected"),
    [
        (f"{B}/planning.md", "first read DESIGN.md «Architecture» and the tach layers, and run make check", ["DESIGN.md", "tach", "make check"]),
        (f"{B}/reviewer.md", "Use the terms of `GLOSSARY.md` at the repo root.", ["GLOSSARY.md"]),
        (f"{S}/instructions.md", "Never edit tach.toml.", ["tach.toml"]),
        (f"{S}/skills/define-task/SKILL.md", "run pytest, then ruff", ["pytest", "ruff"]),
        (f"{B}/hunter.md", "see the Makefile", ["Makefile"]),
        (f"{B}/chain.md", "then run make\ncheck", ["make check"]),
        (f"{B}/parent.md", "then run make  check", ["make check"]),
    ],
)
def test_a_name_of_our_repo_is_reported(tmp_path: Path, rel: str, text: str, expected: list[str]):
    tree(tmp_path, rel, text)
    assert leaks(tmp_path) == [f"{rel}:1 {name}" for name in expected]


def test_a_new_root_file_is_reported_with_no_edit(tmp_path: Path):
    tree(tmp_path, f"{B}/hunter.md", "run the Taskfile.yml targets")
    (tmp_path / "Taskfile.yml").write_text("")
    assert leaks(tmp_path) == [f"{B}/hunter.md:1 Taskfile.yml"]


def test_other_text_is_not_reported(tmp_path: Path):
    tree(tmp_path, f"{B}/a.md", "README.md, CLAUDE.md, AGENTS.md, the project's design doc, its architecture rules, a tachometer")
    tree(tmp_path, "plugins/foreman/foreman/services/agents/brief.py", "Use the terms of `GLOSSARY.md`")
    (tmp_path / B / ".DS_Store").write_bytes(b"\x00\xff\xfe\x80")
    assert leaks(tmp_path) == []
