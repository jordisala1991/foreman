"""The description of a delivered task: its report, with a footer fm writes, shown as the PR body and in the panel.
Pure text: no I/O here. fm owns only the block between the markers; text a human wrote around it is kept."""

from typing import Any

from . import backlog

OPEN = "<!-- fm:description -->"
CLOSE = "<!-- /fm:description -->"
ROUNDS = "## Review rounds"


def wrap(text: str) -> str:
    return f"{OPEN}\n{text.strip()}\n{CLOSE}"


def merge_body(body: str, block: str) -> str:
    """`body` with its marked block replaced by `block`; without markers the block is appended, never overwriting."""
    start = body.find(OPEN)
    end = body.find(CLOSE, start + len(OPEN)) if start >= 0 else -1
    if end >= 0:
        return body[:start] + wrap(block) + body[end + len(CLOSE) :]
    if not body.strip():
        return wrap(block)
    return body.rstrip() + "\n\n" + wrap(block)


def review_line(v: dict[str, Any] | None) -> str:
    if not v:
        return "no review"
    return f"review round {v['round']}, {backlog.open_findings(v)} open"


def footer(commit: str, review: str, at: str) -> str:
    parts = [f"commit {commit[:9]}" if commit else "", review, at]
    return "*fm · " + " · ".join(x for x in parts if x) + "*"


def render(report: str, foot: str) -> str:
    """The report up to its `## Review rounds` section, then the footer, then that section folded."""
    lines = report.splitlines()
    cut = next((n for n, line in enumerate(lines) if line.rstrip().lower() == ROUNDS.lower()), None)
    head = "\n".join(lines if cut is None else lines[:cut]).strip()
    rounds = "" if cut is None else "\n".join(lines[cut + 1 :]).strip()
    out = f"{head}\n\n{foot}" if head else foot
    if rounds:
        out += f"\n\n<details>\n<summary>Review rounds</summary>\n\n{rounds}\n\n</details>"
    return out
