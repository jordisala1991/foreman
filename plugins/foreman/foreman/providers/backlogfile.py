"""The backlog port's local adapter: one `backlog.json` in the project's home, the one module that names it. Every write
goes through `edit()`, under the file lock. Shared (a tick, `registry.tick_scope`): read once, then each `load()` is a
copy of what was read or last saved."""

import contextlib
import copy
import threading
from collections.abc import Generator
from datetime import date
from typing import Unpack

from .. import util
from ..core.project.config import Project
from ..core.tasks import changes
from ..core.tasks.backlog import BacklogError, Data, Local, NewTask, Task, empty, event, get, new_id
from .backlogbase import BacklogBase


class BacklogFile(BacklogBase):
    def __init__(self, p: Project, shared: bool = False) -> None:
        self.p = p
        self.file = p.home / "backlog.json"
        self.shared = shared
        self.kept: Data | None = None
        self.guard = threading.Lock()  # the background check thread calls the shared instance too

    def _read(self) -> Data:
        d: Data = util.read_json(self.file, None) or empty()
        if d.get("schema_version") != 5:
            raise BacklogError(f"{self.file}: schema {d.get('schema_version')!r}, expected 5")
        for k, v in empty().items():
            d.setdefault(k, v)
        return d

    def load(self) -> Data:
        if not self.shared:
            return self._read()
        with self.guard:
            if self.kept is None:
                self.kept = self._read()
            return copy.deepcopy(self.kept)

    def reload(self) -> Data:
        with util.file_lock(self.file):  # as edit(): no write lands between the read and the kept copy
            d = self._read()
            if self.shared:
                with self.guard:
                    self.kept = copy.deepcopy(d)
        return d

    def find(self, tid: str) -> Task:
        return get(self.load(), tid)

    @contextlib.contextmanager
    def edit(self) -> Generator[Data, None, None]:
        with util.file_lock(self.file):
            d = self._read()
            yield d
            d["updated"] = date.today().isoformat()
            util.write_json(self.file, d)
            if self.shared:  # under the file lock: the kept data follows the order of the writes
                with self.guard:
                    self.kept = copy.deepcopy(d)

    def create(self) -> None:
        if not self.file.exists():
            util.write_json(self.file, empty())

    def local(self, tid: str) -> Local | None:
        try:
            i = get(self._read(), tid)
        except BacklogError:
            return None
        return Local(i.get("worktree", ""), i.get("branch", ""))

    def add(self, title: str, *, status: str = "backlog", **f: Unpack[NewTask]) -> str:
        task = changes.filed(title, status, f)
        with self.edit() as d:
            if changes.known(d, task.get("source")):
                return ""
            tid = new_id(self.p, d)
            d["tasks"].append({"id": tid, **task, "created": util.now()})
        event(self.p, tid, f"filed ({task['status']}): {title}")
        return tid
