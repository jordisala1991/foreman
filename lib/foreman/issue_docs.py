"""All the documents of an issue, for its card in the panel and for `foreman backlog show`: its plans, each
sub-issue's report, the reviews of each sub-issue's branch (found by the `Branch:` of their header, whatever their
name, or by a name that holds the issue and sub-issue ids), the pages of the decisions that name it (in `blocks` or by
their id) and the .md files its detail cites. A file that is not there is left out."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from .backlog import Data, Issue
    from .config import Project

Doc = dict[str, Any]
# the review files of a product: (path relative to the home, branch its header names or "", round, open H+M or None)
ReviewFile = tuple[str, str, int, "int | None"]


def review_files(project: Project) -> list[ReviewFile]:
    """Every review under the reviews folder (a name that holds «review»), read once."""
    from . import review  # only a card or a `show` needs it

    folder = project.reviews_dir
    out: list[ReviewFile] = []
    for path in sorted(folder.rglob("*review*.md")) if folder.is_dir() else []:
        got = review.read(project.home, path)
        rel = _rel(project.home, path)
        if got is None:
            out.append((rel, "", 1, None))
        elif isinstance(got[1], review.Review):
            out.append((rel, got[0].rpartition(":")[2], got[1].round, got[1].open))
        else:
            out.append((rel, got[0].rpartition(":")[2], 1, None))
    return out


def _rel(home: Path, path: Path) -> str:
    return str(path.relative_to(home)) if path.is_relative_to(home) else str(path)


def _named(path: str, iid: str, sid: str) -> int | None:
    """The round of a review whose name holds `iid` and `sid` («…review-AB-1-T3-r2.md»; 1 without -rN), else None."""
    m = re.search(rf"review-{re.escape(iid)}-{re.escape(sid)}(?:-r(\d+))?\.md$", path)
    return int(m.group(1) or 1) if m else None


def issue_docs(
    project: Project,
    data: Data,
    issue: Issue,
    reviews: list[ReviewFile],
    ref: Callable[[str], str | None],
) -> list[Doc]:
    """The documents of `issue` (`ref`: a path or file name written in a text -> the path the panel serves, or None
    when there is no such file), each {kind: plan|report|review|decision|cited, doc, + sub, round, open, id, note}."""
    iid = issue["id"]
    out: list[Doc] = []
    seen: set[str] = set()

    def add(kind: str, doc: str | None, **kw: Any) -> None:
        if doc and doc not in seen:
            seen.add(doc)
            out.append({"kind": kind, "doc": doc, **kw})

    for p in issue.get("plan", []):
        add("plan", ref(p.get("path", "")), **({"note": p["note"]} if p.get("note") else {}))
    for s in issue.get("subs", []):
        add("report", ref(s.get("report", "")), sub=s["id"])
    for s in issue.get("subs", []):
        found = []
        for path, branch, rnd, opened in reviews:
            by_name = _named(path, iid, s["id"])
            if (s.get("branch") and branch == s["branch"]) or by_name is not None:
                found.append((rnd, path, opened))
        for rnd, path, opened in sorted(found):
            add("review", ref(path), sub=s["id"], round=rnd, **({"open": opened} if opened is not None else {}))
    named = re.compile(rf"^{re.escape(iid)}(?![\w])")
    for d in [*data.get("decisions", []), *data.get("decisions_resolved", [])]:
        if d.get("page") and (named.match(d.get("id", "")) or any(named.match(r) for r in d.get("blocks") or [])):
            add("decision", ref(d["page"]), id=d.get("id", ""))
    for n in re.findall(r"[\w./-]+\.md", issue.get("detail", "")):
        add("cited", ref(n))
    return out


def lines(docs: list[Doc]) -> list[str]:
    """`docs` as the text lines of `backlog show`."""
    out = []
    for d in docs:
        tag = d["kind"] + (f" {d['sub']}" if d.get("sub") else "") + (f" {d['id']}" if d.get("id") else "")
        if d["kind"] == "review":
            tag += f" r{d['round']}" + (f", {d['open']} open" if "open" in d else "")
        out.append(f"  {tag}: {d['doc']}")
    return out
