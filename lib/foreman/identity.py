"""An agent's identity, recorded when it is launched instead of guessed from the prose of its brief.

- `foreman launch NAME …` (and `foreman worktree`) records a launch in `<state>/launches.json` with a token foreman
  generates: {token: {name, issue, sub, repo, branch, worktree, type, production, at, session, agent}}. It prints the
  launch line, the first line of the agent's brief: `[foreman launch k3f9q2m7xa] zed7 · AB-1 T1 · branch ab1/x ·
  worktree /…/app-zed7 · production`. The line is always in English, whatever the language of the brief.
- The agents hook ties the launch to the agent (`link`): from the PostToolUse of the Agent/Task tool (the token in
  `tool_input.prompt`, the agent id in `tool_response.agentId`: right away for a background launch) or, for a
  foreground launch or a lost event, from the token in the head of the agent's transcript. The launch's fields are
  copied into `<state>/agents.json[agent_id]` with `launch` (the token) and `named_by` (`launch`, or `hand` for
  `foreman agents --name`).
- Models: `foreman launch --model M` records `model` in the launch (only when given), copied to the agent's entry as
  `model_asked`; when the agent stops, `model` in its entry is the one its transcript says ran (`transcript_model`;
  nothing recorded when the transcript has none).
- Pruning: a launch no agent took is dropped after 24 h; a linked one, once its agent left agents.json.
- Reading (`of`): the loop pass, `foreman agents` and the panel read an agent's name, issue, branch, worktree and
  whether it is production from its entry first; the name comes only from there (the launch line or `foreman agents
  --name`); only what else the entry lacks is guessed the old way («Issue: ID», the backlog sub-issue with its name,
  «Branch `y`», a production type or worktree path in the brief).
  `registered` says whether the entry carries an identity (named_by launch or hand).
"""

from __future__ import annotations

import base64
import os
import re
import time
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict

from . import transcripts, util

if TYPE_CHECKING:
    from .config import Project

TOKEN = re.compile(r"\[foreman launch ([a-z2-7]{10})\]")
FIELDS = ("name", "issue", "sub", "repo", "branch", "worktree", "type", "production")
PENDING_S = 86400
AGENT_TOOLS = ("Agent", "Task")
PRODUCTION_TYPES = ("dev", "foreman:dev")
PLANNER_TYPES = ("planner", "foreman:planner")
MECHANIC_TYPES = ("mechanic", "foreman:mechanic")  # may be production: launched for an issue, it is unless told not


class IdentityError(Exception):
    pass


# An entry of <state>/agents.json, {agent id: entry}: written by the agents hook and by this module (link, name_hand),
# read everywhere with .get(). Its keys grow with the hook (start, type, transcript, state, the FIELDS of a launch,
# launch, named_by, model, model_asked…) and are copied by name (FIELDS), so it stays a plain JSON object.
Agent = dict[str, Any]
# A hook's event as Claude Code sends it (a JSON object; tool_input, tool_response, session_id, agent_id…).
Event = Mapping[str, Any]


class _Launch(TypedDict):
    name: str
    issue: str | None
    sub: str | None
    repo: str | None
    branch: str | None
    worktree: str | None
    type: str | None
    production: bool | None  # None: decided when the agent is tied to it (by its type)
    at: str  # local ISO time
    session: str | None  # the launching session (CLAUDE_CODE_SESSION_ID)
    agent: str | None  # the agent id once tied (link)


class Launch(_Launch, total=False):
    """A launch of <state>/launches.json, {token: launch} (new_launch)."""

    model: str  # the model asked for, only when given
    despite: list[dict[str, str]]  # [{decision, why}]: pending decisions that held it (launch --despite), when any
    review: bool  # launched to review its repo:branch (launch --review), only when so
    planned: str  # the sub-issue's `type` (TYPE[/MODEL]) when it has one: the call may not be dearer (call_too_dear)


class Identity(TypedDict):
    """An agent's identity as `of` gives it."""

    name: str | None
    issue: str | None
    sub: str | None
    repo: str | None
    branch: str | None
    worktree: str | None
    production: bool
    registered: bool
    source: str | None  # where the name came from: named_by, or None (no name)


class Caller(TypedDict):
    """An entry of <state root>/callers.json, {tool_use_id: caller} (record_call; see «who is calling» below)."""

    at: float  # epoch seconds
    session: str
    agent: str | None
    state: str
    calls: list[list[Any]]  # [[project or None, sub, words…]]
    background: bool


def launches_file(project: Project) -> Path:
    return project.state / "launches.json"


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def transcript_head(h: Agent, size: int = 20000) -> str:
    return transcripts.head(h.get("transcript", ""), size)


def transcript_model(h: Agent) -> str | None:
    """The model an agent ran on, read from its transcript (the last real one), or None when the transcript is
    missing or has none (nothing is guessed)."""
    return transcripts.model(h.get("transcript", ""))


def _type_in(agent_type: str | None, types: set[str] | tuple[str, ...]) -> bool:
    """True if `agent_type` is one of `types`, with or without a plugin prefix: `foreman:dev` matches `dev` and the
    other way round."""
    if not agent_type:
        return False
    bare = {t.split(":", 1)[-1] for t in types}
    return agent_type in types or agent_type.split(":", 1)[-1] in bare


def is_production_type(project: Project, agent_type: str | None) -> bool:
    """True for `dev`/`foreman:dev` and for the types in [agents] production_types (with or without a plugin
    prefix: `foreman:dev` matches `dev` and the other way round)."""
    return _type_in(agent_type, set(PRODUCTION_TYPES) | set(project.section("agents").get("production_types", [])))


def is_planner_type(project: Project, agent_type: str | None) -> bool:
    """True for `planner`/`foreman:planner` (the same prefix rule)."""
    return _type_in(agent_type, set(PLANNER_TYPES))


def role(project: Project, agent_type: str | None) -> str:
    """What an agent of this type is for foreman: "production" (is_production_type), "planner" (is_planner_type:
    the pipeline notice does not ask for a plan while one is live) or "other"."""
    if is_production_type(project, agent_type):
        return "production"
    return "planner" if is_planner_type(project, agent_type) else "other"


FOREMAN_AGENTS = Path(__file__).resolve().parents[2] / "agents"


def agent_definition_name(f: Path) -> str:
    """The name a Claude Code agent file defines: its frontmatter's `name:`, else the file's stem."""
    try:
        text = f.read_text()
    except (OSError, UnicodeDecodeError):
        return f.stem
    head = text.split("\n---", 1)[0] if text.startswith("---\n") else ""
    m = re.search(r"^name:\s*[\"']?([^\"'\n]+?)[\"']?\s*$", head, re.M)
    return m.group(1) if m else f.stem


MODELS = ("haiku", "sonnet", "opus", "fable")  # by price, cheapest first


def price_rank(agent_type: str | None, model: str | None = None) -> int:
    """Where an agent costs in MODELS: its `model` (a family name or a full id), else the `model:` of the type's
    definition in agents/; `inherit`, no definition or an unknown name count as opus (the session's model)."""
    if not model and agent_type:
        f = FOREMAN_AGENTS / f"{agent_type.rsplit(':', 1)[-1]}.md"
        try:
            m = re.search(r"^model:\s*(\S+)", f.read_text().split("\n---", 1)[0], re.M)
        except (OSError, UnicodeDecodeError):
            m = None
        model = m.group(1) if m else None
    low = (model or "").lower()
    return next((i for i, m in enumerate(MODELS) if m in low), MODELS.index("opus"))


def split_type(planned: str) -> tuple[str, str | None]:
    """`dev/sonnet` -> ("dev", "sonnet"); `mechanic` -> ("mechanic", None)."""
    kind, _, model = planned.partition("/")
    return kind, model or None


def call_too_dear(project: Project, tool_input: dict[str, Any]) -> tuple[str, str] | None:
    """The PreToolUse of an Agent/Task call whose brief has the launch line of a launch with a planned type: None
    when the call is no dearer than the launch, else (planned, the model the call should ask for)."""
    prompt = tool_input.get("prompt")
    rec = util.read_json(launches_file(project), {}).get(token_in(prompt if isinstance(prompt, str) else ""))
    planned = rec.get("planned") if rec else None
    if not rec or not planned:
        return None
    kind, model = tool_input.get("subagent_type"), tool_input.get("model")
    kind, model = (kind if isinstance(kind, str) else None), (model if isinstance(model, str) else None)
    allowed = price_rank(rec.get("type"), rec.get("model"))
    return None if price_rank(kind, model) <= allowed else (planned, MODELS[allowed])


def shadowed_types(project: Project, user_dir: Path | None = None) -> list[tuple[str, Path]]:
    """foreman's agent types (agents/*.md of the plugin) that an agent file of the project defines again, [(type,
    file)]: in `.claude/agents/` of the home and of each repo's main checkout, and in the user's agents folder
    (`user_dir`; default $CLAUDE_CONFIG_DIR/agents or ~/.claude/agents). Claude Code runs the one of higher priority
    (project and user over a plugin) whole, so such a copy no longer follows foreman's procedure when foreman
    changes; `foreman check` lists them (docs/extending.md)."""
    ours = {agent_definition_name(f) for f in FOREMAN_AGENTS.glob("*.md")}
    if user_dir is None:
        conf = os.environ.get("CLAUDE_CONFIG_DIR")
        user_dir = (Path(conf) if conf else Path.home() / ".claude") / "agents"
    dirs = [project.home / ".claude/agents"]
    for key in project.repos:
        path = project.repo(key).get("path")
        if path:
            dirs.append(Path(path) / ".claude/agents")
    dirs.append(user_dir)
    found, seen = [], set()
    for d in dirs:
        if not d.is_dir() or d.resolve() in seen:
            continue
        seen.add(d.resolve())
        for f in sorted(d.glob("*.md")):
            name = agent_definition_name(f)
            if name in ours:
                found.append((name, f))
    return found


def is_production(project: Project, h: Agent, head: str | None = None) -> bool:
    """A production agent: the identity recorded at launch says so (`production`); otherwise (the fallback) its type
    is a production type (role: `dev`, `foreman:dev` and [agents] production_types, with or without a plugin
    prefix, so `foreman:dev` counts for production_types = ["dev"]) or its assignment names a production worktree
    ([agents] production_worktree, default the worktrees folder)."""
    if h.get("production") is not None:
        return bool(h["production"])
    if role(project, h.get("type")) == "production":
        return True
    prefix = project.section("agents").get("production_worktree") or str(project.worktrees_dir) + "/"
    prefix = str(Path(prefix).expanduser())
    head = transcript_head(h) if head is None else head
    return bool(prefix) and prefix in head.replace("~", str(Path.home()))


def new_token() -> str:
    """10 lowercase base32 characters ([a-z2-7])."""
    return base64.b32encode(os.urandom(10)).decode().lower()[:10]


def token_in(text: str | None) -> str | None:
    """The first launch token in a text, or None."""
    m = TOKEN.search(text or "")
    return m.group(1) if m else None


def launch_line(token: str, rec: Launch) -> str:
    """`[foreman launch TOKEN] name · ISSUE Tn · branch B · worktree W · production · review` (the parts it has)."""
    parts = [rec["name"]]
    if rec.get("issue"):
        parts.append(" ".join(x for x in (rec["issue"], rec.get("sub")) if x))
    if rec.get("branch"):
        parts.append(f"branch {rec['branch']}")
    if rec.get("worktree"):
        parts.append(f"worktree {rec['worktree']}")
    if rec.get("production"):
        parts.append("production")
    if rec.get("review"):
        parts.append("review")
    if "planned" in rec:
        parts.append(f"plan {rec.get('planned')}")
    return f"[foreman launch {token}] " + " · ".join(parts)


def _age(iso: object) -> float:
    t = util.iso_epoch(iso)
    return PENDING_S + 1 if t is None else time.time() - t


def prune(launches: dict[str, Any], agents: Mapping[str, Agent]) -> dict[str, Any]:
    """Drops launches no agent took within 24 h and linked ones whose agent left agents.json."""
    return {
        t: r
        for t, r in launches.items()
        if isinstance(r, dict)
        and ((r.get("agent") and r["agent"] in agents) or (not r.get("agent") and _age(r.get("at")) <= PENDING_S))
    }


def pending(project: Project, name: str) -> list[str]:
    """The tokens of the launches of `name` no agent took yet (within 24 h)."""
    from . import loop

    d = prune(util.read_json(launches_file(project), {}), loop.agents(project))
    return [t for t, r in d.items() if r.get("name") == name and not r.get("agent")]


def live_with_name(project: Project, name: str) -> list[str]:
    """The ids of the live agents (loop.live_agents) known by `name`."""
    from . import loop

    return [h["id"] for h in loop.live_agents(project) if h.get("name") == name]


def name_taken(project: Project, name: str) -> str:
    """Why `name` cannot be given to a new agent ('' if it can): a live agent or a pending launch has it."""
    live = live_with_name(project, name)
    if live:
        return f"a live agent is called {name} ({live[0][:8]}…)"
    if pending(project, name):
        return f"a launch of {name} is still waiting for its agent (foreman launch {name} --cancel drops it)"
    return ""


def production_at_launch(
    project: Project, production: bool | None, worktree: bool, agent_type: str | None
) -> bool | None:
    """Whether a launch is production, as new_launch records it: what --production / --no-production says, else true
    with a worktree or a production type, else None (decided when the agent is tied to it, by its type)."""
    if production is None and (worktree or (agent_type and role(project, agent_type) == "production")):
        return True
    return production


def needs_worktree(project: Project, production: bool | None, agent_type: str | None) -> bool:
    """Whether a launch for an issue without --repo must be refused (`launch`): a production one (--production, or a
    production type unless --no-production) or a mechanic not marked --no-production, since a mechanic that changes
    code is production and one launched without its worktree runs with no delivery check (delivery.applies)."""
    if production_at_launch(project, production, False, agent_type):
        return True
    return production is None and _type_in(agent_type, MECHANIC_TYPES)


def new_launch(
    project: Project,
    name: str,
    issue: str | None = None,
    sub: str | None = None,
    repo: str | None = None,
    branch: str | None = None,
    worktree: util.PathLike | None = None,
    agent_type: str | None = None,
    production: bool | None = None,
    model: str | None = None,
    despite: list[dict[str, str]] | None = None,
    review: bool = False,
    planned: str | None = None,
) -> tuple[str, Launch]:
    """Records a launch; (token, record). `production` None: true with a worktree or a production type, otherwise
    decided when the agent is tied to it (by its type). `model`: the one asked for, recorded only when given.
    `despite`: [{decision, why}] of the pending decisions that held it (`launch --despite`), recorded only when
    given. `review`: a reviewer of `repo`:`branch` with no worktree (launch --review), recorded only when true."""
    from . import loop

    production = production_at_launch(project, production, bool(worktree), agent_type)
    rec: Launch = {
        "name": name,
        "issue": issue,
        "sub": sub,
        "repo": repo,
        "branch": branch,
        "worktree": str(worktree) if worktree else None,
        "type": agent_type,
        "production": production,
        "at": now_iso(),
        "session": os.environ.get("CLAUDE_CODE_SESSION_ID") or None,
        "agent": None,
    }
    if model:
        rec["model"] = model  # the model asked for at launch (the one that ran is read at the agent's stop)
    if despite:
        rec["despite"] = despite
    if review:
        rec["review"] = True
    if planned:
        rec["planned"] = planned
    f = launches_file(project)
    with util.locked_json(f, {}) as cur:
        d = prune(cur, loop.agents(project))
        token = new_token()
        while token in d:
            token = new_token()
        d[token] = rec
        util.write_json(f, d)
    return token, rec


def cancel(project: Project, name: str) -> list[str]:
    """Drops the pending launches of `name`; the tokens dropped."""
    f = launches_file(project)
    with util.locked_json(f, {}) as d:
        hits = [t for t, r in d.items() if isinstance(r, dict) and r.get("name") == name and not r.get("agent")]
        for t in hits:
            d.pop(t)
        util.write_json(f, d)
    return hits


def link(
    project: Project,
    agents: dict[str, Agent],
    aid: str,
    token: str,
    how: str = "launch",
    agent_type: str | None = None,
    relink: bool = True,
) -> bool:
    """Ties launch `token` to agent `aid` in `agents` (the agents.json dict, which the caller holds locked and
    writes): copies the launch's fields, creates the entry if SubagentStart did not come yet. relink=False leaves a
    launch already tied to another agent alone (the transcript path: a brief may quote another agent's line).
    Whether it tied it."""
    f = launches_file(project)
    with util.locked_json(f, {}) as d:
        rec = d.get(token)
        if not isinstance(rec, dict) or (not relink and rec.get("agent") not in (None, aid)):
            return False
        a = agents.setdefault(aid, {"start": now_iso(), "type": agent_type or rec.get("type")})
        if agent_type and not a.get("type"):
            a["type"] = agent_type
        for k in FIELDS:
            if k == "type":
                continue
            if rec.get(k) is not None:
                a[k] = rec[k]
        if a.get("production") is None:
            a["production"] = role(project, a.get("type")) == "production"
        if rec.get("model"):
            a["model_asked"] = rec["model"]
        if rec.get("review"):
            a["review"] = True  # it reviews its branch: it holds it while live, its delivery is not the branch's
        a["launch"] = token
        a["named_by"] = how
        rec["agent"] = aid
        util.write_json(f, prune(d, agents))
    return True


def agent_id_of(response: object) -> str | None:
    """The agent id of an Agent tool's response ({"status": "async_launched", "agentId": …}), or None."""
    if isinstance(response, dict):
        v = response.get("agentId") or response.get("agent_id")
        return v if isinstance(v, str) and v else None
    if isinstance(response, str):
        m = re.search(r'"?agentId"?\s*[:=]\s*"?([\w-]+)', response)
        return m.group(1) if m else None
    return None


def tool_input(ev: Event) -> dict[str, Any]:
    """The event's tool_input, {} when it is not a JSON object."""
    ti = ev.get("tool_input")
    return ti if isinstance(ti, dict) else {}


def launched(project: Project, agents: dict[str, Agent], ev: Event) -> tuple[bool, str | None]:
    """The PostToolUse of an Agent/Task call: ties the launch in its prompt to the new agent (in `agents`, held by
    the caller). Returns (tied?, warning): the warning, for a production type launched without a launch line."""
    from . import i18n

    ti = tool_input(ev)
    prompt = ti.get("prompt") if isinstance(ti.get("prompt"), str) else ""
    kind = ti.get("subagent_type") if isinstance(ti.get("subagent_type"), str) else None
    token = token_in(prompt)
    if not token:
        if kind and role(project, kind) == "production":
            aid = agent_id_of(ev.get("tool_response")) or "AGENT_ID"
            return False, i18n.for_project(project)["launch_line_missing"].format(type=kind, agent=aid)
        return False, None
    aid = agent_id_of(ev.get("tool_response"))
    if not aid:
        return False, None
    return link(project, agents, aid, token, "launch", agent_type=kind or "general-purpose"), None


def resolve_agent(agents: Mapping[str, Agent], ref: str) -> str:
    """The agent id for `ref` (a full id or a unique prefix); IdentityError otherwise."""
    if ref in agents:
        return ref
    hits = [k for k in agents if k.startswith(ref)]
    if len(hits) == 1:
        return hits[0]
    raise IdentityError(f"no agent {ref}" if not hits else f"{ref} matches {len(hits)} agents: give more of the id")


def name_hand(project: Project, ref: str, name: str, **fields: object) -> str:
    """`foreman agents --name ID NAME [--issue --sub --repo --branch --worktree]`: the identity by hand
    (named_by=hand). Returns the agent id; IdentityError when there is no such agent or another live one has the
    name."""
    from . import loop

    f = loop.agents_file(project)
    with util.locked_json(f, {}) as d:
        aid = resolve_agent(d, ref)
        others = [x for x in live_with_name(project, name) if x != aid]
        if others:
            raise IdentityError(f"a live agent is already called {name} ({others[0][:8]}…)")
        a = d[aid]
        a["name"] = name
        for k, v in fields.items():
            if v is not None and k in FIELDS:
                a[k] = str(v) if k == "worktree" else v
        a["named_by"] = "hand"
        util.write_json(f, d)
    return aid


REGISTERED = ("launch", "hand")
BRANCH_IN_TEXT = re.compile(r"(?:[Bb]ranch|[Rr]ama) `([\w./-]+)`")


def registered(h: Agent) -> bool:
    """Whether an agents.json entry carries an identity foreman recorded (`foreman launch`, `foreman agents
    --name`), not one guessed from its brief."""
    return h.get("named_by") in REGISTERED


def of(project: Project, h: Agent, head: str | None = None, branch_of: Mapping[str, str] | None = None) -> Identity:
    """An agent's identity: {name, issue, sub, repo, branch, worktree, production, registered, source}. What the
    entry carries wins; the name comes only from it (None without one); the rest is the fallback (module docstring):
    issue by «Issue: ID» in the brief, branch by `branch_of` (name → branch of its sub-issue in the backlog) or
    «Branch `y`», production by the type (role) or a production worktree path in the brief.
    `head` is the brief's text when the caller has it (else the head of the transcript is read once, if needed).
    `source` is where the name came from: named_by, or None (no name)."""
    text = [head]

    def brief() -> str:
        if text[0] is None:
            text[0] = transcript_head(h)
        return text[0]

    name = h.get("name") or None
    source = (h.get("named_by") or None) if name else None
    issue = h.get("issue") or None
    if not issue:
        body = project.section("ids").get("issue", r"[A-Z]{2}-\d+").strip("^$")
        m = re.search(rf"Issue:\s*((?:{body}))(?![\w-])", brief())
        issue = m.group(1) if m else None
    branch = h.get("branch") or None
    if not branch and name and branch_of:
        branch = branch_of.get(name)
    if not branch:
        m = BRANCH_IN_TEXT.search(brief())
        branch = m.group(1) if m else None
    production = h.get("production")
    if production is None:
        production = is_production(project, h, brief())
    return {
        "name": name,
        "issue": issue,
        "sub": h.get("sub") or None,
        "repo": h.get("repo") or None,
        "branch": branch,
        "worktree": h.get("worktree") or None,
        "production": bool(production),
        "registered": registered(h),
        "source": source,
    }


def cites_path(text: str | None, path: util.PathLike | None) -> bool:
    """Whether `text` names the folder `path` (as itself, not as the prefix of a longer name; `~` expanded; also as
    its resolved path and without macOS's `/private` in front, since git lists /private/var/… for /var/…)."""
    if not path:
        return False
    text = (text or "").replace("~", str(Path.home()))
    p = str(path).rstrip("/")
    forms = {p, os.path.realpath(p)}
    forms |= {f[len("/private") :] for f in forms if f.startswith("/private/")}
    return any(re.search(re.escape(f) + r"(?![\w-]|\.\w)", text) for f in forms if f)


# ---------- who is calling ----------
#
# The guard hook (PreToolUse on Bash) sees the agent_id of every Bash call and its command; the CLI sees only its
# session id (CLAUDE_CODE_SESSION_ID) and its own arguments. So the guard writes down the foreman calls that need a
# caller's name (record_call) in <state root>/callers.json: {tool_use_id: {at, session, agent, state, calls:
# [[project|null, sub, words…]], background}}; the PostToolUse of that Bash drops the entry (drop_call), so only the
# calls in progress stay (a background Bash stays until the 2 h pruning: it runs on after its PostToolUse). The CLI
# (who) matches its session and its arguments against them.

WHO_SUBS = ("progress", "lock", "renew")
CALLERS_MAX = 200
CALLERS_S = 7200
LOCK_DEFAULT = "FOREMAN_LOCK_WHO_DEFAULT"  # set by `lock` for its children when its own name was only the default


def callers_file() -> Path:
    from .config import state_root

    return state_root() / "callers.json"


def _wrapper_calls(cmd: str, depth: int = 0) -> list[tuple[Any, ...]]:
    """The `foreman lock RESOURCE run …` calls of a command line (pause.foreman_calls sees only the command a
    wrapper runs, not the wrapper itself)."""
    import shlex
    from pathlib import PurePath

    from . import pause, shell

    out: list[tuple[Any, ...]] = []
    for seg in shell.commands(cmd):
        try:
            w = shlex.split(seg, posix=True)
        except ValueError:
            w = seg.split()
        while w:
            while w and (re.match(r"^\w+=", w[0]) or w[0] in shell.WRAPPERS):
                w = w[1:]
            if len(w) > 2 and PurePath(w[0]).name == "foreman" and w[1] == "time-limit":
                w = w[3:]
                continue
            if len(w) > 2 and PurePath(w[0]).name in pause.SHELLS and w[1] == "-c" and depth < 3:
                out += _wrapper_calls(w[2], depth + 1)
                break
            call = shell.foreman_call(w)
            if not (call and call[1] == "lock" and len(call[2]) > 2 and call[2][1] == "run"):
                break
            out.append(call)
            w = call[2][3:]
    return out


def calls_of(cmd: str) -> list[list[Any]]:
    """[[project or None, sub, words…]] of the foreman calls in a command line that need a caller's name."""
    from . import pause

    if "foreman" not in cmd:
        return []
    found = pause.foreman_calls(cmd) + _wrapper_calls(cmd)
    return [[name, sub, *rest] for name, sub, rest in found if sub in WHO_SUBS]


def _prune_callers(d: dict[str, Any]) -> dict[str, Any]:
    now = time.time()
    live = {k: v for k, v in d.items() if isinstance(v, dict) and now - (v.get("at") or 0) <= CALLERS_S}
    return dict(sorted(live.items(), key=lambda kv: kv[1].get("at") or 0)[-CALLERS_MAX:])


def record_call(project: Project, ev: Event) -> None:
    """The guard's PreToolUse of a Bash: writes down its foreman calls that need a caller's name (the main thread's
    too, with agent null: the CLI must see two candidates when the orchestrator and an agent call at once). Writes
    only when the command has such a call. Never raises."""
    try:
        ti = tool_input(ev)
        cmd, tid, session = ti.get("command"), ev.get("tool_use_id"), ev.get("session_id")
        if not (isinstance(cmd, str) and tid and session):
            return
        calls = calls_of(cmd)
        if not calls:
            return
        f = callers_file()
        with util.locked_json(f, {}) as cur:
            d = _prune_callers(cur or {})
            caller: Caller = {
                "at": time.time(),
                "session": session,
                "agent": ev.get("agent_id") or None,
                "state": str(project.state),
                "calls": calls,
                "background": bool(ti.get("run_in_background")),
            }
            d[tid] = caller
            util.write_json(f, _prune_callers(d))
    except Exception:  # noqa: BLE001 (the guard never fails a call over this)
        pass


def drop_call(ev: Event) -> None:
    """The PostToolUse of a Bash: drops its entry (not a background one's). Reads the file only when the command
    names foreman. Never raises."""
    try:
        ti = tool_input(ev)
        cmd, tid = ti.get("command"), ev.get("tool_use_id")
        if not (tid and isinstance(cmd, str) and "foreman" in cmd):
            return
        f = callers_file()
        if not f.exists():
            return
        with util.locked_json(f, {}) as cur:
            d = cur or {}
            e = d.get(tid)
            if not isinstance(e, dict) or e.get("background"):
                return
            d.pop(tid)
            util.write_json(f, _prune_callers(d))
    except Exception:  # noqa: BLE001
        pass


def agent_name(state: util.PathLike, aid: str) -> str:
    """The name of agent `aid` in the agents.json under `state` (agent-<id6> for one without a name)."""
    h = (util.read_json(Path(state) / "agents.json", {}) or {}).get(aid) or {}
    return h.get("name") or f"agent-{aid[:6]}"


def recorded_caller(sub: str, call: list[Any]) -> tuple[str | None, str | int | None]:
    """Who runs this CLI by the calls the guard wrote down: (agent id, name) for an agent, (None, None) for the
    main thread or nothing written down, or "ambiguous" with the number of open calls when it cannot tell. `call`
    is [project or None, sub, words…], the CLI's own command line."""
    session = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not session:
        return None, None
    d = _prune_callers(util.read_json(callers_file(), {}) or {})
    open_ = [
        e
        for e in d.values()
        if e.get("session") == session and any(isinstance(c, list) and c[1:2] == [sub] for c in e.get("calls") or [])
    ]
    if not open_:
        return None, None

    def one(entries: list[Any]) -> Any:
        agents = {e.get("agent") for e in entries}
        return entries[0] if len(agents) == 1 else None

    hit = one(open_)
    if hit is None:
        same = [e for e in open_ if call in e.get("calls")]
        hit = one(same) if same else None
        if hit is None:
            return "ambiguous", len(open_)
    aid = hit.get("agent")
    return (aid, agent_name(hit.get("state") or "", aid)) if aid else (None, None)


def who(
    default: str, sub: str | None = None, call: list[Any] | None = None, warn: bool = True
) -> tuple[str, str | None, bool]:
    """The caller's name and agent id, one order for notes and lock (docs/lock-protocol.md «Who you are»):
    1. FOREMAN_LOCK_WHO (`foreman lock … run` passes its name to its children; not when that name was only the
       lock's default), 2. FOREMAN_WHO, 3. AGENT, 4. the call the guard wrote down (recorded_caller: needs `sub` and
       `call`; the main thread's call is the default), 5. `default`. An explicit name that differs from the recorded
       one wins, with a line on stderr; a recorded call that cannot be told apart gives the default and a line on
       stderr, never a guess. Returns (name, agent id or None, whether it is the default)."""
    import sys

    env = os.environ
    explicit = None
    if env.get("FOREMAN_LOCK_WHO") and not env.get(LOCK_DEFAULT):
        explicit = ("FOREMAN_LOCK_WHO", env["FOREMAN_LOCK_WHO"])
    else:
        explicit = next(((k, env[k]) for k in ("FOREMAN_WHO", "AGENT") if env.get(k)), None)
    aid = name = None
    if sub and call is not None:
        try:
            aid, name = recorded_caller(sub, call)
        except Exception:  # noqa: BLE001 (a broken callers.json is no reason to fail a progress line)
            aid, name = None, None
    if aid == "ambiguous":
        if explicit is None and warn:
            print(
                f"foreman: cannot tell which agent runs this ({name} calls open in this session): set FOREMAN_WHO",
                file=sys.stderr,
            )
        aid = name = None
    if explicit:
        if name and name != explicit[1] and warn:
            print(
                f"foreman: {explicit[0]}={explicit[1]} is used, but foreman knows this caller as {name}",
                file=sys.stderr,
            )
        return explicit[1], aid, False
    if isinstance(name, str) and name:  # (an int only with "ambiguous", already cleared)
        return name, aid, False
    return default, None, True
