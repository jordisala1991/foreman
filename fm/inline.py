"""A PR review as data: which lines of the diff a comment can sit on, and each finding on its line. Pure."""

import re
from dataclasses import dataclass
from urllib.parse import quote, unquote

from .verdict import WORDS, Finding, key, safe

_HUNK = re.compile(r"^@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_PLACE = re.compile(r"^(.+):(\d+)$")


@dataclass(frozen=True)
class LineComment:
    key: str
    path: str
    line: int
    body: str

    @property
    def place(self) -> str:
        return f"{self.path}:{self.line}"


@dataclass(frozen=True)
class Review:
    key: str
    body: str
    comments: list[LineComment]


def _hunk(rows: list[str], n: int, old: int, new: int, cur: int) -> tuple[set[int], int]:
    """Walks one hunk from row `n` by its counts; returns the visible new-side lines and the next row."""
    lines: set[int] = set()
    while (old > 0 or new > 0) and n < len(rows):
        row = rows[n]
        n += 1
        if row.startswith("\\"):
            continue
        if not row.startswith("+"):
            old -= 1
        if not row.startswith("-"):
            new -= 1
            lines.add(cur)
            cur += 1
    return lines, n


def visible_lines(diff: str) -> dict[str, set[int]]:
    """path -> the new-side lines a comment can sit on (added and context lines)."""
    seen: dict[str, set[int]] = {}
    path = ""
    rows = diff.splitlines()
    n = 0
    while n < len(rows):
        row = rows[n]
        n += 1
        m = _HUNK.match(row)
        if m:
            lines, n = _hunk(rows, n, int(m.group(1) or 1), int(m.group(3) or 1), int(m.group(2)))
            if path:
                seen[path] |= lines
        elif row.startswith("+++ "):
            target = row[4:].strip()
            path = target[2:] if target.startswith("b/") else ""
            if path:
                seen.setdefault(path, set())
    return seen


def _body(f: Finding) -> str:
    text = f"**{WORDS[f.severity]}** · {safe(f.what)}"
    why = re.sub(r"^\s*why\s*:\s*", "", f.why, flags=re.IGNORECASE)
    return text + (f"\n\n<details>\n<summary>Why</summary>\n\n{safe(why)}\n\n</details>" if why else "")


def line_comments(found: list[Finding], visible: dict[str, set[int]]) -> list[LineComment]:
    out: dict[str, LineComment] = {}
    for f in found:
        m = _PLACE.match(f.place)
        if m and int(m.group(2)) in visible.get(m.group(1), set()):
            out.setdefault(key(f), LineComment(key(f), m.group(1), int(m.group(2)), _body(f)))
    return list(out.values())


LINK = re.compile(r"\[([^\]]*)\]\(fm-link:([^)\s]*)\)")
_AT = re.compile(r"^(.+?):(\d+)(?:[-:]\d+)?$")  # path:line, path:first-last, path:line:column


def resolve_links(body: str, base: str, head: str, comments: dict[str, str]) -> str:
    """Replaces each `[text](fm-link:PLACE)` of a review body: with the URL of its inline comment (`comments`: place ->
    URL), else with the file at the reviewed commit on its first line (`base` is the repository's URL); a place that is
    not `path:line` (a range and a column are fine) is left as its text, without a link."""

    def url(m: re.Match[str]) -> str:
        place = unquote(m.group(2))
        if place in comments:
            return f"[{m.group(1)}]({comments[place]})"
        at = _AT.match(place)
        if not at:
            return m.group(1)
        return f"[{m.group(1)}]({base}/blob/{head}/{quote(at.group(1), safe='/')}#L{at.group(2)})"

    return LINK.sub(url, body)
