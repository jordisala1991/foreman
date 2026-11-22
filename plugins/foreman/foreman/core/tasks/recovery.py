"""Recovery (pure): why an active run ended without an outcome, and what follows, a retry or the owner. The step that
reads the runner and acts is `steps/recover.py`."""

from typing import NamedTuple

from .runs import Run

RETRYABLE = ("session_ended", "runner_unavailable", "silent_too_long")
MAX_ATTEMPTS = 2  # failed runs in a row of a kind; the next failure goes to the owner
QUIET_S = 600  # an agent idle and its transcript quiet this long has stopped without finishing
LAUNCH_GRACE_S = 120  # a launched session the runner has not listed after this long never started
IDLE_KINDS = ("parent", "chain")  # their run is one turn: done once the session idles
SETTLE_S = 60  # the listing may still show a session idle this long after a message was sent to it


class Facts(NamedTuple):
    """What the loop saw of a run's session: `known` (it has a session id), `ended` (the listing shows it ended or gone),
    `restarted` (the runner's epoch changed since the last pass), `idle` (alive and not busy), `quiet_s` (its transcript
    and its last start are this old), `age_s` (since the run started)."""

    known: bool
    ended: bool
    restarted: bool
    idle: bool
    quiet_s: float
    age_s: float


def reason(kind: str, f: Facts) -> str:
    """Why the run of `kind` ended without an outcome (a RETRYABLE one, or `launch_failed`), or "" while it may still finish."""
    if not f.known:
        return "launch_failed" if f.age_s > LAUNCH_GRACE_S else ""
    if f.ended:
        return "runner_unavailable" if f.restarted else "session_ended"
    if f.idle and f.quiet_s >= QUIET_S and kind not in IDLE_KINDS:
        return "silent_too_long"
    return ""


def turn_over(f: Facts) -> bool:
    """A parent's or chain's turn is over: its session is alive, idle, and quiet for SETTLE_S."""
    return f.known and not f.ended and f.idle and f.quiet_s >= SETTLE_S


def streak(of_kind: list[Run]) -> int:
    """The failed runs at the end of a task's runs of one kind: a done or canceled run ends the count."""
    n = 0
    for r in reversed(of_kind):
        if r.outcome != "failed":
            break
        n += 1
    return n


def next_step(why: str, streak: int) -> str:
    """`retry` a retryable reason before MAX_ATTEMPTS failures in a row; else `owner`."""
    return "retry" if why in RETRYABLE and streak < MAX_ATTEMPTS else "owner"
