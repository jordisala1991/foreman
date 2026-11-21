"""The backlog port: every read and write of the backlog (tasks and the owner's questions). The adapter is
`backlogfile.BacklogFile` (the local file), handed out by `registry.backlog(p)`. Tasks cross it as dicts (`Task`)."""

from contextlib import AbstractContextManager
from typing import Any, Protocol, Unpack

from ..core.tasks.backlog import Data, Local, NewTask, Task
from ..core.tasks.questions import Question


class Backlog(Protocol):
    def load(self) -> Data:
        """The whole backlog, a copy: the caller may change it, nothing is saved."""
        ...

    def edit(self) -> AbstractContextManager[Data]:
        """The backlog to change under its lock; saved when the block ends, nothing saved if it raises."""
        ...

    def create(self) -> None:
        """Writes the empty backlog, unless one exists."""
        ...

    def local(self, tid: str) -> Local | None:
        """The task's local keys (worktree, branch), read fresh; None when there is no such task or it is unreadable."""
        ...

    def add(self, title: str, *, status: str = "backlog", **f: Unpack[NewTask]) -> str:
        """Files a task; its id ("" when an open task has the same source)."""
        ...

    def set_status(self, tid: str, status: str, why: str = "", **keys: Any) -> Task:
        """Moves a task to `status` (with `keys`), and logs the move."""
        ...

    def update(self, tid: str, **keys: Any) -> Task:
        """Sets `keys` on a task."""
        ...

    def record_misled(self, tid: str, role: str, text: str) -> None:
        """Keeps what misled an agent on its task, under its role, once; «none» is not kept."""
        ...

    def ask(self, tid: str, agent: str, text: str, **f: Unpack[Question]) -> str:
        """Files a question to the owner for the task's agent; its id."""
        ...

    def answer(self, qid: str, text: str, by: str = "owner (panel)") -> dict[str, Any]:
        """Answers an open question; the loop delivers the answer."""
        ...

    def note(self, tid: str, text: str, source: str) -> None:
        """Files a note from outside fm for the task's agent, delivered like an answer."""
        ...

    def mark_delivered(self, qid: str) -> None:
        """Marks a resolved question delivered to its agent."""
        ...
