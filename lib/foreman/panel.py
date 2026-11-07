"""`foreman panel`: one web panel for every project of the registry (backlog, agents, locks, branches and PRs,
decisions and inbox, recurring tasks, progress), to look at from the phone.

Forked from an earlier in-house version (2026-10-30) and generalised: each view takes a project of the
registry; branches and PRs come from its [repos]; the locks section is common (every resource any project declares,
and every machine resource of the registry, with the machine's table whatever the registry order).
Writes: an inbox message, a decision answer (one of its options, or the owner's own words marked free: it resolves
the decision in the backlog at once, under its lock, and tells the orchestrator in the inbox; the inbox alone, 202,
when the backlog cannot be saved), a change of that answer while the orchestrator has not read it
(change_answer), a PR approval (an inbox message the orchestrator turns into `foreman integrate
--approved`), a backlog move (Backlog.rank_issue, noted in the inbox), the owner's mark on a triage issue
(«I want it done», Backlog.want, noted in the inbox; active products only), its discard from triage («I don't
want it», Backlog.reject: canceled with the panel's reason, noted in the inbox; active products only), the
loop pause (active products only), the owner taking or releasing a lock of a resource an active product uses,
or adding 30 min to it, and the owner approving a `[deny.grants]` request the hooks module's question left
unanswered (grant_approve: the grant is written for the marked session and its process only, signed with the
panel's grant key; like every write, it needs the panel's key, and the guard refuses a Bash call that reaches its
endpoint: guard.PANEL_GRANT). A
resource with `busy` shows what that command prints (BusyProbe: in the background, cached a few seconds, with a time
limit).

The interface texts live in panel/strings.<language>.json; the language is the project's [panel] language, else the
registry's [panel] language, else English (/api/strings?lang=). Every request carries the key (?t=…) kept in
<state root>/panel/token.
Speed: git, gh and file parses are cached (memo) until their files change or a short TTL passes; answers carry an
ETag and go gzipped. The backlog's cards come apart from the polled state (/api/backlog, serialized once per change of
their files, the closed ones short: /api/issue gives a whole card; with closed=recent only each product's
RECENT_CLOSED most recent closed ones, the older ones and the archive as stubs that still count and link); the state
carries only what is live about them (issue_live).
Documents (/api/doc, .md) and the images they show (/api/file: PNG, JPEG, GIF or WebP whose first bytes are their
format's, up to FILE_MAX, with nosniff and a sandbox CSP; never SVG) come only from the reviews, notes and report
folders of a registered product (panel_cards.home_file); anything else is a 404.

Where things are: panel_cache (memo, file_sig, the page version), panel_registry (REG), panel_locks (OwnerLock,
BusyProbe, locks), panel_cards (documents, backlog cards, short cards, recommended_option), panel_wiki (the wiki);
this module has the agents, repos, the system, the state, the writes (act) and the server. The panel's texts load in
i18n.panel_strings.
"""

from __future__ import annotations

import contextlib
import errno
import gzip
import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import sys
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, quote, urlparse

from . import (
    agents as agents_mod,
)
from . import (
    config,
    costs,
    heartbeat,
    hooks_mod,
    i18n,
    identity,
    install,
    lock,
    notes,
    orchestrator,
    pause,
    transcripts,
    util,
    versions,
)
from .backlog import Backlog, BacklogError
from .panel_cache import STATIC, iso_age, iso_ts, memo, page_version, sh
from .panel_cards import backlog_body, backlog_cards, clean, doc_rel, find_card, home_file, issue_live
from .panel_locks import OWNER, locks
from .panel_registry import REG
from .panel_wiki import wiki_dir, wiki_get, wiki_list, wiki_search

if TYPE_CHECKING:
    from collections.abc import Callable, Collection

    from .config import Project, Table
    from .lock import Status

Obj = dict[str, Any]  # a JSON object of an answer: its keys are the ones the page reads (see each function)
Query = dict[str, list[str]]  # urllib.parse.parse_qs

ASSETS = {"/app.css": ("app.css", "text/css; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8")}
DOC_MAX = 400_000
FILE_MAX = 5_000_000  # the largest image /api/file serves
# the images /api/file serves: extension -> (type, whether the first bytes are that format's). No SVG: it can carry
# script and, opened as a page, would run with the panel's origin
IMAGES = {
    ".png": ("image/png", lambda b: b.startswith(b"\x89PNG\r\n\x1a\n")),
    ".jpg": ("image/jpeg", lambda b: b.startswith(b"\xff\xd8\xff")),
    ".jpeg": ("image/jpeg", lambda b: b.startswith(b"\xff\xd8\xff")),
    ".gif": ("image/gif", lambda b: b[:6] in (b"GIF87a", b"GIF89a")),
    ".webp": ("image/webp", lambda b: b[:4] == b"RIFF" and b[8:12] == b"WEBP"),
}
# what an image answer carries besides its type: never read as another type, never run as a page
FILE_HEADERS = {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox"}


def home_image(project: Project, path: str) -> tuple[bytes, str, str] | None:
    """(bytes, type, ETag) of an image a document of `project` shows (/api/file): a PNG, JPEG, GIF or WebP under
    the folders of documents (panel_cards.home_file), up to FILE_MAX, whose first bytes are its format's; else None."""
    rel = home_file(project, path, tuple(IMAGES))
    if not rel:
        return None
    f = project.home / rel
    try:
        st = f.stat()
        with open(f, "rb") as fh:
            data = fh.read(FILE_MAX + 1)
    except OSError:
        return None
    ctype, looks = IMAGES[Path(rel).suffix.lower()]
    if len(data) > FILE_MAX or not looks(data):
        return None
    return data, ctype, f'"{st.st_mtime_ns:x}-{st.st_size:x}"'


# ---------- agents ----------


def context_tokens(path: util.PathLike) -> int | None:
    """The agent's context tokens: those of its last response, as transcripts.context defines them (an agent's
    own file is all sidechain records)."""
    return transcripts.last_context(path, sidechain=True)


LAUNCH_LINE = re.compile(r"^\[foreman launch [a-z2-7]{10}\][^\n]*\n?")


def agents(project: Project, lock_list: list[Status], branch_of_agent: dict[str, str]) -> list[Obj]:
    """Agents running, waiting for their own background task, or finished (delivered or stopped) in the last 15 min;
    the state is agents_mod.agent_state, the same one the loop pass and `foreman agents` use. Name, issue and branch are
    identity.of: what was recorded at launch (or by hand) first, then the brief («Issue: ID», «Branch `y`») and
    `branch_of_agent` (the backlog's sub-issue with the agent's name) for what is missing."""
    holders, waiting = lock.people(lock_list)
    out = []
    for aid, h in agents_mod.agents(project).items():
        path = h.get("transcript")
        if not path:  # internal subagents (no transcript): not the orchestrator's agents
            continue
        state = agents_mod.agent_state(h)
        own_wait = state == "waiting"
        end = None
        if state not in agents_mod.LIVE_STATES:
            end = h.get("end") or h.get("delivered") or h.get("activity") or h.get("start")
            if (iso_age(end) or 0) > 900:
                continue
        task = memo(("first", path), lambda path=path: transcripts.first_prompt(path), deps=[path])
        who = identity.of(project, h, task, branch_of_agent)  # recorded first, the brief only for what is missing
        name = who["name"] or f"{h.get('type') or 'agent'} {aid[:6]}"
        short = LAUNCH_LINE.sub("", task.strip(), count=1).strip() or task
        a = {
            "id": aid,
            "name": name,
            "type": h.get("type"),
            "state": state,
            "start": h.get("start"),
            "end": end,
            "tool": h.get("tool"),
            "final": h.get("final"),
            "own_wait": own_wait,
            "issue": who["issue"],
            "branch": who["branch"],
            "task": short.strip()[:200],
            "transcript": path,
            "outcome": h.get("outcome") if state == "delivered" else None,
        }
        last = agents_mod.last_sign(h)
        a["activity_ts"] = None if last is None else int(last)
        with contextlib.suppress(OSError):  # a transcript gone: no last message nor context
            os.stat(path)
            a["last"] = memo(
                ("last", path), lambda path=path: (transcripts.last_text(path) or "")[:600] or None, deps=[path]
            )
            a["context"] = memo(("ctx", path), lambda path=path: context_tokens(path), deps=[path])
        a["start_ts"] = iso_ts(h.get("start"))
        a["status"] = (
            "finished"
            if end
            else "holds a lock"
            if name in holders
            else "waiting for a lock"
            if name in waiting
            else "maybe stuck"
            if agents_mod.quiet_minutes(project, h) is not None
            else "waiting for its task"
            if own_wait
            else "working"
        )
        out.append(a)
    return sorted(out, key=lambda a: (bool(a["end"]), a.get("start") or ""))


# ---------- branches and pull requests ----------


def git(path: util.PathLike, *args: str, ttl: float = 10) -> str:
    return memo(("git", str(path)) + args, lambda: util.git(path, *args, check=False, timeout=10), ttl=ttl)


def prs(repo: Table) -> list[Obj] | None:
    """Open PRs of the repo on GitHub ([] without `github`, None while the first answer is on its way)."""
    if not repo.get("github") or os.environ.get("FOREMAN_PANEL_PRS") == "0":
        return []
    gh = os.environ.get("FOREMAN_GH") or "gh"

    def fetch() -> list[Obj]:
        out = sh(
            gh,
            "pr",
            "list",
            "-R",
            repo["github"],
            "--state",
            "open",
            "--limit",
            "30",
            "--json",
            "number,title,headRefName,isDraft,url,statusCheckRollup,reviewDecision",
            timeout=20,
        )
        try:
            items = json.loads(out)
        except ValueError:
            return []
        res = []
        for x in items:
            checks = x.get("statusCheckRollup") or []
            states = [(c.get("conclusion") or c.get("state") or c.get("status") or "").upper() for c in checks]
            fail = sum(s in ("FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED") for s in states)
            ok = sum(s in ("SUCCESS", "NEUTRAL", "SKIPPED") for s in states)
            res.append(
                {
                    "number": x.get("number"),
                    "title": x.get("title"),
                    "branch": x.get("headRefName"),
                    "draft": x.get("isDraft"),
                    "url": x.get("url"),
                    "review": x.get("reviewDecision"),
                    "checks": {"ok": ok, "fail": fail, "pending": len(states) - ok - fail},
                }
            )
        return res

    return memo(("prs", repo["github"]), fetch, ttl=120, initial=None)


def repo_view(project: Project, key: str, live_branches: Collection[str]) -> Obj:
    r = project.repo(key)
    path = r["path"]
    view = {"key": key, "github": r.get("github") or "", "merge": r["merge"], "branches": [], "today": [], "prs": []}
    if not path or not (Path(path) / ".git").exists():
        view["missing"] = True
        return view
    main = r.get("main") or "main"
    wts = {}
    for block in git(path, "worktree", "list", "--porcelain").split("\n\n"):
        lines = block.split("\n")
        br = [ln.removeprefix("branch refs/heads/") for ln in lines if ln.startswith("branch ")]
        if br:
            wts[br[0]] = lines[0].removeprefix("worktree ")
    last = {}
    for ln in git(
        path, "for-each-ref", "refs/heads", "--format=%(refname:short)|%(committerdate:unix)|%(subject)"
    ).splitlines():
        b, _, rest = ln.partition("|")
        last[b] = rest.split("|", 1)
    open_prs = prs(r)
    view["prs"] = open_prs
    pr_of = {p["branch"]: p for p in open_prs or []}
    unmerged = git(path, "branch", "--no-merged", main, "--format=%(refname:short)").split()
    for b in unmerged + sorted(b for b in wts if b != main and b not in unmerged):
        info = last.get(b, ["0", ""])
        col = "working" if b in live_branches else "pr" if b in pr_of else "ready"
        view["branches"].append(
            {
                "branch": b,
                "committed_ts": int(info[0]) if info[0] else None,
                "subject": info[-1][:140],
                "col": col,
                "worktree": wts.get(b),
                "pr": pr_of.get(b, {}).get("number"),
            }
        )
    for ln in git(
        path,
        "log",
        main,
        "--first-parent",
        f"--since={datetime.now():%F} 00:00",
        "--format=%h|%ad|%s",
        "--date=format:%H:%M",
        "-n",
        "20",
    ).splitlines():
        h, at, msg = (ln.split("|", 2) + ["", ""])[:3]
        view["today"].append({"commit": h, "time": at, "subject": msg[:140]})
    return view


# ---------- the rest of a project ----------


def notes_last_hour(project: Project) -> list[Obj]:
    now = util.now()
    f = project.notes_dir / f"{now:%F}.md"
    try:
        text = f.read_text(errors="replace")
    except OSError:
        return []
    ago = now - timedelta(hours=1)
    cutoff = ago.strftime("%H:%M") if ago.date() == now.date() else "00:00"  # the hour began yesterday: all of today
    out = []
    for ln in text.splitlines():
        m = re.match(r"^(?:## |- )(\d\d:\d\d) (.*)", ln)
        if m and m.group(1) >= cutoff:
            out.append({"time": m.group(1), "text": m.group(2)[:400]})
    return out


def decisions_with_answers(bl: Obj, inbox: list[Obj]) -> list[Obj]:
    """What the owner sees of his questions: the backlog's pending decisions, each with `answer` None (pending is
    not answered: the rule the band counts its questions with), then the ones the panel resolved whose inbox message
    the orchestrator has not read yet, with `answer` (and `free` for his own words): they show as answered until it
    reads them, then they are gone for good. A pending one whose answer only reached the inbox (the backlog could not
    be saved: decide's 202) keeps its buttons and carries `sent`, the text sent, for the page to say so."""
    unread = [m for m in inbox if m.get("decision") and not m.get("read")]
    shown = {m["did"]: m for m in unread if m.get("resolved") and m.get("did")}
    out: list[Obj] = []
    for d in bl["decisions"]:
        sent = next((m["text"] for m in reversed(unread) if not m.get("resolved") and m["decision"] == d["title"]), "")
        out.append({**d, "answer": None, **({"sent": sent} if sent else {})})
    seen = {d["id"] for d in bl["decisions"]}
    for d in reversed(bl.get("decisions_answered", [])):
        if d["id"] in shown and d["id"] not in seen:
            seen.add(d["id"])
            out.append(d)
    return out


def mac_stats() -> Obj:
    return memo("mac", _mac_stats, ttl=10)


def _mac_stats() -> Obj:
    free = re.search(r"free percentage: (\d+)", sh("memory_pressure", "-Q"))
    su = sh("sysctl", "-n", "vm.swapusage")
    swap, swap_tot = re.search(r"used = ([\d.]+)M", su), re.search(r"total = ([\d.]+)M", su)
    du = shutil.disk_usage(Path.home())
    top = []
    for ln in sh("ps", "-Ao", "pcpu=,comm=", "-r").splitlines()[:3]:
        c, _, cmd = ln.strip().partition(" ")
        try:
            if float(c or 0) >= 5:
                top.append({"cpu": float(c), "cmd": Path(cmd.strip()).name})
        except ValueError:
            pass
    mem = memo("memsize", lambda: sh("sysctl", "-n", "hw.memsize")).strip()
    return {
        "mem_free_pct": int(free.group(1)) if free else None,
        "mem_total_gb": round(int(mem) / 2**30) if mem.isdigit() else None,
        "swap_gb": round(float(swap.group(1)) / 1024, 1) if swap else None,
        "swap_total_gb": round(float(swap_tot.group(1)) / 1024, 1) if swap_tot else None,
        "disk_free_gb": round(du.free / 2**30),
        "disk_total_gb": round(du.total / 2**30),
        "load": round(os.getloadavg()[0], 1),
        "ncpu": os.cpu_count(),
        "top": top,
    }


HEALTH_TTL_S = 30  # the panel polls every few seconds; a heartbeat ages without its file changing


def orchestrator_health(p: Project) -> Obj | None:
    """The marked orchestrator's health for the Now and System tabs (orchestrator.health): {verdict, last_pass},
    without ages or status so an unchanged state keeps its ETag; None for an inactive product or nobody marked.
    Memoised for HEALTH_TTL_S, and at once when the mark, the heartbeats or the sessions' heartbeats change."""
    deps = [
        orchestrator.session_file(p),
        p.state / "orchestrator.json",
        p.state / "heartbeats.json",
        heartbeat.sessions_dir(p),
    ]

    def compute() -> Obj | None:
        h = orchestrator.health(p)
        return {"verdict": h["verdict"], "last_pass": h["last_pass"]} if h else None

    return memo(("orch_health", p.name, str(p.state), p.mode), compute, ttl=HEALTH_TTL_S, deps=deps)


SESSION_FIELDS = ("session", "name", "project", "version", "estimated", "behind", "orchestrator")


def foreman_sessions() -> Obj:
    """The foreman each live Claude Code session of the registered products runs, for the System and Now tabs:
    {foreman_installed: {version, root} or None, sessions: [{session, name, project, version, estimated, behind,
    orchestrator}]} from versions.installed and versions.live_sessions, without start times or status so an
    unchanged state keeps its ETag. Memoised for HEALTH_TTL_S (it reads every session's heartbeat and plugin
    manifests), and at once when sessions.json, installs.json, a product's mark or its sessions' heartbeats change."""
    projects = list(REG.projects.values())
    deps = [versions.sessions_file(), install.installs_file()] + [
        f for p in projects for f in (orchestrator.session_file(p), heartbeat.sessions_dir(p))
    ]

    def compute() -> Obj:
        inst = versions.installed()
        live = versions.live_sessions(projects, inst=inst)
        return {
            "foreman_installed": inst,
            "sessions": sorted(
                ({k: e.get(k) for k in SESSION_FIELDS} for e in live),
                key=lambda e: (e["project"], e["name"], e["session"]),
            ),
        }

    key = ("fsessions", str(install.install_dir()), tuple(sorted((p.name, str(p.state)) for p in projects)))
    return memo(key, compute, ttl=HEALTH_TTL_S, deps=deps)


def project_summary(p: Project) -> Obj:
    panel = p.section("panel")
    return {
        "name": p.name,
        "orchestrator": orchestrator.current(p),
        "orchestrator_health": orchestrator_health(p),
        "mode": p.mode,
        "writable": True,
        "active": p.active,
        "owner": p.owner,
        "language": REG.language(),
        "title": panel.get("title") or p.name,
        "wiki": bool(wiki_dir(p)),
        "wiki_first": panel.get("wiki_first") or [],
        "teams": list(p.repos),
        "warnings": p.warnings,
    }


def paused(project: Project) -> str | None:
    """«paused at HH:MM» while the product is paused (pause.since), else None."""
    at = pause.since(project)
    return f"paused at {at}" if at else None


def report(project: Project) -> Obj | None:
    """<state>/report.md: its text and when it was written (not its age: the polled state keeps its ETag while
    nothing changes), or None."""
    f = project.state / "report.md"
    try:
        return {"text": f.read_text(encoding="utf-8", errors="replace"), "ts": int(f.stat().st_mtime)}
    except OSError:
        return None


def project_state(project: Project, lock_list: list[Status]) -> Obj:
    bl = backlog_cards(project)  # shared and cached: read only (the cards themselves go in /api/backlog)
    bl = {**bl, "issues": bl.get("issues", [])}
    branch_of_agent = {
        s["agent"]: s["branch"]
        for p in bl["projects"] + [{"issues": bl["issues"]}]
        for c in p["issues"]
        for s in c["subs"]
        if s.get("agent") and s.get("branch") and s.get("status") == "in progress"
    }
    agent_list = agents(project, lock_list, branch_of_agent)
    live_branches = {a["branch"] for a in agent_list if a["branch"] and not a["end"]}
    repos = [repo_view(project, k, live_branches) for k in project.repos]
    live = issue_live(bl, agent_list, {b["branch"] for r in repos for b in r["branches"]})
    for iid, line in costs.by_issue(project).items():
        live.setdefault(iid, {})["cost"] = line
    worktree_of = {b["branch"]: b["worktree"] for r in repos for b in r["branches"] if b.get("worktree")}
    for a in agent_list:
        wt = worktree_of.get(a["branch"])
        if wt and os.path.isdir(wt):
            a["worktree"] = wt
            a["commit"] = git(wt, "log", "-1", "--format=%h %s").strip()[:140]
            a["uncommitted"] = len(git(wt, "status", "--short").splitlines())
    inbox = notes.inbox(project)
    activity = [m for m in inbox if m.get("move")][::-1]
    return {
        **project_summary(project),
        "home": str(project.home),
        "resources": sorted(project.resources),
        "paused": paused(project),
        "report": report(project),
        "agents": agent_list,
        "issue_live": live,
        "backlog_error": bl.get("error"),
        "decisions": decisions_with_answers(bl, inbox),
        "messages": [m for m in inbox if not m.get("decision") and not m.get("move")][-10:],
        "moves": activity[:5],
        "activity": activity,
        "progress": util.read_jsonl(project.state / "progress.jsonl", tail=30)[::-1],
        "notes": notes_last_hour(project),
        "recurring": recurring(project),
        "grants": grant_requests(project),
        "repos": repos,
        "targets": {k: project.section("agents").get(k) for k in ("production_slots", "ready_target")},
    }


_SHELL_CHARS = set(";&|<>`$()\n\r{}*?~!#")


def grant_say(command: str) -> dict[str, Any] | None:
    """What a pending command does, for the page's plain words, or None when it is not one of a few plain shapes (the
    page then names the group). Conservative on purpose, as the owner's yes rests on it: any shell operator or
    expansion, any option, a `:ref` or `+ref` (deletes, forces) gives None.
    `git push REMOTE [REF…]` → {kind: push, refs, to: REMOTE} (push_all with no refs);
    `gh release create TAG` → release; `make|npm|yarn|pnpm|just [run] deploy` → deploy."""
    if not command or _SHELL_CHARS & set(command):
        return None
    try:
        w = shlex.split(command)
    except ValueError:
        return None
    if any(x.startswith("-") for x in w[1:]):
        return None
    if w[:2] == ["git", "push"] and len(w) >= 3:
        refs = w[3:]
        if any(r[:1] in (":", "+") or ":" in r for r in refs):
            return None
        return {"kind": "push", "to": w[2], "refs": refs} if refs else {"kind": "push_all", "to": w[2]}
    if w[:3] == ["gh", "release", "create"] and len(w) == 4:
        return {"kind": "release", "what": w[3]}
    if w[0] in ("make", "npm", "yarn", "pnpm", "just") and w[1:] in (["deploy"], ["run", "deploy"]):
        return {"kind": "deploy"}
    return None


def grant_requests(project: Project) -> list[Obj]:
    """The owner's grants of an active product with `[deny.grants]` (grants.py), for the You tab: each request the
    hooks module's question left unanswered and still pending (`pending`: group, command, at, expires, orchestrator,
    minutes; the Approve button), then each live grant (`until`, `via`)."""
    from . import grants

    if not project.active or not grants.groups(project, True):
        return []
    mins = grants.minutes(project, "grant_minutes", 30)
    out: list[Obj] = [
        {
            "group": g,
            "pending": True,
            "command": r.get("command") or "",
            "summary": r.get("summary") or "",
            "say": grant_say(str(r.get("command") or "")),
            "orchestrator": r.get("orchestrator") or "",
            "at": r.get("at"),
            "expires": r["expires"],
            "minutes": mins,
        }
        for g, r in grants.pending(project).items()
    ]
    for g in grants.groups(project, True):
        live = grants.live(project, g)
        if live:
            out.append({"group": g, "pending": False, "until": live.get("until"), "via": live.get("via")})
    return out


def grant_approve(p: Project, group: str) -> Obj:
    """/api/grants/approve: the owner approves a pending request (grants.approve: a grant for the marked session and
    its process, exactly like a yes to the question), with its line in approved.jsonl, a note and an inbox message
    for the orchestrator (it may run the command again). Refused (403, with `why`) when there is nothing to grant."""
    from . import approved, grants

    if not p.active:
        raise Refused(f"{p.name} is {p.mode}: no orchestrator to grant to")
    if group not in grants.groups(p, True):
        raise ValueError(f"no [deny.grants] group {group!r}")
    try:
        g, r = grants.approve(p, group)
    except grants.NotApproved as e:
        raise Refused(str(e), e.why) from e
    command = str(r.get("command") or "")
    approved.granted(
        p,
        {
            "verdict": "grant",
            "group": group,
            "answer": "yes",
            "surfaces": ["panel"],
            "session": g["session"],
            "orchestrator": g.get("orchestrator"),
            "command": command,
            "tool_use_id": r.get("tool_use_id"),
            "until": g["until"],
            "via": "panel",
            "asked": r.get("at"),
        },
    )
    t = i18n.for_project(p)
    who = g.get("orchestrator") or str(g["session"])[:8]
    until = g["until"][11:16]
    asked = str(r.get("at") or "")[11:16]
    notes.note(
        p,
        "-",
        t["grant_note_panel"].format(
            group=group, owner=p.owner, orchestrator=who, until=until, asked=asked, command=command[:200]
        ),
    )
    notes.inbox_add(
        p,
        t["grant_panel_inbox"].format(group=group, until=until, command=command[:200]),
        sender="owner",
        grant={"group": group, "until": g["until"]},
    )
    return {"granted": group, "until": g["until"]}


def grant_deny(p: Project, group: str) -> Obj:
    """/api/grants/deny: the owner says no to a pending request (grants.deny: it is dropped and the group cools down
    for that session, as after a no in the chat), with its line in approved.jsonl, a note and a message for the
    orchestrator. It never grants. Refused (403, `why` none) when no request waits."""
    from . import approved, grants

    if not p.active:
        raise Refused(f"{p.name} is {p.mode}: no orchestrator to answer")
    if group not in grants.groups(p, True):
        raise ValueError(f"no [deny.grants] group {group!r}")
    try:
        r = grants.deny(p, group)
    except grants.NotApproved as e:
        raise Refused(str(e), e.why) from e
    command = str(r.get("command") or "")
    approved.granted(
        p,
        {
            "verdict": "deny",
            "group": group,
            "answer": "no",
            "surfaces": ["panel"],
            "session": r.get("session"),
            "orchestrator": r.get("orchestrator"),
            "command": command,
            "tool_use_id": r.get("tool_use_id"),
            "via": "panel",
            "asked": r.get("at"),
            "cooldown_until": r.get("cooldown_until"),
        },
    )
    t = i18n.for_project(p)
    asked = str(r.get("at") or "")[11:16]
    notes.note(p, "-", t["grant_note_panel_no"].format(group=group, owner=p.owner, asked=asked, command=command[:200]))
    notes.inbox_add(p, t["grant_panel_inbox_no"].format(group=group, command=command[:200]), sender="owner")
    return {"denied": group}


def recurring(project: Project) -> list[Obj]:
    """The recurring tasks of a product; an inactive one (set up, its orchestrator not started) has nothing late."""
    out = notes.recurring(project, notes.heartbeats(project))
    for t in out:  # when, not how long ago (the ETag): the page counts the hours
        for part in t.get("parts", []):
            part.pop("hours", None)
            part["last_ts"] = (
                int(datetime.strptime(part["last"], "%Y-%m-%d %H:%M").timestamp()) if part.get("last") else None
            )
    if project.mode == "inactive":
        for t in out:
            t["late"] = False
            t["inactive"] = True
            for part in t.get("parts", []):
                part["late"] = False
    return out


def overview(lock_list: list[Status]) -> list[Obj]:
    out = []
    for p in REG.projects.values():
        bl = backlog_cards(p)
        live = [
            a
            for a in agents_mod.agents(p).values()
            if a.get("transcript") and agents_mod.agent_state(a) in agents_mod.LIVE_STATES
        ]
        out.append(
            {
                **project_summary(p),
                "agents": len(live),
                "counts": bl.get("counts", {}),
                "decisions": len(bl.get("decisions", [])),
                "paused": paused(p),
                "unread": sum(1 for m in notes.inbox(p) if m.get("from") == "orchestrator" and not m.get("read")),
                "late": [t["name"] for t in recurring(p) if t["late"]],
            }
        )
    # a product that does not load stays in the picker, marked, with its error (it has no data views)
    for b in REG.broken:
        out.append(
            {
                "name": b["name"],
                "title": b["name"],
                "mode": b["mode"],
                "broken": True,
                "error": b["error"],
                "warnings": [],
                "writable": False,
                "active": False,
                "agents": 0,
                "counts": {},
                "decisions": 0,
                "paused": "",
                "unread": 0,
                "late": [],
                "teams": [],
                "wiki": False,
                "wiki_first": [],
            }
        )
    return out


# ---------- writes ----------


class Refused(Exception):
    def __init__(self, text: str, why: str = "") -> None:
        super().__init__(text)
        self.why = why


def write_target(name: str) -> Project:
    """The project a write is for (a product that does not load has no data to write)."""
    p = REG.get(name)
    if p is None:
        raise Refused(f"no project {name!r}")
    return p


def act(path: str, q: Query, body: str) -> Obj:
    """POST endpoints. Returns a JSON-able answer; raises Refused (403) or ValueError (400)."""
    arg = lambda k, d="": q.get(k, [d])[0]  # noqa: E731
    if path in ("/api/lock/take", "/api/lock/release", "/api/lock/extend"):
        name = arg("r")
        res = REG.resources().get(name)
        if not res:
            raise ValueError(f"no resource {name!r}")
        cfg, users, _machine = res
        if cfg.get("slots") or not any(REG.projects[u].active for u in users):
            raise Refused(f"{name}: the panel cannot take this lock (with slots or of no active product)")
        if path.endswith("take"):
            OWNER.take(name, cfg, int(arg("min", "60") or 0))
            OWNER.tick()  # a free lock is his at once, not on the next tick
        elif path.endswith("extend"):
            if not OWNER.extend(name, cfg, int(arg("min", "30") or 0)):
                raise ValueError(f"{name}: not held from the panel with an expiry")
        else:
            OWNER.release(name, cfg)
        return {"locks": locks()}
    p = write_target(arg("p"))
    if path == "/api/message":
        text = body.strip()[:4000]
        if not text:
            raise ValueError("empty message")
        return notes.inbox_add(p, text, sender="owner")
    if path == "/api/decide":
        act = change_answer if arg("change") == "1" else decide  # a change of an answer the orchestrator has not read
        if arg("free") == "1":  # his own words, in the body
            return act(p, arg("id"), body.strip(), free=True)
        return act(p, arg("id"), arg("op"))
    if path == "/api/grants/approve":
        return grant_approve(p, arg("g"))
    if path == "/api/grants/deny":
        return grant_deny(p, arg("g"))
    if path == "/api/approve":
        repo, branch = arg("repo"), arg("branch")
        if repo not in p.repos or not branch:
            raise ValueError("repo and branch are required")
        return notes.inbox_add(
            p,
            f"approved {repo}:{branch}" + (f" (PR #{arg('pr')})" if arg("pr") else ""),
            sender="owner",
            approve={"repo": repo, "branch": branch, "pr": arg("pr"), "approved": f"{p.owner}, {stamp()} (panel)"},
        )
    if path == "/api/move" and arg("kind") == "project":
        pid, pos = arg("id"), int(arg("a") or 0)
        with Backlog(p).mutate() as data:
            ids = [x["id"] for x in data["projects"]]
            if pid not in ids:
                raise ValueError(f"no project {pid}")
            k = ids.index(pid)
            if not 0 <= pos < len(ids) or pos == k:
                return {"sentence": None}
            data["projects"].insert(pos, data["projects"].pop(k))
        sentence = f"{pid}: {k + 1} -> {pos + 1}"
        notes.inbox_add(p, sentence, sender="owner", move=pid)
        return {"sentence": sentence}
    if path == "/api/move":
        iid, where = arg("id"), arg("a")

        def dest(k: int, n: int) -> int:
            if where == "up":
                return k - 1
            if where == "down":
                return k + 1
            if where == "first":
                return 0
            return int(where)

        def rank(b: Backlog, data: Any) -> Any:
            moved = b.rank_issue(data, iid, dest)
            if moved is None:
                raise _NoChange
            return moved

        try:
            pid, k, d, order = mutate_or_400(p, rank)
        except _NoChange:
            return {"sentence": None}
        sentence = f"{iid}: {k + 1} -> {d + 1} in {pid}" + (f" (before {order[d + 1]})" if d + 1 < len(order) else "")
        notes.inbox_add(p, sentence, sender="owner", move=iid)
        return {"sentence": sentence}
    if path == "/api/want":
        iid, on = arg("id"), arg("on") == "1"
        if not p.active:
            raise Refused(f"{p.name} is {p.mode}: no orchestrator to take the issue in")
        mutate_or_400(p, lambda b, data: b.want(data, iid, on, stamp()))
        sentence = (
            f"{iid}: the owner wants it (triage): take it in — ready if its brief is the plan, else refining and a "
            "planner"
            if on
            else f"{iid}: the owner no longer wants it"
        )
        notes.inbox_add(p, sentence, sender="owner", move=iid, want="on" if on else "off")
        return {"wanted": on, "sentence": sentence}
    if path == "/api/reject":
        iid = arg("id")
        if not p.active:
            raise Refused(f"{p.name} is {p.mode}: no orchestrator to tell")
        mutate_or_400(p, lambda b, data: b.reject(data, iid, i18n.panel_strings(REG.language())["reject_reason"]))
        sentence = f"{iid}: the owner discarded it (triage): canceled from the panel"
        notes.inbox_add(p, sentence, sender="owner", move=iid, want="reject")
        return {"canceled": True, "sentence": sentence}
    if path == "/api/pause":
        f = p.state / "pause"
        if arg("on") == "1" and not p.active:
            raise Refused(f"{p.name} is {p.mode}: its loop is not running, there is nothing to pause")
        if arg("on") == "1":
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(f"paused at {stamp('%H:%M')}\n")
        elif f.exists():
            f.unlink()
        return {"paused": f.exists()}
    raise ValueError(f"no endpoint {path}")


def stamp(fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Now (util.now: the inbox's clock too) for the marks the panel writes."""
    return util.now().strftime(fmt)


def mutate_or_400(p: Project, fn: Callable[[Backlog, Any], Any]) -> Any:
    """fn(backlog, data) inside the backlog's mutate, and what it returns; a BacklogError (nothing saved) is the
    caller's mistake: 400."""
    b = Backlog(p)
    try:
        with b.mutate() as data:
            out = fn(b, data)
    except BacklogError as e:
        raise ValueError(str(e)) from e
    return out


def card_of(data: Any, did: str) -> str:
    """The «ID, title» of the card of pending decision `did`; `did` itself when it is not pending."""
    d = next((x for x in data["decisions"] if x["id"] == did), None)
    return clean(f"{did}, {d['title']}") if d else did


class _NoChange(Exception):
    pass


class Accepted(dict):  # type: ignore[type-arg]
    """An answer the server sends with 202: recorded, but not done (the orchestrator does it)."""


DECIDE_LOCK_S = 30.0  # how long an answer waits for the backlog's lock before it falls back to the inbox alone
FREE_ANSWER_MAX = 2000  # characters of an answer in the owner's own words


def decide(p: Project, ref: str, option: str, free: bool = False) -> Obj:
    """The owner's answer to a decision with one of its options, or with his own words (`free`: the text of the
    «other answer» box) (`ref`: its id): it resolves the decision in the backlog
    at once, under the backlog's lock (Backlog.answer_decision, `by` «Owner, YYYY-MM-DD HH:MM (panel)», `free` marked),
    then tells the orchestrator in the inbox (with did, resolved and freed, and free) so it applies it, interpreting a
    free one. ValueError (400) when the decision is not pending, the option is not one of its options, or a free
    answer is empty or longer than FREE_ANSWER_MAX. When the backlog cannot be saved (its lock held too long, a
    backlog that does not load or validate), the inbox message alone, as before: Accepted (202), and the orchestrator
    resolves it."""
    if free and not option:
        raise ValueError("empty answer")
    if free and len(option) > FREE_ANSWER_MAX:
        raise ValueError(f"answer too long: {len(option)} characters, at most {FREE_ANSWER_MAX}")
    if not (ref and option):
        raise ValueError("id and op are required")
    mark = {"free": True} if free else {}
    b = Backlog(p)
    by = f"{p.owner}, {stamp()} (panel)"
    did, card, freed = ref, ref, []
    try:
        with b.mutate(timeout=DECIDE_LOCK_S) as data:
            did, card = ref, card_of(data, ref)
            try:
                freed = b.answer_decision(data, did, option, by, free=free)
            except BacklogError as e:  # the caller's mistake: 400, and nothing is saved
                raise ValueError(str(e)) from e
    except BacklogError as e:  # not saved: the owner's answer still reaches the orchestrator
        with contextlib.suppress(BacklogError, OSError, ValueError):  # its card title, read without the lock
            card = card_of(b.load(), ref)
        m = notes.inbox_add(p, option, sender="owner", decision=card, **mark)
        return Accepted(m, note=f"recorded; the orchestrator will resolve it ({e})")
    notes.inbox_add(p, option, sender="owner", decision=card, did=did, resolved=True, freed=freed, **mark)
    return {"resolved": did, "freed": freed, **mark}


def change_answer(p: Project, ref: str, option: str, free: bool = False) -> Obj:
    """The owner changes his panel answer to a decision while the orchestrator has not read it (its inbox message
    still unread: the same rule that keeps it on the page, decisions_with_answers). The backlog's answer becomes the
    new one (Backlog.change_answer) and, in the inbox, the old message is marked read and a new unread one carries the
    answer with `changed_from`: an orchestrator that listed the old one and acts on it still finds the change at its
    next look. Under the inbox's lock, held while the backlog is saved, so the two never disagree. ValueError (400)
    when the orchestrator already read it, the decision is not answered, or the option is not one of its options."""
    if not option or (free and len(option) > FREE_ANSWER_MAX):
        raise ValueError("empty answer" if not option else f"answer too long: at most {FREE_ANSWER_MAX} characters")
    mark = {"free": True} if free else {}
    b, f = Backlog(p), notes.inbox_path(p)
    by = f"{p.owner}, {stamp()} (panel)"
    with util.file_lock(f):
        msgs = util.read_json(f, [])
        try:
            with b.mutate(timeout=DECIDE_LOCK_S) as data:
                did = ref
                was = b.change_answer(data, did, option, by, free=free)  # raising here, nothing is saved
                old = next((m for m in reversed(msgs) if m.get("did") == did and m.get("resolved")), None)
                if old is None or old.get("read"):
                    raise ValueError(f"{did}: the orchestrator already read the answer; tell it in the chat")
        except BacklogError as e:
            raise ValueError(str(e)) from e
        old["read"] = True
        new = {k: v for k, v in old.items() if k not in ("id", "time", "text", "read", "free", "changed_from")}
        msgs.append(
            {
                **new,
                "id": max(m["id"] for m in msgs) + 1,
                "time": stamp(),
                "text": option,
                "read": False,
                "changed_from": was,
                **mark,
            }
        )
        util.write_json(f, msgs[-200:])
    return {"changed": did, "was": was, **mark}


# ---------- HTTP ----------


def token_file() -> Path:
    return config.state_root() / "panel" / "token"


def load_token(given: str | None = None) -> str:
    if given:
        return given
    if os.environ.get("FOREMAN_PANEL_TOKEN"):
        return os.environ["FOREMAN_PANEL_TOKEN"]
    f = token_file()
    if not f.exists():
        write_token(f)
    return f.read_text().strip()


def write_token(f: Path) -> None:
    """A new key in f, readable only by its owner from the start."""
    f.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as out:
        out.write(secrets.token_urlsafe(12))


class Handler(BaseHTTPRequestHandler):
    token = ""

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def ok(self, q: Query) -> bool:
        return secrets.compare_digest(q.get("t", [""])[0], self.token)

    def send(
        self,
        code: int,
        body: str | bytes,
        ctype: str = "application/json; charset=utf-8",
        cache: str = "no-store",
        etag: str | None = None,
        gzipped: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        """`gzipped`: the body already compressed (a cached answer, /api/backlog). `headers`: more headers (an
        image's: never compressed, it already is)."""
        b = body.encode() if isinstance(body, str) else body
        if etag and self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.end_headers()
            return
        gz = not headers and len(b) > 1400 and "gzip" in (self.headers.get("Accept-Encoding") or "")
        if gz:
            b = gzipped if gzipped is not None else gzip.compress(b, 5)
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", cache)
        self.send_header("Content-Length", str(len(b)))
        if gz:
            self.send_header("Content-Encoding", "gzip")
        if etag:
            self.send_header("ETag", etag)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def json(self, obj: Any, code: int = 200) -> None:
        b = json.dumps(obj, ensure_ascii=False).encode()
        return self.send(
            code, b, cache="no-cache", etag='"' + hashlib.sha1(b).hexdigest()[:16] + '"' if code == 200 else None
        )

    def do_GET(self) -> None:
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if not self.ok(q):
            return self.send(403, "wrong key", "text/plain; charset=utf-8")
        REG.refresh()
        if u.path == "/":
            html = (
                (STATIC / "index.html")
                .read_text()
                .replace("{{T}}", quote(self.token))
                .replace("{{V}}", str(page_version()))
                .replace("{{LANG}}", REG.language())
            )
            return self.send(200, html, "text/html; charset=utf-8")
        if u.path in ASSETS:
            name, ctype = ASSETS[u.path]
            data = memo(("asset", name), (STATIC / name).read_bytes, deps=[STATIC / name])
            immutable = q.get("v", [""])[0] == str(page_version())
            return self.send(
                200, data, ctype, cache="private, max-age=31536000, immutable" if immutable else "no-cache"
            )
        if u.path == "/api/version":  # what the page asks when a poll fails: has the server moved on?
            return self.json({"version": page_version()})
        if u.path == "/api/strings":
            return self.json(i18n.panel_strings(q.get("lang", [REG.language()])[0]))
        if u.path == "/api/projects":
            lock_list = locks()
            return self.json(
                {
                    "projects": overview(lock_list),
                    "locks": lock_list,
                    "mac": mac_stats(),
                    "quota": hooks_mod.quota(REG.projects.values()),
                    "errors": REG.errors,
                    "language": REG.language(),
                    "version": page_version(),
                    **install.info(FOLLOWER),
                    **foreman_sessions(),
                }
            )
        if u.path == "/api/state" and q.get("p", [""])[0] == "*":  # the joint view: every project
            lock_list = locks()
            return self.json(
                {
                    "projects": [project_state(p, lock_list) for p in REG.projects.values()],
                    "locks": lock_list,
                    "version": page_version(),
                }
            )
        if u.path == "/api/backlog":  # the cards, apart from the polled state: they only change with their files
            name = q.get("p", [""])[0]
            found = list(REG.projects.values()) if name == "*" else [REG.get(name)]
            ps = [p for p in found if p is not None]
            if len(ps) != len(found):
                return self.json({"error": f"no project {name!r}"}, 404)
            b, gz, etag = backlog_body(ps, name == "*", q.get("closed", [""])[0] == "recent")
            return self.send(200, b, cache="no-cache", etag=etag, gzipped=gz)
        if u.path == "/api/issue":  # the whole card of an issue: /api/backlog sends the closed ones short
            p = REG.get(q.get("p", [""])[0])
            c = find_card(backlog_cards(p), q.get("id", [""])[0]) if p else None
            if c is None:
                return self.json({"error": "not found"}, 404)
            return self.json({"issue": c, "version": page_version()})
        if u.path.startswith("/api/wiki"):
            p = REG.get(q.get("p", [""])[0])
            if p is None or not wiki_dir(p):
                return self.json({"error": "not found"}, 404)
            if u.path == "/api/wiki":
                return self.json({"pages": wiki_list(p)})
            if u.path == "/api/wiki/page":
                page = wiki_get(p, q.get("n", [""])[0])
                return self.json(page) if page else self.json({"error": "not found"}, 404)
            if u.path == "/api/wiki/search":
                return self.json({"results": wiki_search(p, q.get("q", [""])[0])})
        if u.path == "/api/state":
            name = q.get("p", [""])[0]
            p = REG.get(name)
            if p is None:
                return self.json({"error": f"no project {name!r}"}, 404)
            lock_list = locks()
            return self.json({"project": project_state(p, lock_list), "locks": lock_list, "version": page_version()})
        if u.path == "/api/doc":
            p = REG.get(q.get("p", [""])[0])
            rel = doc_rel(p, q.get("path", [""])[0]) if p else None
            if p is None or not rel or (p.home / rel).stat().st_size > DOC_MAX:
                return self.json({"error": "not found"}, 404)
            return self.json({"path": rel, "md": (p.home / rel).read_text(errors="replace")})
        if u.path == "/api/file":  # an image a document shows; anything else is not found, without saying why
            p = REG.get(q.get("p", [""])[0])
            img = home_image(p, q.get("path", [""])[0]) if p else None
            if img is None:
                return self.json({"error": "not found"}, 404)
            data, ctype, etag = img
            return self.send(200, data, ctype, cache="private, max-age=300", etag=etag, headers=FILE_HEADERS)
        return self.json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if not self.ok(q):
            return self.send(403, "wrong key", "text/plain; charset=utf-8")
        REG.refresh()
        body = self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 16000)).decode(errors="replace")
        try:
            out = act(u.path, q, body)
            return self.json(out, 202 if isinstance(out, Accepted) else 200)
        except Refused as e:
            return self.json({"error": str(e), **({"why": e.why} if e.why else {})}, 403)
        except (ValueError, KeyError) as e:
            return self.json({"error": str(e)}, 400)


def make_server(host: str = "127.0.0.1", port: int = 8766, token: str | None = None) -> tuple[ThreadingHTTPServer, str]:
    """The server (not started) and its key; port 0 picks a free one (tests)."""
    REG.refresh()
    Handler.token = load_token(token)
    return ThreadingHTTPServer((host, port), Handler), Handler.token


FOLLOWER: install.Follower | None = None  # install.Follower of `foreman panel --follow-installed`


def warm_up() -> None:
    """Build every project's state once, writing nothing, so the first request after a start (or after the re-exec
    into a new version) finds git, the backlogs and the pull requests already computed or on their way."""
    try:
        lock_list = locks()
        overview(lock_list)
    except Exception:  # noqa: BLE001 (warm-up is best effort: the panel serves without it)
        lock_list = []
    ps = list(REG.projects.values())
    for p in ps:
        try:
            project_state(p, lock_list)
            backlog_body([p], False)
            backlog_body([p], False, True)
        except Exception:  # noqa: BLE001 (warm-up is best effort: one project's failure does not stop the others)
            pass
    with contextlib.suppress(Exception):
        backlog_body(ps, True)
        backlog_body(ps, True, True)


def serve(host: str = "127.0.0.1", port: int = 8766, token: str | None = None, follow: list[str] | None = None) -> int:
    """Serves until interrupted. follow: the panel's own arguments, to watch the installed foreman (install.py) and
    re-execute into a new version: the watcher stops the server, which is closed before execv."""
    global FOLLOWER
    try:
        srv, tok = make_server(host, port, token)
    except OSError as e:
        if e.errno != errno.EADDRINUSE:
            raise
        # one line, not a traceback: a service restarts the panel every 30 s and its log would fill with them
        msg = f"foreman panel: {host}:{port} is in use (another panel? see `foreman panel service status`)"
        print(msg, file=sys.stderr, flush=True)
        return 1
    threading.Thread(target=OWNER.watch, daemon=True).start()
    threading.Thread(target=warm_up, daemon=True).start()
    move: list[str] = []
    if follow is not None:
        FOLLOWER = install.Follower(follow)

        def stop(root: str) -> None:
            move.append(root)
            srv.shutdown()

        threading.Thread(target=FOLLOWER.run, args=(stop,), daemon=True).start()
    names = ", ".join(f"{p.name} ({p.mode})" for p in REG.projects.values()) or "none (empty registry)"
    # a service's output goes to a log file: there the key is named by its file, not written out
    key = f"<the key: --token, FOREMAN_PANEL_TOKEN or {token_file()}>" if follow is not None else quote(tok)
    print(f"foreman panel on http://{host}:{srv.server_address[1]}/?t={key}\nprojects: {names}", flush=True)
    print(
        f"foreman {install.__version__} from {install.ROOT}"
        + (", following the installed foreman" if follow is not None else ""),
        flush=True,
    )
    for e in REG.errors:
        print(f"  problem: {e}", flush=True)
    for p in REG.projects.values():
        for w in p.warnings:
            print(f"  {p.name}: warning: {w}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    if move and FOLLOWER is not None:  # move is only filled with a follower
        FOLLOWER.reexec(move[0])
    return 0
