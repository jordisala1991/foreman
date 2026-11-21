"""A card the owner dragged on the Board (DESIGN.md «The statuses»), pure: the pass compares each open card's Status with the
snapshot no command changed meanwhile. A drag Triage → Backlog is a Go, with `foreman go`'s checks; any other drag is refused
and the card goes back."""

from collections.abc import Mapping
from dataclasses import dataclass

from . import taskrepo
from .backlog import Acceptance, Task


@dataclass(frozen=True)
class Drag:
    id: str
    was: str  # the snapshot's status
    now: str  # the Board's


def found(was: Mapping[str, str], now: Mapping[str, str]) -> list[Drag]:
    """The tasks in both whose status differs."""
    return [Drag(tid, was[tid], s) for tid, s in now.items() if tid in was and was[tid] != s]


def go_checks(i: Task, tasks: list[Task], keys: list[str]) -> str:
    """Why Go cannot let the task in (no criteria, or no repo among several); else empty."""
    acceptance: Acceptance = i.get("acceptance") or {}
    if not acceptance.get("criteria"):
        return f"{i['id']}: define its acceptance criteria with the owner first"
    return taskrepo.go_refusal(i, tasks, keys)


def verdict(d: Drag, i: Task, tasks: list[Task], keys: list[str]) -> str:
    """Empty for a Go; else the error line, the card going back to the snapshot's status."""
    if d.was == "triage" and d.now == "backlog":
        if not (why := go_checks(i, tasks, keys)):
            return ""
        return f"{d.id}: dragged triage → backlog on the Board but {why.removeprefix(d.id + ': ')}; moved back to triage"
    return f"{d.id}: dragged {d.was} → {d.now} on the Board; moved back to {d.was}"
