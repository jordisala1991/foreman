"""The backlog port's GitHub adapter, reads only (DESIGN.md «The backlog on GitHub», D3): the open tasks are the open issues
with a card on the project's Board, read in one request; the closed ones come from the old file and `<home>/state/closed.json`;
a blocker closed on GitHub that neither holds is a stub. Shared (a tick, `registry.tick_scope`): read once, then each `load()`
is a copy. Every write refuses until D4 builds them."""

import copy
import re
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any, Unpack

from ... import util
from ...core.project.backlog_table import Board
from ...core.project.config import Project
from ...core.tasks import ids
from ...core.tasks.backlog import CLOSED, BacklogError, Data, Local, NewTask, Task, empty, get
from ...core.tasks.issues import Issue, stub, to_task
from ...core.tasks.questions import Question
from .api import Api
from .issues import read_one, read_open

READ_ONLY = "the backlog on GitHub is read-only until its writes are built (FN-140)"


class GitHubBacklog:
    def __init__(self, p: Project, b: Board, old: Callable[[], Data], shared: bool = False) -> None:
        self.p = p
        self.b = b
        self.old = old  # the old file's backlog: its closed tasks stay there
        self.shared = shared
        self.api = Api(p.state / "github-rate-limit.json")
        self.prefix = str(p.get("project", "prefix"))
        self.kept: Data | None = None
        self.guard = threading.Lock()

    def _legacy(self) -> dict[str, int]:
        return ids.legacy_ids(util.read_json(self.p.home / "legacy-ids.json", None))

    def _task(self, i: Issue, legacy: dict[str, int]) -> Task:
        return to_task(i, self.prefix, legacy)

    def _read(self) -> Data:
        legacy = self._legacy()
        found = read_open(self.api, self.b)
        closed = [t for t in self.old()["tasks"] if t["status"] in CLOSED]
        held = {t["id"] for t in closed}  # an id is looked up in the old file before the number, as `find` does
        closed += self._closed_json()
        stubs = [stub(x, self.prefix, legacy) for i in found for x in i.blockers if x.state == "CLOSED"]
        out: dict[str, Task] = {}
        for t in [*(t for t in (self._task(i, legacy) for i in found) if t["id"] not in held), *closed, *stubs]:
            out.setdefault(t["id"], t)
        return {**empty(), "tasks": list(out.values())}

    def _closed_json(self) -> list[Task]:
        """The tasks fm closed on GitHub, as it kept them (D4 writes the file): a list of tasks, each with its id and status."""
        f = self.p.state / "closed.json"
        raw: object = util.read_json(f, [])
        rows = util.json_list(raw)
        tasks = [o for t in rows if (o := util.json_object(t)) is not None]
        if rows is not raw or len(tasks) != len(rows) or not all(isinstance(t.get("id"), str) and t.get("status") in CLOSED for t in tasks):
            raise BacklogError(f"{f}: expected a list of closed tasks, each with its id and status")
        return tasks

    def load(self) -> Data:
        if not self.shared:
            return self._read()
        with self.guard:
            if self.kept is None:
                self.kept = self._read()
            return copy.deepcopy(self.kept)

    def reload(self) -> Data:
        d = self._read()
        if self.shared:
            with self.guard:
                self.kept = copy.deepcopy(d)
        return d

    def find(self, tid: str) -> Task:
        legacy = self._legacy()
        if tid in legacy:
            return self._live(tid, legacy[tid], legacy)
        try:
            return get(self.old(), tid)
        except BacklogError:
            pass
        m = re.fullmatch(rf"{re.escape(self.prefix)}-(\d+)", tid)
        if m is None:
            raise BacklogError(f"no task {tid}")
        n = int(m[1])
        if held := next((k for k, v in legacy.items() if v == n), ""):
            raise BacklogError(f"no task {tid}: #{n} is {held}")
        return self._live(tid, n, legacy)

    def _live(self, tid: str, n: int, legacy: dict[str, int]) -> Task:
        i = read_one(self.api, self.b, n)
        if i == "pull request":
            raise BacklogError(f"no task {tid}: #{n} is a pull request")
        if not isinstance(i, Issue):
            raise BacklogError(f"no task {tid}: there is no #{n}")
        if not i.carded:
            raise BacklogError(f"no task {tid}: #{n} is not on the Board")
        return self._task(i, legacy)

    def create(self) -> None:
        """Nothing to create: the repo and the Board exist (`foreman backlog setup` makes the Board fm's)."""

    def local(self, tid: str) -> Local | None:
        return None  # local state on a Board project is D4's

    def edit(self) -> AbstractContextManager[Data]:
        raise BacklogError(READ_ONLY)

    def add(self, title: str, *, status: str = "backlog", **f: Unpack[NewTask]) -> str:
        raise BacklogError(READ_ONLY)

    def set_status(self, tid: str, status: str, why: str = "", **keys: Any) -> Task:
        raise BacklogError(READ_ONLY)

    def update(self, tid: str, **keys: Any) -> Task:
        raise BacklogError(READ_ONLY)

    def record_misled(self, tid: str, role: str, text: str) -> None:
        raise BacklogError(READ_ONLY)

    def ask(self, tid: str, agent: str, text: str, **f: Unpack[Question]) -> str:
        raise BacklogError(READ_ONLY)

    def answer(self, qid: str, text: str, by: str = "owner (panel)") -> dict[str, Any]:
        raise BacklogError(READ_ONLY)

    def note(self, tid: str, text: str, source: str) -> None:
        raise BacklogError(READ_ONLY)

    def mark_delivered(self, qid: str) -> None:
        raise BacklogError(READ_ONLY)
