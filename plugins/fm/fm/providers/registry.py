"""Picks the project's provider: the one place that knows the adapters (github, local) and how to tell which applies."""

import re

from .. import proc
from ..core.project.config import Project
from ..core.rules import decide
from . import MERGES, PRS, ConfigError, Provider
from .github.provider import GitHub
from .local import Local


def detect(p: Project) -> str:
    """github when the repo's origin is on GitHub, else local."""
    r = proc.run(["git", "-C", str(p.repo), "remote", "get-url", "origin"])
    return "github" if r.code == 0 and re.search(r"github\.com[:/]", r.out) else "local"


def get(p: Project) -> Provider:
    """The project's provider; a clear ConfigError on an unknown provider, merge or pr value."""
    name = p.get("merge", "provider")
    providers: dict[str, type[Provider]] = {"local": Local, "github": GitHub}
    if name == "auto":
        name = detect(p)
    merges = {n: c.merges for n, c in providers.items()}
    m = decide.MergeConfig(name, p.get("merge", "strategy"), p.get("merge", "pr"))
    if err := decide.provider_error(m, merges, MERGES, PRS):
        raise ConfigError(err)
    return providers[name](p)
