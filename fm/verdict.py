"""The verdict of a delivered tip as the body of a PR review: the decision first, the change, why it is risky, the
checks, the blocking findings, the rest folded. Pure; the provider posts it."""

import hashlib
import re
from dataclasses import dataclass

WORDS = {"H": "Blocking", "M": "Should fix", "L": "Nit"}
FIX = "⛔ Fix first — {n} blocking"
LOOK = "⚠️ Look first — {n} to check"
READY = "✅ Ready to merge"
SUMMARY_LIMIT = 200
_LINE = re.compile(r"^\s*[-*]?\s*F\d+\s*\|")


@dataclass(frozen=True)
class Finding:
    id: str
    severity: str
    place: str
    what: str
    why: str


@dataclass(frozen=True)
class Case:
    risk: str
    reasons: list[str]
    tests: str
    ci: str
    head: str
    summary: str
    files: int
    lines: int
    counts: tuple[int, int, int] | None  # open H, M, L of the verdict; None = no usable review


def findings(text: str) -> list[Finding]:
    """The open findings the reviewer wrote as `F<n> | H|M|L | path:line | what | why`; any other line is ignored."""
    found: list[Finding] = []
    for line in text.splitlines():
        if not _LINE.match(line):
            continue
        fields = [x.strip() for x in line.lstrip(" \t-*").split("|", 4)]
        if len(fields) >= 4 and fields[1] in WORDS:
            found.append(Finding(fields[0], fields[1], fields[2], fields[3], fields[4] if len(fields) > 4 else ""))
    return found


def safe(text: str) -> str:
    """Reviewer text on a PR: an `@name` must not notify anyone and a `</details>` must not close our fold."""
    return re.sub(r"</?details[^>]*>", "", text, flags=re.IGNORECASE).replace("@", "@\u200b")


def path_of(f: Finding) -> str:
    return f.place.rsplit(":", 1)[0] if ":" in f.place else f.place


def key(f: Finding) -> str:
    """Identifies a finding across tips: its file and its words, not its number nor its line."""
    return "finding-" + hashlib.sha1(f"{path_of(f)}\n{f.what}".encode()).hexdigest()[:12]


def levels(case: Case, found: list[Finding]) -> tuple[int, int, int]:
    """Open (blocking, should fix, nits): the verdict's count or the listed ones, whichever is more."""
    counted = case.counts or (0, 0, 0)
    n = [max(counted[k], sum(1 for f in found if f.severity == s)) for k, s in enumerate("HML")]
    n[0] += (case.tests == "fail") + (case.ci == "fail")
    return n[0], n[1], n[2]


def headline(case: Case, found: list[Finding]) -> str:
    blocking, should, _ = levels(case, found)
    if blocking:
        return FIX.format(n=blocking)
    if should or case.risk != "low":
        return LOOK.format(n=should + (1 if case.risk != "low" else 0))
    return READY


def _item(f: Finding) -> str:
    """One line; its place links to the inline comment or the file (`fm-link:` is resolved by the provider)."""
    place = "" if f.place == "-" else f"[`{f.place}`](fm-link:{f.place}) "
    return f"- {place}{safe(f.what)}"


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _fold(case: Case, found: list[Finding], placed: set[str]) -> list[str]:
    _, should, nits = levels(case, found)
    count = " · ".join(x for x in (f"{should} should fix" if should else "", _plural(nits, "nit", "nits") if nits else "") if x)
    items = [_item(f) for f in found if f.severity == "M" or (f.severity == "L" and f.place not in placed)]
    if not count:
        return []
    if not items:
        return [count]
    return ["<details>", f"<summary>{count}</summary>", "", *items, "", "</details>"]


def render(case: Case, found: list[Finding], placed: set[str]) -> str:
    lines = [headline(case, found), (case.summary.strip().splitlines() or [""])[0][:SUMMARY_LIMIT]]
    lines.append(f"Why {case.risk}: " + "; ".join(case.reasons))
    lines.append(f"Checks: tests {case.tests} · CI {case.ci or 'none'} · reviewed {case.head[:9]}")
    blocking = [f for f in found if f.severity == "H"]
    lines += [_item(f) for f in blocking]
    counted = case.counts[0] if case.counts else 0
    if counted > len(blocking):
        lines.append(f"- {counted - len(blocking)} more open, not itemized")
    lines += _fold(case, found, placed)
    lines.append(f"<sub>{case.files} files · {case.lines} lines</sub>")
    return "\n\n".join(lines) + "\n"
