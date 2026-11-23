"""The `Backlog` writes both adapters share: each changes the data inside the adapter's own `edit()`, then writes the
task's events. `BacklogFile` and `GitHubBacklog` inherit them."""

from abc import ABC, abstractmethod
from contextlib import AbstractContextManager
from typing import Any, Unpack

from ..core.project.config import Project
from ..core.tasks import changes
from ..core.tasks.backlog import STATUS_KINDS, Data, Task, event, get
from ..core.tasks.questions import Question


class BacklogBase(ABC):
    p: Project

    @abstractmethod
    def edit(self) -> AbstractContextManager[Data]: ...

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

    def record_misled(self, tid: str, role: str, text: str) -> None:
        if not changes.misled_text(text):
            return
        with self.edit() as d:
            changes.misled(d, tid, role, text)

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
