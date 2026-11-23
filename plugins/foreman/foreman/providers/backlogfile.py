"""The backlog port's local adapter: one `backlog.json` in the project's home, the one module that names it. Every write
goes through `edit()`, under the file lock. Shared (a tick, `registry.tick_scope`): read once, then each `load()` is a
copy of what was read or last saved."""

import contextlib
import copy
import threading
from collections.abc import Generator
from datetime import date
from typing import Any, Unpack

from .. import util
from ..core.project.config import Project
from ..core.tasks import changes
from ..core.tasks.backlog import STATUS_KINDS, BacklogError, Data, Local, NewTask, Task, empty, event, get, new_id
from ..core.tasks.questions import Question


class BacklogFile:
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

    def record_misled(self, tid: str, role: str, text: str) -> None:
        if not changes.misled_text(text):
            return
        with self.edit() as d:
            changes.misled(d, tid, role, text)

    def add(self, title: str, *, status: str = "backlog", **f: Unpack[NewTask]) -> str:
        task = changes.filed(title, status, f)
        with self.edit() as d:
            if changes.known(d, task.get("source")):
                return ""
            tid = new_id(self.p, d)
            d["tasks"].append({"id": tid, **task, "created": util.now()})
        event(self.p, tid, f"filed ({task['status']}): {title}")
        return tid

    def set_status(self, tid: str, status: str, why: str = "", **keys: Any) -> Task:
        with self.edit() as d:
            moved = changes.moved(d, tid, status, keys)
            i = get(d, tid)
        if moved:
            event(self.p, tid, f"→ {status}" + (f": {why}" if why else ""), STATUS_KINDS.get(status, "other"))
        return i

    def update(self, tid: str, **keys: Any) -> Task:
        with self.edit() as d:
            i = get(d, tid)
            i.update(keys)
        return i

    def ask(self, tid: str, agent: str, text: str, **f: Unpack[Question]) -> str:
        with self.edit() as d:
            qid = changes.asked(d, tid, agent, text, f)
        event(self.p, tid, f"asks the owner ({f.get('kind', 'question')}): {text}")
        return qid

    def answer(self, qid: str, text: str, by: str = "owner (panel)") -> dict[str, Any]:
        with self.edit() as d:
            q = changes.answered(d, qid, text, by)
        event(self.p, q["task"], f"owner answered {qid}: {text}")
        return q

    def note(self, tid: str, text: str, source: str) -> None:
        with self.edit() as d:
            changes.noted(d, tid, text, source)
        event(self.p, tid, f"note from {source}: {text}")

    def mark_delivered(self, qid: str) -> None:
        with self.edit() as d:
            closed = changes.delivered(d, qid)
        for s in closed:
            event(self.p, s["task"], f"stopped need {s['id']} closed: parent woken")
