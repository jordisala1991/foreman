# foreman-next: design

One responsible agent per task, a loop without a model, a short guard, and a system that watches itself.
Nothing exists to keep a coordinator alive, because there is no coordinator.

## Pieces

```
            owner ──── panel (React, today's /next/ panel adapted) ── fm/panel.py (JSON API, local, key)
              │  └─── claude attach / agent view / Claude app: talks to any agent directly
              │
   backlog.json (schema 5) + state/ (events, guard log, grants, seen, ci, loop heartbeat)
              │
   fm loop (plain process, launchd KeepAlive) ── every N s, per registered project:
      sources → answers → agents → queue → health → launch
              │
   claude --bg -n NAME --settings {env, guard hook}   (one session per task, plus one per review round)
      defining agent  (cwd: the repo, read only)     → fm ready / fm ask / fm block / fm add
      responsible agent (cwd: its worktree, branch)  → subagents, fm ask, fm done / fm block
      reviewer (launched by the loop, read only)     → review file, fm verdict
```

| File | Lines | What |
|---|---|---|
| `fm/backlog.py` | ~190 | the backlog file (today's schema 5, new statuses and keys), questions tied to an issue and agent |
| `fm/loop.py` | ~250 | the loop and the launch of agents (briefs, worktrees) |
| `fm/sessions.py` | ~120 | `claude --bg` start / resume / list (from foreman's sessionlaunch.py) |
| `fm/integrate.py` | ~200 | check a delivered branch, risk score, autonomy level, accept (records), merge step |
| `fm/plan_gate.py` | ~90 | the plan gate: reads a plan's Declared risk, decides with the autonomy table whether the loop launches it or the owner approves it |
| `fm/describe.py` | ~50 | the report as a description: marked block, footer, folded review rounds (pure text) |
| `fm/risk.py` | ~80 | risk score and autonomy: pure functions of the facts a check found |
| `fm/providers/` | ~100 | one module per platform behind a small interface (`registry.py` picks it); `local` (git merge into the main checkout), `github` (pull requests with `gh`); `fm/pulls.py`: the loop's PR steps |
| `fm/guard.py` | ~150 | the PreToolUse hook: a handful of actions by name |
| `fm/health.py` | ~150 | checks that find broken states and file triage issues, deduplicated |
| `fm/costs.py`, `fm/metrics.py` | ~150 | cost from transcripts (from foreman's costs.py), per-task metrics |
| `fm/sources.py` | ~50 | the plug-in contract for tool signals (none shipped) |
| `fm/cli.py` | ~300 | `fm`: the agents' commands, the owner's commands, `loop`, `guard`, `panel` |
| `fm/briefs/*.md` | ~80 | the defining agent's (it plans), the responsible agent's and the reviewer's briefs (from foreman's planner, dev and reviewer) |
| `fm/panel.py`, `panel/src` | | the panel server and today's panel, adapted |

Python stdlib only (3.11+, `tomllib`); the panel is today's React/Vite source, built to `panel/dist`.

## A project

A *home* folder: `fm.toml` (the owner's settings), `backlog.json`, `state/`, `worktrees/`, optional `sources/`.
`fm.toml` points to the repo (`[project] repo`, `main`, `prefix`, `branch_prefix`). The registry
(`~/.config/fm/projects.json` or `$FM_REGISTRY`) lists the homes the loop and the panel serve. Every setting is per
project: `[agents] slots` (how many at once), models, extra session settings; `[integrate] autonomy`, `provider`, `merge`, `test`,
`sensitive`; `[guard] ask`; `[health]` timings.

## The states of a task

```
 triage ──go──▶ backlog ──loop──▶ refining ──fm ready──▶ ready ──loop──▶ in progress ──fm done──▶ in review
 (health,        (to define)      (defining agent)                       (responsible agent)      (queue: check,
  sources)                                                                      ▲                    risk score)
                                                                                └──── fm reject ─────┤
                                                                                                     ▼
                                                                                  done ◀── fm accept / autonomy,
                                                                                  then the merge step
 any open state ── fm cancel ──▶ canceled          `blocked` (a reason) on any state: the loop leaves it alone
```

- `triage`: filed by a health check or a source. Never launched until the owner says «go».
- `backlog`: to define. `refining`: its defining agent works. It ends with `fm ready --claim --criterion`.
- `ready`: has a goal and a done criterion. `in progress`: its responsible agent works, in its own worktree and branch.
- `in review`: the integration queue. The loop checks the branch; the owner accepts it or sends it back with a note.
- `blocked`: a reason set by an agent (`fm block`), by the loop (could not launch; stopped without finishing after a
  nudge). The owner clears it (`fm unblock [--relaunch]`).
- A question (`fm ask`) does not change the state: it is a decision tied to the issue and its agent. The agent ends
  its turn; the owner answers in the panel (or talks to the agent directly); the loop resumes *that* session with the
  answer.

## The loop

A plain process, no model (`fm loop`; `fm launchd` prints a job with `KeepAlive`); one per registry (a lock file).
Each tick, per project, each step isolated (an exception goes to `state/errors.jsonl`, which a health check turns
into a finding), and inside a step each issue isolated (a failure blocks that issue with the reason; the others go
on). The heartbeat is written before every step, so a long test run does not look like a dead loop.

1. **heartbeat**: `state/loop.json`.
2. **sources**: every executable in `<home>/sources/` prints JSON lines, each with a `key` (dedup). Without `task`,
   `{key, title, detail}` becomes a triage issue, deduplicated by source and key. With `task` (an issue id),
   `{key, task, action, text}` acts on that open task through the owner's own commands: `note` (text reaches its
   agent like an answer), `reject` (text; as `fm reject`), `accept` (as `fm accept`), `done` (closed as done, merged
   outside fm), `cancel` (as `fm cancel`). Applied once per source and key (`state/applied/`). Unknown or closed
   task, unknown action, missing key, or an action invalid for the task's state: nothing changes, logged in
   `state/errors.jsonl`, not retried. A transient failure (accept before its check, busy checkout, hung stop) is
   logged and the same key is tried again next tick.
   Cursors in `state/cursors/`. None shipped (the owner's call for now).
3. **answers**: each answered question, permission or rejection not yet delivered is sent to its agent's session
   (`claude --bg --resume SID … MESSAGE`), when that session is not busy; dropped if the task closed meanwhile;
   after 3 failed tries the task is blocked with the answer in the reason. A session id not listed yet at launch is
   filled in from its short id on a later tick.
4. **agents**: an agent whose session ended its turn without `fm done`/`ready`/`ask`/`block`, and whose transcript
   has been quiet for 10 minutes (so not while the owner is talking to it), is nudged once, then blocked.
5. **queue**: a branch in review whose head or main's head changed is checked: tests on exactly its committed head
   (a fresh detached worktree, never uncommitted files), diff, deleted files,
   dependencies, sensitive paths, conflicts with main, the agent's review verdict → risk `low|medium|high` with
   reasons. A branch that conflicts with main goes back to its agent with a note to merge main. If the project's
   autonomy level allows the risk, it merges (`git merge --no-ff` into the main checkout),
   removes the worktree and branch and closes the issue.
6. **health**: the checks below.
7. **launch**: while running agents < `slots` and launching is not paused, the next `ready` issue (its responsible
   agent) or `backlog` issue (its defining agent), by priority then id. If the issue changed while its session
   started (the owner cancelled it), the new session is stopped. The loop never plans and never reviews.

`fm cancel` stops the agent, closes its open questions and removes its worktree (the branch stays).

## The agents

Each is a Claude Code session of its own, named after its task (`tk-1`, `tk-3-define`), started with
`claude --bg -n NAME --model M --permission-mode bypassPermissions --settings S PROMPT` in its cwd:

- `S` sets `FM_HOME`, `FM_ISSUE`, `FM_AGENT` and `PATH` (so `fm` works), `worktree.bgIsolation: none`, the guard as
  a PreToolUse hook, and the project's extra settings (the demo turns off today's foreman plugin for its agents).
- The owner opens any of them: `claude attach SHORT`, `claude agents`, or the Claude app; what he says there goes to
  the agent that owns the task.

**Defining agent** (`fm/briefs/define.md`): reads the repo (read only), settles what the task means, asks the owner
only what only he can decide (`fm ask`, then ends its turn), may split into more issues (`fm add`), and ends with
`fm ready --claim --criterion --detail --plan PATH` (or `--no-plan "why trivial"`). It also reads the code and writes a
plan at `<home>/plans/ID.md` (files touched, steps, decisions with alternatives dropped, out of scope, risks, declared
risk); the issue gets `plan_file`, and the responsible agent follows it and `fm ask`s when it does not hold.

Plan gate (`fm/plan_gate.py`): the same `[integrate] autonomy` table (`risk.AUTONOMY`) governs plans and branches. A plan's risk
is read from its Declared risk line: none → low; new dependency / data-format change / size → medium; sensitive paths /
architecture / open decision → high; the highest wins; missing or unrecognised → high. `launch_free` launches a ready issue with
a plan only if the level covers that risk (recording `plan_approved` by the autonomy level) or the owner ran `fm approve-plan`
(panel: `/api/approve-plan`); `fm reject-plan --note` sends it back to `backlog`. `fm ready` clears the approval. Issues without a
plan launch as before. The issue view carries a computed `plan_pending`.

Every brief starts by pointing the agent at the project's `LESSONS.md`: what misled earlier agents, in their words
(each `--misled` text that is not «none» is appended; the owner prunes it).

**Defining agent** also: a criterion checkable by an agent alone (a table input → expected when there are cases),
scope claims cite their search, questions stand alone (context, options, recommendation and why).

**Responsible agent** (`fm/briefs/work.md`, from foreman's dev agent and agent procedure): owns the whole task in its
worktree: splits it, uses subagents, writes the failing test first for bugs and behaviour, keeps tests green, merges
main before delivering if it moved, asks the owner only what only he can answer, writes a report
(`<home>/reports/ID.md`: the task's description, each criterion line with its evidence; fm sets it as the PR body
inside `<!-- fm:description -->` markers, adds a footer with the commit and review state, folds its `## Review rounds`
section in `<details>`, and the panel shows it; the loop seeds the file from `fm/briefs/report.md`, or from the repo's
`.github/pull_request_template.md` plus a Review rounds section, when the agent launches) and ends with
`fm done --summary --report --misled`. If the review or the owner sends it back, the note arrives in the same
conversation: fix or refute each H and M with proof, add a `### Round N` to the report's «Review rounds» section, `fm done` again.

**Reviewer** (`fm/briefs/reviewer.md`, foreman's branch-review protocol): launched by the loop, not by the agent, as
its own session in the branch's worktree, once per delivered tip; it counts against the slots. Stance «not ready until
shown»; reads the criterion, then the diff, then the agent's report last; a table criterion line → met / not met /
not shown with evidence; findings F1… with H/M/L, confirmed or plausible, file:line, a scenario and the test that
would prove it. It writes `<home>/reviews/ID-rN.md` (header `Branch/Tip/Round/Open`) and runs
`fm verdict --high --medium --low --file`. Open H or M in round 1 → back to the responsible agent; round 2 checks
the fixes only; what is still open after round 2 goes to the owner as high risk. A reviewer that ends without a
verdict blocks the task.

What was dropped because it only served a coordinator: memory renewal, session marking, interface mods and band,
catch-up summaries, the pass as a message, plans written by someone else, sub-issues assigned from outside, the
orchestrator's skills. The responsible agent keeps: its brief, `fm`, its own subagents; the review is independent.

## Shared resources

`fm/lock.py`, the lean version of foreman's lock. A resource is declared in fm.toml (`[resources.NAME]`, optional
`slots = [...]` and `max_minutes`, default 20). `fm lock NAME run "what" command…` waits its turn, runs the command
holding the resource (with `FM_SLOT`), stops it at the time limit and always releases it; `fm lock NAME status`.

- Fair queue: first come, first served; a holder or waiter whose process died is cleaned up whenever anyone looks.
- Slots: one holder per slot, one queue.
- The state is common to the machine (`~/.local/state/fm/locks/NAME/`), so projects that declare the same name share
  it; every use is logged (`uses.jsonl`: who, task, what, waited, held, exit code, timeout) for the metrics
  (`lock_wait_min` per task) and the panel (System: who holds each resource and who waits).
- The responsible agent's brief lists the project's resources and the rule. No hook enforces it.

Left out from today's: the «busy» detector, short jobs jumping the queue, the turn file, the «who am I» chain, the
enforcing hook, machine tables merged with the project's.

## The guard

A reminder, not a cage (`fm/guard.py`). It runs only in agent sessions (the hook comes in `--settings`), looks at
the program each shell segment runs (never at words inside strings or messages), and denies a handful of actions
with a reason that says: do not look for another way; `fm ask --action NAME "what and why"`, end your turn. The
owner allows it once from the panel (`fm allow QID`); the loop tells the agent; it passes once.

| Action | Recognised by |
|---|---|
| `publish` | `npm/pnpm/yarn/cargo/uv/poetry publish`, `twine upload`, `docker push`, `gh release/pr/repo create|merge|edit|delete|upload` |
| `push-main` | `git push` to the main branch (named, `HEAD` while on it, or bare while on it), or with `--force`/`--mirror`/`--all` |
| `delete` | `rm`/`rmdir`/`unlink`/`find -delete` of a path outside its worktree and temp dirs; `git branch -d/-D` of another branch; `git worktree remove`; `git push --delete` |
| `config` | Edit/Write of the project's `fm.toml`; a shell command that writes it (`>`, `tee`, `sed -i`, `mv`, `cp`, `rm`) |
| owner's own | `[guard] ask = [{name, pattern}]` in fm.toml: a regex on a shell segment |

Everything else runs. A way around the list is not a hole to close: the review and the owner's accept before
integrating are the safety net. Each stop is logged (`state/guard.jsonl`) and counted in the metrics.

## Integration: risk and autonomy

Every delivered branch gets a risk score:

- **high**: no changes, tests fail, conflicts with main, sensitive paths (`[integrate] sensitive`), the independent
  review leaves H or M open after round 2,
  more than `medium_lines` lines.
- **medium**: more than `small_lines` lines, no test command, deleted files, dependency files, more than 8 files.
- **low**: the rest (small, tests green, review clean).

The project's `[integrate] autonomy` decides what merges without the owner: `manual` (nothing; the default),
`low`, `medium`. `high` always waits. The owner accepts in the panel (`fm accept`), or sends it back with a note
(`fm reject`, counted as rework).

### Provider, accepted → merged

Integration goes through a *provider* module (`fm/providers/`), one per platform; the rest of fm does not know which.
Interface: `publish` (open the change), `ready`, `comment`, `body` / `describe` (read and set the description and title; no-ops locally), `merge(change, strategy)` (the only merge
in fm: a future queue is just another strategy) and `state` (merged? closed?). Steps:

1. **accepted**: `fm accept` (or autonomy) only records `accepted` on the issue (it stays `in review`).
2. **merge**: the loop's queue step asks the provider to merge once, and stores the merge reference.
3. **merged**: the issue closes (`done`), and its worktree and branch go, only when `state` shows the merge in main,
   not when the command ran.

Config: `[integrate] provider = "auto"` (default: `github` if the repo's `origin` is on github.com, else `local`; or force
`local` | `github`) and `merge = "squash"` (default), `"merge"` (`--no-ff` locally) or `"rebase"` (github only). An
unknown value is a clear config error (at accept, and in the merge step). A merge never publishes: no tag, no release.

### GitHub provider: every task is a pull request

The agent commits and pushes its own task branch (the guard allows it); the **loop**, never the agent, opens, readies,
comments on and merges PRs through `gh` (the guard keeps stopping `gh pr` for agents). `[integrate] pr = "draft"`
(default): a task in progress gets a **draft PR** as soon as its first commit is pushed; `pr = "done"`: no PR until
`fm done`. In the queue, after the independent review: the branch must be pushed at its tip (otherwise it goes back to
its agent), the PR exists, and the loop waits while CI is pending. Then the check runs: **failing CI is high risk**, and
with no `[integrate] test` the PR's CI **replaces the local test**. Then the draft is marked **ready** and a comment
carries the risk, its reasons and the review's verdict. Accepted → `gh pr merge` with the configured type; the task
closes only when the PR is seen `MERGED` (a PR closed unmerged blocks it). Autonomy `low`/`medium` merges **only with
green CI** (no CI, pending or failing waits for the owner). After the merge the main checkout is fast-forwarded from
origin when it is clean on main (fetch only, never a push). Conflicts with main go back to the agent as before. Out of
scope: reading approvals or comments from GitHub, merge queues, other platforms.

## Architecture

Hexagonal-lite: a small core, ports only where something external varies, everything else outside. Not Clean Architecture
nor full DDD; what is shared is a vocabulary: *task* (an issue in the backlog), *round* (one review pass), *risk* (low,
medium, high), and *accept* (a record of the owner's decision) is **not** *merge* (the provider's act).

Layers, outside in (`tach.toml`; a module imports its own layer and the ones below, never above):

| Layer | Modules | Role |
|---|---|---|
| ui | `cli*`, `panel*` | the owner's and the agents' entry points; call the loop and below |
| loop | `loop` | the process that ticks: does the I/O, calls the core |
| steps | `sources`, `talk`, `health`, `metrics` | one step of a tick, or a view over several pieces |
| services | `agents`, `integrate`, `pulls`, `sessions`, `lock` | the outside world: git, gh, claude, processes |
| registry | `providers/registry` | picks the adapter (by convention the only caller of the adapters; not enforced by tach) |
| adapters | `providers/github`, `providers/local` | one per platform; they see the port, never the loop or each other |
| ports | `providers/__init__` (the `Provider` interface) | |
| core | `risk`, `guard`, `backlog`, `costs`, `config`, `util` | vocabulary and pure rules; `risk` (score, autonomy) has no I/O at all |

Ports: only where there is an external system or two real implementations: `providers/` (local, github), and the
launcher and source plug-ins (`sources`) when they exist. Not behind a port, because they do not vary: `backlog.json`,
the config, the clock. A new port needs a second real implementation or a system outside the process; say which in the
task.

Rules (enforced by `make check`):
- The core imports nothing above it. **One module runs programs**: `fm/proc.py` (`run`, `run_json`, `spawn`; it knows
  nothing of git, gh or claude; a missing program or a timeout is a `ProcError`). Everything else calls it; ruff bans
  `import subprocess` elsewhere (not `os.system`/`os.popen`). `proc` sits just above the core in the layers: the loop,
  the services and the adapters use it, the core does not. The only other exceptions in `per-file-ignores`
  (`pyproject.toml`) are the tests and `fm/lock.py`, which needs a process group and signals to stop a whole job at its
  time limit (`start_new_session`, `killpg`), which `spawn` deliberately does not offer.
- Data from outside is typed at the border: `providers/github.py` turns `gh`'s JSON into its own types
  (`PullRequest`, the check buckets) with a small converter per shape, and a shape that does not fit is a
  `ProviderError`; only those types go inward, never `gh`'s dicts.
  The ratchet also counts `pylint: disable`, `pyright: basic|off` and `fmt: off|skip`.
- An adapter imports neither the loop nor another adapter (tach).
- Pure decisions (risk, autonomy, state rules) go in the core as functions of their inputs; the loop and the services
  gather the facts (git, gh, claude) and apply the result. New decisions go there, not into `integrate` or `loop`.
- Files stay under 300 lines (`make size`, no exceptions): a module that grows is split along these layers.
- Suppressions do not work: `make lint` runs ruff with `--ignore-noqa`, pyright has `enableTypeIgnoreComments = false`, and `make suppressions` fails on `pyright: ignore` and `pylint: disable`.
- Loosening a rule (`tach.toml`, the ruff ignores, `pyproject.toml`, the `suppressions` target) is the owner's call.

The panel's TypeScript is out of this (a dependency check there can come later).

## Watching itself

Metrics per task (`fm metrics`, the panel): cost of its agents (transcripts at list prices; plan_usd of define sessions and work_usd of work sessions, from `session_kinds`; review rounds), time to the queue and to
done, rework (sent back), nudges (stopped without finishing), questions to the owner, guard stops, risk, and what
misled its agents (their `--confused` words).

Health checks (`fm/health.py`), each finding a triage issue, deduplicated by key while open or while it persists:
loop dead (heartbeat older than 3 ticks; from outside the loop: `fm health`, the panel), agent stuck (working, its
transcript silent for `stuck_minutes`), agent blocked (stopped after a nudge, or could not launch), orphan worktree,
tests failing on main (the test command on main's head, when it moves or every `ci_every_minutes`), loop step errors.
