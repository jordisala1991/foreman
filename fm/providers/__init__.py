"""Providers: where a delivered branch becomes part of main. One module per platform; the rest of fm only talks to this
interface and does not know which platform is behind it. Only `merge` changes main, and it never publishes (no push,
no tag, no release): that is the owner's, and releases are a separate concept.

Operations: `publish` (make the branch visible: open the change), `ready` (mark it ready for review), `comment`,
`merge(change, strategy)` (the one merge in fm; a future queue is just another strategy), `state` (is it merged?
closed?), `ci` (the platform's checks), `pushed` (is the branch on the platform at its tip?).
Config: `[integrate] provider` (`auto` default: github when `origin` is on GitHub, else local; or `local` | `github`),
`merge` (`squash` default | `merge` | `rebase`; local has no rebase) and `pr` (`draft` default | `done`)."""

import re
import subprocess
from typing import Any

from ..config import Project

MERGES = ("squash", "merge", "rebase")
PRS = ("draft", "done")


class ConfigError(Exception):
    pass


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
        """{'merged': bool, 'closed': bool}, as seen on the platform now."""
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


def detect(p: Project) -> str:
    """github when the repo's origin is on GitHub, else local."""
    r = subprocess.run(["git", "-C", str(p.repo), "remote", "get-url", "origin"], capture_output=True, text=True, check=False)
    return "github" if r.returncode == 0 and re.search(r"github\.com[:/]", r.stdout) else "local"


def get(p: Project) -> Provider:
    """The project's provider; a clear ConfigError on an unknown provider, merge or pr value."""
    from .github import GitHub
    from .local import Local

    if "forge" in p.cfg["integrate"]:
        raise ConfigError('[integrate] forge was renamed to provider: use `provider = "local"`')
    name = p.get("integrate", "provider")
    providers: dict[str, type[Provider]] = {"local": Local, "github": GitHub}
    if name == "auto":
        name = detect(p)
    if name not in providers:
        raise ConfigError(f"[integrate] provider = {name!r} is not valid: use one of auto, {', '.join(providers)}")
    pr_mode(p)
    prov = providers[name](p)
    if merge_strategy(p) not in prov.merges:
        raise ConfigError(f"[integrate] merge = {merge_strategy(p)!r} is not valid for {name}: use one of {', '.join(prov.merges)}")
    return prov
