"""What GitHub says as fm's own values: the provider's errors and `PullRequest`."""

import re
from dataclasses import dataclass
from typing import Literal

from .. import Conflict, Permanent, Transient

# Only these exact phrases will not fix themselves; anything else (a network error that says "closed") is transient.
PERMANENT = re.compile(r"pull request is closed|is not mergeable|merge conflict|cannot be merged", re.I)


CONFLICT = re.compile(r"merge conflict", re.I)


class TransientError(Transient):
    pass


class RateLimited(TransientError):
    """GitHub's rate limit is spent: no call until its reset (the stop file); the loop's retry finds it still spent."""


class PermanentError(Permanent):
    pass


class ConflictError(PermanentError, Conflict):
    pass


def failure(what: str, text: str) -> Exception:
    """What went wrong with a gh/git call: transient by default (retried, noted once); permanent only what is known
    not to fix itself: a closed PR, a conflict."""
    text = text.strip()[:300]
    if CONFLICT.search(text):
        return ConflictError(f"{what}: {text}")
    return (PermanentError if PERMANENT.search(text) else TransientError)(f"{what}: {text}")


State = Literal["OPEN", "MERGED", "CLOSED"]
STATES: dict[str, State] = {s: s for s in ("OPEN", "MERGED", "CLOSED")}


@dataclass(frozen=True)
class PullRequest:
    """A pull request as fm sees it: the only shape that leaves this module, never gh's JSON."""

    number: int
    url: str
    draft: bool
    state: State
    merge_commit: str  # "" until merged
