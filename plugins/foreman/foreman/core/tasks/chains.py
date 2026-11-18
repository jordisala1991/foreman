"""Chains (pure): a chain parent is filed by a lens entry and stores `chain`; its steps are its subtasks (DESIGN.md
`## Improvement chains`)."""

from . import backlog, subtasks


def is_parent(tasks: list[backlog.Task], i: backlog.Task) -> bool:
    """A parent: it has subtasks, or it is a chain parent, which is one before its first step."""
    return bool(i.get("chain")) or bool(subtasks.of(tasks, i["id"]))


def outcome(i: backlog.Task, subs: list[backlog.Task]) -> str:
    """How the parent closes on its agent's `foreman done`: a chain with no step is done; else as its subtasks say."""
    return "done" if i.get("chain") and not subs else subtasks.outcome(subs)
