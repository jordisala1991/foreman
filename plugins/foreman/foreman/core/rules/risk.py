"""Risk and autonomy: pure functions, no I/O. Given the facts the checks found about a branch (`Facts`), `score` says how risky
the merge is (low, medium, high, with the reasons) and `allows` says whether the project's autonomy lets that risk
merge without the owner. High never merges alone. The checks that find the facts, and the merge itself, are in foreman/services/delivery/."""

import fnmatch
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..project.config import Project

AUTONOMY = {"manual": (), "low": ("low",), "medium": ("low", "medium")}


def path_matches(path: str, patterns: list[str]) -> bool:
    for pat in patterns:
        if pat.endswith("/"):
            if path.startswith(pat) or f"/{pat}" in f"/{path}":
                return True
        elif fnmatch.fnmatch(path, pat) or fnmatch.fnmatch(Path(path).name, pat):
            return True
    return False


@dataclass
class Plan:
    """What the plan declares in its three lines: its risk and why, the paths it will touch, its expected counted lines.
    A missing line leaves its field empty and its rule off."""

    risk: str = ""  # low | medium | high
    why: str = ""
    areas: list[str] = field(default_factory=list[str])
    size: int | None = None


def parse_plan(text: str) -> Plan:
    """The plan's `Declared risk:`, `Areas:` and `Expected size:` lines, each at the start of its line; the first wins."""
    flags = re.MULTILINE | re.IGNORECASE
    declared = re.search(r"^Declared risk:\s*(low|medium|high)\b\W*(.*)$", text, flags)
    areas = re.search(r"^Areas:\s*(.+)$", text, flags)
    size = re.search(r"^Expected size:\s*(\d+)", text, flags)
    return Plan(
        declared.group(1).lower() if declared else "",
        declared.group(2).strip() if declared else "",
        [a for x in areas.group(1).split(",") if (a := x.strip(" `"))] if areas else [],
        int(size.group(1)) if size else None,
    )


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
    publishes: str = ""  # the repo's key when merging publishes it (`Project.publishes`)
    plan: Plan = field(default_factory=Plan)


UNCOUNTED = ["tests/", "test/", "test_*", "*_test.*", "*.lock", "package-lock.json", "*.min.js", "*_pb2.py", "*.generated.*", "generated/"]


def counted_lines(files: list[dict[str, Any]]) -> int:
    """Changed lines that count towards size: tests, generated and lock files are left out."""
    return sum(x["added"] + x["deleted"] for x in files if not path_matches(x["path"], UNCOUNTED))


def _path_reasons(rules: Mapping[str, list[str]], paths: list[str], tag: str) -> list[str]:
    out: list[str] = []
    for label, patterns in rules.items():
        hit = [x for x in paths if path_matches(x, patterns)]
        if hit:
            out.append(f"touches {label} ({', '.join(hit[:3])}) {tag}")
    return out


def score(p: Project, f: Facts) -> tuple[str, list[str]]:
    """(risk, reasons): the higher of three, impact, doubt and the plan (its declared risk, files it did not announce, size
    over 3 times its expectation); the rule is in DESIGN «Merge: risk and autonomy». A review never lowers it. Open
    findings and a conflict with main add a reason but not a level: they are gates in decide.after_check (a conflict goes
    back to its agent only once someone wants the merge). Up to 3 reasons, a conflict first, then high ones, each ending
    with its source in brackets; with several repos a repo's rule (path, size) names it. A repo that publishes on merge is
    high, that reason first."""
    paths = [x["path"] for x in f.files]
    tag = f"[diff, repo {p.repo_key}]" if p.several and p.repo_key else "[diff]"
    high = [f"merging publishes this repo ({f.publishes})"] if f.publishes else []
    high += _path_reasons(p.get("risk", "high"), paths, tag)
    evidence = [
        (f.commits == 0 or not f.files, "the branch has no changes [diff]"),
        (f.tests == "fail", "tests fail [tests]"),
        (f.ci == "fail", "CI fails [ci]"),
    ]
    high += [why for hit, why in evidence if hit]
    medium = _path_reasons(p.get("risk", "medium"), paths, tag)
    lines, limit = counted_lines(f.files), p.get("risk", "large_lines")
    doubt = [
        (bool(f.deleted), f"deletes files ({', '.join(f.deleted[:3])}) [diff]"),
        (f.tests == "none", "no test command to run [tests]"),
        (lines > limit, f"large change, reviews miss more past ~{limit} lines ({lines} lines) {tag}"),
    ]
    medium += [why for hit, why in doubt if hit]
    plan_high, plan_medium = _plan_reasons(f, lines)
    high, medium = high + plan_high, medium + plan_medium
    risk = "high" if high else "medium" if medium else "low"
    conflict = ["conflicts with main: it goes back to its agent when accepted [git]"] if f.conflict else []
    shown = conflict + high + medium + ([] if f.review.strip().lower().startswith("clean") else [_review_reason(f.review)])
    return risk, shown[:3] or ["nothing sensitive touched, tests green, no open findings [diff]"]


def _plan_reasons(f: Facts, lines: int) -> tuple[list[str], list[str]]:
    """(high, medium) reasons from the plan: its declared level (low adds none), the counted files it did not announce,
    counted lines over 3 times its expected size."""
    declared = [f"declared {f.plan.risk} by the plan: {f.plan.why} [plan]"] if f.plan.risk in ("medium", "high") else []
    counted = [x["path"] for x in f.files if not path_matches(x["path"], UNCOUNTED)]
    unannounced = [x for x in counted if not path_matches(x, f.plan.areas)] if f.plan.areas else []
    medium = [f"touches {', '.join(unannounced[:3])} the plan did not announce [plan+diff]"] if unannounced else []
    if f.plan.size is not None and lines > 3 * f.plan.size:
        medium.append(f"{lines} lines, the plan expected {f.plan.size} [plan+diff]")
    return (declared, medium) if f.plan.risk == "high" else ([], declared + medium)


def _review_reason(review: str) -> str:
    return f"open review findings ({review.strip()[:120]}) [review]" if review.strip() else "no review reported [review]"


def allows(autonomy: str, risk: str) -> bool:
    """The autonomy lets this risk merge alone."""
    return risk in AUTONOMY.get(autonomy, ())
