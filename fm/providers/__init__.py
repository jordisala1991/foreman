"""Providers: where a delivered branch becomes part of main. One module per platform; the rest of fm only talks to this
interface and does not know which platform is behind it. Only `merge` changes main, and it never publishes (no push,
no tag, no release): that is the owner's, and releases are a separate concept.

Operations: `publish` (make the branch visible: open the change), `ready` (mark it ready for review), `comment`,
`body` (its description now), `describe` (set its body and title),
`merge(change, strategy)` (the one merge in fm; a future queue is just another strategy; raises `Conflict` when the
branch conflicts with main), `state` (is it merged?
closed?), `ci` (the platform's checks),
`has_ci` (attribute: is there CI that must be green? github yes, local no; autonomy merges only on a pass when so),
`pushed` (is the branch on the platform at its tip?), `push` (put it there:
never forced, never main; a failure is `Transient`, with the platform's own error).
Config: `[integrate] provider` (`auto` default: github when `origin` is on GitHub, else local; or `local` | `github`),
`merge` (`squash` default | `merge` | `rebase`; local has no rebase) and `pr` (`draft` default | `done`)."""

from typing import Any

from ..config import Project

MERGES = ("squash", "merge", "rebase")
PRS = ("draft", "done")


class ConfigError(Exception):
    pass


class ProviderError(Exception):
    """A provider call that failed: git said no, or what the platform answered is not the shape fm expects."""


class Transient(Exception):
    """A platform call that failed in a way worth retrying (network, rate limit, timeout): the loop retries, never blocks."""


class Conflict(Exception):
    """The platform refused the merge because the branch conflicts with main: the agent can fix it. `merge` raises it
    for that case."""


class Provider:
    name = ""
    merges = MERGES
    has_ci = False  # True: there is CI whose result must be green before autonomy merges

    def __init__(self, p: Project) -> None:
        self.p = p

    def pushed(self, change: dict[str, Any]) -> bool:
        """Is the branch on the platform at the tip of `change['head']`? (nothing to push locally)"""
        return True

    def push(self, change: dict[str, Any]) -> None:
        """Puts the branch on the platform (nothing to do locally). Never forced, never the main branch; any failure is
        a `Transient` carrying the platform's own error."""

    def publish(self, change: dict[str, Any], draft: bool) -> dict[str, Any]:
        """Makes the branch visible as a change (a pull request on a platform); returns its reference."""
        return {}

    def ready(self, change: dict[str, Any]) -> None:
        """Marks the change ready for review."""

    def comment(self, change: dict[str, Any], text: str) -> None:
        """Leaves a note on the change."""

    def body(self, change: dict[str, Any]) -> str:
        """The change's description as the platform has it now (nothing locally)."""
        return ""

    def describe(self, change: dict[str, Any], body: str) -> None:
        """Sets the change's body and its title."""

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
