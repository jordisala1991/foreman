"""The backlog and its documents in the panel: reports and documents (doc_*), the cards (backlog_cards), what is live
about them (issue_live), the short cards, the serialized body and the recommended option of a decision.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import threading
from collections.abc import Callable, Collection, Iterable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .backlog import CLOSED, STATUS_CATEGORY, Backlog, BacklogError
from .panel_cache import memo, page_version
from .panel_registry import REG

if TYPE_CHECKING:
    from .backlog import Issue, Record
    from .config import Project

Card = dict[str, Any]  # a card of the page: an issue as backlog_cards builds it (its keys are the ones that apply)
Cards = dict[str, Any]  # backlog_cards(): {projects, issues, archive, decisions, counts, schema} (or error)


def clean(t: str | None) -> str:
    """Markdown title -> plain text (no bold, code or links)."""
    t = re.sub(r"\*\*|`", "", t or "")
    t = re.sub(r"\[\[([^\]]+)\]\]", r"\1", t)
    return re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t).strip()


def lead(text: str | None, limit: int = 240) -> str:
    """The first sentence of a decision's text, as plain text, for its card: up to the first «.», «?», «!» or «…»
    followed by a space and a capital letter (or an opening mark), or the first line break; cut at `limit`."""
    t = clean(text)
    m = re.search(r"[.?!…](?=\s+[«¿¡\"'(\dA-ZÁÉÍÓÚÑÜ])|\n", t)
    first = (t[: m.start() + (0 if t[m.start()] == "\n" else 1)] if m else t).strip()
    return first if len(first) <= limit else first[: limit - 1].rstrip() + "…"


_REC_LEAD = re.compile(r"^(?:i\s+)?(?:recomiendo|recommended|recommend|recomendada|recomendado)\s*:?\s*", re.I)


def recommended_option(options: Iterable[str], recommendation: str) -> str:
    """The option the recommendation names, or "" when it names none or several equally. It names an option when
    it starts with it («sí: cuesta…», «a: …», «I recommend yes…») or quotes it («recomiendo «no» porque…»); an
    option «a: yes» also answers to «a» and to «yes». The longest match wins, so «sí, con aviso» beats «sí»."""

    def norm(x: str) -> str:
        return re.sub(r"\s+", " ", clean(x).lower().strip(" «»\"'“”"))

    rec = norm(recommendation)
    if not rec:
        return ""
    bare = _REC_LEAD.sub("", rec)
    best, score = [], 0
    for o in options:
        full = norm(o)
        m = re.match(r"^(\w{1,3})\s*[:)]\s*(.+)$", full)
        forms = {full} | ({m.group(2), m.group(1)} if m else set())
        n = 0
        for f in forms:
            if not f:
                continue
            starts = any(re.match(re.escape(f) + r"(?!\w)", r) for r in (rec, bare))
            quoted = any(q.format(f) in rec for q in ("«{}»", '"{}"', "“{}”"))
            if starts or quoted:
                n = max(n, len(f))
        if n > score:
            best, score = [o], n
        elif n and n == score:
            best.append(o)
    return best[0] if len(best) == 1 else ""


def doc_roots(project: Project) -> list[Path]:
    return [project.reviews_dir, project.notes_dir] + [
        project.home / x for x in project.section("ids").get("report_prefixes", [])
    ]


def home_file(project: Project, path: str | None, exts: tuple[str, ...]) -> str | None:
    """`path` as given if it names an existing file with one of the extensions `exts` (lowercase, with their dot)
    under the reviews or notes folders or a report prefix of the home, once resolved (so a symbolic link that leads
    out is refused); relative to the home, or absolute if it is under those folders of any registered product (a
    backlog moved from another product links its reports where they are). None otherwise: a «..», a NUL, another
    extension or another place."""
    if not path or "\0" in path or ".." in Path(path).parts or not path.lower().endswith(exts):
        return None
    f = project.home / path
    roots = doc_roots(project)
    if path.startswith("/"):
        roots = [r for p in [project, *REG.projects.values()] for r in doc_roots(p)]
    for r in roots:
        try:
            f.resolve().relative_to(r.resolve())
        except (OSError, ValueError):
            continue
        if f.is_file():
            return path
    return None


def doc_rel(project: Project, path: str | None) -> str | None:
    """`path` if it is a readable .md the panel may show (home_file)."""
    return home_file(project, path, (".md",))


def doc_dirs(project: Project) -> list[Path]:
    """The folders reports are looked up in: the reviews folder and the report prefixes."""
    return [project.reviews_dir] + [project.home / x for x in project.section("ids").get("report_prefixes", [])]


def doc_index(project: Project) -> dict[str, str]:
    """file name -> relative path of every .md under the reviews folder and the report prefixes, to resolve bare
    citations such as `2026-10-24-x1.md` (cached 60 s)."""
    dirs = doc_dirs(project)

    def build() -> dict[str, str]:
        ix = {}
        for d in dirs:
            if d.is_dir():
                for f in sorted(d.rglob("*.md")):  # an absolute report prefix (another product) stays absolute
                    ix.setdefault(
                        f.name, str(f.relative_to(project.home)) if f.is_relative_to(project.home) else str(f)
                    )
        return ix

    # rebuilt when a folder changes (a new file at its top) or after 60 s (new files deeper down)
    return memo(("docix", str(project.home)), build, ttl=60, deps=dirs)


def doc_ref(project: Project, target: str | None, ix: dict[str, str] | None = None) -> str | None:
    """A path, or a bare file name, written in a text -> the path /api/doc serves, or None."""
    t = (target or "").split("#", 1)[0].strip()
    if t.startswith("/"):
        return doc_rel(project, t)
    t = t.lstrip("./")
    if not t.endswith(".md"):
        return None
    if "/" in t:
        return doc_rel(project, t)
    ix = doc_index(project) if ix is None else ix
    return doc_rel(project, ix.get(t, ""))


RECENT_ANSWERED = 20  # resolved decisions the cards keep (decisions_answered), to show the unread answers


def backlog_cards(project: Project) -> Cards:
    """{projects: [{id, title, issues: [card]}], archive: [card], decisions: [...], decisions_answered: [...], counts}
    from the backlog file; re-read only when the file or a reports folder changes (a new report links from its card).
    The same object comes back until then: nothing may change it (the live part goes apart, issue_live), and
    backlog_body keeps its bytes."""
    b = Backlog(project)

    def parse() -> Cards:
        if not b.exists():
            return {"projects": [], "archive": [], "decisions": [], "counts": {}, "missing": True}
        data = b.load()
        ix = doc_index(project)

        def sub(s: Record) -> dict[str, Any]:
            out = {
                k: s[k]
                for k in ("id", "title", "status", "commit", "agent", "branch", "moved_to", "text", "report")
                if s.get(k)
            }
            ref = s.get("commit") or s.get("agent") or s.get("branch") or s.get("moved_to") or ""
            text = s.get("text", "")
            if not ref and text and not text.startswith("("):  # a report named first in the text
                ref, _, text = text.partition(" ")
                ref = ref.strip("`,;")
            if ref:
                out["ref"] = ref
                if ref.endswith(".md"):
                    out["doc"] = doc_ref(project, ref, ix)
            if text.strip():
                out["note"] = text.strip()
            return out

        def reports(i: Issue, plan: Collection[str | None]) -> list[dict[str, Any]]:
            """Each sub-issue's report, then the other .md files the detail cites."""
            out, seen = [], set(plan)
            for s in i.get("subs", []):
                r = doc_ref(project, s.get("report", ""), ix)
                if r and r not in seen:
                    out.append({"sub": s["id"], "doc": r})
                    seen.add(r)
            for n in re.findall(r"[\w./-]+\.md", i.get("detail", "")):
                r = doc_ref(project, n, ix)
                if r and r not in seen:
                    out.append({"doc": r})
                    seen.add(r)
            return out

        def card(pid: str, i: Issue, archived: bool) -> Card:
            plan = [p.get("path") for p in i.get("plan", []) if doc_rel(project, p.get("path", ""))]
            out = {
                "id": i["id"],
                "project": pid,
                "title": clean(i["title"]),
                "status": i["status"],
                "blocked": i.get("blocked", ""),
                "detail": i.get("detail", ""),
                "subs": [sub(s) for s in i.get("subs", [])],
                "plan": plan,
                "reports": reports(i, plan),
            }
            h = i.get("acceptance")
            if h:
                out["outcome"] = {
                    k: v
                    for k, v in (
                        ("claim", h.get("claim")),
                        ("criterion", h.get("criterion")),
                        ("verdict", b.verdict_label(data, i)),
                        ("aliases", h.get("aliases")),
                    )
                    if v
                }
            fields = ("result", "closed", "github", "priority", "assignee", "repo")
            for k in fields + ("wanted",):
                if i.get(k):
                    out[k] = i[k]
            rel = {k: v for k, v in b.relations_of(data, i["id"]).items() if v}
            if rel:
                out["relations"] = rel
            if rel_blockers.get(i["id"]) and i["status"] not in CLOSED:  # open blockers: it is blocked
                out["blocked_rel"] = rel_blockers[i["id"]]
            if archived:
                out["archived"] = True
            return out

        rel_blockers = b.relation_blockers(data)

        def derived_status(p: Record) -> str:
            """A project without its own status (not set yet) gets one from its issues."""
            st = [i["status"] for i in p["issues"]]
            if any(STATUS_CATEGORY.get(x) == "started" for x in st):
                return "in progress"
            live = [x for x in st if STATUS_CATEGORY.get(x) not in ("canceled", "triage")]
            if live and all(x == "done" for x in live):
                return "completed"
            return "planned" if any(STATUS_CATEGORY.get(x) == "unstarted" for x in st) else "backlog"

        def project_card(p: Record) -> dict[str, Any]:
            out = {
                "id": p["id"],
                "title": clean(p["title"]),
                "issues": [card(p["id"], i, False) for i in p["issues"]],
                "status": p.get("status") or derived_status(p),
            }
            if not p.get("status"):
                out["status_derived"] = True
            for k in ("priority", "repos"):
                if p.get(k):
                    out[k] = p[k]
            return out

        projects = [project_card(p) for p in data["projects"]]
        loose = [card("", i, False) for i in data.get("issues", [])]
        archive = [card(g["project"], i, True) for g in data["archive"] for i in g["issues"]]

        def decision(d: Record) -> dict[str, Any]:
            return {
                "id": d.get("id", ""),
                "title": clean(f"{d['id']}, {d['title']}" if d.get("id") else d["title"]),
                "text": d.get("text", ""),  # whole: the detail shows it all (the card shows only its lead)
                "lead": lead(d.get("text", "")),
                "recommendation": d.get("recommendation", "")[:300],
                "recommended": (
                    d["recommended"]
                    if d.get("recommended") in d.get("options", [])
                    else recommended_option(d.get("options", []), d.get("recommendation", ""))
                ),
                "options": d.get("options", []),
                "date": d.get("date"),
                # what each option changes, what waits until the answer and the page: only when the decision has them,
                # so a decision of before is sent as before
                **{k: d[k] for k in ("details", "unanswered", "page") if d.get(k)},
            }

        decisions = [decision(d) for d in data.get("decisions", [])]
        # the last resolved ones with their answer: the state shows those the panel resolved until the orchestrator
        # reads the answer (panel.decisions_with_answers); not sent in /api/backlog (BACKLOG_KEYS)
        answered = [
            {**decision(d), "answer": d.get("answer", ""), **({"free": True} if d.get("free") else {})}
            for d in data.get("decisions_resolved", [])[-RECENT_ANSWERED:]
        ]
        return {
            "projects": projects,
            "issues": loose,
            "archive": archive,
            "decisions": decisions,
            "decisions_answered": answered,
            "counts": b.counts(data),
            "schema": data.get("schema_version"),
        }

    try:
        return memo(("backlog", project.name, str(b.data_path)), parse, deps=[b.data_path, *doc_dirs(project)])
    except (BacklogError, OSError, ValueError, KeyError) as e:
        return {"projects": [], "archive": [], "decisions": [], "counts": {}, "error": str(e)}


def issue_live(
    bl: Cards, agent_list: list[dict[str, Any]], live_branches: Collection[str] = ()
) -> dict[str, dict[str, Any]]:
    """What is live about the issues, apart from the backlog (which only changes with its file): each live agent goes
    with its issue (identity.of in agents(): recorded at launch, or «Issue: ID» in its brief) or the one whose
    sub-issue names its name or branch; an issue with a live agent shows as in progress, and one with its own branch
    fills a production slot. An issue in progress with no agent and no unmerged branch is flagged `no_agent`.
    -> {issue id: {agent, agent_status, branch, production, status, no_agent}} with only what applies; the page lays
    it over the backlog's cards. The cards are not touched; an agent without an issue gets the one it was joined to."""
    live = [a for a in agent_list if not a["end"]]
    out = {}
    for p in bl["projects"] + [{"issues": bl.get("issues", [])}]:
        for c in p["issues"]:
            x = {}
            names = {n for s in c["subs"] for n in (s.get("agent"), s.get("branch")) if n}
            for a in live:
                if a["issue"] == c["id"] or a["name"] in names or (a["branch"] and a["branch"] in names):
                    x["agent"], x["agent_status"] = a["name"], a["status"]
                    if a["branch"]:
                        x["branch"], x["production"] = a["branch"], True
                    if not a["issue"]:
                        a["issue"] = c["id"]
                    if c["status"] in ("backlog", "ready"):
                        x["status"] = "in progress"
            if not x.get("branch"):
                cited = re.findall(r"`([\w./-]+)`", c["detail"]) + [
                    n for s in c["subs"] for n in (s.get("ref"), s.get("branch")) if n
                ]
                b = next((b for b in cited if b in live_branches), None)
                if b:
                    x["branch"] = b
            if x.get("status", c["status"]) == "in progress" and not x.get("agent") and not x.get("branch"):
                x["no_agent"] = True
            if x:
                out[c["id"]] = x
    return out


BACKLOG_KEYS = ("projects", "issues", "archive", "counts")
_bl_bodies: dict[Any, tuple[int, list[Cards], tuple[bytes, bytes, str]]] = {}
_bl_lock = threading.Lock()
SHORT_SUB = ("id", "title", "status", "ref", "doc")
RECENT_CLOSED = 15  # closed issues per product /api/backlog?closed=recent keeps short, not as stubs


def short_card(c: Card) -> Card:
    """A closed card (done, canceled, duplicate, or archived) as /api/backlog sends it: only what its row in the list
    uses (id, title, status, priority, plan, a blocked reason, the aliases a link may name, and
    its sub-issues without their text), marked `short`; /api/issue gives the whole card when it is opened. An open
    card comes back as it is."""
    if c["status"] not in CLOSED and not c.get("archived"):
        return c
    out = {k: c[k] for k in ("id", "project", "title", "status", "blocked", "priority", "archived") if c.get(k)}
    out["plan"] = c["plan"]
    out["subs"] = [{k: s[k] for k in SHORT_SUB if s.get(k)} for s in c["subs"]]
    aliases = (c.get("outcome") or {}).get("aliases")
    if aliases:
        out["outcome"] = {"aliases": aliases}
    out["short"] = True
    return out


def map_cards(bl: Cards, f: Callable[[Card], Card]) -> Cards:
    """The backlog's cards for /api/backlog with `f` applied to every card in its place (projects' issues, loose
    issues, archive); the other keys of BACKLOG_KEYS as they are."""
    out = {k: bl[k] for k in BACKLOG_KEYS if k in bl}
    if "projects" in out:
        out["projects"] = [{**e, "issues": [f(c) for c in e["issues"]]} for e in out["projects"]]
    for k in ("issues", "archive"):
        if k in out:
            out[k] = [f(c) for c in out[k]]
    return out


def short_backlog(bl: Cards) -> Cards:
    """The backlog's cards for /api/backlog: the closed ones short (short_card), the open ones whole."""
    return map_cards(bl, short_card)


def stub_card(c: Card) -> Card:
    """An old closed card as /api/backlog?closed=recent sends it: only what counts it and links to it (id, status,
    its project when archived, `archived`, the aliases a link may name), marked `stub`; /api/issue gives
    the whole card. A card in a project's issues takes its project from there, so it does not carry it."""
    out = {k: c[k] for k in ("id", "status") if c.get(k)}
    if c.get("archived"):
        if c.get("project"):
            out["project"] = c["project"]
        out["archived"] = True
    aliases = (c.get("outcome") or {}).get("aliases")
    if aliases:
        out["outcome"] = {"aliases": aliases}
    out["stub"] = True
    return out


def trim_closed(bl: Cards, k: int = RECENT_CLOSED) -> Cards:
    """The backlog's cards for /api/backlog?closed=recent: the open ones whole, the `k` most recent closed ones that
    are not archived short (short_card), every other closed or archived card a stub in its place (stub_card), so the
    first load does not grow with old closed issues while counts and links still find every id. Most recent: the
    newest `closed` date first, and without one, the later in the file first (dated ones before undated ones)."""
    live = [c for e in bl.get("projects", []) for c in e["issues"]] + bl.get("issues", [])
    closed = [(str(c.get("closed") or ""), n, c) for n, c in enumerate(live) if c["status"] in CLOSED]
    recent = {id(c) for _, _, c in sorted(closed, key=lambda x: x[:2], reverse=True)[:k]}

    def one(c: Card) -> Card:
        if c["status"] not in CLOSED and not c.get("archived"):
            return c
        return short_card(c) if id(c) in recent else stub_card(c)

    return map_cards(bl, one)


def find_card(bl: Cards, iid: str) -> Card | None:
    """The whole card of issue `iid` in the backlog's cards (its projects, loose issues and archive), or None."""
    for c in [c for e in bl.get("projects", []) for c in e["issues"]] + bl.get("issues", []) + bl.get("archive", []):
        if c["id"] == iid:
            return c
    return None


def backlog_body(projects: Sequence[Project], joint: bool, recent: bool = False) -> tuple[bytes, bytes, str]:
    """(bytes, gzipped bytes, ETag) of /api/backlog for `projects`: serialized again only when one of their
    backlog_cards changes (a backlog file or a reports folder) or the page's version does, so an unchanged poll costs
    a few stat calls and a 304. The ETag is the body's hash, as for every other answer. `recent` (closed=recent):
    each product's old closed issues as stubs (trim_closed), else every closed one short (short_backlog)."""
    cards = [(p.name, backlog_cards(p)) for p in projects]
    key, ver = (joint, tuple(n for n, _ in cards), recent), page_version()
    hit = _bl_bodies.get(key)
    if hit and hit[0] == ver and len(hit[1]) == len(cards) and all(a is b for a, (_, b) in zip(hit[1], cards)):
        return hit[2]
    trim = trim_closed if recent else short_backlog
    one = [{"name": n, "backlog": trim(bl), "backlog_error": bl.get("error")} for n, bl in cards]
    body = {"projects": one} if joint else {"project": one[0]}
    b = json.dumps({**body, "version": ver}, ensure_ascii=False).encode()
    out = (b, gzip.compress(b, 5), '"' + hashlib.sha1(b).hexdigest()[:16] + '"')
    with _bl_lock:
        _bl_bodies[key] = (ver, [bl for _, bl in cards], out)
    return out
