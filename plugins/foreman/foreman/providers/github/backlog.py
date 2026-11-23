"""The backlog port's GitHub adapter (DESIGN.md «The backlog on GitHub»): the open tasks are the open issues with a card on
the project's Board, read in one request; the closed ones come from the old file and `<home>/state/closed.json`; a blocker
closed on GitHub that neither holds is a stub; each task's local keys and the questions are local files (`files.Files`).
Every write goes through `edit()`, which writes only what changed and reads it back (`apply`); `add` files. The pass's read
(a shared instance's first, `registry.tick_scope`), every write and its snapshot update run under the backlog lock
`<home>/state/backlog.lock`; a shared instance keeps its read, so a tick reads the Board once. After the pass's read and each write's read-back the Board's
side of the open tasks is saved (`backlog-cards.json`); a `seen` instance (`registry.view_scope`: the panel, `watch`) serves it."""

import contextlib
import copy
import dataclasses
from collections.abc import Callable, Generator
from typing import Unpack

from ... import util
from ...core.project.backlog_table import Board
from ...core.project.config import Project
from ...core.tasks import changes, drags, ids
from ...core.tasks.backlog import CLOSED, BacklogError, Data, Local, NewTask, NoSnapshot, Task, empty, event, get, tasks, unblocked
from ...core.tasks.issues import Issue, joined, stub, with_old_blockers, without_old_blockers
from ..backlogbase import BacklogBase
from .api import STOP_FILE, Api
from .apply import Where, apply, file
from .files import Cards, Files
from .issues import Read, read_one, read_open


class GitHubBacklog(BacklogBase):
    def __init__(self, p: Project, b: Board, old: Callable[[], Data], shared: bool = False, seen: bool = False) -> None:
        self.p = p
        self.b = b
        self.old = old  # the old file's backlog: its closed tasks stay there
        self.shared = shared
        self.seen = seen  # serves the saved cards, never the Board
        self.api = Api(p.state / STOP_FILE)
        self.prefix = str(p.get("project", "prefix"))
        self.files = Files(p.state)
        self.lock = p.state / "backlog"  # util.file_lock adds .lock
        self.kept: tuple[Read, Data] | None = None  # a shared instance's Board read and its data

    def _where(self) -> Where:
        return Where(self.api, self.b, self.prefix, ids.legacy_ids(util.read_json(self.p.home / "legacy-ids.json", None)), self.files)

    def _cards(self, read: Read) -> Cards:
        w = self._where()
        return Cards([w.task(i) for i in read.issues], [stub(x, self.prefix, w.legacy) for i in read.issues for x in i.blockers if x.state == "CLOSED"])

    def _join(self, cards: Cards) -> Data:
        """The backlog from the Board's cards, the closed tasks and the local files, read now."""
        runs, old = self.files.runs(), {t["id"]: t for t in self.old()["tasks"]}
        closed = [t for t in old.values() if t["status"] in CLOSED]
        held = {t["id"] for t in closed}  # an id is looked up in the old file before the number, as `find` does
        mine = [with_old_blockers(t, old) for t in cards.tasks if t["id"] not in held]
        out: dict[str, Task] = {}
        for t in [*(joined(t, runs.get(t["id"], {})) for t in mine), *closed, *(joined(t, runs.get(t["id"], {})) for t in self.files.closed()), *cards.stubs]:
            out.setdefault(t["id"], t)
        decisions, resolved = self.files.questions()
        return {**empty(), "tasks": list(out.values()), "decisions": decisions, "decisions_resolved": resolved}

    def _data(self, read: Read) -> Data:
        return self._join(self._cards(read))

    def _view(self) -> Data:
        """The data as the last pass or write saw the Board; the local files read now. No call to GitHub."""
        if (cards := self.files.cards()) is None:
            raise NoSnapshot("the loop has not read the Board yet")
        return self._join(cards)

    def _fresh(self) -> tuple[Read, Data]:
        r = read_open(self.api, self.b)
        return r, self._data(r)

    def _keep(self, r: Read) -> tuple[Read, Data]:
        self.kept = (r, self._data(r))
        return self.kept

    def _board(self, fresh: bool = False) -> tuple[Read, Data]:
        """Under the lock: the Board's read and the data. A shared instance's first read is the pass's; it keeps the
        read, and reads again only when `fresh`."""
        if not self.shared:
            return self._fresh()
        if self.kept is None:
            return self._keep(self._pass(read_open(self.api, self.b)))
        return self._keep(read_open(self.api, self.b)) if fresh else self.kept

    def _pass(self, read: Read) -> Read:
        """The cards the owner dragged since the snapshot: a Go is let in, any other drag is an error line and the card
        goes back. No snapshot yet: nothing is a drag. Then the snapshot is the open tasks' statuses."""
        w = self._where()
        snap = self.files.snapshot()
        if snap is not None:
            before = self._data(read)
            after = copy.deepcopy(before)
            lets: list[str] = []
            refused: list[str] = []
            for d in drags.found(snap, {t["id"]: t["status"] for t in map(w.task, read.issues)}):
                i = get(after, d.id)
                if why := drags.verdict(d, i, tasks(after), self.p.repo_keys):
                    i["status"] = d.was
                    refused.append(why)
                else:
                    i.update(unblocked())
                    lets.append(d.id)
            read = apply(w, read, before, after)
            for tid in lets:
                event(self.p, tid, "→ backlog: let in by the owner (dragged on the Board)")
            for why in refused:
                util.log_error(self.p.state / "errors.jsonl", "board", why, trace="")
        self.files.save_snapshot({t["id"]: t["status"] for t in map(w.task, read.issues)})
        self.files.save_cards(self._cards(read))
        return read

    def load(self) -> Data:
        if self.seen:
            return self._view()
        if not self.shared:
            return self._fresh()[1]
        kept = self.kept
        if kept is None:
            with util.file_lock(self.lock):
                kept = self._board()
        return copy.deepcopy(kept[1])

    def reload(self) -> Data:
        if self.seen:
            return self._view()
        with util.file_lock(self.lock):
            return copy.deepcopy(self._board(fresh=True)[1])

    def find(self, tid: str) -> Task:
        if self.seen:
            return get(self._view(), tid)
        legacy = self._where().legacy
        if tid in legacy:
            return self._live(tid, legacy[tid], legacy)
        try:
            return get(self.old(), tid)
        except BacklogError:
            pass
        n = ids.number(self.prefix, tid, {})
        if n is None:
            raise BacklogError(f"no task {tid}")
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
        old = {t["id"]: t for t in self.old()["tasks"]}
        return joined(with_old_blockers(self._where().task(i), old), self.files.runs().get(tid, {}))

    def create(self) -> None:
        """Nothing to create: the repo and the Board exist (`foreman backlog setup` makes the Board fm's)."""

    def local(self, tid: str) -> Local | None:
        try:
            run = self.files.runs().get(tid)
        except BacklogError:
            return None
        return None if run is None else Local(run.get("worktree", ""), run.get("branch", ""))

    @contextlib.contextmanager
    def edit(self) -> Generator[Data, None, None]:
        """The backlog to change: a tick's from its kept read, else read now; the local files always read now. When the
        block ends, what changed is written and read back."""
        with util.file_lock(self.lock):
            read = self._board()[0] if self.shared else read_open(self.api, self.b)
            before = self._data(read)
            after = copy.deepcopy(before)
            yield after
            old = {t["id"]: t for t in self.old()["tasks"]}  # its closed blockers are no issues: a write leaves them
            new = apply(self._where(), read, *({**x, "tasks": [without_old_blockers(t, old) for t in x["tasks"]]} for x in (before, after)))
            if new != read:
                self.files.save_cards(self._cards(new))
            read = new
            if self.shared:
                self._keep(read)

    def add(self, title: str, *, status: str = "backlog", **f: Unpack[NewTask]) -> str:
        task = changes.filed(title, status, f)
        with util.file_lock(self.lock):
            read, d = self._board(fresh=True)
            if changes.known(d, task.get("source")):
                return ""
            w = self._where()
            back = file(w, read, task)
            tid = w.task(back)["id"]
            if (snap := self.files.snapshot()) is not None:
                self.files.save_snapshot({**snap, tid: task["status"]})
            read = dataclasses.replace(read, issues=(*read.issues, back))
            self.files.save_cards(self._cards(read))
            if self.shared:
                self._keep(read)
        event(self.p, tid, f"filed ({task['status']}): {title}")
        return tid
