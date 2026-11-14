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
      sources → answers → agents → queue → health → blockers → launch
              │
   claude --bg -n NAME --settings {env, guard hook}   (one session per task, plus one per review round)
      planning agent  (cwd: the repo, read only)     → fm ready / fm ask / fm block
      responsible agent (cwd: its worktree, branch)  → subagents, fm ask, fm done / fm block
      hunter, then reviewer (launched by the loop, read only) → hunter file, review file, fm verdict
```

| File | Lines | What |
|---|---|---|
| `fm/backlog.py` | ~190 | the backlog file (today's schema 5, new statuses and keys), questions tied to an issue and agent |
| `fm/loop.py` | ~250 | the loop and the launch of agents (briefs, worktrees) |
| `fm/sessions.py` | ~120 | `claude --bg` start / resume / list (from foreman's sessionlaunch.py) |
| `fm/integrate.py` | ~200 | check a delivered branch, risk score, autonomy level, accept (records), merge step |
| `fm/describe.py` | ~50 | the report as a description: marked block, footer, folded review rounds (pure text) |
| `fm/subtasks.py` | ~35 | subtasks: which belong to a parent, listing order, when a subtask may be filed, how the parent closes (pure) |
| `fm/blockers.py` | ~50 | which tasks a task waits for (`blocked_by`): pending, dead, refusals incl. cycles (pure) |
| `fm/release.py` | ~25 | the loop's blockers step: marks a task unblocked once, or blocks it when a blocker closed undone |
| `fm/risk.py` | ~80 | risk score and autonomy: pure functions of the facts a check found |
| `fm/providers/` | ~100 | one module per platform behind a small interface (`registry.py` picks it); `local` (git merge into the main checkout), `github` (pull requests with `gh`); `fm/pulls.py`: the loop's PR steps |
| `fm/guard.py` | ~150 | the PreToolUse hook: a handful of actions by name |
| `fm/health.py` | ~150 | checks that find broken states and file triage issues, deduplicated |
| `fm/diagnosis.py`, `fm/mechanic.py` | ~100 | the mechanic: required sections of a diagnosis and how it is stored; the tick step that launches, times out and stops mechanics |
| `fm/costs.py`, `fm/metrics.py` | ~150 | cost from transcripts (from foreman's costs.py), per-task metrics |
| `fm/sources.py` | ~50 | the plug-in contract for tool signals (none shipped) |
| `fm/cli.py` | ~300 | `fm`: the agents' commands, the owner's commands, `loop`, `guard`, `panel` |
| `fm/briefs/*.md` | ~80 | the planning agent's, the responsible agent's and the reviewer's briefs (from foreman's planner, dev and reviewer) |
| `fm/panel.py`, `panel/src` | | the panel server and today's panel, adapted |

Python stdlib only (3.11+, `tomllib`); the panel is today's React/Vite source, built to `panel/dist`.

## A project

A *home* folder: `fm.toml` (the owner's settings), `backlog.json`, `state/`, `worktrees/`, optional `sources/`.
`fm.toml` points to the repo (`[project] repo`, `main`, `prefix`, `branch_prefix`). The registry
(`~/.config/fm/projects.json` or `$FM_REGISTRY`) lists the homes the loop and the panel serve. Every setting is per
project: `[agents] slots` (how many at once), models, extra session settings; `[integrate] autonomy`, `provider`, `merge`, `test`,
`[risk.high]`, `[risk.medium]` (label → path patterns); `[guard] ask`; `[health]` timings.

## The states of a task

```
 triage ──go──▶ backlog ──loop──▶ refining ──fm ready──▶ ready ──loop──▶ in progress ──fm done──▶ in review
 (health,        (to plan)        (planning agent)                       (responsible agent)      (queue: check,
  sources)                                                                      ▲                    risk score)
                                                                                └──── fm reject ─────┤
                                                                                                     ▼
                                                                                  done ◀── fm accept / autonomy,
                                                                                  then the merge step
 any open state ── fm cancel ──▶ canceled          `blocked` (a reason) on any state: the loop leaves it alone
```

- `triage`: filed by a health check, a source, or without acceptance criteria (the only state such a task can be in).
  Never launched until the owner says «go»; `fm go` refuses a task without acceptance criteria.
- `backlog`: to plan (it has acceptance criteria, set with the owner). `refining`: its planning agent works. It ends
  with `fm ready --plan|--no-plan`.
- `ready`: has acceptance criteria and a plan (or none, if trivial). `in progress`: its responsible agent works, in its own worktree and branch.
- `in review`: the integration queue. The loop checks the branch; the owner accepts it or sends it back with a note.
- `blocked`: a reason set by an agent (`fm block`), by the loop (could not launch; stopped without finishing after a
  nudge). The owner clears it (`fm unblock [--relaunch]`).
- `blocked_by` (`fm add/edit --blocked-by FN-n …`; `none` clears): the task is never planned or launched until every
  blocker is `done`, then the loop marks it unblocked in the history. A canceled or duplicate blocker blocks the task
  with a reason: the owner edits `--blocked-by` or cancels, then `fm unblock`.
- `fm edit` changes title, claim, criteria, detail, model, effort or blockers of a task in triage, backlog or ready (never one with an agent,
  nor a closed one); criteria or detail on a backlog or ready task drops its plan and sends it back to backlog.
- A task too big for one PR is a parent with subtasks (`fm add --parent FN-n`, only the owner, only while the parent is
  in triage or backlog; one level). A subtask is an ordinary task (own agent, branch, PR) that names its parent; the
  parent never gets an agent and closes by itself when every subtask has (done if any is done, else canceled).
  `fm cancel` of a parent cancels its open subtasks and closes their PRs.
- A question (`fm ask`) does not change the state: it is a decision tied to the issue and its agent. The agent ends
  its turn; the owner answers in the panel (or talks to the agent directly); the loop resumes *that* session with the
  answer.

## The loop

A plain process, no model (`fm loop`; `fm launchd` prints two jobs, loop and panel, with `RunAtLoad` and `KeepAlive`; `--dir` writes them as files. Agents use the normal claude.ai login, so they show in the Claude app; only while it does not work (`claude auth status`) the loop puts the Keychain token in their environment, never in the plist); one per registry (a lock file).
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
   (`claude --bg --resume SID MESSAGE`, no other flag: a flag or a live process makes claude start a copy), when that
   session is not busy: the idle session is stopped, fm waits until its process is gone (+3 s), then resumes; at most
   one answer per task per tick; a copy (different short id) or an unreadable `claude agents` fails the send; dropped if the task closed meanwhile;
   a failed try is retried next tick; the answer stays not delivered and `fm health` shows it after 10 minutes. A session id not listed yet at launch is
   filled in from its short id on a later tick.
4. **agents**: an agent whose session ended its turn without `fm done`/`ready`/`ask`/`block`, and whose transcript
   has been quiet for 10 minutes (so not while the owner is talking to it), is nudged once, then blocked.
5. **queue**: accepted tasks are merged first. Then a branch in review whose head or main's head changed is checked,
   one at a time, in the background (a tick waits for it only `BACKGROUND_WAIT_S`, so answers, launches and merges keep
   flowing; each task is read again just before its turn): tests on exactly its committed head
   (a fresh detached worktree, never uncommitted files), diff, deleted files,
   dependencies, the paths it touches, conflicts with main, the agent's review verdict → risk `low|medium|high` with
   reasons. A branch that conflicts with main goes back to its agent with a note to merge main. If the project's
   autonomy level allows the risk, it merges (`git merge --no-ff` into the main checkout),
   removes the worktree and branch and closes the issue.
6. **health**: the checks below; the tests of main run in the background the same way (the tick waits for them
   only `BACKGROUND_WAIT_S`; the result lands in `ci.json`).
7. **blockers**: a task whose blockers are all `done` is marked unblocked once; one whose blocker closed undone is blocked
   with a reason (`fm/release.py`).
8. **launch**: while running agents < `slots` and launching is not paused, the next `ready` issue (its responsible
   agent) or `backlog` issue (its planning agent), by priority then id, skipping any with a blocker not `done`. If the issue changed while its session
   started (the owner cancelled it), the new session is stopped. The loop never plans and never reviews.
9. **mechanic**: for each triage task health filed (`source` `health:*`) that has none yet, one mechanic session, in the
   slots launch left free (it counts as a slot while it has no diagnosis). It stops once the diagnosis is in, and a mechanic
   that ended without one or ran over 30 minutes leaves a «Diagnosis missing» note; it is never relaunched.

`fm cancel` stops the agent, closes its open questions and removes its worktree (the branch stays).

## The agents

Each is a Claude Code session of its own, named after its task (`tk-1`, `tk-3-define`), started with
`claude --bg -n NAME --model M --effort E --permission-mode bypassPermissions --settings S PROMPT` in its cwd:

- `M` and `E` are the project's, except for the responsible agent: the task's own `model`/`effort` (optional issue keys, chosen by the definer with the rubric in `define.md`) win.
- `S` sets `FM_HOME`, `FM_ISSUE`, `FM_AGENT` and `PATH` (so `fm` works), `worktree.bgIsolation: none`, the guard as
  a PreToolUse hook, and the project's extra settings (the demo turns off today's foreman plugin for its agents).
- The owner opens any of them: `claude attach SHORT`, `claude agents`, or the Claude app; what he says there goes to
  the agent that owns the task.

**Planning agent** (`fm/briefs/define.md`): the acceptance criteria are product (observable behaviour, never how) and
are settled with the owner when the task is filed; the planning agent never writes, rewrites or splits them, and `fm ask`s
the owner if one cannot be met as written. It reads the repo (read only), asks the owner only what only he can decide
(`fm ask`, then ends its turn), and ends with `fm ready --detail --plan PATH` (or `--no-plan "why trivial"`). It writes a
plan at `<home>/plans/ID.md` (files touched, steps, decisions with alternatives dropped, out of scope, risks, declared
risk); the issue gets `plan_file`, and the responsible agent follows it and `fm ask`s when it does not hold.

Every brief starts by pointing the agent at the project's `LESSONS.md`: what misled earlier agents, in their words
(each `--misled` text that is not «none» is appended; the owner prunes it).

**Planning agent** also: scope claims cite their search, questions stand alone (context, options, recommendation and why).

**Responsible agent** (`fm/briefs/work.md`, from foreman's dev agent and agent procedure): owns the whole task in its
worktree: splits it, uses subagents, writes the failing test first for bugs and behaviour, keeps tests green, merges
main before delivering if it moved, asks the owner only what only he can answer, writes a report
(`<home>/reports/ID.md`: the task's description, each acceptance criterion with its evidence; fm sets it as the PR body
inside `<!-- fm:description -->` markers, adds a footer with the commit and review state, folds its `## Review rounds`
section in `<details>`, and the panel shows it; the loop seeds the file from `fm/briefs/report.md`, or from the repo's
`.github/pull_request_template.md` plus a Review rounds section, when the agent launches) and ends with
`fm done --summary --report --misled`. If the review or the owner sends it back, the note arrives in the same
conversation: each finding gets the smallest change that settles it, or a one-line answer if the agent thinks it is wrong; add a `### Round N` to the report's «Review rounds» section, `fm done` again.

**Review in two passes** (`fm/briefs/hunter.md`, `fm/briefs/reviewer.md`): launched by the loop, not by the agent, each as
its own session in the branch's worktree, once per delivered tip; each counts against the slots (the hunter until its file exists, so its own reviewer can take its place). Both get the task's detail
and the owner's notes. The **hunter** lists everything it suspects (with a confidence each, plus the acceptance criteria table and an
inventory of what the diff adds) in `<home>/reviews/ID-rN-<tip>-hunter.md`. When that file exists and the hunter has ended,
the **reviewer** starts; it sees only the file, never the hunter's reasoning. It checks each Wrong or Missing finding
(confirmed, refuted, or neither) and each Too much item; a finding leaves only with a cited line or output that refutes it,
so what it could not refute stays in To fix. What was dropped goes under Noted and is not sent to the worker. It writes
`<home>/reviews/ID-rN-<tip>.md` (header `Branch/Tip/Round/Open`, N = items under To fix) and runs
`fm verdict --tip --open N --file`; a verdict for a tip other than the newest launch is stored in the review history but
decides nothing. There are no severities. Open findings in round 1 → back to the responsible agent (`risk.review_step`);
round 2 checks the fixes only and opens nothing new unless a fix is itself Wrong or Too much; what is still open after
round 2 goes to the owner as high risk. A hunter that ends without its file, or a reviewer without a verdict, blocks the task.

The **mechanic** (`mechanic` kind, `issue.mechanic`) diagnoses a health task and changes nothing else: read-only by its
brief (as the hunter's), its only write is `fm diagnose --file`, which checks the required `##` headings (`fm/diagnosis.py`)
and appends the diagnosis to the task's `detail`, so `fm show` has it. The task stays in triage; the owner decides next.

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

- **impact**, from what the change touches: high = paths in `[risk.high]`; medium = paths in `[risk.medium]` and deleted
  files; low = the rest. A review never lowers it.
- **doubt**, from the evidence: high = no changes, tests fail, CI fails, conflicts with main; medium = no test command, or a large change: over `[risk] large_lines` (400) changed lines, not counting tests, generated and lock files; size never makes a change high by itself.
- Risk is the higher of the two. It shows up to 3 reasons, high ones first, each with its source (`[diff]`, `[tests]`,
  `[ci]`, `[git]`, `[review]`). Size is only the large-change reason; file counts are not a reason.
- Open review findings are a gate, not a level: they go back to the agent and add a `[review]` reason, but never change the risk.
  After the last round, an open finding never merges alone, at any autonomy or risk (`integrate.merges_alone`).

The project's `[integrate] autonomy` decides what merges without the owner: `manual` (nothing; the default),
`low`, `medium`. `high` always waits. Where the provider `has_ci` (github yes, local no), autonomy merges only on a green CI. The owner accepts in the panel (`fm accept`), or sends it back with a note
(`fm reject`, counted as rework).

### Provider, accepted → merged

Integration goes through a *provider* module (`fm/providers/`), one per platform; the rest of fm does not know which.
Interface: `pushed` (is the branch on the platform at its tip?) and `push` (put it there: never forced, never main, 20 s; any failure is `Transient` with the platform's error; a no-op locally), `publish` (open the change), `ready`, `comment`, `body` / `describe` (read and set the description and title; no-ops locally), `merge(change, strategy)` (the only merge
in fm: a future queue is just another strategy) and `state` (merged? closed?). Steps:

1. **accepted**: `fm accept` (or autonomy) only records `accepted` on the issue (it stays `in review`).
2. **merge**: the loop's queue step asks the provider to merge once, and stores the merge reference.
3. **merged**: the issue closes (`done`), and its worktree and branch go, only when `state` shows the merge in main,
   not when the command ran.

Retry policy, one place (`loop.guarded`, every provider call: push, PR, CI, merge): a `Transient` failure is retried next tick and counted per task (`retry_count`, next to `retry_error`); the 3rd in a row blocks the task with the last error; a success resets both.

Config: `[integrate] provider = "auto"` (default: `github` if the repo's `origin` is on github.com, else `local`; or force
`local` | `github`) and `merge = "squash"` (default), `"merge"` (`--no-ff` locally) or `"rebase"` (github only). An
unknown value is a clear config error (at accept, and in the merge step). A merge never publishes: no tag, no release.

### GitHub provider: every task is a pull request

The agent only commits; the **loop** pushes the task branches (`provider.push`: `git push origin refs/heads/<b>:refs/heads/<b>`,
never forced, never main, 20 s timeout; a failure is `Transient` with git's error) and, never the agent, opens, readies,
comments on and merges PRs through `gh` (the guard keeps stopping `gh pr` for agents). `[integrate] pr = "draft"`
(default): a task in progress has its commits pushed and gets a **draft PR** as soon as it has its first commit; `pr = "done"`: no PR until
`fm done`. In the queue, after the independent review: the branch is pushed at its tip if it is not yet, the PR exists, and the loop waits while CI is pending. Then the check runs: **failing CI is high risk**, and
with no `[integrate] test` the PR's CI **replaces the local test**. Then the draft is marked **ready** and a comment
carries the risk, its reasons and the review's verdict. Accepted → `gh pr merge` with the configured type; the task
closes only when the PR is seen `MERGED` (a PR closed unmerged blocks it). Autonomy `low`/`medium` merges **only with
green CI** (no CI, pending or failing waits for the owner). After the merge the main checkout is fast-forwarded from
origin when it is clean on main (fetch only, never a push). Conflicts with main go back to the agent as before, also when the merge itself is refused for a conflict after acceptance: the acceptance is dropped and the new tip is checked and accepted again. Out of
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
| steps | `sources`, `talk`, `mechanic`, `health`, `metrics` | one step of a tick, or a view over several pieces |
| services | `agents`, `integrate`, `pulls`, `sessions`, `lock` | the outside world: git, gh, claude, processes |
| registry | `providers/registry` | picks the adapter (by convention the only caller of the adapters; not enforced by tach) |
| adapters | `providers/github`, `providers/local` | one per platform; they see the port, never the loop or each other |
| ports | `providers/__init__` (the `Provider` interface) | |
| core | `risk`, `subtasks`, `blockers`, `diagnosis`, `guard`, `backlog`, `costs`, `config`, `util` | vocabulary and pure rules; `risk` (score, autonomy) has no I/O at all |

Ports: only where there is an external system or two real implementations: `providers/` (local, github), and the
launcher and source plug-ins (`sources`) when they exist. Not behind a port, because they do not vary: `backlog.json`,
the config, the clock. A new port needs a second real implementation or a system outside the process; say which in the
task.

Rules (enforced by `make check`):
- The core imports nothing above it. **One module runs programs**: `fm/proc.py` (`run`, `run_json`; it knows
  nothing of git, gh or claude; a missing program or a timeout is a `ProcError`). Everything else calls it; ruff bans
  `import subprocess` elsewhere (not `os.system`/`os.popen`). `proc` sits just above the core in the layers: the loop,
  the services and the adapters use it, the core does not. The only other exceptions in `per-file-ignores`
  (`pyproject.toml`) are the tests and `fm/lock.py`, which needs a process group and signals to stop a whole job at its
  time limit (`start_new_session`, `killpg`).
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

Metrics per task (`fm metrics`, the panel): the model and effort its responsible agent ran on, cost of its agents (transcripts at list prices; plan_usd of define sessions and work_usd of work sessions, from `session_kinds`; review rounds), time to the queue and to
done, rework (sent back), nudges (stopped without finishing), questions to the owner, guard stops, risk, and what
misled its agents (their `--confused` words).

Health checks (`fm/health.py`), each finding a triage issue, deduplicated by key while open or while it persists:
loop dead (heartbeat older than 3 ticks; from outside the loop: `fm health`, the panel), agent stuck (working, its
transcript silent for `stuck_minutes`), agent blocked (stopped after a nudge, or could not launch), orphan worktree,
tests failing on main (the test command on main's head, when it moves or every `ci_every_minutes`; one task per failing
episode, from the first failing run until a pass, with the commit, the failing step and the output tail), loop step errors.
Every finding has three parts: what it means, the evidence fm gathered, and a hypothesis marked pending; none proposes a fix.
