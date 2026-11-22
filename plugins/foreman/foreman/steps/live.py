"""`foreman agents --json`: the agents the loop has live now, each tied to its task and its session. Read from the backlog under
`registry.view_scope` (the loop's snapshot, no platform call) and the runner's session listing."""

from typing import Any, NamedTuple

from ..core.project.config import Project
from ..core.review import reviews
from ..core.tasks import backlog
from ..providers import registry
from ..providers.runner import Rows, ref_of

RECORDS = ("agent", "mechanic", "hunter", "reviewer")  # the task's records, one agent each
KIND_OF = {"hunter": "hunter", "reviewer": "review", "mechanic": "mechanic"}  # a record that is not `agent`


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


def _kind(i: dict[str, Any], key: str, record: dict[str, Any]) -> str:
    if key != "agent":
        return KIND_OF[key]
    kind = str(record.get("kind") or "")
    return "chain" if kind == "parent" and i.get("chain") else kind


def _round(i: dict[str, Any], key: str, record: dict[str, Any]) -> int | None:
    if key in ("hunter", "reviewer"):
        return record.get("round")
    if key == "agent" and record.get("kind") == "responsible" and reviews.review_round(i) > 1:
        return reviews.review_round(i)
    return None


def agents(p: Project, rows: Rows) -> list[Live]:
    """The agents of the open tasks whose session is alive in `rows` (idle included), in backlog order."""
    with registry.view_scope(p):
        d = registry.backlog(p).load()
    runner = registry.runner(p)
    out: list[Live] = []
    for i in backlog.tasks(d):
        if i["status"] in backlog.CLOSED:
            continue
        for key in RECORDS:
            record: dict[str, Any] = i.get(key) or {}
            ref = ref_of(record)
            if not record.get("session") or not ref or not rows.alive(ref):
                continue
            out.append(
                Live(
                    i["id"],
                    _kind(i, key, record),
                    record.get("model") or "",
                    record.get("effort") or "",
                    record.get("started") or "",
                    _round(i, key, record),
                    record["session"],
                    runner.handle(ref),
                )
            )
    return out
