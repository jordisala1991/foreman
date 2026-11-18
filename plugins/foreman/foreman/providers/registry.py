"""Picks the project's provider and hands out its repo: the one place that knows the adapters (github, local, gitrepo) and
how to tell which provider applies."""

import re

from ..core.project.config import Project
from ..core.rules import decide
from . import MERGES, PRS, ConfigError, Provider
from .github.provider import GitHub
from .gitrepo import GitRepo
from .local import Local
from .repo import Repo


def repo(p: Project) -> Repo:
    """The project's repo, behind the `Repo` port."""
    return GitRepo(p.repo)


def detect(p: Project) -> str:
    """github when the repo's remote is on GitHub, else local (always local with remote = "none")."""
    if p.remote == "none":
        return "local"
    return "github" if re.search(r"github\.com[:/]", repo(p).remote_url(p.remote)) else "local"


def get(p: Project) -> Provider:
    """The project's provider; a clear ConfigError on an unknown provider, merge or pr value."""
    name = p.get("merge", "provider")
    providers: dict[str, type[Provider]] = {"local": Local, "github": GitHub}
    if name == "auto":
        name = detect(p)
    if name == "github" and p.remote == "none":
        raise ConfigError('remote = "none" cannot use the github provider')
    merges = {n: c.merges for n, c in providers.items()}
    m = decide.MergeConfig(name, p.get("merge", "strategy"), p.get("merge", "pr"))
    if err := decide.provider_error(m, merges, MERGES, PRS):
        raise ConfigError(err)
    return providers[name](p, repo(p))
