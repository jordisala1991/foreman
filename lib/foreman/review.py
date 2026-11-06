"""When a branch needs a review by an agent that did not write it, and whether the review it has lets it in.

One rule, read by `foreman integrate` (its review gate) and by the loop pass: a branch of a repo with `review =
"auto"` needs a review when it touches one of the repo's `risky_paths` or `review_paths` (the same matcher as the
risky paths: "dir/", "file", a glob, or "file:text"), when it changes more than `review_lines` lines of code (0: size
never triggers), or when its agent delivered it `partial`. Lines of code are those of `git diff --numstat
main...branch` (renames detected) outside test folders, `*.md`, `*.json` and lock files (EXCLUDED). A measurement
never comes here (`integrate --measure`); a `failed` delivery adds no reason of its own.

A review is a .md file under the home's reviews folder whose name holds «review» and whose first lines hold a fixed
header, one key per line (HEADER):

    Branch: REPO:BRANCH
    Tip: SHA
    Round: 1
    Open: H 1, M 2

The verdict is foreman's, counted from `Open:` (H and M block; L never does). The latest review of a branch is the one
with the highest round, then the newest file. It covers the branch's tip when `Tip` is the tip, or when the branch's
own diff (merge-base..tip) has the same `git patch-id --stable` as at `Tip` (a merge of main or a clean rebase does not
ask for another review). There are at most two rounds: a round 2 that leaves H or M open goes to the owner (a question;
the branch waits), and only the owner's yes (`integrate --approved`) lets it in as it is.

A branch whose reasons went (a `partial` delivered again as `ok`) while its latest review leaves H or M open still
needs its rounds (a reason of kind "review"). `state()` gives one of STATES; `integrate` lets the branch in on
NOT_NEEDED and CLEAN only. The reviewer agent of a branch is the one `foreman launch NAME --review REPO:BRANCH`
recorded (`review` in its agents.json entry, with its repo and branch): reviewer_of() finds it; while it is live it
holds the branch (the loop does not offer it to integrate, `integrate` says IN_REVIEW).
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

from . import util

if TYPE_CHECKING:
    from .config import Project, Table
    from .util import PathLike

MODES = ("auto", "off")
MAX_ROUNDS = 2
HEADER_LINES = 15  # a review's header lies within its first lines
# not code, for the size: test folders (any level), docs, data and lock files
EXCLUDED = (
    "tests/",
    "test/",
    "*.md",
    "*.json",
    "*.lock",
    "*-lock.yaml",
    "*.lockb",
    "go.sum",
)

NOT_NEEDED = "not needed"
NEEDED = "needed"
IN_REVIEW = "in review"
FINDINGS = "findings"
FIXED = "fixed, round 2 needed"
OWNER = "for the owner"
CLEAN = "clean"
STATES = (NOT_NEEDED, NEEDED, IN_REVIEW, FINDINGS, FIXED, OWNER, CLEAN)
PASSES = (NOT_NEEDED, CLEAN)  # the states `integrate` lets in

_OPEN = re.compile(r"^H\s*(\d+)\s*,\s*M\s*(\d+)\s*$")


class Reason(NamedTuple):
    """Why a branch needs a review: kind is "paths", "size" or "partial"; text says it."""

    kind: str
    text: str


@dataclass
class Review:
    """A review file's header (HEADER): its path relative to the home, the branch it names, the tip it read, its round
    and the H and M it leaves open."""

    path: str
    branch: str
    tip: str
    round: int
    high: int
    medium: int
    mtime: float = 0.0

    @property
    def open(self) -> int:
        return self.high + self.medium

    def counts(self) -> str:
        return f"H {self.high}, M {self.medium}"


@dataclass
class State:
    """Where a branch stands: status (one of STATES), the reasons it needs a review, its latest review (or None),
    the round the next review would be, the reviewer that holds it (IN_REVIEW) and a line for people."""

    status: str
    reasons: list[Reason] = field(default_factory=list)
    review: Review | None = None
    round: int = 1
    reviewer: str = ""
    problems: list[str] = field(default_factory=list)  # review files that name the branch but do not read

    @property
    def passes(self) -> bool:
        return self.status in PASSES

    @property
    def size_only(self) -> bool:
        return bool(self.reasons) and all(r.kind == "size" for r in self.reasons)

    def line(self) -> str:
        why = "; ".join(r.text for r in self.reasons)
        rv = self.review
        if self.status == NOT_NEEDED:
            return NOT_NEEDED
        if self.status == CLEAN and rv:
            return f"clean ({rv.path}, round {rv.round}, {rv.counts()} open; {why})"
        if self.status == IN_REVIEW:
            return f"in review by {self.reviewer} ({why})"
        if self.status == FINDINGS and rv:
            return f"findings: {rv.counts()} open ({rv.path}, round {rv.round} of {MAX_ROUNDS})"
        if self.status == FIXED and rv:
            return (
                f"fixed, round {MAX_ROUNDS} needed ({rv.path} left {rv.counts()} open at {rv.tip[:10]}; the branch "
                "has moved since)"
            )
        if self.status == OWNER and rv:
            return (
                f"for the owner: round {rv.round} of {MAX_ROUNDS} left {rv.counts()} open ({rv.path}); no third round"
            )
        stale = f"; {rv.path} reviewed {rv.tip[:10]}, another diff" if rv else ""
        bad = f"; unreadable: {', '.join(self.problems)}" if self.problems else ""
        return f"needed, round {self.round} ({why}){stale}{bad}"


def mode(repo: Table) -> str:
    return str(repo["review"])


def matches(path: str, entry: str) -> bool:
    """Whether a changed file PATH falls under ENTRY's path part ("dir/", "file" or a glob)."""
    return path == entry or (entry.endswith("/") and path.startswith(entry)) or fnmatch.fnmatch(path, entry)


def paths_touched(main_path: PathLike, main: str, branch: str, entries: list[str]) -> list[str]:
    """The ENTRIES ("dir/", "file", a glob, or "file:text": a changed line contains text) the branch touches in
    main...branch, each as "entry" when it names the file, else "file (entry)"; sorted."""
    if not entries:
        return []
    changed = util.git(main_path, "diff", "--name-only", f"{main}...{branch}").splitlines()
    hits: list[str] = []
    for entry in entries:
        path, _, needle = entry.partition(":")
        for f in changed:
            if not matches(f, path):
                continue
            if needle:
                diff = util.git(main_path, "diff", f"{main}...{branch}", "--", f)
                if not any(ln[:1] in "+-" and needle in ln for ln in diff.splitlines()[4:]):
                    continue
            hits.append(entry if entry == f else f"{f} ({entry})")
    return sorted(set(hits))


def excluded(path: str) -> bool:
    """Whether PATH is not code for the size (EXCLUDED): under a test folder, docs, data or a lock file."""
    parts = path.split("/")
    name = parts[-1]
    if any(p in ("tests", "test") for p in parts[:-1]):
        return True
    return any(fnmatch.fnmatch(name, pat) for pat in EXCLUDED if not pat.endswith("/"))


def code_lines(main_path: PathLike, main: str, branch: str) -> int:
    """Lines added plus removed in main...branch outside EXCLUDED (renames detected; binary files count 0)."""
    raw = util.git(main_path, "diff", "--numstat", "-z", "-M", f"{main}...{branch}")
    fields = raw.split("\0")
    total, i = 0, 0
    while i < len(fields):
        head = fields[i]
        i += 1
        if not head:
            continue
        added, removed, path = (head.split("\t", 2) + ["", ""])[:3]
        if not path:  # a rename: «added\tremoved\t» then the old and the new path
            path = fields[i + 1] if i + 1 < len(fields) else ""
            i += 2
        if excluded(path) or not added.isdigit() or not removed.isdigit():
            continue
        total += int(added) + int(removed)
    return total


def reasons(project: Project, repo: Table, branch: str, outcome: str | None = None) -> list[Reason]:
    """Why BRANCH of REPO (project.repo()) needs a review; [] when it does not, or when the repo's `review` is off."""
    if mode(repo) != "auto":
        return []
    main_path, main = repo["path"], repo["main"]
    out: list[Reason] = []
    entries = list(dict.fromkeys([*repo["risky_paths"], *repo["review_paths"]]))
    touched = paths_touched(main_path, main, branch, entries)
    if touched:
        out.append(Reason("paths", "delicate paths: " + ", ".join(touched)))
    limit = int(repo["review_lines"] or 0)
    if limit:
        n = code_lines(main_path, main, branch)
        if n > limit:
            out.append(Reason("size", f"{n} lines of code, over {limit}"))
    if outcome == "partial":
        out.append(Reason("partial", "delivered partial"))
    return out


def parse(text: str) -> dict[str, str]:
    """The HEADER keys of a review's first lines: {branch, tip, round, open}, as written."""
    out: dict[str, str] = {}
    for ln in text.splitlines()[:HEADER_LINES]:
        key, sep, value = ln.strip().partition(":")
        key = key.strip().lower()
        if sep and key in ("branch", "tip", "round", "open") and key not in out:
            out[key] = value.strip()
    return out


def read(home: Path, path: Path) -> tuple[str, Review | str] | None:
    """(the branch its header names, the review in PATH); None when it names no branch; the review is a str (what is
    wrong) when the rest of the header does not read."""
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            head = "".join(f.readline() for _ in range(HEADER_LINES))
        mtime = path.stat().st_mtime
    except OSError:
        return None
    h = parse(head)
    if not h.get("branch"):
        return None
    rel = str(path.relative_to(home)) if path.is_relative_to(home) else str(path)
    m = _OPEN.match(h.get("open", ""))
    rnd = h.get("round", "")
    tip = h.get("tip", "")
    if not (m and rnd.isdigit() and int(rnd) >= 1 and re.fullmatch(r"[0-9a-fA-F]{4,64}", tip)):
        return h["branch"], f"{rel} (header: Branch, Tip SHA, Round N, Open: H n, M n)"
    return h["branch"], Review(rel, h["branch"], tip, int(rnd), int(m.group(1)), int(m.group(2)), mtime)


def reviews(project: Project, repo_key: str, branch: str) -> tuple[list[Review], list[str]]:
    """The reviews of REPO_KEY:BRANCH under the reviews folder, oldest first, and those that name it but do not
    read."""
    folder = project.reviews_dir
    found: list[Review] = []
    bad: list[str] = []
    if not folder.is_dir():
        return found, bad
    want = f"{repo_key}:{branch}"
    for path in sorted(folder.rglob("*review*.md")):
        r = read(project.home, path)
        if r is None or r[0] != want:
            continue
        if isinstance(r[1], Review):
            found.append(r[1])
        else:
            bad.append(r[1])
    found.sort(key=lambda r: (r.round, r.mtime, r.path))
    return found, bad


def latest(project: Project, repo_key: str, branch: str) -> Review | None:
    """The latest review of REPO_KEY:BRANCH: the highest round, then the newest file; None without one."""
    found, _ = reviews(project, repo_key, branch)
    return found[-1] if found else None


def _own_patch_id(main_path: PathLike, main: str, commit: str) -> str | None:
    """`git patch-id --stable` of COMMIT's own diff (its merge-base with main..COMMIT), or None when git cannot."""
    base = util.git(main_path, "merge-base", main, commit, check=False)
    if not base:
        return None
    diff = util.git(main_path, "diff", "--no-color", "--no-ext-diff", f"{base}..{commit}", check=False)
    if not diff:
        return ""
    r = util.git_run(main_path, "patch-id", "--stable", input=diff + "\n")
    return r.stdout.split()[0] if r.returncode == 0 and r.stdout.strip() else None


def covers(main_path: PathLike, main: str, branch: str, tip: str) -> bool:
    """Whether a review of TIP covers BRANCH's tip: the same commit, or the same own diff (patch-id)."""
    full = util.git(main_path, "rev-parse", "--verify", "--quiet", f"{tip}^{{commit}}", check=False)
    head = util.git(main_path, "rev-parse", "--verify", "--quiet", f"{branch}^{{commit}}", check=False)
    if not full or not head:
        return False
    if full == head:
        return True
    a, b = _own_patch_id(main_path, main, full), _own_patch_id(main_path, main, head)
    return a is not None and a == b


def state(project: Project, repo: Table, branch: str, outcome: str | None = None, reviewer: str = "") -> State:
    """Where BRANCH of REPO (project.repo()) stands (STATES). REVIEWER: a live agent reviewing it (IN_REVIEW, the loop
    knows it)."""
    why = reasons(project, repo, branch, outcome)
    if mode(repo) != "auto":
        return State(NOT_NEEDED)
    found, bad = reviews(project, repo["key"], branch)
    rv = found[-1] if found else None
    if not why:
        if not (rv and rv.open):
            return State(NOT_NEEDED)
        # its reasons went (a partial delivered again as ok), but a review left H or M open: the rounds go on
        why = [Reason("review", f"{rv.path} left {rv.counts()} open")]
    st = State(NEEDED, why, rv, problems=bad)
    if reviewer:
        st.status, st.reviewer = IN_REVIEW, reviewer
        st.round = min(rv.round + 1, MAX_ROUNDS) if rv else 1
        return st
    if rv is None:
        return st
    current = covers(repo["path"], repo["main"], branch, rv.tip)
    if rv.open and rv.round >= MAX_ROUNDS:
        st.status = OWNER
    elif current:
        st.status = CLEAN if not rv.open else FINDINGS
    elif rv.open:
        st.status, st.round = FIXED, rv.round + 1
    else:  # a clean review of another diff: read again
        st.round = min(rv.round + 1, MAX_ROUNDS)
    return st


def reviewer_of(agents: Iterable[Mapping[str, Any]], repo_key: str, branch: str) -> Mapping[str, Any] | None:
    """The reviewer agent of REPO_KEY:BRANCH among AGENTS (agents.json entries with their `id`): one launched with
    `--review` (`review` in its entry) on that repo and branch, the latest started; None without one."""
    mine = [
        h for h in agents if h.get("review") and h.get("branch") == branch and (h.get("repo") or repo_key) == repo_key
    ]
    return max(mine, key=lambda h: str(h.get("start") or "")) if mine else None
