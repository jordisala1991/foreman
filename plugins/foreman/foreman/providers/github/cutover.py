"""`foreman backlog cutover` (DESIGN.md «The backlog on GitHub», `decision.cutover`, D6): the old file's open tasks filed as
issues on the Board, each with its legacy id (the body's first line and `<home>/legacy-ids.json`, written after each task so
a run cut short resumes); then their local keys and the questions in the local files, and last the `[backlog]` table appended
to foreman.toml (deleting it points fm back at the file). Every refusal comes before any write; the old file is only read."""

import dataclasses
from collections.abc import Iterator

from ... import util
from ...core.project.backlog_table import Board, table
from ...core.project.config import Project
from ...core.tasks import board as names
from ...core.tasks import ids
from ...core.tasks.backlog import BacklogError, Data
from ...core.tasks.cutover import line, local, mark, refusal, todo
from ...core.tasks.issues import Issue
from ...core.tasks.questions import open_questions
from .. import ConfigError
from . import board, writes
from .api import Api, nodes, obj, whole
from .apply import Where, file
from .files import Files
from .issues import query, read_one, read_open

NEWEST = """query Newest($owner:String!,$repo:String!){repository(owner:$owner,name:$repo){
 issues(first:1,orderBy:{field:CREATED_AT,direction:DESC}){nodes{number}}
 pullRequests(first:1,orderBy:{field:CREATED_AT,direction:DESC}){nodes{number}}}}"""


def newest(api: Api, b: Board) -> int:
    """The repo's newest issue or PR number; 0 when it has neither."""
    repo = obj(query(api, b, NEWEST)["repository"], "repository")
    return max((whole(n, "number", key) for key in ("issues", "pullRequests") for n in nodes(repo, key, key)), default=0)


def run(p: Project, b: Board, old: Data, dry_run: bool) -> Iterator[str]:
    """The cutover, one output line at a time; a dry run lists what it would file and writes nothing."""
    api, prefix = Api(p.state / "github-rate-limit.json"), str(p.get("project", "prefix"))
    top = newest(api, b)
    if why := refusal(old, prefix, top):
        raise BacklogError(why)
    if dry_run:
        tasks = todo(old, {})
        yield f"dry run: nothing is written; {len(tasks)} open tasks would be filed as issues numbered after #{top}, in this order:"
        yield from (line(t, old) for t in tasks)
        yield "then the Board is set up and the [backlog] table is added to foreman.toml"
        return
    if not p.paused:
        raise ConfigError("the loop is not paused: run foreman pause first")
    yield from board.setup(b, api)
    read = read_open(api, b)
    legacy_file = p.home / "legacy-ids.json"
    w = Where(api, b, prefix, ids.legacy_ids(util.read_json(legacy_file, None)), Files(p.state))
    for t in todo(old, w.legacy):
        tid = t["id"]
        back = file(w, read, t, mark(tid))
        w.legacy[tid] = back.number
        util.write_json(legacy_file, w.legacy)
        read = dataclasses.replace(read, issues=(*read.issues, back))
        if open_questions(old, tid):
            writes.label(writes.Target(api, b, read.meta), back.node, True)
            again = read_one(api, b, back.number)
            if not isinstance(again, Issue) or names.LABEL not in again.labels:
                raise BacklogError(f"{tid}: label {names.LABEL} did not stick")
        yield f"{tid} → #{back.number}"
    runs = w.files.runs() | {t["id"]: kept for t in old["tasks"] if t["id"] in w.legacy and (kept := local(t))}
    w.files.save_runs(runs)
    w.files.save_questions(old["decisions"], old["decisions_resolved"])
    util.write_text_atomic(p.config_file, p.config_file.read_text(encoding="utf-8") + table(b))
    yield "foreman.toml: [backlog] added; to go back, delete that table"
