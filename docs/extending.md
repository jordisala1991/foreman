# Extending foreman without changing it

How a product adapts foreman to its own needs without touching foreman's repository: what it can change, where, with
which contract, and what is not open and why. The details of each key are in docs/config.md; this page is the map, and the contract of a product's commands.
`examples/extend/` is a product that uses each layer, checked by the selftest.

## Three layers, one rule to choose

| what you want to change | layer | where it lives |
|---|---|---|
| **a value**: a threshold, a text, the name of an agent type, how loud a notice is | configuration | the product's `foreman.toml` (and the machine's registry) |
| **a behaviour**: check something, summarise a CI run, prepare a worktree, gate a merge | a command of the product, run by foreman at a fixed point | the product's home or repo, in any language |
| **what lives in Claude Code**: agent types, skills, hooks | Claude Code's own pieces | the repo's `.claude/`, or a companion plugin |

Pick the first layer that can do it. foreman never loads a product's code into itself: a command that fails or hangs
is cut and reported without taking foreman down, it may be written in any language, and foreman can reorganise its
own code without breaking anyone.

## 1. A value: configuration

Everything below is a key of `foreman.toml` (docs/config.md lists every key with its default). The ones products use
most to adapt foreman:

| to change | key |
|---|---|
| which agent types count as production or as planners | `[agents] production_types` (a plugin prefix matches the bare name) |
| how many agents and ready issues the loop asks for, when an agent counts as stalled | `[agents] production_slots`, `ready_target`, `stalled_min` |
| how often the loop runs, the free disk it warns at, when a held lock is long, when the session renews | `[loop] every_min`, `disk_low_gb`, `lock_long_min`, `context_rotate` |
| the texts foreman writes for people | `[strings]` (an unknown key is a warning) |
| the terms the owner knows (not flagged in what the owner reads) | `[project] owner_knows` |
| what is never run without the owner, what only the orchestrator runs | `[deny] patterns`, `message`, `[deny.grants]`; `[check] guard_table` to test them |
| shared resources and their locks | `[resources.*]` (a machine's: once, in the registry) |
| the panel's name, language and wiki | `[panel]` |
| recurring tasks and when they are late | `[[recurring]]` |
| how a merge goes | `[repos.*] merge`, `require_approval`, `risky_paths`, `public`, `remote_checks` |

**Where a value comes from.** A product's key is in its `foreman.toml`; a machine resource's lock keys (`dir`,
`busy`, `slots`…) are in the registry and win over the product's (docs/config.md, «Machine resources»); every other
key not written takes foreman's default. `foreman check` reads the file strictly (an unknown key fails it), and
`config_schema` says which foreman the file was written for, so «a typo» and «update foreman» are told apart
(docs/config.md, «Compatibility»).

## 2. A behaviour: a command at a fixed point

foreman runs a product's command at nine points. Each is a shell string in the configuration, run with `bash -c` in
its own process group, in the folder of its point, with a fixed time limit; past it the whole group is stopped. The
full contract (placeholders, variables, quoting, versioning) is below, «The contract».

| `FOREMAN_POINT` | key | when, and in which folder | placeholders | stdin | what counts | time limit |
|---|---|---|---|---|---|---|
| `loop.checks` | `[loop] checks` | every loop pass, in the home | — | — | each output line is a notice (JSON or plain text); rc 0 or 1; otherwise a `check_failed` notice | 30 s |
| `resource.busy` | `[resources.*] busy` | when `foreman lock` takes the lock (the caller's folder), and the panel every few seconds (the registry's folder or the home) | — | — | any output: the resource is in use outside the lock; cut, it counts as free | 5 s |
| `repo.local_ci` | `[repos.*] local_ci`, `ci_runs` | `foreman integrate`, once per run, in the worktree (the main checkout after a fast-forward) | `{wt} {main} {branch} {ci}` | — | rc 0 lets the integration go on | none |
| `repo.main_ci` | `[repos.*] main_ci` | without a remote, on main after the fast-forward, in the main checkout | as `local_ci` | — | rc 0; otherwise main goes back | none |
| `repo.ci_summary` | `[repos.*] ci_summary` | after each CI run, where it ran | as `local_ci` | the run's output | stdout: one bullet of the PR comment; a failure: «(ci_summary failed, rc N)» | 300 s |
| `repo.privacy_scan` | `[repos.*] privacy_scan` | before `foreman integrate` publishes a text (PR title, body, comment), in the home | — | the text | rc 0 lets it be published; otherwise (cut too) the integration stops | 300 s |
| `repo.pre_merge_report` | `[repos.*] pre_merge_report` | before merging, in the main checkout | `{wt} {main} {branch}` | — | stdout is shown (its rc and stderr under it) | 300 s |
| `repo.post_merge` | `[repos.*] post_merge` | after the merge, in the main checkout | `{main} {branch} {before} {head}` | — | its last line goes into the note; a failure is a warning, the merge stands | 600 s |
| `repo.post_create` | `[repos.*] post_create` | in each new worktree | `{main} {wt} {repo} {name} {branch}`, quoted | — | rc 0; otherwise the worktree is removed | 600 s |

Writing one:

- **Read what foreman gives, not foreman.toml.** Every command gets `FOREMAN_API`, `FOREMAN_POINT`, `FOREMAN_BIN`
  (call it for the same foreman version), `FOREMAN_PROJECT`, `FOREMAN_HOME`, `FOREMAN_STATE`, `FOREMAN_LANGUAGE`, and
  each value of its point as a variable (`FOREMAN_WT`, `FOREMAN_MAIN`, `FOREMAN_BRANCH`, `FOREMAN_CI`…). Anything
  else of the configuration comes from `"$FOREMAN_BIN" config --json`: every key resolved with its default, the
  paths absolute, and where each value comes from (below, «Reading the configuration»). A
  check that parses foreman.toml with defaults of its own breaks when foreman's change.
- **Prefer the variables to the placeholders.** `"$FOREMAN_WT"` needs no quoting rules; `{wt}` is quoted in
  `post_create` and written as is elsewhere. `${HOME}`, `{ cmd; }` and `awk '{print $1}'` reach bash as written; only
  a lowercase `{name}` the point knows is replaced, and an unknown one is an error before anything runs.
- **Check the version** when you rely on the contract: `[ "${FOREMAN_API:-0}" -ge 1 ]`. It goes up only when a
  change breaks a command written for the previous one (CHANGELOG, «Upgrade notes»); new variables or points do not
  raise it.
- **Stay within the time limit**; a long job belongs in the CI or behind `foreman lock`, not in a loop check.
- **A loop check that only informs** prints `"actionable": false` so the pass does not wake the orchestrator for it,
  and a `key` when its text changes for the same case (a size, a count). It cannot give one of foreman's notice kinds:
  such a line becomes a plain `check` notice. `foreman check --run-checks` runs every loop check once and warns
  about such lines (bad JSON, foreman's kinds, an `actionable` that is not a boolean) without marking anything.

Examples: `examples/extend/ops/checks/state-size.sh` (a loop check from the variables),
`examples/extend/ops/checks/worktrees.py` (a loop check from `config --json`) and `examples/extend/ops/ci-summary.sh`
(a CI summary), used by `examples/extend/ops/foreman.toml`; `examples/machine/gpu-busy.sh` (a resource's `busy`).

### The contract (`FOREMAN_API = 1`)

Every shell command foreman takes from the configuration runs the same way (lib/foreman/commands.py): `bash -c CMD`
in its own process group, in the folder of its point, stdin what its point gives or `/dev/null`, stdout and stderr to
files (a command that leaves a child alive on purpose, a server, does not make foreman wait for it). The points
are the table above.

- **Which folder each `[repos.*]` command runs in** (`path` is the repo's main checkout, `{wt}` the agent's worktree):
  `local_ci` and `ci_summary` in the worktree (`{wt}`) before the merge; with `remote = "none"` the same runs repeat
  in `path` after the fast-forward, where `{wt}` then means `path`; `main_ci` in `path`; `pre_merge_report` and
  `post_merge` in `path`; `post_create` in the new worktree; `privacy_scan`
  in the product's home (not the repo). Whatever a command wants relative to another folder it reaches through the
  placeholders (`{main}`, `{wt}`).
- **Environment**, on top of foreman's own: `FOREMAN_API` (`1`), `FOREMAN_POINT`, `FOREMAN_BIN` (the `bin/foreman`
  running it: call it to get the same version), and with a project `FOREMAN_PROJECT`, `FOREMAN_HOME`,
  `FOREMAN_STATE` (its state folder) and `FOREMAN_LANGUAGE`, so a `foreman` called from the command acts on the same
  project. Each value of the point is also a variable, unquoted: `FOREMAN_WT`, `FOREMAN_MAIN`, `FOREMAN_BRANCH`,
  `FOREMAN_CI`, `FOREMAN_NAME`, `FOREMAN_BEFORE`, `FOREMAN_HEAD`, and `FOREMAN_REPO` (the repo key) in every repo
  step. `busy` also gets the resource's `FOREMAN_LOCK_*`; `foreman lock` gives it `FOREMAN_API`,
  `FOREMAN_POINT`, `FOREMAN_BIN` and `FOREMAN_PROJECT` (the project that takes the lock), and its 5 s limit.
  A command never inherits an outer point's `FOREMAN_API`, `FOREMAN_POINT` or `FOREMAN_<NAME>` values (foreman
  called from inside a command, such as a CI that takes a lock): what it sees is its own point's. The command run
  under `foreman lock` keeps them: it is part of the outer command.
- **Placeholders**: a lowercase `{name}` the point knows is replaced (quoted with shell rules in `post_create`, as
  written in the others: `{ci}` may be several arguments). Everything else in braces reaches bash as written:
  `${HOME}`, `{ cmd; }`, `{a,b}`, `{}`, `awk '{print $1}'`. An unknown `{name}` outside single quotes (a typo, or a
  placeholder of another point) is an error before anything runs: exit 2 for `foreman integrate`, nothing made for
  `foreman worktree`. Inside single quotes it is left (`awk '{print}'`). The variables avoid quoting altogether:
  `cp -R "$FOREMAN_MAIN/vendor" "$FOREMAN_WT/"`.
- **Time limit**: past it, SIGTERM to the whole process group and SIGKILL 5 s later; no child survives. The limits
  are fixed (no key changes them). A command that ends on its own is never killed, nor what it leaves running.
- **Versioning**: `FOREMAN_API` goes up only when a change breaks a command written for the previous one (a
  variable renamed, an exit code that means something else), with an «Upgrade notes» entry in the CHANGELOG; new
  variables or points do not raise it. A command that needs more can check `[ "${FOREMAN_API:-0}" -ge 1 ]`.

### Reading the configuration: `foreman config --json`

`foreman config --json` (read-only) prints the project's configuration as foreman resolved it, so a product's own
command (a loop check, a CI step) reads the same values foreman uses instead of parsing foreman.toml with defaults of
its own, which break when foreman's change. From inside a command, `"$FOREMAN_BIN" config --json` reads the same
project and the same foreman (`FOREMAN_HOME` is set). `foreman config` without `--json` prints one
`section.key = value  (source)` line per key.

```text
{
 "foreman": "0.37.0", "api": 1,
 "name": "quayside", "home": "/…/ops", "config_file": "/…/ops/foreman.toml",
 "mode": "active", "registered": true,
 "config": {"project": {…}, "paths": {"notes": "/…", "state": "/…", "worktrees": "/…", …}, "loop": {…},
            "repos": {"web": {"path": "/…/quayside", "main": "main", …}}, "resources": {…}, "recurring": […], …},
 "sources": {"project": {"name": "foreman.toml", "owner": "default", …}, "paths": {"state": "environment", …},
             "repos": {"web": {"path": "foreman.toml", …}}, "resources": {"gpu": {"dir": "registry", …}},
             "recurring": "default", …},
 "warnings": []
}
```

- **`config`**: every section of docs/config.md with every key, a key left out of foreman.toml at its default.
  `[paths]` are absolute paths, the defaults resolved (`state` under the state root, `worktrees` beside the home),
  and so is each repo's `path`; every other value is as written (paths relative to the
  home). A machine resource is the merged table foreman uses (the registry's keys, the product's `guard` and `allow`).
- **`sources`**: the same shape, with where each value comes from: `foreman.toml`, `default` (foreman's, docs/config.md),
  `registry` (the keys of a machine resource, `[resources.NAME]` in the registry, and a value of the registry's
  `[machine]` settings), `registry+foreman.toml` (a list or table both the registry's `[machine]` and foreman.toml
  add to, such as `deny.patterns`) or `environment` (the default state folder under `FOREMAN_STATE_HOME`).
  `recurring` has one source for the whole list. A new value here is additive: `api` stays 1.
- **The rest**: `foreman` (its version), `api` (`FOREMAN_API`, the contract above), the project's `name`,
  `home`, `config_file` (the file it was read from: a registry entry's `config`, or the home's foreman.toml),
  `mode` and `registered` (`true` when it was loaded from its registry entry, whose `mode` it is; otherwise `active`),
  and `warnings` (what a tolerant read ignored: `foreman check` fails on them).

The keys and their meaning follow `config_schema`; a new key appears in `config` with its default. Example:
examples/extend/ops/checks/worktrees.py reads the worktrees folder and the repos from it.

## 3. Claude Code's own pieces

### An agent type of your own

foreman's agent types (`dev`, `planner`, `reviewer`, `mechanic`) come with its plugin, so Claude Code also names
them `foreman:dev`, `foreman:planner`, `foreman:reviewer` and `foreman:mechanic`. For a procedure of your own, **make a type with its own
name** and tell foreman it is production (or a planner):

```toml
[agents]
production_types = ["quayside-dev"]   # .claude/agents/quayside-dev.md in the repo
```

- Its body starts by sending the agent to foreman's procedure: «First read the agent procedure (`foreman docs
  agent`), the project's rules file named in your brief, and your issue (`foreman backlog show ID`)». Then only what
  is specific to the product. foreman's procedure keeps improving underneath it.
- Launch it as any agent: `foreman launch NAME --issue ID --sub Tn --repo R --branch B` prints the launch line, and
  the brief starts with it (docs/orchestrator.md); `subagent_type: "quayside-dev"`. A launch with a worktree records
  the agent as production whatever its type; `production_types` is what counts when an agent comes without one.
- `examples/extend/.claude/agents/quayside-dev.md` is one.

**Do not copy `dev` under the same name.** An agent file named `dev` in the project's `.claude/agents/` (or in the
user's) is a definition of higher priority than the plugin's (Claude Code: managed, then `--agents`, then the
project's, then the user's, then a plugin's). Claude Code runs the one it picks whole, never a mix, so a copy of
foreman's `dev.md` stops following foreman's procedure the day it changes, without a word. `foreman check` lists
every agent file of the home, of each repo and of the user's folder that defines one of foreman's types again
(«warning: … shadows foreman's agent type dev»). If you must, keep it to a short body that sends the agent to
`foreman docs agent` and adds only your own points; `foreman:dev` still names foreman's.

### Skills

A plugin's skills are named with its prefix (`/foreman:orchestrator-pass`), so they cannot be shadowed, only
completed: add your own skills in `.claude/skills/` and say in the project's rules file when to use them. The rules
file wins over foreman's procedures when they disagree (docs/agent.md, docs/orchestrator.md).

### Hooks: a guard of your own

Hooks from the user, the project (`.claude/settings.json`), plugins, skills and agents all run, side by side. Between
several `PreToolUse` hooks a `deny` wins over any `allow`, so a product adds a rule of its own without touching
foreman's guard and cannot weaken it: a hook that exits 2 (or answers `permissionDecision: "deny"`) blocks the call
with its reason. A rule that is only a pattern is simpler as `[deny] patterns`, which the guard table can test.

### A companion plugin

A product (or a team) can ship its agent types, skills and hooks as a plugin of its own that depends on foreman:

```json
{ "name": "quayside-tools", "dependencies": ["foreman"] }
```

- With `dependencies`, Claude Code does not load the plugin when foreman is not installed (it says «Dependency
  "foreman" is not installed»), so its agents never run without foreman's guard and procedures.
- Its agents are named with its own prefix (`quayside-tools:dev` is not `foreman:dev`); list the ones that are
  production in `[agents] production_types` (the prefix matches the bare name).
- Settings per product go in `foreman.toml`, not in the plugin's `userConfig` (stored per user, not per product). A
  hook that needs a `userConfig` value must not put `${user_config.KEY}` inside a shell-form command: Claude Code
  refuses to load that hook (the value would be parsed by the shell again), and `claude plugin validate` does not
  catch it. Use the exec form, or read the value from the environment.

### Agent teams and `isolation: worktree`

- **Agent teams** (experimental in Claude Code, off unless `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`): foreman does
  not use them; keep them off in the orchestrator's session. With teams on, every `Agent` call with a `name` starts
  a teammate instead of a subagent. foreman's guard still reaches a teammate (its hooks run in the lead session), but
  the hooks give the teammate's name as its agent type, so foreman records `quayside-dev` launched as `ana` as type
  `ana`: a launch line with a worktree still tells foreman it is production, an agent without one is not. A teammate also dies with
  the lead session and does not come back after `/resume`.
- **`isolation: worktree`** in an agent's header makes Claude Code create its own worktree, outside foreman: no
  `post_create`, no branch name from the plan, nothing in the backlog. Do not set it on production types; foreman
  makes the worktree (`foreman launch` / `foreman worktree`) and the agent works there.

## 4. What is not open, and why

- **Python plugins inside foreman.** A product's code in foreman's process would stop the guard or the loop when it
  fails, and would tie foreman's internal modules to every product. Commands and configuration do the same safely.
- **The text of the loop's notices and of the procedures.** They are foreman's protocol with the model. A product
  adds its own instructions in its rules file (`[project] rules`), which wins over the procedures.
- **The backlog model.** It is Linear's (docs/config.md, «The backlog model»); the panel and the pages rely on it.
- **Tabs or cards of a product's own in the panel.** Opening it would fix the panel's shape for good; a product has
  its wiki tab (`[panel] wiki`). Nor its own wording of the panel's texts: they are foreman's.
- **The guard to external commands.** A guard that runs someone else's code to decide fails open when that code does;
  a product's own rule is a `PreToolUse` hook of its own (above), and a `deny` there always wins.

A need none of the three layers covers is a change to foreman for everyone: open an issue rather than patch a copy.
