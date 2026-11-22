"""`foreman agents --json`: the agents the loop has live now, each an active run (`core/tasks/runs.py`) tied to its task and
its session. Read from the ledger, the backlog under `registry.view_scope` (the loop's snapshot, no platform call) and the
runner's session listing."""

from typing import Any, NamedTuple

from ..core.project.config import Project
from ..core.review import reviews
from ..core.tasks import backlog, runs
from ..providers import registry
from ..providers.runner import Rows, ref_of

ROLE = {"review": "reviewer", "hunter": "hunter", "mechanic": "mechanic"}  # the task record of a run kind; any other: `agent`


class Live(NamedTuple):
    """One live agent: `kind` planning, responsible, review, hunter, parent, chain or mechanic; `round` the review's round
    when it has one (a hunter's or reviewer's own; a responsible agent's above 1), else None; `session` its id and `handle`
    the runner's own id for it ("" when unknown)."""

    task: str
    kind: str
    model: str
    effort: str
    started: str
    round: int | None
    session: str
    handle: str


def _round(i: dict[str, Any], run: runs.Run, record: dict[str, Any]) -> int | None:
    if run.kind in ("hunter", "review"):
        return record.get("round")
    if run.kind == "responsible" and reviews.review_round(i) > 1:
        return reviews.review_round(i)
    return None


def agents(p: Project, rows: Rows) -> list[Live]:
    """The active runs of the open tasks whose session is alive in `rows` (idle included), in backlog order, then start order."""
    with registry.view_scope(p):
        d = registry.backlog(p).load()
    runner = registry.runner(p)
    active = [r for r in runs.load(p) if r.active]
    out: list[Live] = []
    for i in backlog.tasks(d):
        if i["status"] in backlog.CLOSED:
            continue
        for run in (r for r in active if r.task == i["id"]):
            record: dict[str, Any] = i.get(ROLE.get(run.kind, "agent")) or {}
            ref = ref_of(record)
            if not record.get("session") or not ref or not rows.alive(ref):
                continue
            out.append(Live(i["id"], run.kind, run.model, run.effort, run.started, _round(i, run, record), record["session"], runner.handle(ref)))
    return out
