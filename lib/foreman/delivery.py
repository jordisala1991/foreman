"""The delivery check: a production agent cannot end its turn as delivered when its delivery is obviously
incomplete, and every delivery states its outcome.

- **Outcome.** `foreman progress --done "line" --outcome ok|partial|failed` (default ok) records `outcome` in the
  progress line; the agents hook reads it from the command line (`outcome_in`) and records it in the agent's entry
  of agents.json with `delivered`. The loop's `ready_to_integrate`, the panel and `foreman integrate` show it
  (`outcome_of`).
- **Kind.** `--kind measure` with `--done` says the delivery only measured (a report, no code for main): recorded
  in the progress line and, by the agents hook (`kind_in`), as `kind` in the entry; read back by `delivered_kind`
  and `kind_of`. A sub-issue planned as a measurement carries `kind = "measure"` in the backlog. `branch_kind` is
  the one rule of what a branch brings: `closed` (every sub-issue naming it closed, none with a commit), `measure`
  (its open sub-issues are all measurements, by their `kind` or by their agent's delivery) or `code`.
- **The named report.** `--report PATH` with `--done` names the agent's report: the CLI checks it before writing
  (`named_report`: under the prefixes, a file in the home or in the home's folder inside the agent's worktree) and
  records it in the progress line; the agents hook records it in the entry (`report_in`, `report`). The pass and
  `foreman integrate` read it back (`report_of`), the delivery check with `delivered_report`.
- **When it runs.** At the SubagentStop of an agent whose entry was tied to a launch record (`launch`), is
  production and has a branch or a worktree (`applies`), unless the event lists background tasks of the agent (it
  stops to wait for one of its own: not a delivery). Planners, other agents and agents with no launch record are
  never checked.
- **What it checks**, cheaply and only from local files and git (`missing`): a delivery was recorded (`delivered`
  in the entry, or a `done` line of the agent in progress.jsonl since its start or its last block); the branch has
  commits beyond the repo's main; a report exists when the project requires them (`[ids] report_prefixes` not
  empty), by `branch_report`, the rule the loop's `ready_to_integrate` uses too: the `report` of the branch's
  sub-issue when it exists; the report the agent named at delivery, which must then exist (no guess replaces it);
  with the home inside the repo, a file the branch adds or changes under one of the
  prefixes (as `foreman integrate` finds a report the branch adds); with the home elsewhere, a file under the
  prefixes written since the agent started that names its branch, or whose name has its name or issue. The report
  found must carry every fixed line of REPORT_LINES (`report_lines`: `Misled: …`, or `Misled: none`), read from the
  branch when the home lives in the repo (`report_text`). An agent
  that delivered with `--outcome failed` needs neither commits nor a report: a failure must be deliverable. A
  measurement needs no commits, only its report (`measures`: the delivery said `--kind measure`, the agent's
  sub-issue is `kind = "measure"`, or `branch_kind` says `measure`). A step
  that errors (git fails, a folder is gone) counts as found: the check never blocks on its own failure.
- **Blocking.** The hook answers SubagentStop (and a session agent's `Stop`) with
  `{"decision": "block", "reason": …}` (`block_reason`): the agent
  goes back to work with the reason. `end` and `delivered` go from its entry (it is working again and must run
  `progress --done` again as its last command), `delivery_blocks` counts the blocks. At most MAX_BLOCKS per agent
  (an entry is one launch): after that it is let go, with `delivery_incomplete` ({at, missing}) in its entry, which
  the loop shows as the actionable notice `delivery_incomplete` until the agent is resumed or delivers complete.
  Claude Code sends no SubagentStop when a subagent is cut by maxTurns: that case is not covered.
- **Gathering the lines.** `foreman agents --misled` (`misled`, `misled_text`): the `Misled:` lines of the reports of
  a period, each tied to the delivery that named it (`progress --done` records the agent's `type` and `model`,
  `type_and_model`), with a footer counting the reports with something, `none` and without the line.
"""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

from . import util

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from .backlog import Data
    from .config import Project, Table
    from .identity import Agent
    from .util import PathLike

    Agents = Mapping[str, Agent]  # agents.json, read once by a caller that looks up several deliveries

Result = dict[str, Any]  # check(): items

OUTCOMES = ("ok", "partial", "failed")
MAX_BLOCKS = 2
GIT_TIMEOUT = 10
REPORT_DAYS = 3  # how far back the pass looks in the home when no agent of the branch is recorded

# The fixed lines of a report (`report_lines`): a production delivery that is not `failed` is refused while its
# report lacks one of them (`missing`). LINE_HOW says, in the refusal, how to write each.
REPORT_LINES = ("Misled",)
LINE_HOW = {
    "Misled": "what in the brief, the plan, the issue, the rules, the docs or a message of foreman was wrong, stale "
    "or unclear and cost you time, one line each as `Misled: <where>: <what> (<cost>)`, <where> one of "
    "brief, plan, issue, rules, procedure, docs, foreman, code, environment; `Misled: none` if nothing did",
}
WHERE = ("brief", "plan", "issue", "rules", "procedure", "docs", "foreman", "code", "environment")
NOTHING = ("none", "nothing", "nada", "-")


class Unknown(Exception):
    """A step of the check could not tell (git failed, a folder is gone): it counts as found."""


def _flag(words: Iterable[str], name: str) -> str | None:
    """The value of the first `--NAME X` (or `--NAME=X`) of a `foreman progress` call's words: "" when it has none,
    None when the flag is absent."""
    words = list(words)
    for i, w in enumerate(words):
        if w == f"--{name}":
            return words[i + 1] if i + 1 < len(words) else ""
        if w.startswith(f"--{name}="):
            return w.split("=", 1)[1]
    return None


def outcome_in(words: Iterable[str]) -> str | None:
    """The `--outcome X` of a `foreman progress` call's words; "ok" when absent, None when the value is not one of
    OUTCOMES."""
    v = _flag(words, "outcome")
    return "ok" if v is None else v if v in OUTCOMES else None


def kind_in(words: Iterable[str]) -> str | None:
    """The `--kind measure` of a `foreman progress` call's words: "measure", or None when absent or any other value
    (code is the default and is not recorded)."""
    return "measure" if _flag(words, "kind") == "measure" else None


def report_in(words: Iterable[str]) -> str | None:
    """The `--report PATH` of a `foreman progress` call's words, or None when absent or empty."""
    return _flag(words, "report") or None


def applies(project: Project, h: Agent) -> bool:
    """Whether the delivery check runs for this agents.json entry: tied to a launch record, production, with a branch
    or a worktree."""
    return bool(h.get("launch")) and h.get("production") is True and bool(h.get("branch") or h.get("worktree"))


def git(cwd: PathLike, *args: str) -> str:
    try:
        r = util.git_run(cwd, *args, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Unknown(str(e)) from e
    if r.returncode:
        raise Unknown(r.stderr.strip())
    return r.stdout


def repo_of(project: Project, h: Agent) -> Table:
    """The agent's repo table ({} when the launch named none or it is not configured)."""
    try:
        return project.repo(h["repo"]) if h.get("repo") else {}
    except Exception:  # noqa: BLE001 (an unconfigured or broken repo table reads as none)
        return {}


def where(project: Project, h: Agent) -> tuple[Path, str, str, Path]:
    """(worktree folder, branch, main branch name, main checkout path) of the agent; the worktree is the recorded one,
    else the repo's main checkout."""
    repo = repo_of(project, h)
    wt = h.get("worktree") or repo.get("path")
    if not wt or not Path(wt).is_dir():
        raise Unknown(f"no worktree {wt}")
    branch = h.get("branch") or git(wt, "rev-parse", "--abbrev-ref", "HEAD").strip()
    main = repo.get("main") or "main"
    main_path = repo.get("path")
    if not main_path:  # the main checkout holds the common git folder
        main_path = (Path(wt) / git(wt, "rev-parse", "--git-common-dir").strip()).resolve().parent
    return Path(wt), branch, main, Path(main_path)


def progress_done(project: Project, aid: str | None, name: str | None, since: str | None) -> dict[str, Any] | None:
    """The last `done` line of this agent in progress.jsonl (by agent_id, else by name) at or after `since` (an ISO
    time, compared by the minute), or None."""
    since = (since or "").replace("T", " ")[:16]
    for x in reversed(util.read_jsonl(project.state / "progress.jsonl", tail=300)):
        if not x.get("done") or str(x.get("time", "")) < since:
            continue
        if (aid and x.get("agent_id") == aid) or (name and x.get("agent") == name):
            return x
    return None


def _done_record(project: Project, aid: str | None, h: Agent) -> dict[str, Any] | None:
    """The record of an entry's delivery: the entry once `delivered`, else the agent's `done` line in progress.jsonl
    since its start or its last block; None when it has not delivered."""
    if h.get("delivered"):
        return h
    return progress_done(project, aid, h.get("name"), h.get("blocked_at") or h.get("start"))


def delivery(project: Project, aid: str | None, h: Agent) -> tuple[bool, str | None]:
    """(delivered?, outcome) of an entry (`_done_record`)."""
    x = _done_record(project, aid, h)
    return (True, x.get("outcome") or "ok") if x else (False, None)


def delivered_kind(project: Project, aid: str | None, h: Agent) -> str | None:
    """The `--kind` of an entry's delivery ("measure" or None, `_done_record`)."""
    return (_done_record(project, aid, h) or {}).get("kind") or None


def last_delivery(
    project: Project, branch: str | None = None, name: str | None = None, agents: Agents | None = None
) -> dict[str, Any] | None:
    """The last delivery of the agent with this branch or name: its newest `delivered` entry in agents.json (AGENTS
    when given, read once by the caller), reviewers left out (launch --review: their delivery is not the branch's);
    else, by name, its last `done` line in progress.jsonl; None when none is known."""
    if agents is None:
        from . import agents as agents_mod

        agents = agents_mod.agents(project)
    best = None
    for h in agents.values():
        mine = h.get("delivered") and ((branch and h.get("branch") == branch) or (name and h.get("name") == name))
        if mine and not h.get("review") and (best is None or h["delivered"] > best["delivered"]):
            best = h
    return best or (progress_done(project, None, name, "") if name else None)


def kind_of(
    project: Project, branch: str | None = None, name: str | None = None, agents: Agents | None = None
) -> str | None:
    """The `--kind` ("measure" or None) of the agent's last delivery (`last_delivery`)."""
    return (last_delivery(project, branch, name, agents) or {}).get("kind") or None


def report_of(
    project: Project, branch: str | None = None, name: str | None = None, agents: Agents | None = None
) -> str | None:
    """The report named by the agent's last delivery (`last_delivery`), or None when it named none or none is known."""
    return (last_delivery(project, branch, name, agents) or {}).get("report")


def outcome_of(
    project: Project, branch: str | None = None, name: str | None = None, agents: Agents | None = None
) -> str | None:
    """The outcome of the agent's last delivery (`last_delivery`), or None when none is known."""
    d = last_delivery(project, branch, name, agents)
    return (d.get("outcome") or "ok") if d else None


def sub_is_measure(project: Project, s: Mapping[str, Any], agents: Agents | None = None) -> bool:
    """Whether a sub-issue is a measurement: `kind = "measure"`, or its agent's last delivery said `--kind measure`."""
    agent = s.get("agent")
    return s.get("kind") == "measure" or bool(agent and kind_of(project, name=agent, agents=agents) == "measure")


SUB_CLOSED = ("done", "canceled", "moved")


def load_backlog(project: Project) -> Data | None:
    """The project's backlog data, or None when it has none or it cannot be read."""
    from .backlog import Backlog, BacklogError

    b = Backlog(project)
    try:
        return b.load() if b.exists() else None
    except (BacklogError, OSError, ValueError, KeyError):
        return None


def branch_kind(project: Project, branch: str, data: Data | None = None, agents: Agents | None = None) -> str:
    """What BRANCH brings, by the backlog (`data`, loaded when None) and the deliveries: "closed" when every
    sub-issue naming it is closed (done, canceled, moved) and none has a commit (closed on purpose without
    integrating); "measure" when its open sub-issues are all measurements (`kind = "measure"`, or their agent's last
    delivery said `--kind measure`), or, with no sub-issue naming it, when the branch's last delivery said so;
    "code" otherwise (a single open code sub-issue makes it code). AGENTS: agents.json, read here when None."""
    from . import agents as agents_mod
    from .backlog import Backlog

    if data is None:
        data = load_backlog(project)
    subs = [s for _, s in Backlog.subs_of_branch(data, branch)] if data else []
    if not subs:
        return "measure" if kind_of(project, branch=branch, agents=agents) == "measure" else "code"
    open_ = [s for s in subs if s.get("status") not in SUB_CLOSED]
    if not open_:
        return "code" if any(s.get("commit") for s in subs) else "closed"
    agents = agents_mod.agents(project) if agents is None else agents  # once for every sub-issue
    return "measure" if all(sub_is_measure(project, s, agents) for s in open_) else "code"


def measures(project: Project, aid: str | None, h: Agent, branch: str) -> bool:
    """Whether the agent's delivery is a measurement (it needs no commits): its delivery said `--kind measure`, its
    sub-issue (the launch's issue and sub, else the one naming its name and branch) is `kind = "measure"`, or
    `branch_kind` says "measure"."""
    if delivered_kind(project, aid, h) == "measure":
        return True
    data = load_backlog(project)
    if data is not None:
        from .backlog import Backlog, on_branch

        for _, i, _ in Backlog.iter_issues(data):
            for s in i["subs"]:
                by_launch = h.get("issue") and h.get("sub") and (i["id"], s["id"]) == (h["issue"], h["sub"])
                by_name = h.get("name") and s.get("agent") == h["name"] and on_branch(s, branch)
                if (by_launch or by_name) and s.get("kind") == "measure":
                    return True
    return branch_kind(project, branch, data) == "measure"


def has_commits(wt: PathLike, branch: str, main: str) -> bool:
    return int(git(wt, "rev-list", "--count", f"{main}..{branch}").strip() or 0) > 0


def report_dirs(project: Project) -> list[str]:
    return [p for p in (project.section("ids").get("report_prefixes") or []) if p]


def home_in(main_path: PathLike, project: Project) -> Path | None:
    """The home's path relative to the main checkout when it lives in the repo, else None."""
    try:
        return project.home.resolve().relative_to(Path(main_path).resolve())
    except ValueError:
        return None


def worktree_home(project: Project, wt: PathLike) -> Path | None:
    """The home's folder inside the checkout WT (a worktree of the repo the home lives in), or None when the home
    lives elsewhere or WT is no git checkout."""
    try:
        top = Path(git(wt, "rev-parse", "--show-toplevel").strip())
        common = git(wt, "rev-parse", "--git-common-dir").strip()
    except Unknown:
        return None
    rel = home_in((Path(wt) / common).resolve().parent, project)
    return None if rel is None else top / rel


def homes(project: Project, wts: Iterable[PathLike | None]) -> list[Path]:
    """The home, then its folder inside each of the checkouts WTS (when the home lives in their repo)."""
    out = [project.home]
    for wt in wts:
        h = worktree_home(project, wt) if wt and Path(wt).is_dir() else None
        if h is not None and h.resolve() not in {o.resolve() for o in out}:
            out.append(h)
    return out


def report_rel(project: Project, path: str, wts: Iterable[PathLike | None] = ()) -> str | None:
    """PATH, a report named relative to the home or absolute inside it (or inside its folder in one of the
    checkouts WTS), as a path relative to the home; None when it is absolute outside them or climbs out with «..»."""
    p = Path(path)
    if not p.is_absolute():
        parts = p.as_posix().split("/")
        return None if ".." in parts else p.as_posix().removeprefix("./")
    for h in homes(project, wts):
        try:
            return p.resolve().relative_to(h.resolve()).as_posix()
        except ValueError:
            continue
    return None


def named_report(project: Project, path: str, wts: Iterable[PathLike | None] = ()) -> str:
    """The report an agent names at delivery (`foreman progress --done --report PATH`), checked before anything is
    written: relative to the home (or absolute inside it), under `[ids] report_prefixes` (the rule of `backlog
    integrated --report`), and a file in the home or, when the home lives in the repo, in its folder inside one of
    the checkouts WTS (the agent's worktree). Returns it relative to the home; raises ValueError saying what is
    expected."""
    wts = list(wts)
    where_ = homes(project, wts)
    rel = report_rel(project, path, wts)
    if rel is None:
        raise ValueError(f"{path} is not inside the home ({' or '.join(str(h) for h in where_)})")
    prefixes = report_dirs(project)
    if prefixes and not rel.startswith(tuple(prefixes)):
        raise ValueError(f"the report must be a path under {' or '.join(prefixes)} in the home (got {rel})")
    if not any((h / rel).is_file() for h in where_):
        raise ValueError(f"{rel} does not exist (looked in {' and '.join(str(h) for h in where_)})")
    return rel


def name_warning(report: str, issue: str | None) -> str | None:
    """A warning (never a refusal) when the report's file name does not contain the issue's id as a whole word: the
    name format is `YYYY-MM-DD-<ID>-<Tn>.md` (docs/agent.md). None when it does, or when there is no issue."""
    if not issue or names_key(report.rsplit("/", 1)[-1], [issue]):
        return None
    return f"the report name {report} does not contain the issue id {issue}: name it YYYY-MM-DD-{issue}-<Tn>.md"


def worktrees_of(project: Project, aid: str | None, name: str | None) -> list[str]:
    """The worktree recorded for the agent (agents.json, by agent id, else the newest entry with this name); [] when
    none is."""
    h = entry_of(project, aid, name)
    return [h["worktree"]] if h and h.get("worktree") else []


def entry_of(project: Project, aid: str | None, name: str | None) -> Agent | None:
    """The agent's entry in agents.json: by agent id, else the newest entry with this name; None when none is."""
    from . import agents as agents_mod

    d = agents_mod.agents(project)
    h = d.get(aid) if aid else None
    if h is None and name:
        mine = [x for x in d.values() if x.get("name") == name]
        h = max(mine, key=lambda x: str(x.get("start") or ""), default=None)
    return h


def type_and_model(project: Project, aid: str | None, name: str | None) -> tuple[str | None, str | None]:
    """(type without a plugin prefix, model) of the agent that delivers, for its `done` line: the model its
    transcript says it runs on, else the recorded one, else the one asked at launch; None for what is not known."""
    from . import identity

    h = entry_of(project, aid, name)
    if not h:
        return None, None
    t = h.get("type")
    return (t.split(":", 1)[-1] if t else None), identity.transcript_model(h) or h.get("model") or h.get("model_asked")


def delivered_report(project: Project, aid: str | None, h: Agent) -> str | None:
    """The report an entry named at its delivery (`progress --done --report`, `_done_record`)."""
    return (_done_record(project, aid, h) or {}).get("report")


def is_plan(name: str) -> bool:
    """Whether a file name is an issue's plan (`plan-ID.md`, with or without a date in front): a plan names the
    issue and often the branch, but it is no branch's report."""
    return re.match(r"(?:\d{4}-\d{2}-\d{2}-)?plan(?:[-_.]|$)", name.lower()) is not None


def started(project: Project, branch: str, agents: Agents | None = None) -> float | None:
    """When the first agent of agents.json (AGENTS, read when None) with this branch started (epoch), or None when
    none is recorded."""
    from . import agents as agents_mod

    agents = agents_mod.agents(project) if agents is None else agents
    times = [util.iso_epoch(h.get("start")) for h in agents.values() if h.get("branch") == branch]
    known = [t for t in times if t]
    return min(known) if known else None


class Report(NamedTuple):
    """What `branch_report` found. `path`: relative to the home (as the sub-issue gives it, which may be absolute),
    None when nothing was found; `how`: "sub-issue", "delivery", "branch" or "home"; `stale`: a report the sub-issue
    or the delivery names that exists nowhere, and `stale_how` which of the two ("sub-issue" or "delivery");
    `required`: False when the project keeps no reports (`[ids] report_prefixes = []`)."""

    path: str | None
    how: str | None = None
    stale: str | None = None
    required: bool = True
    stale_how: str | None = None


def sub_reports(project: Project, branch: str, data: Data | None) -> tuple[list[str], list[str]]:
    """(reports, keys) of the backlog's non-archived sub-issues with this branch: their `report` fields, and their
    issue ids and agents. `data` is the loaded backlog; None loads it (an unreadable backlog gives nothing)."""
    from .backlog import Backlog, BacklogError

    b = Backlog(project)
    if data is None:
        try:
            data = b.load()
        except (BacklogError, OSError, ValueError):
            return [], []
    reports: list[str] = []
    keys: list[str] = []
    for i, s in b.subs_of_branch(data, branch, archived=False):
        keys += [k for k in (i.get("id"), s.get("agent")) if k]
        if s.get("report") and s["report"] not in reports:
            reports.append(s["report"])
    return reports, keys


def in_branch(wt: PathLike, branch: str, path: str) -> bool:
    """Whether the branch has PATH (relative to the repo) as a file."""
    try:
        return git(wt, "cat-file", "-t", f"{branch}:{path}").strip() == "blob"
    except Unknown:
        return False


def repo_path(rel: Path, path: str) -> str:
    """PATH (relative to the home) relative to the repo, with the home at REL inside it."""
    return path if str(rel) == "." else f"{rel.as_posix()}/{path}"


def names_key(name: str, keys: Iterable[str]) -> bool:
    """Whether a file name has one of the keys (an issue id, an agent's name) as a whole word: RG-4 is not in
    rg-43.md."""
    low = name.lower()
    return any(re.search(rf"(?<![a-z0-9]){re.escape(k.lower())}(?![a-z0-9])", low) for k in keys if k)


def report_exists(project: Project, report: str, wt: PathLike | None, branch: str, rel: Path | None) -> bool:
    """Whether a sub-issue's report exists: in the home (relative to it, or absolute) or, with the home inside the
    repo (REL), in the branch."""
    full = Path(report) if Path(report).is_absolute() else project.home / report
    if full.is_file():
        return True
    if rel is None or wt is None:
        return False
    try:
        inner = full.resolve().relative_to(project.home.resolve()).as_posix()
    except ValueError:
        return False
    return in_branch(wt, branch, repo_path(rel, inner))


def home_reports(project: Project, prefixes: list[str], since: float) -> list[Path]:
    """The home's `.md` files under every prefix and the reviews folder, changed at or after `since`, newest first."""
    dirs = [project.home / p for p in prefixes]
    if not any(project.reviews_dir.resolve().is_relative_to(d.resolve()) for d in dirs):
        dirs.append(project.reviews_dir)
    seen: dict[Path, float] = {}
    for d in dirs:
        if not d.is_dir():
            continue
        for f in d.rglob("*.md"):
            try:
                t = f.stat().st_mtime
            except OSError:
                continue
            if t >= since:
                seen[f.resolve()] = t
    return sorted(seen, key=lambda f: -seen[f])


def branch_report(
    project: Project,
    branch: str,
    *,
    wt: PathLike | None = None,
    main: str = "main",
    main_path: PathLike | None = None,
    data: Data | None = None,
    delivered: str | None = None,
    since: float | None = None,
    keys: Iterable[str] = (),
    strict: bool = False,
) -> Report:
    """The report of a branch: the one rule of the pass's `ready_to_integrate` and of the delivery check
    (`has_report`). The first found wins:

    1. the `report` of a non-archived sub-issue with this branch (`data`, the loaded backlog, or None to load it),
       when it exists: in the home, or added by the branch (`wt`, its worktree) when the home lives in the repo
       (`main_path`, its main checkout);
    2. `delivered`, the report the agent named at its delivery (`progress --done --report`), when it exists (the
       same test);
    3. with the home inside the repo, the first file the branch adds or changes under `[ids] report_prefixes`
       (`git diff main...branch`);
    4. the home's `.md` files under every prefix and `[paths] reviews`, changed since `since` (default: REPORT_DAYS
       ago), plans left out (`is_plan`): first one whose name has a key (the sub-issue's issue and agent, and
       `keys`) and that cites the branch, then one whose name has a key, then one that cites the branch.

    `strict` (the delivery check): a delivered report that exists nowhere ends the search (the agent named it: no
    guess replaces it); with the home inside the repo only the branch counts (step 4 is left out), and a git error
    in step 3 is raised as Unknown (the caller counts it as found). Otherwise a step that errors is skipped. A report
    named by the sub-issue or the delivery that exists nowhere is `stale`; with no prefixes the project requires no
    report and steps 1 and 2 are the only ones."""
    prefixes = report_dirs(project)
    rel = home_in(main_path, project) if main_path is not None else None
    reports, sub_keys = sub_reports(project, branch, data)
    stale = stale_how = None
    for r in reports:
        if report_exists(project, r, wt, branch, rel):
            return Report(r, "sub-issue")
        stale, stale_how = stale or r, stale_how or "sub-issue"
    if delivered:
        if report_exists(project, delivered, wt, branch, rel):
            return Report(delivered, "delivery", stale, stale_how=stale_how)
        if strict:
            return Report(None, "delivery", delivered, bool(prefixes), "delivery")
        stale, stale_how = stale or delivered, stale_how or "delivery"
    if not prefixes:
        return Report(None, stale=stale, required=False, stale_how=stale_how)
    if rel is not None and wt is not None:
        paths = [repo_path(rel, p) for p in prefixes]
        try:
            out = git(wt, "diff", "--name-only", "--diff-filter=AMR", f"{main}...{branch}", "--", *paths)
        except Unknown:
            if strict:
                raise
            out = ""
        for ln in out.splitlines():
            if ln.strip():
                found = ln.strip()
                cut = len(repo_path(rel, ""))
                return Report(found[cut:], "branch", stale, stale_how=stale_how)
        if strict:
            return Report(None, stale=stale, stale_how=stale_how)
    cutoff = since if since is not None else time.time() - REPORT_DAYS * 86400
    keys = [*sub_keys, *(k for k in keys if k)]
    named = cited = None
    for f in home_reports(project, prefixes, cutoff):
        if is_plan(f.name):
            continue
        try:
            cites = branch in f.read_text(errors="replace")
        except OSError:
            continue
        has_key = names_key(f.name, keys)
        if has_key and cites:
            named = cited = f
            break
        named = named or (f if has_key else None)
        cited = cited or (f if cites else None)
    best = named or cited
    if best is None:
        return Report(None, stale=stale, stale_how=stale_how)
    try:
        shown = str(best.relative_to(project.home.resolve()))
    except ValueError:
        shown = str(best)
    return Report(shown, "home", stale, stale_how=stale_how)


def has_report(
    project: Project, h: Agent, wt: PathLike, branch: str, main: str, main_path: PathLike, aid: str | None = None
) -> Report:
    """The report the delivery check finds (`branch_report`, strict, with the report the agent named at its
    delivery, its start as the cutoff and its name and issue as keys). It is there when `path` is set or `required`
    is False (the project keeps no reports)."""
    return branch_report(
        project,
        branch,
        wt=wt,
        main=main,
        main_path=main_path,
        delivered=delivered_report(project, aid, h),
        since=util.iso_epoch(h.get("start")) or 0,
        keys=[k for k in (h.get("name"), h.get("issue")) if k],
        strict=True,
    )


def missing(project: Project, aid: str | None, h: Agent) -> list[tuple[str, str]]:
    """What the delivery lacks: a list of (key, text) in plain English with the command that fixes each. A step that
    cannot tell counts as found."""
    done, outcome = delivery(project, aid, h)
    items: list[tuple[str, str]] = []
    try:
        wt, branch, main, main_path = where(project, h)
    except Unknown:
        wt = branch = main = main_path = None  # all four are None together when where() cannot tell
    if outcome != "failed" and wt is not None and branch is not None and main is not None and main_path is not None:
        try:
            if not has_commits(wt, branch, main) and not measures(project, aid, h, branch):
                items.append(
                    (
                        "commits",
                        f"no commits on {branch} beyond {main}: commit your work in {wt} "
                        f'(git -C {wt} add -A && git -C {wt} commit -m "…")',
                    )
                )
        except (Unknown, ValueError):
            pass
        try:
            rep = has_report(project, h, wt, branch, main, main_path, aid)
            if rep.path is None and rep.required:
                dirs, rel = report_dirs(project), home_in(main_path, project)
                if rep.how == "delivery":
                    items.append(
                        (
                            "report",
                            f"the report you named at delivery, {rep.stale}, is not "
                            + (f"in {branch}: commit it in {wt}" if rel is not None else f"in {project.home}: write it")
                            + " (or name the right one with --report)",
                        )
                    )
                elif rel is not None:
                    where_ = " or ".join(str(rel / d) if str(rel) != "." else d for d in dirs)
                    items.append(
                        (
                            "report",
                            f"no report under {where_} in your branch: write "
                            f"{where_.split(' or ')[0].rstrip('/')}/YYYY-MM-DD-<task>.md in your worktree "
                            "(what changed, how it was checked, what is missing) and commit it",
                        )
                    )
                else:
                    items.append(
                        (
                            "report",
                            f"no report of yours under {project.home / dirs[0]}: write "
                            f"{project.home / dirs[0]}/YYYY-MM-DD-<task>.md (what changed, how it was "
                            "checked, what is missing; it names your branch)",
                        )
                    )
            elif rep.path is not None:
                text = report_text(project, rep.path, wt, branch, main_path)
                for k, found in report_lines(text).items():
                    if not found:
                        items.append((k.lower(), line_missing(k, rep.path, wt, home_in(main_path, project))))
        except Unknown:
            pass
    if not done:
        items.append(
            (
                "progress --done",
                "no delivery recorded: you have not run `foreman progress --done` since "
                "you started (or since your last stop was refused)",
            )
        )
    return items


def block_reason(project: Project, result: Result, n: int) -> str:
    """The reason given back to the agent (plain English): what is missing and the command for each."""
    lines = [f"foreman delivery check (try {n} of {MAX_BLOCKS}): your delivery is not complete yet."]
    lines += [f"- {text}" for _, text in result["items"]]
    lines.append(
        f'Fix it, then run `foreman --project {project.name} progress --done "<result in one line>" '
        "--outcome ok|partial` as your last command and stop. If the task failed, say so with "
        f'`foreman --project {project.name} progress --done "<what failed and why>" --outcome failed`: '
        "a failed delivery needs no commits or report."
    )
    if any(k == "commits" for k, _ in result["items"]):
        lines.append(
            "If your task only measured (a report, no code for main), add `--kind measure`: it needs no commits."
        )
    return "\n".join(lines)


# ---------- the fixed lines of a report ----------

LINE_RE = r"^[ \t]*(?:[-*][ \t]+)?(?:\*\*)?({keys})(?:\*\*)?:(?:\*\*)?[ \t]*(\S.*?)?[ \t]*$"


def report_lines(text: str, keys: Iterable[str] = REPORT_LINES) -> dict[str, list[str]]:
    """{key: [the text of each `Key: text` line]} of a report, for every key (REPORT_LINES by default): a line that
    starts with the key and a colon (a list dash and bold around the key are let through) and has text after it.
    The lines that follow it (reports are wrapped at 120 columns) are joined to it with a space, up to a blank line,
    a line of a key or one that starts with `#`, `-`, `*` or `|`.
    The one reading of every fixed line of a report: the delivery check and `foreman agents --misled`."""
    keys = list(keys)
    out: dict[str, list[str]] = {k: [] for k in keys}
    if not keys:
        return out
    key_line = re.compile(LINE_RE.format(keys="|".join(map(re.escape, keys))))
    open_: list[str] | None = None  # the pieces of the line being read
    slot: list[str] = []  # where it goes
    for line in text.splitlines():
        m = key_line.match(line)
        if m:
            open_ = None
            if m.group(2):
                open_ = [m.group(2)]
                out[m.group(1)].append(m.group(2))
                slot = out[m.group(1)]
        elif open_ is not None and line.strip() and line.lstrip()[0] not in "#-*|":
            open_.append(line.strip())
            slot[-1] = " ".join(open_)
        else:
            open_ = None
    return out


def is_nothing(value: str) -> bool:
    """Whether a line's text says nothing happened (`none`, `nothing`, `nada`, `-`, with or without a full stop)."""
    return value.strip().rstrip(".").strip().lower() in NOTHING


def report_text(project: Project, path: str, wt: PathLike, branch: str, main_path: PathLike) -> str:
    """The text of the report the delivery check found (PATH, relative to the home or absolute): the branch's copy
    when the home lives in the repo and the branch has it, else the file in the home. Raises Unknown when neither
    can be read (the caller counts it as found)."""
    full = Path(path) if Path(path).is_absolute() else project.home / path
    rel = home_in(main_path, project)
    if rel is not None:
        try:
            inner = full.resolve().relative_to(project.home.resolve()).as_posix()
            return git(wt, "show", f"{branch}:{repo_path(rel, inner)}")
        except (ValueError, Unknown):
            pass
    try:
        return full.read_text(errors="replace")
    except OSError as e:
        raise Unknown(str(e)) from e


def line_missing(key: str, path: str, wt: PathLike, rel: Path | None) -> str:
    """The refusal's text for a report that lacks the line KEY: what to write, and where to commit it when the home
    lives in the repo (REL)."""
    how = LINE_HOW.get(key, f"one line `{key}: …`")
    tail = f"; commit it in {wt}" if rel is not None else ""
    return f"your report {path} lacks the line `{key}: …`: {how}{tail}"


def report_date(f: Path) -> str:
    """The day of a report: the `YYYY-MM-DD` its name starts with, else the day it was last changed ("" when it
    cannot be read)."""
    m = re.match(r"\d{4}-\d{2}-\d{2}", f.name)
    if m:
        return m.group(0)
    try:
        return time.strftime("%Y-%m-%d", time.localtime(f.stat().st_mtime))
    except OSError:
        return ""


def is_review(name: str) -> bool:
    """Whether a file name is a review (`review-….md`, with or without a date in front)."""
    return re.match(r"(?:\d{4}-\d{2}-\d{2}-)?review(?:[-_.]|$)", name.lower()) is not None


def deliveries_by_report(project: Project) -> dict[str, dict[str, Any]]:
    """{report: the last `done` line of progress.jsonl that names it}."""
    out: dict[str, dict[str, Any]] = {}
    for x in util.read_jsonl(project.state / "progress.jsonl"):
        if x.get("done") and x.get("report"):
            out[str(x["report"])] = x
    return out


def misled(
    project: Project, since: str | None = None, type_: str | None = None, model: str | None = None
) -> dict[str, Any]:
    """`foreman agents --misled`: the `Misled:` lines of the reports (the `.md` files right under each of `[ids]
    report_prefixes` and the reviews folder; what is in their subfolders is a report's attachment) dated at or after
    SINCE (YYYY-MM-DD; default the day of the newest `*-review-harness*.md`, else 7 days ago). Each report is
    tied to the last delivery that named it (progress.jsonl: agent, type, model); without one, its type comes from
    its name (a plan: planner, a review: reviewer). TYPE_ (exact, without a plugin prefix) and MODEL (a part of its
    id) keep only those reports. Returns {since, reports, something, none, without, without_line: [paths], rows:
    [{date, agent, type, model, report, where, text}]}."""
    dirs = [project.home / d for d in report_dirs(project)] + [project.reviews_dir]
    files = list({f.resolve(): f for d in dirs if d.is_dir() for f in d.glob("*.md")}.values())
    if since is None:
        harness = [report_date(f) for f in files if "review-harness" in f.name.lower()]
        since = (
            max(harness) if harness else time.strftime("%Y-%m-%d", time.localtime(util.now().timestamp() - 7 * 86400))
        )
    by = deliveries_by_report(project)
    home = project.home.resolve()
    out: dict[str, Any] = {"since": since, "reports": 0, "something": 0, "none": 0, "without": 0}
    rows: list[dict[str, str]] = []
    without: list[str] = []
    for f in sorted(files, key=lambda f: (report_date(f), f.name)):
        day = report_date(f)
        if day < since:
            continue
        try:
            path = f.relative_to(home).as_posix()
        except ValueError:
            path = str(f)
        x = by.get(path) or {}
        kind = str(x.get("type") or "").split(":", 1)[-1]
        kind = kind or ("planner" if is_plan(f.name) else "reviewer" if is_review(f.name) else "-")
        ran = str(x.get("model") or "-")
        if (type_ and kind != type_.split(":", 1)[-1]) or (model and model not in ran):
            continue
        try:
            found = report_lines(f.read_text(errors="replace"), ("Misled",))["Misled"]
        except OSError:
            continue
        out["reports"] += 1
        real = [v for v in found if not is_nothing(v)]
        out["something" if real else "none" if found else "without"] += 1
        if not found:
            without.append(path)
        for v in real:
            m = re.match(r"(\w+):\s*(.*)$", v)
            where_ = m.group(1).lower() if m and m.group(1).lower() in WHERE else "-"
            text = m.group(2) if where_ != "-" and m else v
            rows.append(
                {
                    "date": day,
                    "agent": str(x.get("agent") or "-"),
                    "type": kind,
                    "model": ran,
                    "report": path,
                    "where": where_,
                    "text": text,
                }
            )
    return {**out, "without_line": without, "rows": rows}


def misled_text(result: dict[str, Any]) -> str:
    """`foreman agents --misled` as text: one line per `Misled:` line, then the footer (how many reports, with
    something, none and without the line; how many lines per <where>)."""
    lines = [
        f"{r['date']} {r['agent']} {r['type']} {r['model']} {r['report']}: {r['where']}: {r['text']}"
        for r in result["rows"]
    ]
    w = result["without_line"]
    foot = (
        f"{result['reports']} reports since {result['since']}: {result['something']} with something, "
        f"{result['none']} none, {result['without']} without the line"
    )
    if w:
        foot += " (" + ", ".join(w[:10]) + (", …" if len(w) > 10 else "") + ")"
    lines.append(foot)
    counts: dict[str, int] = {}
    for r in result["rows"]:
        counts[r["where"]] = counts.get(r["where"], 0) + 1
    if counts:
        lines.append("by where: " + ", ".join(f"{k} {n}" for k, n in sorted(counts.items(), key=lambda kv: -kv[1])))
    return "\n".join(lines)
