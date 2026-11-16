"""A task's reviews: the shape of a reviewer and of a verdict, and the pure rules on them (round, tip)."""

from collections.abc import Mapping
from typing import Any, NotRequired, TypedDict


class Reviewer(TypedDict, total=False):
    """A task's `reviewer`: the newest review launched (`reviewers` keeps every launch, same shape)."""

    round: int
    tip: str


class Review(TypedDict):
    """A task's `review`: a reviewer's verdict."""

    round: int
    tip: str
    open: int
    returned: NotRequired[bool]
    holds: NotRequired[str]  # a later tip this verdict was carried to (a clean merge of main, or no review after it)


def verdict_tip(v: Mapping[str, Any]) -> str:
    """The tip a verdict stands for: the one it was carried to, or the one reviewed."""
    return v.get("holds") or v.get("tip", "")


def review_round(i: Mapping[str, Any]) -> int:
    """The round the next review is: reviews since the owner last asked for a fresh one (`review_from`), plus one."""
    return len(i.get("reviews") or []) - int(i.get("review_from", 0)) + 1
