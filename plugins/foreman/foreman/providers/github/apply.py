"""A change to the backlog on GitHub, written and read back (DESIGN.md «Reads and writes per pass»): `apply` writes what
an `edit()` changed, task by task, only the difference; `file` files a new task. Each task's writes end with one read-back,
and a value that did not stick is an error naming it. Then the local files: the local keys, the questions, a closed task
and the snapshot."""

import dataclasses
from dataclasses import dataclass

from ... import util
from ...core.project.backlog_table import Board
from ...core.tasks import board, ids
from ...core.tasks.backlog import BacklogError, Data, Task
from ...core.tasks.issues import CLOSED_AS, Issue, body_of, facets, fields_of, split, stuck, to_task
from ...core.tasks.questions import open_questions
from . import writes
from .api import Api
from .files import Files
from .issues import Read, read_one

CLOSE = {status: reason for reason, status in CLOSED_AS.items()}  # done → COMPLETED, canceled → NOT_PLANNED, duplicate → DUPLICATE
FIELDS = {f.name for f in board.FIELDS}


@dataclass(frozen=True)
class Where:
    """The project's backlog on GitHub: its Board, its ids and its local files."""

    api: Api
    b: Board
    prefix: str
    legacy: dict[str, int]
    files: Files

    def task(self, i: Issue) -> Task:
        return to_task(i, self.prefix, self.legacy)


def _label(before: Data, after: Data, tid: str) -> bool | None:
    """`needs-owner` on (True) when the task's first question opens, off (False) when its last closes; else None."""
    was, now = bool(open_questions(before, tid)), bool(open_questions(after, tid))
    return None if was == now else now


def _nodes(w: Where, read: Read, tids: list[str]) -> dict[str, str]:
    """The issue node of each task id: from the pass's read, else (a closed issue) looked up in one request."""
    numbers = {tid: ids.number(w.prefix, tid, w.legacy) for tid in tids}
    if bad := [tid for tid, n in numbers.items() if n is None]:
        raise BacklogError(f"no task {bad[0]}")
    known = {i.number: i.node for i in read.issues}
    if rest := sorted({n for n in numbers.values() if n is not None and n not in known}):
        known |= writes.nodes(writes.Target(w.api, w.b, read.meta), rest)
    return {tid: known[n] for tid, n in numbers.items() if n is not None}


def _back(w: Where, tid: str, number: int, want: Task, names: list[str]) -> Issue:
    """The issue read back; a BacklogError naming the first of `names` that did not stick."""
    back = read_one(w.api, w.b, number)
    if not isinstance(back, Issue):
        raise BacklogError(f"{tid}: #{number} read back as {back}")
    got = w.task(back)
    if bad := [n for n in stuck(want, got) if n in names]:
        raise BacklogError(f"{tid}: {bad[0]} did not stick: wrote {facets(want)[bad[0]]!r}, read back {facets(got).get(bad[0])!r}")
    return back


def _links(w: Where, read: Read, was: Task, now: Task) -> writes.Links:
    """The parent (when it changed) and the blockers added and removed, as issue nodes."""
    parent = now.get("parent", "") if now.get("parent") != was.get("parent") else ""
    had, has = set(was.get("blocked_by") or []), set(now.get("blocked_by") or [])
    add, remove = sorted(has - had), sorted(had - has)
    found = _nodes(w, read, [x for x in (parent, *add, *remove) if x])
    return writes.Links(found.get(parent, ""), [found[x] for x in add], [found[x] for x in remove])


def _issue(w: Where, read: Read, tid: str) -> Issue | None:
    """The task's open issue in the pass's read; None when it is closed."""
    n = ids.number(w.prefix, tid, w.legacy)
    return next((i for i in read.issues if i.number == n), None)


def _write(w: Where, read: Read, old: Task, x: Task, label: bool | None) -> Issue:
    """One task's writes, in order: title and body, fields (and the close), links, label; then its read-back. `x` is
    the task after the edit."""
    tid, was, (now, local), t = old["id"], split(old)[0], split(x), writes.Target(w.api, w.b, read.meta)
    changed = stuck(was, now)
    i = _issue(w, read, tid)
    if i is None:
        raise BacklogError(f"{tid} is {old['status']}: fm changes no closed task on the Board")
    if "parent" in changed and not now.get("parent"):
        raise BacklogError(f"{tid}: fm does not take a task out of its parent")
    if {"title", "body"} & set(changed):
        writes.body(t, i.node, now["title"], body_of(now, i.body))
    if names := [c for c in changed if c in FIELDS]:
        values = fields_of(now)
        close = CLOSE.get(now["status"], "") if "Status" in names else ""
        writes.fields(t, i.node, i.item, {f: values.get(f) for f in names}, close)
        if close:  # it is closed: kept now with its local keys, as the open read no longer has it, should the read-back fail
            w.files.keep(now)
            w.files.save_runs({k: v for k, v in (w.files.runs() | {tid: local}).items() if v})
        if "Status" in names and (snap := w.files.snapshot()) is not None:  # fm's move, landed: never a drag for the pass
            snap.pop(tid, None)
            w.files.save_snapshot(snap if close else {**snap, tid: now["status"]})
    if "parent" in changed or "blocked_by" in changed:
        writes.links(t, i.node, _links(w, read, was, now))
    if label is not None:
        writes.label(t, i.node, label)
    back = _back(w, tid, i.number, now, changed)
    if label is not None and (board.LABEL in back.labels) != label:
        raise BacklogError(f"{tid}: label {board.LABEL} did not stick: wrote {'on' if label else 'off'}, read back {list(back.labels)}")
    return back


def _save(w: Where, before: Data, after: Data, backs: dict[str, Issue]) -> None:
    """The local files after GitHub's writes: a task closed, the local keys and the questions that changed."""
    for back in backs.values():
        if back.state == "CLOSED":
            w.files.keep(w.task(back))
    olds, runs = {x["id"]: x for x in before["tasks"]}, w.files.runs()
    if changed := {x["id"]: now for x in after["tasks"] if (now := split(x)[1]) != split(olds[x["id"]])[1]}:
        w.files.save_runs({k: v for k, v in (runs | changed).items() if v})
    if (before["decisions"], before["decisions_resolved"]) != (after["decisions"], after["decisions_resolved"]):
        w.files.save_questions(after["decisions"], after["decisions_resolved"])


def apply(w: Where, read: Read, before: Data, after: Data) -> Read:
    """Writes what changed from `before` to `after` (an `edit()`): GitHub first, each task read back; then the local
    files. Returns the pass's read with each read-back in its place (a closed issue leaves it)."""
    olds = {x["id"]: x for x in before["tasks"]}
    backs: dict[str, Issue] = {}
    for x in after["tasks"]:
        if x["id"] not in olds:
            raise BacklogError(f"{x['id']}: a new task is filed with add, not in an edit")
        now, label = split(x)[0], _label(before, after, x["id"])
        if _issue(w, read, x["id"]) is None:
            asked = {q["id"] for q in open_questions(after, x["id"])} - {q["id"] for q in open_questions(before, x["id"])}
            if asked:
                raise BacklogError(f"{x['id']} is {x['status']}: fm changes no closed task on the Board")
            if label is False:
                label = None  # an answer on a closed task: its questions only
        if stuck(split(olds[x["id"]])[0], now) or label is not None:
            backs[x["id"]] = _write(w, read, olds[x["id"]], x, label)
    _save(w, before, after, backs)
    by_number = {b.number: b for b in backs.values()}
    kept = (by_number.get(i.number, i) for i in read.issues)
    return dataclasses.replace(read, issues=tuple(i for i in kept if i.state == "OPEN"))


def file(w: Where, read: Read, task: Task) -> Issue:
    """Files the task: createIssue, its card with its parent and blockers, its fields in one request, the read-back.
    One cut short after the issue exists leaves an error line naming it, for the owner to close."""
    t = writes.Target(w.api, w.b, read.meta)
    node, number = writes.create(t, task["title"], body_of(task, ""))
    try:
        item = writes.card(t, node, _links(w, read, {}, task))
        writes.fields(t, node, item, fields_of(task))
        return _back(w, f"#{number}", number, task, list(facets(task)))
    except Exception as e:
        why = f"#{number} was filed but not finished: close it by hand ({e})"
        util.append_jsonl(w.files.state / "errors.jsonl", {"time": util.now(), "step": "filing", "error": why, "trace": ""})
        raise
