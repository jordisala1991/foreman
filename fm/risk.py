"""Risk and autonomy: pure functions, no I/O. Given the facts the checks found about a branch (`Facts`), `score` says how risky
the merge is (low, medium, high, with the reasons) and `allows` says whether the project's autonomy level lets that risk
merge without the owner. High never merges alone. The checks that find the facts, and the merge itself, are in fm/integrate.py."""

import fnmatch
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import backlog
from .config import Project

AUTONOMY = {"manual": (), "low": ("low",), "medium": ("low", "medium")}


def review_step(v: Mapping[str, Any], max_rounds: int) -> str:
    """What the loop does with a verdict: `back` to the agent while findings are open before the last round, else `queue`
    (what is still open then goes to the owner); a verdict already sent back never goes twice."""
    if not v.get("sent_back") and backlog.open_findings(v) > 0 and v["round"] < max_rounds:
        return "back"
    return "queue"


def path_matches(path: str, patterns: list[str]) -> bool:
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


UNCOUNTED = ["tests/", "test/", "test_*", "*_test.*", "*.lock", "package-lock.json", "*.min.js", "*_pb2.py", "*.generated.*", "generated/"]


def counted_lines(files: list[dict[str, Any]]) -> int:
    """Changed lines that count towards size: tests, generated and lock files are left out."""
    return sum(x["added"] + x["deleted"] for x in files if not path_matches(x["path"], UNCOUNTED))


def _path_reasons(rules: Mapping[str, list[str]], paths: list[str]) -> list[str]:
    out: list[str] = []
    for label, patterns in rules.items():
        hit = [x for x in paths if path_matches(x, patterns)]
        if hit:
            out.append(f"touches {label} ({', '.join(hit[:3])}) [diff]")
    return out


def score(p: Project, f: Facts) -> tuple[str, list[str]]:
    """(risk, reasons): the higher of impact (paths, deletions) and doubt (tests, CI, conflict); a review never lowers it.
    Open findings add a [review] reason but not a level: they are a gate in integrate.merges_alone. Up to 3 reasons,
    high ones first, each ending with its source in brackets."""
    paths = [x["path"] for x in f.files]
    high = _path_reasons(p.get("risk", "high"), paths)
    evidence = [
        (f.commits == 0 or not f.files, "the branch has no changes [diff]"),
        (f.tests == "fail", "tests fail [tests]"),
        (f.ci == "fail", "CI fails [ci]"),
        (f.conflict, "conflicts with main [git]"),
    ]
    high += [why for hit, why in evidence if hit]
    medium = _path_reasons(p.get("risk", "medium"), paths)
    lines, limit = counted_lines(f.files), p.get("risk", "large_lines")
    doubt = [
        (bool(f.deleted), f"deletes files ({', '.join(f.deleted[:3])}) [diff]"),
        (f.tests == "none", "no test command to run [tests]"),
        (lines > limit, f"large change, reviews miss more past ~{limit} lines ({lines} lines) [diff]"),
    ]
    medium += [why for hit, why in doubt if hit]
    risk = "high" if high else "medium" if medium else "low"
    shown = high + medium + ([] if f.review.strip().lower().startswith("clean") else [_review_reason(f.review)])
    return risk, shown[:3] or ["nothing sensitive touched, tests green, no open findings [diff]"]


def _review_reason(review: str) -> str:
    return f"open review findings ({review.strip()[:120]}) [review]" if review.strip() else "no review reported [review]"


def allows(autonomy: str, risk: str) -> bool:
    """The autonomy level lets this risk merge alone."""
    return risk in AUTONOMY.get(autonomy, ())
