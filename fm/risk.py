"""Risk and autonomy: pure functions, no I/O. Given the facts the checks found about a branch (`Facts`), `score` says how risky
the merge is (low, medium, high, with the reasons) and `allows` says whether the project's autonomy level lets that risk
merge without the owner. High never merges alone. The checks that find the facts, and the merge itself, are in fm/integrate.py."""

import fnmatch
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Project

RISKS = ("low", "medium", "high")
AUTONOMY = {"manual": (), "low": ("low",), "medium": ("low", "medium")}
DEPENDENCY_FILES = ("requirements*.txt", "pyproject.toml", "package.json", "package-lock.json", "go.mod", "Cargo.toml")


def is_sensitive(path: str, patterns: list[str]) -> bool:
    for pat in patterns:
        if pat.endswith("/"):
            if path.startswith(pat) or f"/{pat}" in f"/{path}":
                return True
        elif fnmatch.fnmatch(path, pat) or fnmatch.fnmatch(Path(path).name, pat):
            return True
    return False


@dataclass
class Facts:
    """What the checks found about a branch: the input of its risk score."""

    tests: str
    review: str
    files: list[dict[str, Any]]
    deleted: list[str]
    conflict: bool
    commits: int
    ci: str = ""


def _high_risks(p: Project, f: Facts, lines: int, paths: list[str]) -> list[str]:
    sensitive = [x for x in paths if is_sensitive(x, p.get("integrate", "sensitive"))]
    reasons = [
        (f.commits == 0 or not f.files, "the branch has no changes"),
        (f.tests == "fail", "tests fail"),
        (f.ci == "fail", "CI fails"),
        (f.conflict, "conflicts with main"),
        (bool(sensitive), "touches sensitive paths: " + ", ".join(sensitive[:5])),
        (not f.review.strip().lower().startswith("clean"), "review not clean: " + (f.review.strip()[:120] or "no review reported")),
        (lines > p.get("integrate", "medium_lines"), f"{lines} lines changed"),
    ]
    return [why for hit, why in reasons if hit]


def _medium_risks(p: Project, f: Facts, lines: int, paths: list[str]) -> list[str]:
    reasons = [
        (p.get("integrate", "small_lines") < lines <= p.get("integrate", "medium_lines"), f"{lines} lines changed"),
        (f.tests == "none", "no test command to run"),
        (bool(f.deleted), "deletes files: " + ", ".join(f.deleted[:5])),
        (any(fnmatch.fnmatch(Path(x).name, pat) for x in paths for pat in DEPENDENCY_FILES), "changes dependencies"),
        (len(paths) > 8, f"{len(paths)} files"),
    ]
    return [why for hit, why in reasons if hit]


def score(p: Project, f: Facts) -> tuple[str, list[str]]:
    """(risk, reasons): high beats medium beats low; each reason says what raised it."""
    lines = sum(x["added"] + x["deleted"] for x in f.files)
    paths = [x["path"] for x in f.files]
    high, medium = _high_risks(p, f, lines, paths), _medium_risks(p, f, lines, paths)
    risk = "high" if high else "medium" if medium else "low"
    return risk, high + medium or ["small, tests green, review clean"]


def allows(autonomy: str, risk: str) -> bool:
    """The autonomy level lets this risk merge alone."""
    return risk in AUTONOMY.get(autonomy, ())
