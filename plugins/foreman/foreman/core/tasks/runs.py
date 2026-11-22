"""The run ledger: every agent session the loop started for a task, from its start to its outcome. A plain append-only
file, `<home>/state/ledger.jsonl` (`runs.json` is the GitHub backlog's local keys): the first line of an id is the run,
later lines with that id patch it (`end`, `attach`); `load` folds them in file order."""

from pathlib import Path
from typing import Any, NamedTuple

from ... import util
from ..project.config import Project

KINDS = ("planning", "responsible", "review", "hunter", "parent", "chain", "mechanic")


class Run(NamedTuple):
    """One run: `attempt` counts the task's runs of this kind; `session` and `handle` are "" while unknown; `ended`,
    `outcome` (done, failed or canceled) are None while it is active; `failure_reason` is "" unless it failed."""

    id: str
    task: str
    kind: str
    attempt: int
    session: str
    handle: str
    model: str
    effort: str
    started: str
    ended: str | None = None
    outcome: str | None = None
    failure_reason: str = ""

    @property
    def active(self) -> bool:
        return self.ended is None

    def json(self) -> dict[str, Any]:
        return self._asdict()


class Session(NamedTuple):
    """The session a run is of: its id and the runner's handle ("" while unknown), its model and effort."""

    id: str
    handle: str
    model: str
    effort: str


TEXT_OR_NULL = (str, type(None))
TYPES: dict[str, Any] = {
    "id": str,
    "task": str,
    "kind": str,
    "attempt": int,
    "session": str,
    "handle": str,
    "model": str,
    "effort": str,
    "started": str,
    "ended": TEXT_OR_NULL,
    "outcome": TEXT_OR_NULL,
    "failure_reason": str,
}


def _file(p: Project) -> Path:
    return p.state / "ledger.jsonl"


def _lock(p: Project) -> Path:
    return p.state / "ledger"  # not the file's own name: `append_jsonl` takes that lock inside this one


def _run(d: dict[str, Any]) -> Run | None:
    """The folded object as a run; None when it does not fit."""
    if not all(k in d and isinstance(d[k], t) for k, t in TYPES.items()):
        return None
    return Run(**{k: d[k] for k in TYPES})


def load(p: Project) -> list[Run]:
    """Every run, in start order; no file is no runs."""
    folded: dict[str, dict[str, Any]] = {}
    for line in util.read_jsonl(_file(p)):
        obj = util.json_object(line)
        if obj is not None and isinstance(obj.get("id"), str):
            folded[obj["id"]] = {**folded.get(obj["id"], {}), **obj}
    return [r for d in folded.values() if (r := _run(d))]


def _active(runs: list[Run], task: str, kinds: tuple[str, ...]) -> list[Run]:
    return [r for r in runs if r.task == task and r.kind in kinds and r.active]


def _end(p: Project, runs: list[Run], outcome: str, reason: str) -> None:
    for r in runs:
        patch = {"id": r.id, "ended": util.now(), "outcome": outcome, "failure_reason": reason if outcome == "failed" else ""}
        util.append_jsonl(_file(p), patch)


def start(p: Project, task: str, kind: str, s: Session) -> Run:
    """Appends a new run of `kind` for `task`; an active run of the same task and kind is superseded (canceled)."""
    with util.file_lock(_lock(p)):
        every = load(p)
        _end(p, _active(every, task, (kind,)), "canceled", "")
        attempt = sum(1 for r in every if r.task == task and r.kind == kind) + 1
        run = Run(f"R-{len(every) + 1}", task, kind, attempt, s.id, s.handle, s.model, s.effort, util.now())
        util.append_jsonl(_file(p), run.json())
    return run


def end(p: Project, task: str, kinds: tuple[str, ...], outcome: str, reason: str = "") -> None:
    """Ends the task's active runs of these kinds with `outcome`; a run already ended keeps its first outcome."""
    with util.file_lock(_lock(p)):
        _end(p, _active(load(p), task, kinds), outcome, reason)


def attach(p: Project, task: str, kind: str, session: str) -> None:
    """The session id of the task's active run of `kind`, once the runner lists it (`fill_sessions`)."""
    with util.file_lock(_lock(p)):
        for r in _active(load(p), task, (kind,)):
            if not r.session:
                util.append_jsonl(_file(p), {"id": r.id, "session": session})
