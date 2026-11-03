"""The wiki of a project (Wiki tab of a project with [panel] wiki), read-only."""

from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING, Any

from .panel_cache import memo
from .panel_cards import doc_index, doc_ref, doc_rel

if TYPE_CHECKING:
    from pathlib import Path

    from .config import Project

Page = dict[str, Any]  # wiki_page(): {n, path, title, type, status, tags, updated, sources, kb, md, plain}

# ---------- the wiki, read-only (Wiki tab of a project with [panel] wiki) ----------
# Only .md files under the wiki folder that are not hidden, looked up by name (the file name without .md, the page id
# in [[links]]): a request never carries a path, so it cannot leave the folder. Texts are cached by mtime.
FRONT = re.compile(r"\A---\n(.*?)\n---\n?", re.S)


def wiki_dir(project: Project) -> Path | None:
    rel = project.section("panel").get("wiki") or ""
    if not rel:
        return None
    d = (project.home / rel).resolve()
    try:
        d.relative_to(project.home.resolve())
    except ValueError:
        return None
    return d if d.is_dir() else None


def wiki_files(project: Project) -> dict[str, Path]:
    d = wiki_dir(project)
    if not d:
        return {}

    def build() -> dict[str, Path]:
        out = {}
        for f in sorted(d.rglob("*.md")):
            rel = f.relative_to(d)
            if any(x.startswith(".") for x in rel.parts) or not f.resolve().is_relative_to(d):
                continue
            out.setdefault(f.stem, f)
        return out

    return memo(("wikifiles", project.name), build, ttl=5)


def front_matter(txt: str) -> tuple[dict[str, Any], str]:
    """Simple YAML front matter (key: value and [a, b] lists) -> (dict, body)."""
    m = FRONT.match(txt)
    if not m:
        return {}, txt
    meta = {}
    for line in m.group(1).splitlines():
        k, sep, v = line.partition(":")
        if not sep or not re.fullmatch(r"[\w-]+", k.strip()):
            continue
        v = v.strip()
        if v.startswith("[") and v.endswith("]"):
            v = [x.strip().strip("'\"") for x in v[1:-1].split(",") if x.strip()]
        else:
            v = v.strip("'\"")
        meta[k.strip()] = v
    return meta, txt[m.end() :]


def fold(t: str) -> str:
    """Lower case without accents, for the search."""
    return "".join(c for c in unicodedata.normalize("NFKD", t.lower()) if not unicodedata.combining(c))


def wiki_page(project: Project, f: Path) -> Page:
    d = wiki_dir(project)
    assert d is not None  # `f` comes from wiki_files, which has no pages without the folder

    def load() -> Page:
        txt = f.read_text(errors="replace")
        meta, body = front_matter(txt)
        h1 = re.search(r"^# (.+)$", body, re.M)
        rel = f.relative_to(d)
        folder = rel.parts[0] if len(rel.parts) > 1 else ""
        lst = lambda k: meta.get(k) if isinstance(meta.get(k), list) else []  # noqa: E731
        return {
            "n": f.stem,
            "path": str(f.relative_to(project.home.resolve())),
            "title": h1.group(1).strip() if h1 else f.stem,
            "type": meta.get("type") or (folder[:-1] if folder.endswith("s") else folder) or "overview",
            "status": meta.get("status"),
            "tags": lst("tags"),
            "updated": meta.get("updated"),
            "sources": lst("sources"),
            "kb": round(len(txt.encode()) / 1024, 1),
            "md": body,
            "plain": fold(body),
        }

    return memo(("wiki", str(f)), load, deps=[f])


def wiki_list(project: Project) -> list[dict[str, Any]]:
    """Every page with its header (no text): the Wiki tab groups them by type."""
    keep = ("n", "path", "title", "type", "status", "tags", "updated", "kb")
    return [{k: p[k] for k in keep} for p in (wiki_page(project, f) for f in wiki_files(project).values())]


def wiki_get(project: Project, name: str) -> dict[str, Any] | None:
    f = wiki_files(project).get(name)
    if not f:
        return None
    p = wiki_page(project, f)
    refs = set(re.findall(r"\]\(([^)\s]+)\)", p["md"])) | set(re.findall(r"`([^`\s]+)`", p["md"])) | set(p["sources"])
    ix = doc_index(project)
    docs = {r: d for r in refs if (d := wiki_doc(project, r, ix))}
    return {k: v for k, v in p.items() if k != "plain"} | {"docs": docs}


def wiki_doc(project: Project, target: str, ix: dict[str, str]) -> str | None:
    """A document reference written in a wiki page (link, code or source) -> the path /api/doc serves, or None: from
    the first reviews, notes or report-prefix folder it names, or a bare `name.md`."""
    t = target.split("#", 1)[0].strip()
    home = project.home.resolve()
    roots = [
        str(d.resolve().relative_to(home)) + "/"
        for d in (project.reviews_dir, project.notes_dir)
        if d.resolve().is_relative_to(home)
    ] + project.section("ids").get("report_prefixes", [])
    for r in roots:
        i = t.find(r)
        if i >= 0:
            return doc_rel(project, t[i:])
    return doc_ref(project, t, ix) if re.fullmatch(r"[\w.-]+\.md", t) else None


def plain(md: str) -> str:
    """Markdown excerpt as plain text for the search results."""
    t = re.sub(r"\[\[([^\]|#]+)[^\]]*\]\]", r"\1", md)
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"\*\*|`|^#+ |^\s*[-*] |\|", " ", t, flags=re.M)
    return re.sub(r"\s+", " ", t).strip()


def wiki_search(project: Project, q: str, limit: int = 40) -> list[dict[str, Any]]:
    """Pages with every word of q (accents and case ignored): title hits first, then by number of hits; a short
    excerpt around the first hit."""
    words = fold(q).split()
    if not words:
        return []
    out = []
    for f in wiki_files(project).values():
        p = wiki_page(project, f)
        title = fold(p["title"] + " " + p["n"])
        if not all(w in p["plain"] or w in title for w in words):
            continue
        i = min((p["plain"].find(w) for w in words if w in p["plain"]), default=-1)
        snip = ""
        if i >= 0:
            a = max(0, i - 60)
            snip = ("…" if a else "") + plain(p["md"][a : i + 140]) + "…"
        out.append(
            {
                "n": p["n"],
                "title": p["title"],
                "type": p["type"],
                "status": p["status"],
                "in_title": sum(w in title for w in words),
                "hits": sum(p["plain"].count(w) for w in words),
                "excerpt": snip,
            }
        )
    out.sort(key=lambda x: (-x["in_title"], -x["hits"]))
    return out[:limit]
