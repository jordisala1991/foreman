"""Picks the project's provider and hands out its repo and backlog: the one place that knows the adapters (github, local, gitrepo,
backlogfile, and the runners claude and orca) and how to tell which provider applies."""

import re
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path

from ..core.project.config import Project
from ..core.rules import decide
from . import MERGES, PRS, ConfigError, Provider
from .backlog import Backlog
from .backlogfile import BacklogFile
from .claude.sessions import Claude
from .github import board
from .github.api import Api
from .github.backlog import GitHubBacklog
from .github.provider import GitHub
from .gitrepo import GitRepo
from .local import Local
from .orca.sessions import Orca
from .repo import Repo
from .runner import NO_ROWS, Rows, Runner


def repo(p: Project) -> Repo:
    """The project's repo, behind the `Repo` port."""
    return GitRepo(p.repo)


TICKS: dict[Path, Backlog] = {}  # the shared backlog of each home whose tick is running


def _make(p: Project, shared: bool) -> Backlog:
    """The local file, or with `[backlog]` the repo's issues on the Board (the old file still holds the closed tasks)."""
    if p.board is None:
        return BacklogFile(p, shared)
    return GitHubBacklog(p, p.board, BacklogFile(p).load, shared)


def backlog(p: Project) -> Backlog:
    """The project's backlog, behind the `Backlog` port: within a tick, the tick's shared one (loaded once)."""
    return TICKS.get(p.home) or _make(p, False)


def setup_board(p: Project) -> list[str]:
    """`foreman backlog setup`: makes the project's Board fm's; one line per change."""
    if p.board is None:
        raise ConfigError("no [backlog] table in foreman.toml")
    return board.setup(p.board, Api(p.state / "github-rate-limit.json"))


@contextmanager
def tick_scope(p: Project) -> Generator[None]:
    """While open, `backlog(p)` hands out one shared backlog for `p`'s home: the tick loads it once."""
    TICKS[p.home] = _make(p, True)
    try:
        yield
    finally:
        del TICKS[p.home]


def runner(p: Project) -> Runner:
    """The project's agent runner, behind the `Runner` port; a ConfigError naming an unknown `[agents] runner`."""
    name = p.get("agents", "runner")
    runners: dict[str, Callable[[Project], Runner]] = {"claude": Claude, "orca": Orca}
    if name not in runners:
        raise ConfigError(f"[agents] runner = {name!r} is not valid: use one of {', '.join(runners)}")
    return runners[name](p)


def rows(p: Project) -> Rows:
    """This tick's snapshot of the runner's sessions; no session is known when it is unreadable."""
    return runner(p).rows() or NO_ROWS


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
