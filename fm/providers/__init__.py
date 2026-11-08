"""Providers: where a delivered branch becomes part of main. One module per platform; the rest of fm only talks to this
interface and does not know which platform is behind it. Only `merge` changes main, and it never publishes (no push,
no tag, no release): that is the owner's, and releases are a separate concept.

Operations: `publish` (make the branch visible: open the change), `ready` (mark it ready for review), `comment`,
`merge(change, strategy)` (the one merge in fm; a future queue is just another strategy), `state` (is it merged?
closed?), `ci` (the platform's checks), `pushed` (is the branch on the platform at its tip?).
Config: `[integrate] provider` (`auto` default: github when `origin` is on GitHub, else local; or `local` | `github`),
`merge` (`squash` default | `merge` | `rebase`; local has no rebase) and `pr` (`draft` default | `done`)."""

from typing import Any

from ..config import Project

MERGES = ("squash", "merge", "rebase")
PRS = ("draft", "done")


class ConfigError(Exception):
    pass


class Transient(Exception):
    """A platform call that failed in a way worth retrying (network, rate limit, timeout): the loop retries, never blocks."""


class Permanent(Exception):
    """A platform call that will fail the same way if repeated (the platform rejects it, the branch is not there): the
    task is blocked with the reason instead of retried."""


class Provider:
    name = ""
    merges = MERGES

    def __init__(self, p: Project) -> None:
        self.p = p

    def pushed(self, change: dict[str, Any]) -> bool:
        """Is the branch on the platform at the tip of `change['head']`? (nothing to push locally)"""
        return True

    def publish(self, change: dict[str, Any], draft: bool) -> dict[str, Any]:
        """Makes the branch visible as a change (a pull request on a platform); returns its reference."""
        return {}

    def ready(self, change: dict[str, Any]) -> None:
        """Marks the change ready for review."""

    def comment(self, change: dict[str, Any], text: str) -> None:
        """Leaves a note on the change."""

    def merge(self, change: dict[str, Any], strategy: str) -> str:
        """Merges the change into main with `strategy`; returns a reference to the merge. Never publishes."""
        raise NotImplementedError

    def ci(self, change: dict[str, Any]) -> str:
        """pass | fail | pending | none (no checks, or no platform)."""
        return ""

    def state(self, change: dict[str, Any]) -> dict[str, Any]:
        """{'merged': bool, 'closed': bool, 'commit'?: str}, as seen on the platform now."""
        raise NotImplementedError


def merge_strategy(p: Project) -> str:
    v = p.get("integrate", "merge")
    if v not in MERGES:
        raise ConfigError(f"[integrate] merge = {v!r} is not valid: use one of {', '.join(MERGES)}")
    return str(v)


def pr_mode(p: Project) -> str:
    v = p.get("integrate", "pr")
    if v not in PRS:
        raise ConfigError(f"[integrate] pr = {v!r} is not valid: use one of {', '.join(PRS)}")
    return str(v)
