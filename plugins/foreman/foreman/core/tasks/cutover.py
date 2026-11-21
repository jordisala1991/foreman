"""The cutover's rules (DESIGN.md «The backlog on GitHub», `decision.cutover`, «Ids»): which of the old file's tasks are filed
as issues and in what order, what refuses the move before anything is written, and what each task keeps locally. Pure: the
GitHub adapter (`providers/github/cutover.py`) files them."""

import re
from collections.abc import Mapping

from .backlog import CLOSED, Acceptance, BacklogError, Data, Task
from .issues import body_of, body_parts, fields_of, split

NOT_CARRIED = ("plan", "subs", "defined_by", "undelivered")  # no code reads them; they stay in the old file


def mark(tid: str) -> str:
    """The issue body's first line for a migrated task (`issues.LEGACY_MARK` reads it)."""
    return f"<!-- foreman:legacy-id={tid} -->"


def _open(d: Data) -> list[Task]:
    return [t for t in d["tasks"] if t["status"] not in CLOSED]


def highest(d: Data, prefix: str) -> int:
    """The largest number among the ids `<prefix>-<n>`, open or closed (0 for none)."""
    return max((int(m[1]) for t in d["tasks"] if (m := re.fullmatch(rf"{re.escape(prefix)}-(\d+)", t["id"]))), default=0)


def _texts(t: Task) -> tuple[str, str, str]:
    a: Acceptance = t.get("acceptance") or {}
    return a.get("goal", "").strip(), a.get("criteria", "").strip(), str(t.get("detail", "")).strip()


def refusal(d: Data, prefix: str, newest: int) -> str:
    """Why the old file cannot move to a repo whose newest issue or PR is #newest ("" when it can); the first hit wins."""
    if newest < (h := highest(d, prefix)):
        return f"the repo's newest issue or PR is #{newest}, below the old file's highest id {prefix}-{h}: an id could mean two tasks"
    status = {t["id"]: t["status"] for t in d["tasks"]}
    for t in _open(d):
        if (pid := t.get("parent")) and status.get(pid) in CLOSED:
            return f"{t['id']}: its parent {pid} is closed and stays in the old file, so {t['id']} cannot become its sub-issue"
        if body_parts(body_of(t, "")) != _texts(t):
            return f"{t['id']}: the issue body would not keep its goal, criteria or detail as they are"
    return ""


def todo(d: Data, filed: Mapping[str, int]) -> list[Task]:
    """The open tasks not yet filed, each after its open parent and open blockers (file order otherwise), each with
    `blocked_by` cut to its open blockers (the closed ones stay in the old file, read from there)."""
    opened = {t["id"] for t in _open(d)}
    placed = set(filed)
    rest = [t for t in _open(d) if t["id"] not in placed]
    out: list[Task] = []
    while rest:
        t = next((t for t in rest if all(x in placed for x in [t.get("parent"), *t.get("blocked_by", [])] if x in opened)), None)
        if t is None:
            raise BacklogError(f"{', '.join(x['id'] for x in rest)} wait for each other: no order files them")
        rest.remove(t)
        placed.add(t["id"])
        cut = {k: v for k, v in t.items() if k != "blocked_by"}
        if blockers := [x for x in t.get("blocked_by", []) if x in opened]:
            cut["blocked_by"] = blockers
        out.append(cut)
    return out


def local(t: Task) -> Task:
    """What the task keeps in `<home>/state/runs.json`: its local keys, without those not carried."""
    return {k: v for k, v in split(t)[1].items() if k not in NOT_CARRIED}


def line(t: Task, d: Data) -> str:
    """The dry run's line of a task as it would be filed: id, title, its Board fields, parent and blockers."""
    fields = ", ".join(f"{k}={v}" for k, v in fields_of(t).items())
    out = f"{t['id']}  «{t['title']}»  {fields}"
    if pid := t.get("parent"):
        out += f"  parent={pid}"
    if blockers := t.get("blocked_by"):
        out += f"  blockers={', '.join(blockers)}"
    old = next(x for x in d["tasks"] if x["id"] == t["id"])
    if lost := [x for x in old.get("blocked_by", []) if x not in (blockers or [])]:
        out += f"  blockers in the old file={', '.join(lost)}"
    return out
