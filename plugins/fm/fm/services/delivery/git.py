from dataclasses import asdict
from typing import Any

from ...core.project.config import Project
from ...providers import registry


class DeliveryError(Exception):
    pass


class MergeConflict(DeliveryError):
    """The accepted branch conflicts with main: it goes back to its agent."""


def diff_stat(p: Project, branch: str) -> list[dict[str, Any]]:
    return [asdict(f) for f in registry.repo(p).diff_stat(p.get("project", "main"), branch)]


def deleted_files(p: Project, branch: str) -> list[str]:
    return registry.repo(p).deleted_files(p.get("project", "main"), branch)


def conflicts(p: Project, branch: str) -> bool:
    return registry.repo(p).conflicts(p.get("project", "main"), branch)


def merge_of(p: Project, tip: str, head: str) -> tuple[str, str]:
    """How `head` follows the reviewed `tip`: ("clean", "") when it is a merge of `tip` and a main commit whose tree is
    git's automatic merge of the two; ("resolved", that automatic tree) when the agent resolved it otherwise; ("", "")
    when it is no such merge (plain commits, a rebase, a merge of another branch)."""
    return registry.repo(p).follows(tip, head, p.get("project", "main"))
