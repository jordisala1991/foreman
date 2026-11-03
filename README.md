# foreman

foreman lets one Claude Code session run a software project's backlog for you: it plans the work, launches
short-lived agents that each work on their own branch in their own worktree, checks and merges what they deliver,
and asks you only what is yours to decide.

**The problem it solves.** One long Claude Code session doing everything forgets, drifts and needs watching; many
sessions by hand need you to keep track of who does what, which branch is ready and what must never run without
you. foreman gives that orchestrator session the pieces that do not depend on your project: a backlog with a
done criterion per issue, a worktree and a branch per agent, integration through your CI (a pull request, or a
fast-forward without a remote), a queue for shared resources (a GPU, a simulator), a guard that refuses what you
said never happens without you (deploys, releases, secrets), a timed loop that wakes the orchestrator, and a web
panel where you follow the work and answer its questions. It does nothing in folders you have not set up; in a
project you have, its guard watches every Claude Code session, yours included.

## Requirements

- **Claude Code 2.1.299 or newer** (`claude --version`; `claude update` to upgrade).
- **macOS or Linux.**
- **Python 3.9 or newer**, standard library only. On macOS the one that comes with the Command Line Tools is enough
  (`xcode-select --install` if `python3` is missing).
- **git.** `gh` too if your repository is on GitHub and you want foreman to open and merge pull requests.

## Install

foreman is a Claude Code plugin; its GitHub repository is also its marketplace. In a Claude Code session:

```text
/plugin marketplace add https://github.com/jordisala1991/foreman.git#release
/plugin install foreman@foreman
```

The `release` branch only moves from one released version to the next (CHANGELOG.md says what each one changes).
Then open a new session, or type `/reload-plugins`. The `foreman` command is on the PATH of Claude Code's Bash tool
only, not of your own terminal: ask Claude to run `foreman --version` to check it worked (in a terminal, call the
plugin's `bin/foreman`).

**Updating.** `/plugin marketplace update foreman`, then `/plugin update foreman@foreman`. Sessions already open
keep the version they started with until you type `/reload-plugins` in them; foreman tells you when one is behind.

## Getting started

Open a Claude Code session in your project's folder and type:

```text
/foreman:setup
```

A short conversation in your language sets foreman up: a few questions (the project's name, where the code is, the
command that checks it, how many agents at once), then foreman's folder for the project (its settings,
`foreman.toml`, its rules, the backlog, notes and reports), the web panel if you want it, a check that all is well,
and a first guided pass of the session that runs your project. Nothing is written without your yes, and you do not
need to read anything first.

Near the end it asks you to open a **new** session in the project's foreman folder and type `/foreman:setup` there
again: that session becomes the orchestrator, the one that runs your project. Keep it open; you talk to it in your
own words («note down that…», «what happened?», «what do I have to decide?») or use the panel, and it runs a pass of
its loop every hour (`[loop] every_min`) without you scheduling anything. The agents it launches are Claude Code
subagents: they use your Claude Code plan or API usage like any other session.

## How it works, in short

- **The orchestrator** is a Claude Code session marked as such (`foreman session`). Every loop pass it reads one
  short summary of what changed (`foreman loop`), launches agents for ready issues, integrates delivered branches
  and keeps the backlog and the notes current (docs/orchestrator.md).
- **Agents** are Claude Code subagents of foreman's types (`foreman:dev`, `foreman:planner`, `foreman:reviewer`,
  `foreman:mechanic`), each with a launch line from `foreman launch`, one worktree and one branch. They deliver with a
  report and `foreman progress --done` (docs/agent.md).
- **The guard** is a hook on every tool call in a set-up project: it refuses what `foreman.toml`
  `[deny]` lists, makes shared resources wait in their queue (`foreman lock`), and keeps foreman's own files and
  your git configuration out of the agents' reach. `foreman permissions` shows what it refuses, in plain words.
- **The panel** (`foreman panel`, http://127.0.0.1:8766) shows every set-up project: backlog, agents, locks,
  branches, questions for you, inbox. It asks for its key once (in `~/.local/state/foreman/panel/token`).
  `foreman panel service install` keeps it running as a user service.
- **Your settings** live in the project's `foreman.toml` (docs/config.md); `foreman check` validates them.

## Commands

`foreman [--project NAME] COMMAND …`; `foreman COMMAND --help` for each one's arguments, `foreman --help` for all.
You rarely type them: the orchestrator and the agents do.

<!-- commands: generated from the parser (lib/foreman/cli.py, COMMAND_GROUPS); tests/test_docs_tied.py checks it -->
**Set up and check**

| Command | What it does |
|---|---|
| `init` | make a new project's home and register it (foreman.toml, rules, backlog) |
| `check` | validate foreman.toml, the backlog and the guard table |
| `config` | the resolved configuration, with the source of each value (read-only) |
| `permissions` | what the guard refuses, in plain bands; --candidate: what a new foreman.toml would change; apply FILE: test it and put it in place; grants: the owner's grants |
| `guard` | what the command guard would say about a command or a file write, or check a table of commands |
| `docs` | print a page of foreman's docs (alone: the list of pages) |
| `selftest` | foreman's own checks: static checks, the test suite on fake projects, code quality |

**The orchestrator's session**

| Command | What it does |
|---|---|
| `start` | what a new orchestrator session needs to start, in one short summary |
| `session` | mark the orchestrator's session, or show who orchestrates; --all: every product's sessions |
| `loop` | the notices a loop pass would get (markers read, not written) |
| `renew` | renew the orchestrator session's memory in place (docs/orchestrator.md) |
| `catchup` | what the owner missed, in four parts: done, running, waiting for them, went wrong |
| `recurring` | the recurring tasks and which are late; done NAME: the task was just done |

**Agents and branches**

| Command | What it does |
|---|---|
| `launch` | record an agent's identity (and make its worktree) before launching it |
| `worktree` | a worktree on a new or an existing branch (local or only on the remote) |
| `agents` | the agents the hooks saw, with their state; name or stop one |
| `integrate` | a finished branch into main: a PR, or a fast-forward without a remote |

**Backlog, notes and the owner**

| Command | What it does |
|---|---|
| `backlog` | the project's backlog: issues, projects, decisions for the owner, generated pages |
| `note` | append an entry to today's notes (what must last: figures, corrections) |
| `progress` | a progress line for the owner's panel; --done: an agent's delivery |
| `inbox` | the owner's messages from the panel and the orchestrator's replies |
| `plain` | check a text for the owner: bare ids and internal names (docs/owner.md) |
| `panel` | the web panel of every registered project; panel service: run it as a user service |

**Shared resources and waiting**

| Command | What it does |
|---|---|
| `lock` | a shared resource's queue: RESOURCE run "what" cmd… \| acquire \| release \| check |
| `wait` | wait in the background for a pid, a file, a line or a lock: [-t S] [-i S] pid\|file\|line\|lock … |
| `time-limit` | run a command with a time limit: SECONDS cmd… |
| `hung-shells` | list (or --kill) shells left hanging by a command |
<!-- /commands -->

## Documentation

`foreman docs NAME` prints a page; the same pages are in `docs/`:

| Page | For |
|---|---|
| `orchestrator` | the orchestrator's procedure: the loop pass, launching, integrating, the notices |
| `agent` | what every agent foreman launches follows |
| `owner` | how the orchestrator and the agents word what they write for you |
| `config` | every setting of `foreman.toml` and of the registry of projects |
| `extending` | adapting foreman without changing it: settings, your own commands at fixed points, agent types, hooks |
| `lock-protocol` | how the queues of shared resources work |
| `internals` | for contributors: how the guard reads commands, the reserved files, the state foreman keeps, the panel's API |

`examples/` has full `foreman.toml` files and a project that uses every extension point (`examples/extend/`).

## Uninstall

First, while the `foreman` command is still there, remove the panel service if you installed it (`foreman panel
service uninstall`). Then `/plugin uninstall foreman@foreman`, and remove the project's entry from
`~/.config/foreman/projects.toml` (the list of set-up projects). foreman's state is in `~/.local/state/foreman/`; the
project's foreman folder (backlog, notes, reports) is yours to keep.

## Contributing

CONTRIBUTING.md: the checks (`bin/foreman selftest`), code quality, the module map, the golden files, the developer
tool and how a version is cut. foreman is MIT licensed (LICENSE).
