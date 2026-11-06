"""The work state of a project: one backlog JSON file, and this module is its only writer.

Forked from an earlier in-house version (schema 2, 2026-10-30) and driven by the project's foreman.toml: the file's
path, ID patterns, which ID prefixes need an acceptance criterion, the owner of the decisions and the language of its
texts.

It holds the projects (E1…) in priority order, their issues (XX-n) in rank order with their sub-issues (T0…), the
archive and the owner's pending decisions. An issue is acceptance criterion + plan + result: an optional outcome
block (the goal, its criterion and, once closed, the verdict; the criterion is required to put an issue whose prefix
is in `ids.criterion_required` into backlog or ready), the plan document it links, the agents' reports on its
sub-issues and a one-line `result` (required to close it; with a criterion, also its verdict).

The model: the registry is the workspace, a registered project is a *product* whose repos are its teams; a
product's backlog holds *projects* (groups of work E1…: status, priority, repos), *issues* (Linear's workflow
states by category, priority, relations, assignee, repo; plus ours:
acceptance criterion, verdict, result, plan, reports, branch and commit) inside a project or in the product's own list
(no project), and *sub-issues* (T0, T1…). Triage is the old «idea». No layers above the projects, no cycles.

Schema: 5 (the model above, SCHEMA below), the only one foreman reads and writes. Every command refuses an older
file: foreman 0.54 moves a schema 4 file to 5 (`foreman backlog migrate`), foreman 0.25 or older a schema 2 or 3 file
to 4.

It is read in the panel or with `foreman backlog list|show`; foreman writes no text copy of it (since 0.60).
Every command that writes takes the file lock and writes atomically (temp file + rename).

This module holds the data model (load, save, queries and changes); its one-line terminal text is in backlog_text,
the checks in backlog_check and the `foreman backlog` command line in backlog_cli.
"""

from __future__ import annotations

import contextlib
import json
import re
import subprocess
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any, overload

from . import i18n, util

if TYPE_CHECKING:
    from .config import Project

# The file's objects are plain JSON (SCHEMA below): this module is their only writer, its changes go through keys
# chosen at run time (ISSUE_KEYS, SUB_KEYS, set_field…) and backlog_check validates every write, so they are typed as
# JSON objects with these names for what each one is.
Data = dict[str, Any]  # the whole file, as `load` gives it
Issue = dict[str, Any]  # an ISSUE of SCHEMA
Record = dict[str, Any]  # any other object of SCHEMA: a project, sub-issue, decision
Holds = dict[str, dict[Any, list[str]]]  # {issue: {None (the whole issue) or sub-issue id: [decision ids]}}

# Issue statuses: Linear's workflow states, each in one of its categories. «idea» (triage in schemas 2 and 3) is still
# accepted as a name for triage when a status is set.
ISSUE_STATUSES = ("triage", "backlog", "refining", "ready", "in progress", "done", "canceled", "duplicate")
STATUS_CATEGORY = {
    "triage": "triage",
    "backlog": "backlog",
    "refining": "unstarted",
    "ready": "unstarted",
    "in progress": "started",
    "done": "completed",
    "canceled": "canceled",
    "duplicate": "canceled",
}
STATUS_ALIASES = {
    "todo": "ready",
    "in-progress": "in progress",
    "cancelled": "canceled",
    "idea": "triage",
}
CLOSED = ("done", "canceled", "duplicate")
INACTIVE = CLOSED + ("triage",)  # not part of the rank of active issues
PRIORITIES = ("urgent", "high", "medium", "low", "none")
PROJECT_STATUSES = ("backlog", "planned", "in progress", "paused", "completed", "canceled")
RELATIONS = ("blocks", "blocked-by", "related", "duplicate")
SUB_SYMBOLS = {"todo": "", "in progress": "▶", "done": "✓", "canceled": "✗", "moved": "→", "unknown": "?"}
SYMBOL_STATUS = {v: k for k, v in SUB_SYMBOLS.items() if v}
SUB_STATUS_ALIASES = {"doing": "in progress", "dropped": "canceled", "cancelled": "canceled"}
SUB_ID = re.compile(r"^[A-Z]\d+[a-z0-9]*(?:[+–-][A-Z]?\d+[a-z0-9]*)*$")
ISSUE_KEYS = ("id", "title", "status", "blocked", "plan", "subs", "detail")
OUTCOME = "acceptance"  # the key of an issue's outcome block (OUTCOME_KEYS)
OUTCOME_KEYS = ("claim", "criterion", "verdict", "aliases")
SUB_KEYS = ("id", "title", "status", "commit", "agent", "branch", "moved_to", "text", "report", "kind")
SUB_KINDS = ("measure",)  # the kinds a sub-issue stores; none is code (`--kind code` clears it)
DECISION_KEYS = (
    "id",
    "title",
    "date",
    "text",
    "options",
    "recommendation",
    "recommended",
    "as_is",
    "blocks",
    "details",
    "unanswered",
    "page",
    "resolved",
    "answer",
    "by",
    "free",
)
DECISION_OPTIONAL = ("recommended", "as_is", "blocks", "details", "unanswered", "page", "by", "free")  # only when set
DETAIL_KEYS = ("effect", "cost", "undo")  # what a decision's `details` says of one option
UNDO = ("yes", "hard", "no")  # whether an option can be undone: yes, with work, no
UNANSWERED_KEYS = ("waits", "by")  # what a decision's `unanswered` says: what stays stopped meanwhile, reminder date
PAGE_MAX = 400_000  # the largest page the panel shows (panel.DOC_MAX)
# The longest page that reads as written for one question (docs/owner.md, «Question page»: about 30 lines). The
# pages written for one question measured 11-26 lines; the studies linked as pages, 132 and 263.
PAGE_LINES = 60
EXTRA_ISSUE_KEYS = (
    "short",
    "result",
    "closed",
    "github",
    "priority",
    "relations",
    "assignee",
    "repo",
    "wanted",
)
WANTED = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")  # when the owner marked a triage issue from the panel
PROJECT_KEYS = ("status", "priority", "repos")
SCHEMA_VERSION = 5  # the schema foreman writes
LISTS = ("projects", "issues", "archive", "decisions", "decisions_resolved")  # every schema 5 file
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
QUOTED = re.compile(r"«[^»]*»|“[^”]*”|\"[^\"]*\"")  # quoted text: a question quoted in a question is not asked
PENDING_SUBS = ("todo", "in progress", "unknown")
OLD_SCHEMA_HINT = {
    4: "foreman 0.54 moves it to schema 5 (foreman backlog migrate --write), then this one reads it",
    2: "foreman 0.25 or older moves it to schema 4, then foreman 0.54 to 5",
    3: "foreman 0.25 or older moves it to schema 4, then foreman 0.54 to 5",
}

SCHEMA = """
{
  "schema_version": 5,
  "updated": "YYYY-MM-DD",
  "pages"?: {...},                         # unused since 0.60 (the text pages it headed are gone); kept as it is
                                           # when present, because foreman 0.59 or older fails to write without it
  "projects": [{"id": "E1", "title": "...", "intro": "markdown",
     "status"?: backlog|planned|in progress|paused|completed|canceled,
     "priority"?: urgent|high|medium|low|none, "repos"?: [repo keys], "issues": [ISSUE]}],
  "issues": [ISSUE],                       # the product's issues without a project
  ISSUE = {"id": "XX-1", "title": "markdown", "status": ISSUE_STATUSES, "blocked": "reason or empty",
                 "short"?: "two to four words for the orchestrator's band (else the title, trimmed)",
                 "plan": [{"label": "plan", "path": "reviews/...md", "note": ""}],
                 "subs": [{"id": "T0", "title"?, "status": SUB_STATUSES, "commit"?, "agent"?, "branch"?,
                           "moved_to"?, "text"?, "report"?, "kind"?: "measure" (no code for main; absent = code)}],
                 "detail": "markdown",
                 "acceptance"?: {"claim"?, "criterion", "verdict"?, "aliases"?},
                 "result"?: "one line", "closed"?: "YYYY-MM-DD", "github"?: "owner/repo#n",
                 "priority"?, "assignee"?: "agent or owner", "repo"?: "repo key",
                 "wanted"?: "YYYY-MM-DD HH:MM" (the owner marked it in triage from the panel: «I want it done»;
                            kept as provenance once taken in, cleared only by the owner's «no longer»),
                 "relations"?: {"blocks": [ids], "related": [ids], "duplicate_of": "id"}}
  "archive": [{"project": "E1" or "" (no project), "issues": [issue, ...]}],
  "decisions": [{"id": "AREA J1", "title", "date", "text", "options": [...], "recommendation",
                 "recommended": "the exact text of one option" (only when given with --recommend-option),
                 "as_is": true (only when recorded with --as-is: its texts did not pass `foreman plain`),
                 "blocks": ["XX-1 T3", "XX-2"] (only when set: the sub-issues, or whole issues, that wait for the
                           answer while the decision is pending; none by default),
                 "details"?: {"exact text of an option": {"effect"?: "what the owner will see or do differently",
                              "cost"?: "its cost or risk", "undo"?: "yes"|"hard"|"no" (can it be undone)}},
                 "unanswered"?: {"waits": "what stays stopped while there is no answer",
                                 "by"?: "YYYY-MM-DD" (the day the orchestrator reminds the owner)},
                 "page"?: "reviews/...md" (relative to the home, under the reviews or notes folders or a report
                          prefix: a page with screenshots or tables that explains the question)}],
  "decisions_resolved": [decision + {"resolved": "YYYY-MM-DD", "answer": "...",
                          "by"?: "who and when, e.g. «Owner, YYYY-MM-DD HH:MM (panel)» or «… (chat)» (only when set)",
                          "free"?: true (only when the answer is the owner's own words, not one of the options:
                                   the orchestrator interprets it)}]
}
"""


class BacklogError(Exception):
    pass


class DecisionResolved(BacklogError):
    """The decision to resolve is resolved already (by the panel, or by hand): `decision resolve` says so and
    changes nothing."""

    def __init__(self, d: Record) -> None:
        super().__init__(f"{d['id']} already resolved {self.said(d)}; nothing changed")
        self.decision = d

    @staticmethod
    def said(d: Record) -> str:
        """«2026-11-02 (by Owner, 2026-11-02 10:00 (panel)): «answer»», «… (free text): «…»» for the owner's own
        words."""
        by = f" (by {d['by']})" if d.get("by") else ""
        free = " (free text)" if d.get("free") else ""
        return f"{d.get('resolved', '')}{by}{free}: «{d.get('answer', '')}»"


def md_cell(text: object) -> str:
    """One table cell: no line breaks, bare pipes escaped."""
    return re.sub(r"(?<!\\)\|", r"\\|", " ".join(str(text).split("\n"))).strip()


@overload
def clean_text(t: None) -> None: ...
@overload
def clean_text(t: str) -> str: ...
def clean_text(t: str | None) -> str | None:
    """md_cell of a text, None kept (a field left as it is)."""
    return md_cell(t) if t is not None else None


def valid_date(v: object) -> bool:
    """`v` is a YYYY-MM-DD date that exists."""
    if not isinstance(v, str) or not DATE.match(v):
        return False
    try:
        date.fromisoformat(v)
    except ValueError:
        return False
    return True


def norm_sub_status(v: str) -> str:
    v = SUB_STATUS_ALIASES.get(v, v)
    v = SYMBOL_STATUS.get(v, v)
    if v not in SUB_SYMBOLS:
        raise BacklogError(f"bad sub-issue status {v!r}: {', '.join(SUB_SYMBOLS)} or ▶ ✓ ✗ → ?")
    return v


SHORT_MAX = 32  # the longest short name the band shows (characters)


def short_title(i: Issue) -> str:
    """The issue's name in the orchestrator's band: its `short` field, else its title up to the first «:», «(», «,»
    or « - » and then cut by words to SHORT_MAX characters with «…»."""
    if i.get("short"):
        return str(i["short"])
    head = re.split(r":|\(|,| - ", re.sub(r"`|\*\*", "", str(i.get("title") or "")), maxsplit=1)[0].strip()
    if len(head) <= SHORT_MAX:
        return head
    out = ""
    for w in head.split():
        if len(f"{out} {w}".strip()) > SHORT_MAX - 1:
            break
        out = f"{out} {w}".strip()
    return (out or head[: SHORT_MAX - 1]) + "…"


def tidy_relations(i: Issue) -> None:
    """Drops the empty relations of issue `i`, and its `relations` when none is left."""
    rel = {k: v for k, v in (i.get("relations") or {}).items() if v}
    if rel:
        i["relations"] = rel
    else:
        i.pop("relations", None)


def on_branch(s: Record, branch: str) -> bool:
    """Whether sub-issue `s` names `branch` (in backquotes too, as an older foreman wrote it)."""
    return bool(branch) and s.get("branch", "").strip("`") == branch


def find_sub(i: Issue, sid: str) -> int | None:
    for k, s in enumerate(i["subs"]):
        if s["id"] == sid:
            return k
    return None


def due_decisions(data: Data, today: date | None = None) -> list[Record]:
    """The pending decisions whose reminder day (`unanswered.by`) is today or past, in the backlog's order: each
    `{id, title, by, waits}`. A decision with no `by` (or a bad one) never is. Only reads; saying it once is the
    loop's (loop.decision_notices)."""
    today = today or date.today()
    out: list[Record] = []
    for d in data["decisions"]:
        u = d.get("unanswered")
        by = u.get("by") if isinstance(u, dict) else None
        if isinstance(by, str) and valid_date(by) and date.fromisoformat(by) <= today:
            out.append({"id": d["id"], "title": d.get("title", ""), "by": by, "waits": u.get("waits", "")})
    return out


class Backlog:
    """The backlog of one project (a config.Project)."""

    def __init__(self, project: Project) -> None:
        self.project = project
        cfg = project.section("backlog")
        ids = project.section("ids")
        self.root = project.home
        self.data_path = project.path(cfg["file"])
        self.lock_path = self.data_path.parent / ".backlog.lock"
        self.project_id = re.compile(ids["project"])
        self.new_issue_id = re.compile(ids["issue"])
        self.legacy_id = re.compile(ids["legacy"]) if ids.get("legacy") else None
        self.decision_id = re.compile(ids["decision"])
        self.criterion_required = tuple(ids.get("criterion_required") or ())
        self.needs_criterion = tuple(ids.get("needs_criterion") or ())
        self.report_prefixes = tuple(ids.get("report_prefixes") or ())
        self.s = i18n.for_project(project)
        self.verdicts: dict[str, str] = self.s["verdicts"]
        # Issue references inside blocking reasons: any word that matches the issue pattern (anchors dropped).
        body = ids["issue"].strip("^$")
        self.issue_ref = re.compile(rf"(?<![\w-])(?:{body})(?![\w-])")

    # ---------- ids ----------

    def is_issue_id(self, iid: str) -> bool:
        return bool(self.new_issue_id.match(iid) or (self.legacy_id and self.legacy_id.match(iid)))

    def is_legacy(self, iid: str) -> bool:
        return bool(self.legacy_id and self.legacy_id.match(iid))

    def needs_crit(self, iid: str) -> bool:
        return bool(self.criterion_required) and iid.startswith(self.criterion_required)

    # ---------- load, save, lock ----------

    def exists(self) -> bool:
        return self.data_path.exists()

    def load(self, raw: bool = False) -> Data:
        """The data of a schema 5 file, as the file has it. Any other schema is refused (2, 3 and 4 with the way to
        move them), and so is a file without one of the lists of the schema. `raw=True` skips the checks."""
        try:
            data = json.loads(self.data_path.read_text())
        except FileNotFoundError:
            raise BacklogError(f"{self.data_path} does not exist: foreman init HOME --name NAME makes it") from None
        if raw:
            return data
        v = data.get("schema_version")
        if v in OLD_SCHEMA_HINT:
            raise BacklogError(
                f"{self.rel(self.data_path)} is schema {v}, which this foreman no longer reads: {OLD_SCHEMA_HINT[v]}"
            )
        if v != SCHEMA_VERSION:
            raise BacklogError(
                f"{self.rel(self.data_path)} is schema {v!r}: this foreman reads schema {SCHEMA_VERSION}"
            )
        missing = [k for k in LISTS if not isinstance(data.get(k), list)]
        if missing:
            raise BacklogError(
                f"{self.rel(self.data_path)} is not a valid schema {v} file: no list {', '.join(missing)}"
            )
        return data

    @staticmethod
    def norm_status(v: str | None) -> str:
        """An issue status (idea is triage; todo is ready)."""
        v = STATUS_ALIASES.get((v or "").strip().lower(), (v or "").strip().lower())
        if v not in ISSUE_STATUSES:
            raise BacklogError(f"bad status {v!r}: {', '.join(ISSUE_STATUSES)}")
        return v

    def empty(self) -> Data:
        return {
            "schema_version": SCHEMA_VERSION,
            "updated": date.today().isoformat(),
            "projects": [],
            "issues": [],
            "archive": [],
            "decisions": [],
            "decisions_resolved": [],
        }

    def canon(self, data: Data) -> Data:
        """The same data with every object's keys in the schema order (stable diffs)."""

        def issue(i: Issue) -> Issue:
            out = {k: i.get(k, [] if k in ("plan", "subs") else "") for k in ISSUE_KEYS}
            out["plan"] = [
                {"label": p.get("label", "plan"), "path": p["path"], "note": p.get("note", "")} for p in out["plan"]
            ]
            out["subs"] = [{k: s[k] for k in SUB_KEYS if s.get(k) or k in ("id", "status")} for s in out["subs"]]
            h = {k: i[OUTCOME][k] for k in OUTCOME_KEYS if (i.get(OUTCOME) or {}).get(k)}
            if h:
                out[OUTCOME] = h
            out.update({k: i[k] for k in EXTRA_ISSUE_KEYS if i.get(k) not in (None, "", [], {})})
            tidy_relations(out)
            return out

        def project(p: Record) -> Record:
            out = {"id": p["id"], "title": p["title"], "intro": p.get("intro", "")}
            for k in PROJECT_KEYS:
                if p.get(k):
                    out[k] = p[k]
            out["issues"] = [issue(i) for i in p.get("issues", [])]
            return out

        def decision(d: Record) -> Record:
            return {
                k: d.get(k, [] if k == "options" else "")
                for k in DECISION_KEYS
                if (k in d and (k not in DECISION_OPTIONAL or d[k]))
                or k not in DECISION_OPTIONAL + ("resolved", "answer")
            }

        # the old pages block, as it is: foreman 0.59 or older reads it on every write
        pages = {"pages": data["pages"]} if data.get("pages") else {}
        return {
            "schema_version": data.get("schema_version", SCHEMA_VERSION),
            "updated": data.get("updated", ""),
            **pages,
            "projects": [project(p) for p in data.get("projects", [])],
            "issues": [issue(i) for i in data.get("issues", [])],
            "archive": [
                {"project": g["project"], "issues": [issue(i) for i in g.get("issues", [])]}
                for g in data.get("archive", [])
            ],
            "decisions": [decision(d) for d in data.get("decisions", [])],
            "decisions_resolved": [decision(d) for d in data.get("decisions_resolved", [])],
        }

    def dumps(self, data: Data) -> str:
        return json.dumps(self.canon(data), ensure_ascii=False, indent=2) + "\n"

    @contextlib.contextmanager
    def locked(self, timeout: float = 30) -> Iterator[None]:
        """Exclusive lock next to the backlog file (panel, orchestrator and hooks may write at the same time)."""
        try:
            with util.file_lock(self.lock_path.with_suffix(""), timeout=timeout):  # .backlog.lock
                yield
        except TimeoutError:
            raise BacklogError(f"{self.lock_path} is held by another writer for more than {timeout} s") from None

    @contextlib.contextmanager
    def mutate(self, timeout: float = 30) -> Iterator[Data]:
        """Lock (waiting `timeout` seconds at most), load (see `load`), let the caller change the data, validate
        (check_or_raise) and save."""
        with self.locked(timeout):
            data = self.load()
            yield data
            self.check_or_raise(data)
            data["updated"] = date.today().isoformat()
            util.write_text_atomic(self.data_path, self.dumps(data))

    def check_or_raise(self, data: Data) -> None:
        """BacklogError «not saved: …» when `data` fails the checks of a save (backlog_check, without the files)."""
        from . import backlog_check

        errors = backlog_check.hard(backlog_check.validate(self, data, files=False))
        if errors:
            raise BacklogError("not saved: " + "; ".join(errors))

    def init(self) -> Path:
        if self.exists():
            raise BacklogError(f"{self.data_path} already exists")
        with self.locked():
            util.write_text_atomic(self.data_path, self.dumps(self.empty()))
        return self.data_path

    def rel(self, p: Path) -> Path:
        try:
            return p.relative_to(self.root)
        except ValueError:
            return p

    # ---------- queries ----------

    @staticmethod
    def containers(data: Data) -> Iterator[tuple[str, list[Issue]]]:
        """(project id, issue list) of every active list: the projects, then the product's own issues ("")."""
        for p in data["projects"]:
            yield p["id"], p["issues"]
        yield "", data.setdefault("issues", [])

    @staticmethod
    def iter_issues(data: Data, archived: bool = True) -> Iterator[tuple[str, Issue, bool]]:
        """(project id or "", issue, archived?) in file order: the projects, the issues without project, then the
        archive."""
        for pid, lst in Backlog.containers(data):
            for i in lst:
                yield pid, i, False
        if archived:
            for g in data["archive"]:
                for i in g["issues"]:
                    yield g["project"], i, True

    @staticmethod
    def subs_of_branch(data: Data, branch: str, archived: bool = True) -> list[tuple[Issue, Record]]:
        """(issue, sub-issue) of every sub-issue that names `branch` (on_branch), in file order."""
        return [(i, s) for _, i, _ in Backlog.iter_issues(data, archived) for s in i["subs"] if on_branch(s, branch)]

    @staticmethod
    def get_project(data: Data, pid: str) -> Record:
        for p in data["projects"]:
            if p["id"] == pid:
                return p
        raise BacklogError(f"{pid} is not a project")

    @staticmethod
    def find(data: Data, iid: str, archived: bool = False) -> tuple[list[Issue], int, str]:
        """(container issue list, index, project id or "") of issue `iid`."""
        for pid, lst in Backlog.containers(data):
            for k, i in enumerate(lst):
                if i["id"] == iid:
                    return lst, k, pid
        if archived:
            for g in data["archive"]:
                for k, i in enumerate(g["issues"]):
                    if i["id"] == iid:
                        return g["issues"], k, g["project"]
        raise BacklogError(
            f"{iid} is not an issue of the backlog" + ("" if archived else " (archived issues are read-only)")
        )

    def issue(self, data: Data, iid: str, archived: bool = False) -> Issue:
        lst, k, _ = self.find(data, iid, archived)
        return lst[k]

    def resolve(self, data: Data, key: str) -> str:
        """The issue id for `key`: itself, or the issue that has `key` (a legacy id) among its aliases."""
        if self.is_legacy(key):
            for _, i, _ in self.iter_issues(data):
                if i["id"] == key or key in (i.get(OUTCOME) or {}).get("aliases", []):
                    return i["id"]
        return key

    @staticmethod
    def relation_blockers(data: Data) -> dict[str, list[str]]:
        """{issue id: ids of the open issues that block it through `relations.blocks`}. A closed blocker (done,
        canceled, duplicate) lets go by itself; the relation stays as history."""
        out: dict[str, list[str]] = {}
        for _, x, _ in Backlog.iter_issues(data):
            if x["status"] not in CLOSED:
                for t in (x.get("relations") or {}).get("blocks", []):
                    out.setdefault(t, []).append(x["id"])
        return out

    @staticmethod
    def is_blocked(i: Issue, rel_blockers: dict[str, list[str]]) -> bool:
        """An issue is blocked by its free-text reason or by an open issue that blocks it (relation_blockers)."""
        return bool(i["blocked"].strip() or rel_blockers.get(i["id"]))

    # ---------- what pending decisions hold (`blocks` of a decision) ----------

    @staticmethod
    def split_hold(ref: str) -> tuple[str, str | None]:
        """(issue, sub-issue or None) of a stored hold reference «XX-1 T3» or «XX-1»."""
        iid, _, sid = ref.partition(" ")
        return iid, sid.strip() or None

    @staticmethod
    def decision_holds(data: Data) -> Holds:
        """{issue: {None: [ids of the pending decisions that hold the whole issue], "T3": [ids that hold T3]}}, from
        the `blocks` of the pending decisions (an answer from the panel resolves its decision: it holds nothing)."""
        out: Holds = {}
        for d in data["decisions"]:
            for ref in d.get("blocks") or []:
                iid, sid = Backlog.split_hold(ref)
                ids = out.setdefault(iid, {}).setdefault(sid, [])
                if d["id"] not in ids:
                    ids.append(d["id"])
        return out

    @staticmethod
    def hold_open(data: Data, ref: str) -> bool:
        """Whether a hold reference «XX-1 T3» or «XX-1» still names work to do: its issue is in the backlog (not
        archived) and open, and its sub-issue, when named and present, is still pending (one not created yet
        counts as to do)."""
        iid, sid = Backlog.split_hold(ref)
        try:
            lst, n, _ = Backlog.find(data, iid)
        except BacklogError:  # archived, or gone
            return False
        i = lst[n]
        k = find_sub(i, sid) if sid else None
        return i["status"] not in CLOSED and (k is None or i["subs"][k]["status"] in PENDING_SUBS)

    def pending_review(self, data: Data, today: date | None = None) -> list[Record]:
        """The pending decisions in the order the owner goes through them: those that hold open work first, then
        the rest, each group oldest first (the backlog's order on a tie). Each one: its fields (id, title, date,
        age_days, text, options, recommendation, recommended), `holds` (what it holds that is still open,
        hold_open), `issue` ({id, title, short, status} of the issue its id starts with, archived included; None
        when there is none) and `plans` (that issue's plan paths); and, only when the decision has them, `details`,
        `unanswered`, `page` and `page_path` (the page's absolute path). Only reads."""
        today = today or date.today()
        out: list[Record] = []
        for d in data["decisions"]:
            try:
                age: int | None = (today - date.fromisoformat(str(d.get("date") or ""))).days
            except ValueError:
                age = None
            iid = Backlog.split_hold(d["id"])[0]
            try:
                lst, n, _ = Backlog.find(data, iid, archived=True)
                i: Issue | None = lst[n]
            except BacklogError:
                i = None
            out.append(
                {
                    "id": d["id"],
                    "title": d.get("title", ""),
                    "date": d.get("date", ""),
                    "age_days": age,
                    "text": d.get("text", ""),
                    "options": list(d.get("options", [])),
                    "recommendation": d.get("recommendation", ""),
                    "recommended": d.get("recommended", ""),
                    "holds": [r for r in d.get("blocks") or [] if Backlog.hold_open(data, r)],
                    "issue": (
                        {"id": i["id"], "title": i["title"], "short": short_title(i), "status": i["status"]}
                        if i
                        else None
                    ),
                    "plans": [p["path"] for p in i.get("plan") or []] if i else [],
                }
            )
            for k in ("details", "unanswered", "page"):  # only when set: a decision of before reads as before
                if d.get(k):
                    out[-1][k] = d[k]
            if d.get("page"):
                out[-1]["page_path"] = str(self.page_path(d["page"]))
        # stable: the backlog's order on a tie; an unreadable date goes last in its group
        return sorted(out, key=lambda x: (not x["holds"], x["age_days"] is None, -(x["age_days"] or 0)))

    @staticmethod
    def option_of(data: Data, did: str, option: str) -> str:
        """The exact text of the option `option` names for pending decision `did`: its number (from 1) or its exact
        text (a text that is an option wins over a number). DecisionResolved when `did` is resolved already;
        BacklogError when it is no decision or `option` names none of its options."""
        opts = list(Backlog.pending_decision(data, did).get("options", []))
        if option in opts:
            return option
        if option.strip().isdigit() and 1 <= int(option) <= len(opts):
            return opts[int(option) - 1]
        raise BacklogError(
            f"{did}: {option!r} is not one of its options: --option 1 to {len(opts)}, or the exact text of one"
        )

    @staticmethod
    def held_by(holds: Holds, iid: str, sub: str | None = None) -> list[str]:
        """Ids of the pending decisions that hold sub-issue `sub` of `iid` (those that hold the whole issue, then
        those that name the sub); without `sub`, only those that hold the whole issue."""
        h = holds.get(iid) or {}
        out = list(h.get(None, []))
        if sub:
            out += [x for x in h.get(sub, []) if x not in out]
        return out

    @staticmethod
    def launchable(i: Issue, rel_blockers: dict[str, list[str]], holds: Holds) -> bool:
        """Not blocked (is_blocked), not held whole by a pending decision, and either without sub-issues to do or
        with at least one `todo` sub-issue that no pending decision holds (a ▶ one is not to do)."""
        if Backlog.is_blocked(i, rel_blockers) or Backlog.held_by(holds, i["id"]):
            return False
        todo = [s["id"] for s in i["subs"] if s["status"] == "todo"]
        return not todo or any(not Backlog.held_by(holds, i["id"], s) for s in todo)

    def hold_refs(self, data: Data, refs: Sequence[str], issue: str | None = None) -> list[str]:
        """The stored form («XX-1 T3», «XX-1») of the references of `--blocks` / `decision blocks`: «T3» and «issue»
        are relative to `issue` (--issue), the rest are full («XX-1», «XX-1 T3» or «XX-1/T3»). The issue must exist
        (archived counts: `hold_problems` warns); a sub-issue need not exist yet."""
        out: list[str] = []
        for raw in refs:
            ref = " ".join(raw.replace("/", " ").split())
            if not ref:
                continue
            if ref == "issue" or (SUB_ID.match(ref) and (issue or not self.is_issue_id(ref))):
                if not issue:
                    raise BacklogError(f"--blocks {raw!r} is relative to the issue: give --issue, or a full reference")
                ref = issue if ref == "issue" else f"{issue} {ref}"
            iid, sid = self.split_hold(ref)
            if not self.is_issue_id(iid) or (sid is not None and (" " in sid or not SUB_ID.match(sid))):
                raise BacklogError(f"--blocks {raw!r} is not «ISSUE», «ISSUE Tn», «Tn» or «issue» (with --issue)")
            iid = self.resolve(data, iid)
            self.find(data, iid, archived=True)  # raises when there is no such issue
            ref = f"{iid} {sid}" if sid else iid
            if ref not in out:
                out.append(ref)
        return out

    def hold_problems(self, data: Data, refs: Sequence[str]) -> tuple[list[str], list[str]]:
        """(errors, warnings) of stored hold references: an issue that does not exist is an error; a closed issue, or a
        sub-issue that does not exist (yet) or is closed, a warning."""
        errors: list[str] = []
        warnings: list[str] = []
        for ref in refs:
            if not isinstance(ref, str):
                errors.append(f"{ref!r} is not a reference")
                continue
            iid, sid = self.split_hold(ref)
            try:
                i = self.issue(data, iid, archived=True)
            except BacklogError:
                errors.append(f"{ref}: there is no issue {iid}")
                continue
            if i["status"] in CLOSED:
                warnings.append(f"{ref}: {iid} is {i['status']}")
                continue
            if sid:
                k = find_sub(i, sid)
                if k is None:
                    warnings.append(f"{ref}: {iid} has no sub-issue {sid} (yet)")
                elif i["subs"][k]["status"] not in PENDING_SUBS:
                    warnings.append(f"{ref}: {sid} is {i['subs'][k]['status']}")
        return errors, warnings

    def blocked_by(self, data: Data, iid: str) -> list[tuple[str, Issue, str]]:
        """(project id, issue, how) of the open issues blocked by `iid`: «text» if their reason names it, «relation»
        if `iid` is open and blocks them through `relations.blocks`."""
        pat = re.compile(rf"(?<![\w-]){re.escape(iid)}(?![\w-])")
        rb = self.relation_blockers(data)
        out: list[tuple[str, Issue, str]] = []
        for pid, i, _ in self.iter_issues(data, archived=False):
            if pat.search(i["blocked"]):
                out.append((pid, i, "text"))
            elif i["status"] not in CLOSED and iid in rb.get(i["id"], []):
                out.append((pid, i, "relation"))
        return out

    def released(self, data: Data, iid: str) -> list[str]:
        """Lines on the issues that closed `iid` blocked through `relations.blocks`: unblocked, or what still blocks
        them (another open blocker, or a free-text reason). Nothing if `iid` is open."""
        i = self.issue(data, iid, archived=True)
        if i["status"] not in CLOSED:
            return []
        rb = self.relation_blockers(data)
        out: list[str] = []
        for t in (i.get("relations") or {}).get("blocks", []):
            try:
                b = self.issue(data, t)
            except BacklogError:  # archived, or gone
                continue
            if b["status"] in CLOSED:
                continue
            left = [f"by relation {', '.join(rb[t])}"] if rb.get(t) else []
            if b["blocked"].strip():
                left.append(f"by its reason: {b['blocked']}")
            out.append(
                f"{t} unblocked (relation: {iid} {i['status']})"
                if not left
                else f"{t} no longer blocked by {iid}; still blocked {'; '.join(left)}"
            )
        return out

    def verdict_label(self, i: Issue) -> str:
        return self.verdicts.get((i.get(OUTCOME) or {}).get("verdict", ""), "")

    def counts(self, data: Data) -> dict[str, int]:
        """Launchable ready issues (unblocked, and not held by a pending decision: `launchable`) and refining issues
        (the loop hook's pipeline line)."""
        issues = [i for _, i, _ in self.iter_issues(data, archived=False)]
        rb = self.relation_blockers(data)
        holds = self.decision_holds(data)
        return {
            "ready": sum(1 for i in issues if i["status"] == "ready" and self.launchable(i, rb, holds)),
            "refining": sum(1 for i in issues if i["status"] == "refining"),
        }

    # ---------- changes (each takes the loaded data) ----------

    def norm_verdict(self, v: str | None) -> str:
        v = (v or "").strip()
        v = {label: key for key, label in self.verdicts.items()}.get(v, v)
        if v and v not in self.verdicts:
            raise BacklogError(
                f"bad verdict {v!r}: one of {', '.join(self.verdicts)} "
                f"(or its label: {', '.join(repr(x) for x in self.verdicts.values())})"
            )
        return v

    def set_outcome(
        self,
        i: Issue,
        claim: str | None = None,
        criterion: str | None = None,
        verdict: str | None = None,
        result: str | None = None,
    ) -> None:
        """Outcome fields, verdict and result of issue `i`; None leaves a field, an empty string clears it."""
        h = dict(i.get(OUTCOME) or {})
        for key, raw in (("claim", claim), ("criterion", criterion), ("verdict", verdict)):
            if raw is None:
                continue
            v = self.norm_verdict(raw) if key == "verdict" else clean_text(raw)
            if v:
                h[key] = v
            else:
                h.pop(key, None)
        if h:
            if not h.get("criterion"):
                raise BacklogError(f"{i['id']}: an outcome block needs its criterion (--criterion)")
            i[OUTCOME] = h
        else:
            i.pop(OUTCOME, None)
        if result is not None:
            if clean_text(result):
                i["result"] = clean_text(result)
            else:
                i.pop("result", None)

    def set_status(
        self, data: Data, ref: str, value: str, result: str | None = None, verdict: str | None = None
    ) -> None:
        """Issue or sub-issue status. An issue that goes to a `needs_criterion` status must have its criterion if
        its prefix is in `criterion_required`; one that is closed, its result (and verdict, with a criterion)."""
        iid, _, sid = ref.partition("/")
        i = self.issue(data, self.resolve(data, iid))
        if sid:
            if result is not None or verdict is not None:
                raise BacklogError("--result and --verdict are for the issue, not for a sub-issue")
            k = find_sub(i, sid)
            if k is None:
                raise BacklogError(f"{iid} has no sub-issue {sid}")
            i["subs"][k]["status"] = norm_sub_status(value)
            return
        value = self.norm_status(value)
        self.set_outcome(i, result=result, verdict=verdict)
        if (
            value in self.needs_criterion
            and value != i["status"]
            and self.needs_crit(i["id"])
            and not (i.get(OUTCOME) or {}).get("criterion")
        ):
            raise BacklogError(
                f"{i['id']} needs its acceptance criterion before {value} "
                f"(foreman backlog edit {i['id']} "
                '--claim "…" --criterion "…")'
            )
        if value in CLOSED and i["status"] not in CLOSED:
            if value == "duplicate" and not (i.get("relations") or {}).get("duplicate_of"):
                raise BacklogError(f"{i['id']}: say of which issue: foreman backlog relate {i['id']} duplicate ID")
            if value == "duplicate":
                pass
            elif not i.get("result"):
                raise BacklogError(f'closing {i["id"]} needs its result: --result "figure + source"')
            elif i.get(OUTCOME) and not i[OUTCOME].get("verdict"):
                raise BacklogError(
                    f"{i['id']} has a criterion: closing it needs the verdict (--verdict {'|'.join(self.verdicts)})"
                )
            self._close(i, value)
        elif value not in CLOSED:
            i.pop("closed", None)
        i["status"] = value

    @staticmethod
    def _close(i: Issue, status: str) -> None:
        """Closes issue `i` as `status` today; a closed issue waits for nothing."""
        i["status"], i["closed"], i["blocked"] = status, date.today().isoformat(), ""

    def set_sub(
        self, data: Data, iid: str, sid: str, before: str | None = None, remove: bool = False, **fields: str | None
    ) -> None:
        i = self.issue(data, iid)
        if not SUB_ID.match(sid):
            raise BacklogError(f"bad sub-issue id {sid!r}")
        k = find_sub(i, sid)
        if remove:
            if k is None:
                raise BacklogError(f"{iid} has no sub-issue {sid}")
            # a repeated id goes from the end: the later copy is the stale one
            i["subs"].pop(max(n for n, s in enumerate(i["subs"]) if s["id"] == sid))
            return
        if k is None:
            s = {"id": sid, "status": "todo"}
            if before:
                b = find_sub(i, before)
                if b is None:
                    raise BacklogError(f"{iid} has no sub-issue {before}")
                i["subs"].insert(b, s)
            else:
                i["subs"].append(s)
        else:
            s = i["subs"][k]
        for key, v in fields.items():
            if v is None:
                continue
            if key == "status":
                s["status"] = norm_sub_status(v)
            elif key == "kind":
                if v in ("", "code"):
                    s.pop("kind", None)
                elif v in SUB_KINDS:
                    s["kind"] = v
                else:
                    raise BacklogError(f"bad sub-issue kind {v!r} ({'|'.join((*SUB_KINDS, 'code'))})")
            else:
                text = md_cell(v)
                if key == "branch":
                    text = text.strip("`")
                if text:
                    s[key] = text
                else:
                    s.pop(key, None)

    def parse_plan_args(self, values: Iterable[str]) -> list[Record]:
        out: list[Record] = []
        for v in values:
            if not v:
                continue
            label, sep, path = v.partition("=")
            if not sep:
                label, path = "plan", v
            path = path.removeprefix("../").removeprefix(str(self.root) + "/")
            out.append({"label": label, "path": path, "note": ""})
        return out

    def edit(
        self,
        data: Data,
        iid: str,
        title: str | None = None,
        detail: str | None = None,
        append: str | None = None,
        prepend: str | None = None,
        plan: Iterable[str] | None = None,
        claim: str | None = None,
        criterion: str | None = None,
        verdict: str | None = None,
        result: str | None = None,
        github: str | None = None,
        short: str | None = None,
        priority: str | None = None,
        assignee: str | None = None,
        repo: str | None = None,
    ) -> None:
        """Changes what is given (None: left as it is); an empty priority, assignee or repo clears it."""
        i = self.issue(data, self.resolve(data, iid))
        self.set_outcome(i, claim, criterion, verdict, result)
        self.set_field(i, "priority", self.norm_choice(priority, PRIORITIES, "priority"))
        self.set_field(i, "assignee", clean_text(assignee))
        if repo and self.project.repos and repo not in self.project.repos:
            raise BacklogError(f"repo {repo} is not in foreman.toml ({', '.join(self.project.repos)})")
        self.set_field(i, "repo", repo)
        if title is not None:
            i["title"] = clean_text(title)
        if short is not None:
            if short.strip():
                i["short"] = clean_text(short)
            else:
                i.pop("short", None)
        if detail is not None:
            i["detail"] = clean_text(detail)
        if prepend:
            i["detail"] = md_cell(prepend) + ("; " + i["detail"] if i["detail"] else "")
        if append:
            i["detail"] = (i["detail"] + "; " if i["detail"] else "") + md_cell(append)
        if plan is not None:
            i["plan"] = self.parse_plan_args(plan)
        if github is not None:
            if github:
                i["github"] = github.strip()
            else:
                i.pop("github", None)

    def block(self, data: Data, iid: str, reason: str | None) -> None:
        self.issue(data, iid)["blocked"] = clean_text(reason)

    def integrated(
        self,
        data: Data,
        iid: str,
        sid: str | None = None,
        commit: str = "",
        branch: str = "",
        report: str = "",
        result: str = "",
        verdict: str = "",
    ) -> tuple[list[str], list[str]]:
        """The whole backlog close of an integration, its only entry point (foreman integrate): the sub-issue
        (given, or the ones with that branch) to done with the commit and the agent's report (a measurement, `kind =
        "measure"`, without the commit; `commit` is "" when `integrate --measure` closes one); the issue to done with
        its result and verdict if no sub-issue is left to do (a warning if it has none), or back to ready if nobody
        is on the next one; and unblock the issues whose blocking reason names only this issue, once it is done,
        and say which issues it blocked through `relations.blocks` are free now (nothing to write: an issue is
        blocked by a relation only while its blocker is open).
        Returns (what was done, blocked issues left for the orchestrator to judge)."""
        i = self.issue(data, iid)
        did: list[str] = []
        sids = [x for x in (sid or "").split(",") if x]
        if not sids:
            sids = [s["id"] for s in i["subs"] if on_branch(s, branch)]
        if report and self.report_prefixes and not report.startswith(self.report_prefixes):
            raise BacklogError(f"--report must be a path under {' or '.join(self.report_prefixes)} (got {report!r})")
        if report and not sids:
            raise BacklogError(f"--report needs the sub-issue: {iid} has none for this branch (pass Tn)")
        for one in sids:
            if find_sub(i, one) is None:
                raise BacklogError(f"{iid} has no sub-issue {one}")
        for one in sids:
            k = find_sub(i, one)
            # a measurement brings no code: it is closed without the commit, also on a branch it shares with code
            c = "" if k is not None and i["subs"][k].get("kind") == "measure" else commit
            self.set_sub(data, iid, one, status="done", commit=c, report=report or None)
            did.append(f"{iid} {one} ✓ {c}".rstrip() + (f", report {report}" if report else ""))
        pending = [s["id"] for s in i["subs"] if s["status"] in PENDING_SUBS]
        if not pending and i["status"] not in CLOSED:
            self.set_outcome(i, result=result or None, verdict=verdict or None)
            self._close(i, "done")
            label = self.verdict_label(i)
            did.append(
                f"{iid} done" + (f" ({label})" if label else "") + (f": {i['result']}" if i.get("result") else "")
            )
            if not i.get("result"):
                did.append(f'warning: {iid} closed without result: foreman backlog edit {iid} --result "…"')
            if i.get(OUTCOME) and not i[OUTCOME].get("verdict"):
                did.append(
                    f"warning: {iid} closed without the verdict of its criterion: foreman backlog edit {iid} "
                    f"--verdict …"
                )
        elif result or verdict:
            did.append(
                f"warning: result not saved: {iid} is not closed by this integration"
                + (f" (left: {', '.join(pending)})" if pending else "")
            )
        elif pending and i["status"] == "in progress" and not any(s["status"] == "in progress" for s in i["subs"]):
            i["status"] = "ready"
            did.append(f"{iid} ready (left: {', '.join(pending)})")
        doubtful: list[str] = []
        freed: set[str] = set()
        rb = self.relation_blockers(data)
        for _, b, how in self.blocked_by(data, iid):
            if how != "text":
                continue
            if i["status"] == "done" and set(self.issue_ref.findall(b["blocked"])) == {iid}:
                was, b["blocked"] = b["blocked"], ""
                freed.add(b["id"])
                left = ", ".join(rb.get(b["id"], []))
                did.append(
                    f"{b['id']}: reason cleared (was: {was}); still blocked by relation {left}"
                    if left
                    else f"{b['id']} unblocked (was: {was})"
                )
            else:
                doubtful.append(f"{b['id']} ({b['status']}): {b['blocked']}")
        return did + [x for x in self.released(data, iid) if x.split(" ", 1)[0] not in freed], doubtful

    def issues_of(self, data: Data, pid: str | None) -> list[Issue]:
        """The issue list of project `pid`, or the product's own list for "" or "-" (no project)."""
        if pid in ("", "-", None):
            return data.setdefault("issues", [])
        return self.get_project(data, pid)["issues"]

    def move_issue(
        self,
        data: Data,
        iid: str,
        before: str | None = None,
        after: str | None = None,
        to_project: str | None = None,
    ) -> None:
        lst, k, _pid = self.find(data, iid)
        if to_project is not None and to_project != "":
            dest = self.issues_of(data, to_project)
            if dest is lst:
                return
            it = lst.pop(k)
            dest.insert(self._insert_at(dest, it["status"]), it)
            return
        ref = before or after
        if not ref or ref == iid:
            raise BacklogError("move needs --before, --after or --project with another issue")
        rlst, _, _ = self.find(data, ref)
        it = lst.pop(k)
        rk = next(n for n, x in enumerate(rlst) if x["id"] == ref)
        rlst.insert(rk if before else rk + 1, it)

    @staticmethod
    def _insert_at(lst: list[Issue], status: str) -> int:
        """Where a new or moved issue of `status` goes in `lst`: an active one before the first inactive one (done,
        canceled, triage), an inactive one last."""
        if status in INACTIVE:
            return len(lst)
        return next((n for n, x in enumerate(lst) if x["status"] in INACTIVE), len(lst))

    @staticmethod
    def move_project(data: Data, pid: str, before: str | None = None, after: str | None = None) -> None:
        ps = data["projects"]
        k = next((n for n, p in enumerate(ps) if p["id"] == pid), None)
        if k is None:
            raise BacklogError(f"{pid} is not a project")
        ref = before or after
        p = ps.pop(k)
        rk = next((n for n, x in enumerate(ps) if x["id"] == ref), None)
        if rk is None:
            ps.insert(k, p)
            raise BacklogError(f"{ref} is not a project")
        ps.insert(rk if before else rk + 1, p)

    def next_issue_ids(self, data: Data) -> dict[str, str]:
        """{prefix: next free id} for every prefix of the ids (archive included) that match `[ids] issue` and end
        in a number: one above the highest number of that prefix."""
        top: dict[str, int] = {}
        for _, i, _ in self.iter_issues(data):
            m = re.fullmatch(r"(.*?)(\d+)", i["id"]) if self.new_issue_id.match(i["id"]) else None
            if m:
                top[m.group(1)] = max(top.get(m.group(1), 0), int(m.group(2)))
        return {px: f"{px}{n + 1}" for px, n in sorted(top.items())}

    def next_issue_id(self, data: Data) -> str:
        """The next free issue id, learnt from the existing ones; an error asking for --id when there is none to
        learn from or when several prefixes are in use."""
        nxt = self.next_issue_ids(data)
        if not nxt:
            raise BacklogError(
                f"no issue id matching {self.new_issue_id.pattern} to learn the prefix from: "
                "give the first one with --id"
            )
        if len(nxt) > 1:
            raise BacklogError(
                f"several id prefixes in use: give --id (the next free ones are {', '.join(nxt.values())})"
            )
        return next(iter(nxt.values()))

    def project_for_add(self, data: Data, pid: str | None) -> str:
        """The project id an added issue goes to ("" for none, also from "-"), with a helpful error when `pid` is
        not one."""
        if pid in ("", "-", None):
            return ""
        if any(p["id"] == pid for p in data["projects"]):
            return pid
        known = ", ".join(p["id"] for p in data["projects"]) or "none"
        raise BacklogError(
            f'{pid} is not a project (projects: {known}); leave the project out, or pass "" or -, '
            "to add the issue without one"
        )

    def add_issue(
        self,
        data: Data,
        pid: str | None,
        iid: str | None,
        title: str,
        status: str = "backlog",
        detail: str = "",
        blocked: str = "",
        plan: Iterable[str] = (),
        before: str | None = None,
        claim: str | None = None,
        criterion: str | None = None,
        github: str | None = None,
        short: str | None = None,
    ) -> str:
        """Add an issue and return its id. Without `iid` it is the next free one (`next_issue_id`); without `pid`
        (or with "" or "-") the issue has no project."""
        pid = self.project_for_add(data, pid)
        if not iid:
            iid = self.next_issue_id(data)
        if not self.new_issue_id.match(iid):
            raise BacklogError(f"bad issue id {iid!r} ({self.new_issue_id.pattern})")
        if any(i["id"] == iid for _, i, _ in self.iter_issues(data)):
            raise BacklogError(f"{iid} already exists")
        status = self.norm_status(status)
        if status in CLOSED:
            raise BacklogError(f"bad status {status!r} for a new issue")
        lst = self.issues_of(data, pid)
        it = {
            "id": iid,
            "title": clean_text(title),
            "status": status,
            "blocked": clean_text(blocked or ""),
            "plan": self.parse_plan_args(plan),
            "subs": [],
            "detail": clean_text(detail or ""),
        }
        if github:
            it["github"] = github
        if short and short.strip():
            it["short"] = clean_text(short)
        self.set_outcome(it, claim, criterion)
        if status in self.needs_criterion and self.needs_crit(iid) and not criterion:
            raise BacklogError(f"{iid} needs an acceptance criterion: {status} needs --criterion, or start as triage")
        k = next((n for n, x in enumerate(lst) if x["id"] == before), None) if before else self._insert_at(lst, status)
        if k is None:
            raise BacklogError(f"{before} is not an issue of {pid}")
        lst.insert(k, it)
        return iid

    def add_project(self, data: Data, pid: str, title: str, intro: str, before: str | None = None) -> None:
        if not self.project_id.match(pid) or any(p["id"] == pid for p in data["projects"]):
            raise BacklogError(f"bad or existing project id {pid!r}")
        p = {"id": pid, "title": clean_text(title), "intro": intro.strip(), "issues": []}
        ps = data["projects"]
        k = next((n for n, x in enumerate(ps) if x["id"] == before), None) if before else len(ps)
        if k is None:
            raise BacklogError(f"{before} is not a project")
        ps.insert(k, p)

    @staticmethod
    def norm_choice(v: str | None, choices: Sequence[str], what: str) -> str | None:
        v = (v or "").strip().lower().replace("_", " ").replace("-", " ") if v else v
        if v and v not in choices:
            raise BacklogError(f"bad {what} {v!r}: {', '.join(choices)}")
        return v

    @staticmethod
    def set_field(obj: Record, key: str, v: Any) -> None:
        """None leaves the field, an empty value removes it."""
        if v is None:
            return
        if v in ("", [], {}):
            obj.pop(key, None)
        else:
            obj[key] = v

    @staticmethod
    def edit_list(
        cur: Iterable[str] | None, add: Iterable[str | None] | None = (), remove: Iterable[str] | None = ()
    ) -> list[str]:
        out = [x for x in cur or [] if x not in (remove or ())]
        for x in add or ():
            if x and x not in out:
                out.append(x)
        return out

    def edit_project(
        self,
        data: Data,
        pid: str,
        title: str | None = None,
        intro: str | None = None,
        status: str | None = None,
        priority: str | None = None,
        repos: Sequence[str] | None = (),
        unrepos: Sequence[str] | None = (),
    ) -> None:
        p = self.get_project(data, pid)
        if title is not None:
            p["title"] = clean_text(title)
        if intro is not None:
            p["intro"] = intro.strip()
        self.set_field(p, "status", self.norm_choice(status, PROJECT_STATUSES, "project status"))
        self.set_field(p, "priority", self.norm_choice(priority, PRIORITIES, "priority"))
        if repos or unrepos:
            self.set_field(p, "repos", self.edit_list(p.get("repos"), repos, unrepos))

    def remove_project(self, data: Data, pid: str) -> None:
        """Removes an empty project (its issues moved or archived first)."""
        p = self.get_project(data, pid)
        if p["issues"] or any(g["project"] == pid and g["issues"] for g in data["archive"]):
            raise BacklogError(f"{pid} still has issues (active or archived): move them first")
        data["projects"].remove(p)
        data["archive"] = [g for g in data["archive"] if g["project"] != pid]

    # ---------- issue fields and relations ----------

    def relate(self, data: Data, iid: str, kind: str | None, other: str, remove: bool = False) -> None:
        """blocks / blocked-by (stored as «blocks» on the blocking issue) / related / duplicate (marks `iid` as a
        duplicate of `other` and moves it to the status duplicate, unless it is closed already)."""
        kind = (kind or "").strip().lower().replace("_", "-").replace(" ", "-")
        kind = {
            "blocked": "blocked-by",
            "relates": "related",
            "relates-to": "related",
            "duplicate-of": "duplicate",
        }.get(kind, kind)
        if kind not in RELATIONS:
            raise BacklogError(f"bad relation {kind!r}: {', '.join(RELATIONS)}")
        a = self.issue(data, self.resolve(data, iid), archived=True)
        b = self.issue(data, self.resolve(data, other), archived=True)
        if a is b:
            raise BacklogError("an issue cannot relate to itself")
        if kind == "blocked-by":
            a, b, kind = b, a, "blocks"
        rel = a.setdefault("relations", {})
        if kind == "duplicate":
            if remove:
                rel.pop("duplicate_of", None)
            else:
                rel["duplicate_of"] = b["id"]
                if a["status"] not in CLOSED:
                    self._close(a, "duplicate")
        else:
            if kind == "related" and remove:
                other_rel = b.get("relations") or {}
                if a["id"] in other_rel.get("related", []):
                    other_rel["related"].remove(a["id"])
            rel[kind] = self.edit_list(rel.get(kind), [] if remove else [b["id"]], [b["id"]] if remove else [])
        tidy_relations(a)
        tidy_relations(b)

    def relations_of(self, data: Data, iid: str) -> dict[str, Any]:
        """{blocks, blocked_by, related, duplicate_of, duplicates}: both directions of every relation of `iid`."""
        i = self.issue(data, iid, archived=True)
        rel = i.get("relations") or {}
        out: dict[str, Any] = {
            "blocks": list(rel.get("blocks", [])),
            "blocked_by": [],
            "related": list(rel.get("related", [])),
            "duplicate_of": rel.get("duplicate_of", ""),
            "duplicates": [],
        }
        for _, x, _ in self.iter_issues(data):
            r = x.get("relations") or {}
            if iid in r.get("blocks", []):
                out["blocked_by"].append(x["id"])
            if iid in r.get("related", []) and x["id"] not in out["related"]:
                out["related"].append(x["id"])
            if r.get("duplicate_of") == iid:
                out["duplicates"].append(x["id"])
        return out

    # ---------- progress (Linear: completed issues over every issue that is not canceled) ----------

    @staticmethod
    def project_progress(p: Record) -> dict[str, int]:
        live = [i for i in p["issues"] if STATUS_CATEGORY.get(i["status"]) not in ("canceled", "triage")]
        return {
            "done": sum(1 for i in live if i["status"] == "done"),
            "total": len(live),
            "started": sum(1 for i in live if STATUS_CATEGORY.get(i["status"]) == "started"),
        }

    def archive(self, data: Data, iid: str) -> None:
        lst, k, pid = self.find(data, iid)
        if lst[k]["status"] not in CLOSED:
            raise BacklogError(f"{iid} is {lst[k]['status']}: only done or canceled issues are archived")
        it = lst.pop(k)
        group = next((g for g in data["archive"] if g["project"] == pid), None)
        if group is None:
            group = {"project": pid, "issues": []}
            data["archive"].append(group)
        group["issues"].insert(0, it)

    DEFAULT_DECISION_AREA = "DEC"

    def next_decision_id(self, data: Data, area: str) -> str:
        """The next free «AREA J<n>»: one above the highest J number of that area, pending or resolved."""
        used = [
            int(m.group(1))
            for d in data["decisions"] + data["decisions_resolved"]
            for m in [re.fullmatch(re.escape(area) + r" J(\d+)", d["id"])]
            if m
        ]
        return f"{area} J{max(used, default=0) + 1}"

    def default_decision_area(self, data: Data, issue: str | None = None) -> str | None:
        """The area of a generated decision id: the first of the issue id, its prefix before the hyphen and
        DEFAULT_DECISION_AREA for which «AREA J<n>» matches `[ids] decision`; None when none does."""
        cands = [issue, issue.split("-")[0] if issue and "-" in issue else None, self.DEFAULT_DECISION_AREA]
        for area in cands:
            if area and self.decision_id.match(self.next_decision_id(data, area)):
                return area
        return None

    def add_decision(
        self,
        data: Data,
        did: str | None,
        title: str,
        text: str,
        options: Sequence[str],
        recommendation: str = "",
        when: str | None = None,
        issue: str | None = None,
        as_is: bool = False,
        recommended: str = "",
        blocks: Sequence[str] = (),
        details: Sequence[Mapping[str, str]] = (),
        waits: str = "",
        answer_by: str = "",
        page: str = "",
    ) -> str:
        """Add a pending decision and return its id. Without `did` it is the next free one: «<issue> J<n>» when the
        decision names an issue (else the issue's prefix, else «DEC»: the first area the `[ids] decision` pattern
        accepts), else «DEC J<n>». `as_is`: recorded although its texts fail `foreman plain` (the
        command line checks them, see `decision_findings`). `recommended`: the exact text of one of `options`, the
        one the recommendation picks (stored as `recommended`; the panel marks it). `blocks`: what waits for the
        answer (`hold_refs`: «T3» or «issue» relative to `issue`, or full references); none by default.
        `details`: one mapping per option, in their order ({effect?, cost?, undo?: yes|hard|no}; stored by the
        option's text, only the options that have one). `waits`: what stays stopped while nobody answers, and
        `answer_by` the day the orchestrator reminds the owner (needs `waits`); stored as `unanswered`. `page`: a .md
        under the reviews or notes folders or a report prefix (`page_problem`). Each of them stored only when set,
        so a decision without them is written as before."""
        if issue and not self.is_issue_id(issue):
            raise BacklogError(f"--issue {issue!r} is not an issue id (e.g. AB-22)")
        if not did:
            area = self.default_decision_area(data, issue)
            if area is None:
                raise BacklogError(
                    f"no default decision id fits {self.decision_id.pattern}: give --id matching that pattern"
                )
            did = self.next_decision_id(data, area)
        if not self.decision_id.match(did):
            area = self.default_decision_area(data, issue)
            if area is None:
                raise BacklogError(
                    f"decision id {did!r} must match {self.decision_id.pattern}; no default id "
                    "fits it, so give --id matching that pattern"
                )
            raise BacklogError(
                f"decision id {did!r} must match {self.decision_id.pattern} (e.g. «{area} J1»); "
                f"the next free one is «{self.next_decision_id(data, area)}» "
                "(leave --id out to get it)"
            )
        if any(d["id"] == did for d in data["decisions"]):
            raise BacklogError(f"decision {did} already exists")
        if not options:
            raise BacklogError("a decision needs at least one --option")
        held = self.hold_refs(data, blocks, issue)
        options = [clean_text(o) for o in options]
        recommended = clean_text(recommended or "")
        if recommended and recommended not in options:
            raise BacklogError(
                f"--recommend-option {recommended!r} is not one of the --option texts: "
                + " · ".join(f"«{o}»" for o in options)
            )
        data["decisions"].append(
            {
                "id": did,
                "title": clean_text(title),
                "date": when or date.today().isoformat(),
                "text": clean_text(text),
                "options": options,
                "recommendation": clean_text(recommendation or ""),
            }
        )
        if recommended:
            data["decisions"][-1]["recommended"] = recommended
        if as_is:
            data["decisions"][-1]["as_is"] = True
        if held:
            data["decisions"][-1]["blocks"] = held
        rich = self.decision_extras(options, details, waits, answer_by, page)
        data["decisions"][-1].update(rich)
        return did

    def decision_extras(
        self, options: Sequence[str], details: Sequence[Mapping[str, str]], waits: str, answer_by: str, page: str
    ) -> Record:
        """The optional keys `details`, `unanswered` and `page` of a decision with these (clean) options, checked;
        only those that have a value. BacklogError naming the flag on a bad value."""
        out: Record = {}
        if len(details) > len(options):
            raise BacklogError("more option details than --option: --effect, --cost and --undo follow their --option")
        det: Record = {}
        for o, x in zip(options, details):
            unknown = sorted(set(x) - set(DETAIL_KEYS))
            if unknown:
                raise BacklogError(
                    f"option {o!r}: unknown detail {', '.join(unknown)} (known: {', '.join(DETAIL_KEYS)})"
                )
            if x.get("undo") and x["undo"] not in UNDO:
                raise BacklogError(f"--undo {x['undo']!r} for option {o!r}: yes, hard (with work) or no")
            one = {k: clean_text(x[k]) for k in DETAIL_KEYS if (x.get(k) or "").strip()}
            if one:
                det[o] = one
        if det:
            out["details"] = det
        waits, answer_by = clean_text(waits or ""), (answer_by or "").strip()
        if answer_by:
            if not waits:
                raise BacklogError("--answer-by needs --if-no-answer: say what stays stopped until the owner answers")
            if not valid_date(answer_by):
                raise BacklogError(f"--answer-by {answer_by!r} is not a date (YYYY-MM-DD)")
        if waits:
            out["unanswered"] = {"waits": waits, **({"by": answer_by} if answer_by else {})}
        page = (page or "").strip()
        if page:
            bad = self.page_problem(page)
            if bad:
                raise BacklogError(f"--page {bad}")
            out["page"] = page
        return out

    def page_problem(self, path: str) -> str:
        """Why `path` cannot be a decision's page (empty when it can): it must be a .md relative to the home, without
        «..», under the reviews or notes folders or a report prefix (the folders the panel shows documents from).
        Whether it exists is not checked here (`decision_warnings` says so)."""
        if not isinstance(path, str) or not path or "\0" in path:
            return f"{path!r} is not a path"
        if path.startswith("/") or ".." in Path(path).parts or not path.endswith(".md"):
            return f"{path!r} must be a .md file relative to the home, without «..»"
        roots = [self.project.reviews_dir, self.project.notes_dir] + [self.root / x for x in self.report_prefixes]
        f = (self.root / path).resolve()
        for r in roots:
            try:
                f.relative_to(r.resolve())
                return ""
            except (OSError, ValueError):
                continue
        return f"{path!r} is outside the folders of documents (the reviews or notes folders, or a report prefix)"

    def page_path(self, path: str) -> Path:
        """The absolute path of a decision's page."""
        return self.root / path

    @staticmethod
    def question_count(text: str) -> int:
        """How many questions `text` asks: the runs of «?» once what is quoted («…», “…”, "…") is taken out."""
        return len(re.findall(r"\?+", QUOTED.sub("", text or "")))

    def decision_warnings(self, d: Record, today: date | None = None, data: Data | None = None) -> list[str]:
        """What to fix in pending decision `d`, never blocking (`decision add` prints them on stderr): an option
        that does not say what it changes, two questions in one, a reminder date in the past, a page that does not
        exist yet or that the panel cannot show (larger than PAGE_MAX), and a page not written for this question
        (`page_warnings`)."""
        today = today or date.today()
        out = []
        opts = list(d.get("options", []))
        det = d.get("details") or {}
        for n, o in enumerate(opts, 1):
            if not (det.get(o) or {}).get("effect"):
                which = f"option {n}" if len(opts) > 1 else "the option"
                out.append(
                    f"{which} («{o}») has no --effect: say what the owner will see or do differently with it "
                    "(--effect after its --option)"
                )
        for field in ("title", "text"):
            k = self.question_count(d.get(field, ""))
            if k >= 2:
                out.append(
                    f"the {field} asks {k} questions: split it, one decision per question, or keep only the one "
                    "the owner answers (a quoted question «…» does not count)"
                )
        by = (d.get("unanswered") or {}).get("by", "")
        if by and valid_date(by) and date.fromisoformat(by) < today:
            out.append(f"--answer-by {by} is in the past: the reminder would come at once; give a later date")
        page = d.get("page")
        if page:
            f = self.page_path(page)
            if not f.is_file():
                out.append(f"the page {page} is not there yet: write it before the owner reads the question")
            elif f.stat().st_size > PAGE_MAX:
                out.append(
                    f"the page {page} is larger than {PAGE_MAX // 1000} KB: the panel will not show it; "
                    "shorten it or move tables to another page"
                )
            else:
                out += self.page_warnings(d, data)
        return out

    def page_warnings(self, d: Record, data: Data | None = None) -> list[str]:
        """Why the page of decision `d` (it exists) does not read as written for this question alone (docs/owner.md,
        «Question page»): longer than PAGE_LINES, already the page of another decision of `data`, a delivery report or
        a plan (named as one: a dated file right in the reviews folder or a plan's name; or recorded as one in the
        backlog), or text that fails `foreman plain` (the targets of links and images are not read)."""
        from . import delivery, plain

        page = d["page"]
        f = self.page_path(page)
        out = []
        text = f.read_text(encoding="utf-8", errors="replace")
        lines = len(text.splitlines())
        if lines > PAGE_LINES:
            out.append(
                f"the page {page} has {lines} lines: a page for one question takes about 30 (what is happening, an "
                "example, the options, the recommendation); write one for this question (docs/owner.md, "
                "«Question page»)"
            )
        here = f.resolve()
        others = [
            x["id"]
            for x in (data or {}).get("decisions", []) + (data or {}).get("decisions_resolved", [])
            if x is not d and x.get("page") and self.page_path(x["page"]).resolve() == here
        ]
        if others:
            out.append(
                f"the page {page} is already the page of {', '.join(others)}: each question gets its own page, "
                "written for it alone (docs/owner.md, «Question page»)"
            )
        recorded = set()
        for _, i, _ in self.iter_issues(data or {"projects": [], "archive": []}):
            recorded.update(p.get("path", "") for p in i.get("plan") or [])
            recorded.update(s.get("report", "") for s in i.get("subs") or [])
        named = f.parent.resolve() == self.project.reviews_dir.resolve() and re.match(r"\d{4}-\d{2}-\d{2}-", f.name)
        if delivery.is_plan(f.name) or named or any(r and (self.root / r).resolve() == here for r in recorded):
            out.append(
                f"the page {page} is a report or a plan, written for other readers and topics: write a page for this "
                "question alone, in its own folder with its screenshots (docs/owner.md, «Question page»)"
            )
        found = plain.findings(self.project, re.sub(r"\]\([^)\n]*\)", "]", text))
        if found:
            out.append(
                f"the page {page} names without explaining: {plain.summary(found)}; the owner reads it too "
                "(`foreman plain`)"
            )
        return out

    def set_decision_blocks(self, data: Data, did: str, refs: Sequence[str], issue: str | None = None) -> list[str]:
        """Replace what pending decision `did` holds (`hold_refs`; empty: nothing) and return the stored references."""
        d = self.pending_decision(data, did, resolved="")
        held = self.hold_refs(data, refs, issue)
        if held:
            d["blocks"] = held
        else:
            d.pop("blocks", None)
        return held

    def decision_findings(
        self,
        title: str,
        text: str,
        options: Sequence[str],
        recommendation: str = "",
        details: Sequence[Mapping[str, str]] = (),
        waits: str = "",
    ) -> list[tuple[str, list[dict[str, Any]]]]:
        """[(field, findings)] of a decision's texts that fail `foreman plain` (docs/owner.md); empty when they
        pass. The owner reads each option's effect and cost and what waits without an answer too."""
        from . import plain

        fields = [("title", title), ("text", text)]
        for n, o in enumerate(options, 1):
            name = f"option {n}" if len(options) > 1 else "option"
            fields.append((name, o))
            x = details[n - 1] if n <= len(details) else {}
            fields += [(f"{name} {k}", x[k]) for k in ("effect", "cost") if x.get(k)]
        fields.append(("recommendation", recommendation))
        if waits:
            fields.append(("if no answer", waits))
        return plain.check(self.project, fields)

    @staticmethod
    def decision_ref(data: Data, ref: str) -> str:
        """The id of the decision `ref` names: its id, or the panel's card title «ID, title» (an older page sends
        that); `ref` as given when nothing matches."""
        every = data["decisions"] + data["decisions_resolved"]
        if any(d["id"] == ref for d in every):
            return ref
        return next(
            (d["id"] for d in every if ref.startswith(f"{d['id']}, ") or ref == d.get("title")),
            ref,
        )

    @staticmethod
    def resolved_decision(data: Data, did: str) -> Record | None:
        """The resolved decision `did` (the last one, should an id repeat), or None."""
        return next((d for d in reversed(data["decisions_resolved"]) if d["id"] == did), None)

    @staticmethod
    def pending_decision(data: Data, did: str, resolved: str = "raise") -> Record:
        """Pending decision `did`. When there is none: BacklogError «no pending decision», or, when `did` is resolved
        already, DecisionResolved («raise»), BacklogError «already answered: …» («said») or that same «no pending
        decision» («»)."""
        d = next((x for x in data["decisions"] if x["id"] == did), None)
        done = Backlog.resolved_decision(data, did) if d is None and resolved else None
        if done is not None and resolved == "said":
            raise BacklogError(f"already answered: {DecisionResolved.said(done)}")
        if done is not None:
            raise DecisionResolved(done)
        if d is None:
            raise BacklogError(f"no pending decision {did}")
        return d

    @staticmethod
    def resolve_decision(data: Data, did: str, answer: str = "", by: str = "", free: bool = False) -> list[str]:
        """Move pending decision `did` to the resolved ones (it keeps its `blocks` as history), with the answer and,
        when given, who answered and when (`by`) and whether the answer is free text (`free`), and return a line per
        open issue or sub-issue it held: «freed: XX-1 T3», or «XX-1 T3: still held by XX-1 J2» when another pending
        decision holds it too. A held issue as a whole also lists its sub-issues that another one still holds.
        DecisionResolved when `did` is no longer pending but resolved."""
        d = Backlog.pending_decision(data, did)
        data["decisions"].remove(d)
        d.update(resolved=date.today().isoformat(), answer=clean_text(answer or ""))
        if by:
            d["by"] = clean_text(by)
        if free:
            d["free"] = True
        data["decisions_resolved"].append(d)
        holds = Backlog.decision_holds(data)
        out: list[str] = []
        for ref in d.get("blocks") or []:
            if not Backlog.hold_open(data, ref):
                continue
            iid, sid = Backlog.split_hold(ref)
            left = Backlog.held_by(holds, iid, sid)
            out.append(f"{ref}: still held by {', '.join(left)}" if left else f"freed: {ref}")
            if not sid and not left:
                out += [
                    f"{iid} {s}: still held by {', '.join(ids)}"
                    for s, ids in (holds.get(iid) or {}).items()
                    if s is not None and ids
                ]
        return out

    @staticmethod
    def answer_decision(data: Data, did: str, option: str, by: str, free: bool = False) -> list[str]:
        """The owner's answer from the panel: one of the pending decision's options, or his own words (`free`: any
        text, marked `free` for the orchestrator to interpret), resolves it at once (resolve_decision, with `by`).
        BacklogError when the decision is not pending (already answered, or no such decision) or `option` is not one
        of its options (unless `free`)."""
        d = Backlog.pending_decision(data, did, resolved="said")
        if not free and option not in d.get("options", []):
            raise BacklogError(f"{did}: {option!r} is not one of its options")
        return Backlog.resolve_decision(data, did, option, by, free=free)

    @staticmethod
    def change_answer(data: Data, did: str, option: str, by: str, free: bool = False) -> str:
        """The owner changes his panel answer to resolved decision `did` (one of its options, or his own words: `free`):
        it keeps its date and what it freed; its answer, `by` and `free` become the new ones. Returns the answer it
        replaces. Whether it may still change (the orchestrator has not read it) is the panel's to say
        (panel.change_answer). BacklogError when `did` is not resolved or `option` is not one of its options."""
        d = Backlog.resolved_decision(data, did)
        if d is None:
            raise BacklogError(f"no answered decision {did}")
        if not free and option not in d.get("options", []):
            raise BacklogError(f"{did}: {option!r} is not one of its options")
        was = d.get("answer", "")
        d.update(answer=clean_text(option), by=clean_text(by))
        if free:
            d["free"] = True
        else:
            d.pop("free", None)
        return was

    # ---------- moves from the panel ----------

    @classmethod
    def rank_issue(
        cls, data: Data, iid: str, dest_of: Callable[[int, int], int]
    ) -> tuple[str, int, int, list[str]] | None:
        """Moves issue `iid` to dest_of(k, n) among the active issues of its project; returns (pid, k, dest, order
        ids) or None when nothing changes. The inactive ones (done, canceled, triage) keep their places."""
        for pid, lst in cls.containers(data):
            pos = [n for n, i in enumerate(lst) if i["status"] not in INACTIVE]
            ids = [lst[n]["id"] for n in pos]
            if iid not in ids:
                continue
            k = ids.index(iid)
            dest = dest_of(k, len(pos))
            if not 0 <= dest < len(pos) or dest == k:
                return None
            order = [lst[n] for n in pos]
            order.insert(dest, order.pop(k))
            for n, it in zip(pos, order):
                lst[n] = it
            return pid, k, dest, [i["id"] for i in order]
        raise BacklogError(f"{iid} is not among the active issues of the backlog")

    def want(self, data: Data, iid: str, on: bool, when: str) -> None:
        """The owner's mark on issue `iid` (the panel's «I want it done»): `on` sets `wanted` to `when` on an issue
        in triage; off clears it from an issue that has it. The status is the orchestrator's: never changed here."""
        i = self.issue(data, iid)
        if on:
            if i["status"] != "triage":
                raise BacklogError(f"{iid} is {i['status']}, not in triage: only a triage issue can be wanted")
            if i.get("wanted"):
                raise BacklogError(f"{iid} is already wanted (since {i['wanted']})")
            i["wanted"] = when
        else:
            if not i.get("wanted"):
                raise BacklogError(f"{iid} is not wanted: nothing to clear")
            i.pop("wanted")

    def reject(self, data: Data, iid: str, reason: str) -> None:
        """The owner discards triage issue `iid` (the panel's «I don't want it»): canceled with `reason` as its
        result (verdict discarded when it has a criterion), its mark cleared. Nothing is deleted: `backlog status`
        brings it back."""
        i = self.issue(data, iid)
        if i["status"] != "triage":
            raise BacklogError(f"{iid} is {i['status']}, not in triage: only a triage issue can be discarded")
        crit = (i.get(OUTCOME) or {}).get("criterion")
        self.set_status(data, iid, "canceled", result=reason, verdict="discarded" if crit else None)
        i.pop("wanted", None)

    # ---------- GitHub import ----------

    def import_gh(
        self, data: Data, repo_slug: str, pid: str | None, prefix: str, status: str = "backlog", gh: str = "gh"
    ) -> list[str]:
        """Adds the open GitHub issues of `repo_slug` not imported yet (by their `github` field) as PREFIX-n issues
        of project `pid`. Returns the ids added."""
        r = subprocess.run(
            [gh, "issue", "list", "-R", repo_slug, "--state", "open", "--limit", "200", "--json", "number,title,url"],
            capture_output=True,
            text=True,
            check=False,
        )
        if r.returncode != 0:
            raise BacklogError(f"gh issue list -R {repo_slug} failed: {r.stderr.strip()[:300]}")
        have = {i.get("github") for _, i, _ in self.iter_issues(data)}
        nxt = self.next_issue_ids(data).get(f"{prefix}-")  # «AP-7» when AP-6 is the highest
        n = int(nxt[len(prefix) + 1 :]) - 1 if nxt else 0
        key = next((k for k, r in self.project.repos.items() if r.get("github") == repo_slug), None)
        added: list[str] = []
        for gi in sorted(json.loads(r.stdout or "[]"), key=lambda x: x["number"]):
            ref = f"{repo_slug}#{gi['number']}"
            if ref in have:
                continue
            n += 1
            iid = f"{prefix}-{n}"
            self.add_issue(data, pid, iid, gi["title"], status=status, detail=gi.get("url", ""), github=ref)
            if key:
                self.issue(data, iid)["repo"] = key
            added.append(iid)
        return added
