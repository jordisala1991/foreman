"""The local files of a project whose backlog is on GitHub (DESIGN.md «Every key of a task», «The statuses»), in
`<home>/state/`: `runs.json` (each task's local keys), `questions.json` (the owner's questions and the agents' messages),
`closed.json` (the tasks fm closed, as read back) and `backlog-snapshot.json` (each open task's status after the last pass or
write). Read and written under the backlog lock by `GitHubBacklog`."""

from pathlib import Path
from typing import Any

from ... import util
from ...core.tasks.backlog import CLOSED, BacklogError, Task


class Files:
    def __init__(self, state: Path) -> None:
        self.state = state

    def _object(self, name: str, default: object) -> dict[str, Any] | None:
        f = self.state / name
        raw: object = util.read_json(f, default)
        if raw is None:
            return None
        o = util.json_object(raw)
        if o is None:
            raise BacklogError(f"{f}: expected an object, found {raw!r}"[:300])
        return o

    def runs(self) -> dict[str, Task]:
        """Task id → its local keys."""
        return self._object("runs.json", {}) or {}

    def save_runs(self, runs: dict[str, Task]) -> None:
        util.write_json(self.state / "runs.json", runs)

    def questions(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """(the open questions, the resolved ones and the messages for agents)."""
        o = self._object("questions.json", {}) or {}
        return list(o.get("decisions", [])), list(o.get("decisions_resolved", []))

    def save_questions(self, open_: list[dict[str, Any]], resolved: list[dict[str, Any]]) -> None:
        util.write_json(self.state / "questions.json", {"decisions": open_, "decisions_resolved": resolved})

    def closed(self) -> list[Task]:
        """The tasks fm closed on GitHub, as it kept them: a list of tasks, each with its id and status."""
        f = self.state / "closed.json"
        raw: object = util.read_json(f, [])
        rows = util.json_list(raw)
        tasks = [o for t in rows if (o := util.json_object(t)) is not None]
        if rows is not raw or len(tasks) != len(rows) or not all(isinstance(t.get("id"), str) and t.get("status") in CLOSED for t in tasks):
            raise BacklogError(f"{f}: expected a list of closed tasks, each with its id and status")
        return tasks

    def keep(self, task: Task) -> None:
        """Keeps a task fm closed, in place of one with its id."""
        util.write_json(self.state / "closed.json", [*(t for t in self.closed() if t["id"] != task["id"]), task])

    def snapshot(self) -> dict[str, str] | None:
        """Open task id → its status; None before the first pass."""
        o = self._object("backlog-snapshot.json", None)
        if o is None:
            return None
        return {k: str(v) for k, v in (util.json_object(o.get("statuses")) or {}).items()}

    def save_snapshot(self, statuses: dict[str, str]) -> None:
        util.write_json(self.state / "backlog-snapshot.json", {"statuses": statuses})
