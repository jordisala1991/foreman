"""Providers: where a delivered branch becomes part of main. One module per platform; the rest of fm only talks to this
interface and does not know which platform is behind it. Only `merge` changes main, and it never publishes (no push,
no tag, no release): that is the owner's, and releases are a separate concept.

Operations: `publish` (make the branch visible: open the change), `ready` (mark it ready for review), `comment`,
`merge(change, strategy)` (the one merge in fm; a future queue is just another strategy), `state` (is it merged?
closed?). Config: `[integrate] provider` (only `local` for now) and `merge` (`squash` default | `merge`)."""

from __future__ import annotations

from typing import Any

from ..config import Project

MERGES = ("squash", "merge")


class ConfigError(Exception):
    pass


class Provider:
    name = ""

    def __init__(self, p: Project) -> None:
        self.p = p

    def publish(self, change: dict[str, Any]) -> None:
        """Makes the branch visible as a change (a pull request on a platform)."""

    def ready(self, change: dict[str, Any]) -> None:
        """Marks the change ready for review."""

    def comment(self, change: dict[str, Any], text: str) -> None:
        """Leaves a note on the change."""

    def merge(self, change: dict[str, Any], strategy: str) -> str:
        """Merges the change into main with `strategy`; returns a reference to the merge. Never publishes."""
        raise NotImplementedError

    def state(self, change: dict[str, Any]) -> dict[str, Any]:
        """{'merged': bool, 'closed': bool}, as seen on the platform now."""
        raise NotImplementedError


def merge_strategy(p: Project) -> str:
    v = p.get("integrate", "merge")
    if v not in MERGES:
        raise ConfigError(f"[integrate] merge = {v!r} is not valid: use one of {', '.join(MERGES)}")
    return str(v)


def get(p: Project) -> Provider:
    """The project's provider; a clear ConfigError on an unknown provider or merge value."""
    from .local import Local

    if "forge" in p.cfg["integrate"]:
        raise ConfigError('[integrate] forge was renamed to provider: use `provider = "local"`')
    name = p.get("integrate", "provider")
    providers = {"local": Local}
    if name not in providers:
        raise ConfigError(f"[integrate] provider = {name!r} is not valid: use one of {', '.join(providers)}")
    merge_strategy(p)
    return providers[name](p)
