"""The checks of a backlog's data: `validate` lists the problems (errors and «warning: …» lines), `hard` keeps the
ones that block a write. Functions that take the Backlog and its data."""

from __future__ import annotations

import re
from collections import Counter

from .backlog import (
    CLOSED,
    DECISION_KEYS,
    DETAIL_KEYS,
    EXTRA_ISSUE_KEYS,
    ISSUE_KEYS,
    ISSUE_STATUSES,
    OUTCOME_KEYS,
    PRIORITIES,
    PROJECT_KEYS,
    PROJECT_STATUSES,
    SCHEMA_VERSION,
    SUB_ID,
    SUB_KINDS,
    SUB_SYMBOLS,
    UNANSWERED_KEYS,
    UNDO,
    WANTED,
    Backlog,
    Data,
    Issue,
    Record,
    valid_date,
)

SOFT = ("duplicate sub-issues", "does not exist", "warning: ")


def hard(errors: list[str]) -> list[str]:
    """The errors that block a write (the soft ones are reported by `check` but do not stop the work)."""
    return [e for e in errors if not any(s in e for s in SOFT)]


def validate(b: Backlog, data: Data, files: bool = True) -> list[str]:
    """Problems (list of strings). files=True also checks plan files and project headers."""
    err = []
    v = data.get("schema_version")
    if v != SCHEMA_VERSION:
        err.append(f"schema_version {v} (expected {SCHEMA_VERSION})")
    pids = [p["id"] for p in data["projects"]]
    for pid in pids:
        if not b.project_id.match(pid):
            err.append(f"bad project id {pid!r}")
    dup = {x for x, n in Counter(pids).items() if n > 1}
    if dup:
        err.append(f"duplicate projects: {', '.join(sorted(dup))}")
    for p in data["projects"]:
        err += unknown_keys(p["id"], p, ("id", "title", "intro", "issues") + PROJECT_KEYS)
        if not p["title"].strip():
            err.append(f"{p['id']}: project without title")
        if files and not p.get("intro", "").strip():
            err.append(f"warning: {p['id']}: project without header text (intro)")
    for g in data["archive"]:
        if g["project"] and g["project"] not in pids:
            err.append(f"archive group {g['project']} is not a project")
    err += validate_projects(b, data)
    ids = {x[1]["id"] for x in b.iter_issues(data)}
    seen = {}
    for pid, i, arch in b.iter_issues(data):
        where = f"{i.get('id')} ({pid}{', archive' if arch else ''})"
        if not b.is_issue_id(i.get("id", "")):
            err.append(f"bad issue id {where}")
        if i["id"] in seen:
            err.append(f"duplicate issue id {i['id']} ({seen[i['id']]} and {pid})")
        seen[i["id"]] = pid
        if i.get("status") not in ISSUE_STATUSES:
            err.append(
                f"{where}: bad status {i.get('status')!r} ({', '.join(ISSUE_STATUSES)}): "
                f"foreman backlog status {i.get('id')} STATUS sets one"
            )
        err += unknown_keys(where, i, ISSUE_KEYS + EXTRA_ISSUE_KEYS + ("acceptance",))
        if arch and i.get("status") not in CLOSED:
            err.append(f"{where}: archived but {i.get('status')}")
        if not i.get("title", "").strip():
            err.append(f"{where}: no title")
        for field in ("title", "blocked", "detail"):
            if "\n" in i.get(field, ""):
                err.append(f"{where}: line break in {field}")
        if i.get("github") and not re.match(r"^[\w.-]+/[\w.-]+#\d+$", i["github"]):
            err.append(f"{where}: bad github reference {i['github']!r} (owner/repo#n)")
        err += validate_issue_fields(b, ids, pid, i, where)
        sids = []
        for s in i["subs"]:
            if not SUB_ID.match(s.get("id", "")):
                err.append(f"{where}: bad sub-issue id {s.get('id')!r}")
            if s.get("status") not in SUB_SYMBOLS:
                err.append(f"{where} {s.get('id')}: bad sub-issue status {s.get('status')!r}")
            if "kind" in s and s["kind"] not in SUB_KINDS:
                err.append(f"{where} {s.get('id')}: bad sub-issue kind {s['kind']!r} ({'|'.join(SUB_KINDS)}, or none)")
            if files and s.get("report") and not (b.root / s["report"]).is_file():
                err.append(f"{where} {s.get('id')}: report {s['report']} does not exist")
            sids.append(s.get("id"))
        dup = {x for x, n in Counter(sids).items() if n > 1}
        if dup:
            err.append(f"{where}: duplicate sub-issues {', '.join(sorted(dup))}")
        if files:
            for p in i["plan"]:
                if not (b.root / p["path"]).is_file():
                    err.append(f"{where}: plan {p['path']} does not exist")
        err += validate_outcome(b, data, i, where, arch)
    dids = [d["id"] for d in data["decisions"]]
    dup = {x for x, n in Counter(dids).items() if n > 1}
    if dup:
        err.append(f"duplicate decisions: {', '.join(sorted(dup))}")
    for d in data["decisions"] + data["decisions_resolved"]:
        if "by" in d and not isinstance(d["by"], str):
            err.append(f"decision {d['id']}: by is not a string")
        if "free" in d and d["free"] is not True:
            err.append(f"decision {d['id']}: free is not true")
        err += unknown_keys(f"decision {d['id']}", d, DECISION_KEYS)
        err += validate_decision_extras(b, d)
    for d in data["decisions"]:
        if not d.get("options"):
            err.append(f"decision {d['id']}: no options")
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", d.get("date", "")):
            err.append(f"decision {d['id']}: bad date {d.get('date')!r}")
        if d.get("recommended") and d["recommended"] not in d.get("options", []):
            err.append(f"decision {d['id']}: recommended option {d['recommended']!r} is not one of its options")
        if "blocks" in d:
            if not isinstance(d["blocks"], list):
                err.append(f"decision {d['id']}: blocks is not a list")
            else:
                bad, doubtful = b.hold_problems(data, d["blocks"])
                err += [f"decision {d['id']} blocks {e}" for e in bad]
                err += [f"warning: decision {d['id']} blocks {w}" for w in doubtful]
        if d.get("as_is"):
            err.append(
                f"warning: decision {d['id']} was recorded with --as-is: it names things the owner would "
                "have to ask about (foreman plain; docs/owner.md)"
            )
    err += validate_aliases(b, data)
    err += validate_block_cycles(data)
    return err


def validate_decision_extras(b: Backlog, d: Record) -> list[str]:
    """The problems of a decision's optional `details`, `unanswered` and `page` (Backlog.SCHEMA): each option's
    effect, cost and undo (yes, hard or no) for one of its options, what waits without an answer and its date, and a
    page under the folders of documents (a pending decision's page that is missing is a warning)."""
    where = f"decision {d['id']}"
    err = []
    if "details" in d:
        det = d["details"]
        if not isinstance(det, dict):
            err.append(f"{where}: details is not an object")
            det = {}
        for o, x in det.items():
            if o not in d.get("options", []):
                err.append(f"{where}: details for {o!r}, which is not one of its options")
            if not isinstance(x, dict):
                err.append(f"{where}: details of {o!r} is not an object")
                continue
            for k, v in x.items():
                if k not in DETAIL_KEYS:
                    err.append(f"warning: {where}: details of {o!r}: unknown key {k} (dropped at the next write)")
                elif not isinstance(v, str) or not v.strip():
                    err.append(f"{where}: details of {o!r}: {k} is not a text")
            if "undo" in x and x["undo"] not in UNDO:
                err.append(f"{where}: details of {o!r}: undo {x['undo']!r} is not one of {', '.join(UNDO)}")
    if "unanswered" in d:
        u = d["unanswered"]
        if not isinstance(u, dict):
            err.append(f"{where}: unanswered is not an object")
        else:
            err += unknown_keys(f"{where} unanswered", u, UNANSWERED_KEYS)
            if not isinstance(u.get("waits"), str) or not u["waits"].strip():
                err.append(f"{where}: unanswered has no waits (what stays stopped until the owner answers)")
            if "by" in u and not valid_date(u["by"]):
                err.append(f"{where}: unanswered by {u['by']!r} is not a date (YYYY-MM-DD)")
    if "page" in d:
        bad = b.page_problem(d["page"])
        if bad:
            err.append(f"{where}: page {bad}")
        elif not d.get("resolved") and not b.page_path(d["page"]).is_file():
            err.append(f"warning: {where}: page {d['page']} is missing")
    return err


def unknown_keys(where: str, obj: dict[str, object], known: tuple[str, ...]) -> list[str]:
    """A warning for the keys of a project or an issue this foreman does not know (a field it no longer has, or a
    newer foreman's): they are dropped at the next write (Backlog.canon), as config.py ignores an unknown key."""
    extra = sorted(k for k in obj if k not in known)
    return [f"warning: {where}: unknown keys {', '.join(extra)} (dropped at the next write)"] if extra else []


def validate_block_cycles(data: Data) -> list[str]:
    """A warning per cycle of `relations.blocks` among open issues: none of them would ever be unblocked."""
    edges = {
        i["id"]: (i.get("relations") or {}).get("blocks", [])
        for _, i, _ in Backlog.iter_issues(data, archived=False)
        if i["status"] not in CLOSED
    }
    err, done = [], set()
    for start in edges:
        if start in done:
            continue
        path, on_path, stack = [start], {start}, [(start, iter(edges[start]))]
        while stack:
            node, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                stack.pop()
                on_path.discard(path.pop())
                done.add(node)
            elif nxt in on_path:
                cycle = path[path.index(nxt) :]
                key = min(range(len(cycle)), key=lambda k: cycle[k])
                cycle = cycle[key:] + cycle[:key]
                line = f"warning: blocks cycle: {' → '.join(cycle + [cycle[0]])} (none of them can be unblocked)"
                if line not in err:
                    err.append(line)
            elif nxt in edges and nxt not in done:
                stack.append((nxt, iter(edges[nxt])))
                path.append(nxt)
                on_path.add(nxt)
    return err


def validate_projects(b: Backlog, data: Data) -> list[str]:
    """The fields of the projects (status, priority, repos)."""
    err = []
    repos = set(b.project.repos)
    for p in data["projects"]:
        where = p["id"]
        if p.get("status") and p["status"] not in PROJECT_STATUSES:
            err.append(f"{where}: bad project status {p['status']!r} ({', '.join(PROJECT_STATUSES)})")
        if p.get("priority") and p["priority"] not in PRIORITIES:
            err.append(f"{where}: bad priority {p['priority']!r}")
        for r in p.get("repos", []):
            if repos and r not in repos:
                err.append(f"{where}: repo {r} is not in foreman.toml")
    return err


def validate_issue_fields(b: Backlog, ids: set[str], pid: str, i: Issue, where: str) -> list[str]:
    err = []
    if i.get("priority") and i["priority"] not in PRIORITIES:
        err.append(f"{where}: bad priority {i['priority']!r} ({', '.join(PRIORITIES)})")
    if i.get("wanted") and not (isinstance(i["wanted"], str) and WANTED.match(i["wanted"])):
        err.append(f"{where}: wanted {i['wanted']!r} is not a YYYY-MM-DD HH:MM time")
    if i.get("repo") and b.project.repos and i["repo"] not in b.project.repos:
        err.append(f"{where}: repo {i['repo']} is not in foreman.toml")
    rel = i.get("relations") or {}
    if set(rel) - {"blocks", "related", "duplicate_of"}:
        err.append(f"{where}: bad relations {sorted(rel)}")
    for k in ("blocks", "related"):
        for x in rel.get(k, []):
            if x not in ids or x == i["id"]:
                err.append(f"warning: {where}: {k} {x}, which is not another issue")
    if rel.get("duplicate_of") and rel["duplicate_of"] not in ids:
        err.append(f"warning: {where}: duplicate of {rel['duplicate_of']}, which does not exist")
    return err


def validate_outcome(b: Backlog, data: Data, i: Issue, where: str, archived: bool) -> list[str]:
    """The outcome block, the result and the closing date of one issue."""
    err = []
    ok = "acceptance"
    if b.is_legacy(i["id"]) and not archived:
        err.append(f"{where}: a legacy id is only valid in the archive")
    h = i.get(ok)
    if h is not None:
        if not isinstance(h, dict) or set(h) - set(OUTCOME_KEYS):
            err.append(f"{where}: bad {ok} block (keys: {', '.join(OUTCOME_KEYS)})")
            return err
        if not h.get("criterion", "").strip():
            err.append(f"{where}: {ok} without criterion")
        for field in ("claim", "criterion"):
            if "\n" in h.get(field, ""):
                err.append(f"{where}: line break in {ok} {field}")
        if h.get("verdict") and h["verdict"] not in b.verdicts:
            err.append(f"{where}: bad verdict {h['verdict']!r} ({', '.join(b.verdicts)})")
        for a in h.get("aliases", []):
            if not b.is_legacy(a):
                err.append(f"{where}: bad alias {a!r} (must match ids.legacy)")
    if "\n" in i.get("result", ""):
        err.append(f"{where}: line break in result")
    if i.get("closed"):
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", i["closed"]):
            err.append(f"{where}: bad closing date {i['closed']!r}")
        if i["status"] not in CLOSED:
            err.append(f"{where}: closed on {i['closed']} but {i['status']}")
        elif i["status"] == "duplicate":
            pass
        elif not i.get("result"):
            err.append(
                f'warning: {where}: closed on {i["closed"]} without result: foreman backlog edit {i["id"]} --result "…"'
            )
        elif h and not h.get("verdict"):
            err.append(f"warning: {where}: closed without the verdict of its criterion: --verdict")
    return err


def validate_aliases(b: Backlog, data: Data) -> list[str]:
    """Each legacy id names one issue: as its id or as an alias."""
    seen, err = {}, []
    for _, i, _ in b.iter_issues(data):
        names = [i["id"]] if b.is_legacy(i["id"]) else []
        for a in names + list((i.get("acceptance") or {}).get("aliases", [])):
            if a in seen:
                err.append(f"alias {a} in {seen[a]} and {i['id']}")
            seen[a] = i["id"]
    return err
