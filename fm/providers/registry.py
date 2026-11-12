"""Picks the project's provider: the one place that knows the adapters (github, local) and how to tell which applies."""

import re

from .. import proc
from ..config import Project
from . import ConfigError, Provider, merge_strategy, pr_mode
from .github import GitHub
from .local import Local


def detect(p: Project) -> str:
    """github when the repo's origin is on GitHub, else local."""
    r = proc.run(["git", "-C", str(p.repo), "remote", "get-url", "origin"])
    return "github" if r.code == 0 and re.search(r"github\.com[:/]", r.out) else "local"


def get(p: Project) -> Provider:
    """The project's provider; a clear ConfigError on an unknown provider, merge or pr value."""
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
