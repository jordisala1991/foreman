"""The verdict comment of a PR as text: risk in words with its reasons, each open finding as one line, the full
review folded. Pure; the provider keeps the one comment up to date."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

CLEAN = "Review: clean, nothing open."
NO_REVIEW = "No independent review."
SAYS = {
    "low": "small and checked; fine to merge on a quick look",
    "medium": "worth a read before it merges",
    "high": "needs the owner's decision before it merges",
}
FOLD_LIMIT = 20000
_LINE = re.compile(r"^\s*[-*]?\s*(F\d+)\s*\|\s*([HML])\s*\|\s*([^|]*?)\s*\|\s*(.+?)\s*$")


@dataclass(frozen=True)
class Finding:
    id: str
    severity: str
    place: str
    what: str


def findings(review_text: str) -> list[Finding]:
    """The open findings the reviewer wrote as `F<n> | H|M|L | path:line | what`; any other line is ignored."""
    found: list[Finding] = []
    for line in review_text.splitlines():
        m = _LINE.match(line)
        if m:
            found.append(Finding(m.group(1), m.group(2), m.group(3), m.group(4)))
    return found


def _review_block(review: Mapping[str, Any] | None, found: list[Finding]) -> list[str]:
    if not review:
        return [NO_REVIEW]
    high, medium, low = int(review.get("high", 0)), int(review.get("medium", 0)), int(review.get("low", 0))
    total = high + medium + low
    if total == 0:
        return [CLEAN]
    lines = [f"**Review round {review.get('round', '?')}: {total} open**"]
    ordered = sorted(found, key=lambda f: "HML".index(f.severity))
    lines += [f"- {f.severity} · {f.place} · {f.what}" for f in ordered]
    if len(found) < total:
        lines.append(f"- {total - len(found)} more open: see the full review below")
    return lines


def render(risk: str, reasons: list[str], checks: str, review: Mapping[str, Any] | None, review_text: str) -> str:
    """`checks` is the one line on tests and CI; the findings are read from `review_text`."""
    parts = [f"**Merge risk: {risk}** · {SAYS.get(risk, '')}".rstrip(" ·")]
    if reasons:
        parts.append("\n".join(f"- {r}" for r in reasons))
    parts.append(checks)
    parts.append("\n".join(_review_block(review, findings(review_text))))
    text = review_text.strip()
    if text:
        if len(text) > FOLD_LIMIT:
            text = text[:FOLD_LIMIT] + "\n… (cut)"
        parts.append(f"<details>\n<summary>Full review</summary>\n\n{text}\n\n</details>")
    return "\n\n".join(parts) + "\n"
