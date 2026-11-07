# Contributing to foreman

foreman is a Claude Code plugin with a Python CLI (Python 3.9+, standard library only). Code, docs, comments and
commit messages are in English; nothing in `bin/`, `lib/`, `hooks/`, `skills/`, `agents/`, `docs/` or `panel/` may
name a particular project or machine (the selftest checks it): what belongs to one project goes in `examples/` or in
that project's home.

## Checks

```sh
bin/foreman selftest            # static checks + the test suite on fake projects in $TMPDIR (touches nothing real)
bin/foreman selftest -v NAME    # only the tests whose name contains NAME, one line per test
bin/foreman selftest -j 1       # serially, in one process (default: the cores shared by the runs on the machine)
bin/foreman selftest --python /usr/bin/python3   # the same with another interpreter (here the Mac's 3.9)
bin/foreman selftest --python /usr/bin/python3 --quality=require --mods=require   # the CI's one pass (below)
bin/foreman selftest --update-golden             # rewrite the golden files of the output corpus (below)
bin/foreman selftest --quality-only              # only the static, code quality and mods checks, no tests (below)
bin/foreman selftest --mods=require              # the hooks module's check must run (below)
bin/foreman selftest --fix                       # ruff's safe fixes, then --quality-only: run it before delivering
bin/foreman selftest --coverage                  # the suite under coverage.py: total and a table per module (below)
```

**The CI is one pass on the oldest supported Python, 3.9**: the whole suite with the code quality and mods checks
required, `bin/foreman selftest --python PY39 --quality=require --mods=require`. foreman's own integration runs it
before each merge (its home's `local_ci`, with macOS's `/usr/bin/python3`), and so does GitHub Actions on Linux
(`.github/workflows/ci.yml`: Python 3.9 from `actions/setup-python`, ruff and pyright from `[dependency-groups] dev`,
Claude Code at a pinned version for the mods check). How often: while working, the tests of what you touch
(`bin/foreman selftest NAME`); before delivering, that pass once, with any Python 3.9.

foreman runs on Python 3.9 and newer (the oldest is the Mac's `/usr/bin/python3`). Without one, ruff (`target-version
= "py39"`) catches the newer syntax and pyright (`pythonVersion = "3.9"`, on `lib/foreman` and `dev/`) the newer
standard library from 3.11 on and `X | Y` annotations; `py39_checks` in `lib/foreman/selftest.py` adds what neither
sees (the 3.10 names, methods and keywords it lists, tomllib, and `lib/main.py` parsing on 3.6). Behaviour that
changed under the same name (`datetime.fromisoformat`, argparse's texts, docstrings) only the suite on 3.9 shows. TOML is read through `foreman.toml_reader` (tomllib, or on 3.9 and 3.10 the vendored copy of tomli in
`lib/foreman/_tomli/`, MIT, unmodified below its provenance header), never with `import tomllib`.

The test classes run in parallel worker processes, longest first: those never timed on this machine (the classes
with more tests first), then the others by the seconds they took the last time (`times.json` in the folder of the
slots below). Without `-j N` a run takes
its share of the machine: the cores divided by the selftest runs alive on it (each holds a locked file in
`$TMPDIR/foreman-selftest-runs/`, or `$FOREMAN_SELFTEST_SLOTS`), at least 2, counted again while it runs (a worker
over the share stops after its class, one is added under it); its first line says how many workers it starts with
and why. `-j N` wins and still counts for the others. Each worker gets its own `$TMPDIR`
subtree, so tests never share folders, ports or lock directories; the output is one summary, the same as a serial
run, and the exit code is 0 only when everything passed.

What keeps a pass cheap: the tests run `lib/main.py` through a fork server (`tests/forkentry.py`, behind
`helpers.ProjectCase.cli` and the golden corpus's `run_entry`): a new process per call, with its own environment,
folder, stdin, stdout, stderr and exit code, forked from one that imported foreman once and ran nothing
(`FOREMAN_TEST_FORK=0` runs plain subprocesses; coverage does). Repos a fixture builds the same way every time come
from a snapshot built once per process (`helpers.make_repo`, `helpers.snapshot`). git is the tests' own: a global
file of theirs (an identity, no signing, an empty template), no system file. `FOREMAN_SELFTEST_TIMES=FILE` writes
each class's wall and CPU seconds, to find the costly ones. Tests wait for
conditions (a file, a queue, a gate the test opens) or shift the clocks they read, never for fixed times (a test that
runs another process of foreman at a known hour sets `FOREMAN_NOW=YYYY-MM-DDTHH:MM:SS`, which `util.now()` reads: the
panel's locks and notes, and the notes written by `foreman note`; seeds are never at fixed hours of today that the
real hour can put in the future); a time bound
in an assertion is far from the slow case it rules out, so a loaded machine does not break it.

The static checks include the version checks (`lib/foreman/release.py`): the version in `.claude-plugin/plugin.json`
and the one the code reports agree, the marketplace manifest declares no version, `CHANGELOG.md` has an
`[Unreleased]` section and one for the current version, a `foreman--v*` tag on HEAD names the current version, and a
`release` branch sits on the tag of the version it carries.

User-visible changes get a line under `## [Unreleased]` in `CHANGELOG.md`, in the same branch.

## Code quality

The selftest also runs code quality checks (`lib/foreman/quality.py`), in a thread of the parent while the workers
run the tests, so they add no time: **ruff** (lint), **ruff format** and **pyright** (types, on `lib/foreman`). They
are development tools, never run-time dependencies: foreman still needs only the standard library.

- **Versions**: `pyproject.toml` `[dependency-groups] dev` is the one list (`ruff`, `pyright[nodejs]`, `coverage`);
  `[tool.ruff]` and `[tool.pyright]` configure them; `[tool.foreman.quality] checks` says which checks run (all
  three: `ruff`, `format` and `pyright`). Raising a version is a change of its own, with what the new version finds
  fixed in the same commit.
- **Finding the tools**: `uvx --offline` (uv's cache, no network); else `uvx` once, with a line «quality: fetching
  ruff X (first run)»; else, without uv, the tool on the PATH only at the pinned version. pyright runs as
  `pyright[nodejs]` when there is no `node` on the PATH, so it never downloads node by itself. Without uv:
  `pip install --group dev` (pip 25.1+) in a virtual environment on the PATH.
- **Modes**: `--quality=auto` (the default) skips a tool it cannot get with a `SKIP quality: …` line and says so in
  the last line (`quality skipped: ruff`); `--quality=require` fails instead (foreman's own CI runs it once, in
  the pass above); `--quality=off` does not run them, and is the default with `--python` (the tools do
  not depend on the interpreter).
- **Fixing**: each finding is one line, `FAIL ruff lib/foreman/x.py:12:5 F401 … (fix: bin/foreman selftest --fix)`
  or `(fix by hand; recheck: bin/foreman selftest --quality-only)`, at most 30 per tool. `--fix` applies ruff's safe
  fixes and `ruff format`, says which files changed and runs `--quality-only`. Review its diff like
  any other: the golden corpus must stay the same.
- **Exceptions**: a `# noqa: RULE (reason)` or `# pyright: ignore[rule]  # reason` only with the rule and the reason;
  the `quality:` line counts them, so their number grows in sight.
- **Type annotations**: every module of `lib/foreman` must annotate every parameter and return (ruff's `ANN` rules,
  in the global selection of `pyproject.toml`; `tests/` is ruff-only and exempt from them). A new module is typed from
  its first line. `from __future__ import annotations` in each module (so `X | None` works on 3.9 in annotations; a type alias
  is evaluated at run time, so it uses `Union`/`Optional` or plain generics like `dict[str, Any]`). A JSON object of
  fixed keys that crosses modules and is built in one place is a `TypedDict` next to its writer (a notice:
  `notices.Notice`; `identity.Launch`, `identity.Identity`, `config.RegistryEntry`); optional keys go in a
  `total=False` subclass, never `NotRequired` (3.11). A JSON object whose keys are chosen at run time or grow with
  other writers is a named alias of `dict[str, Any]` (`config.Table` for a TOML table, `backlog.Issue`,
  `identity.Agent` for an agents.json entry). A value with behaviour stays a class or a dataclass
  (`config.Project`, `commands.Point`, `commands.Result`). `config.Project` is imported under `TYPE_CHECKING`, so
  typing adds no import at run time; paths are `util.PathLike` (a str or a Path).

## Coverage

`bin/foreman selftest --coverage` (`lib/foreman/cover.py`) runs the whole suite under coverage.py, at the `coverage`
version of `pyproject.toml`, and prints the selftest's own output, then a table per module from the least covered and
the total. It takes about a minute (the suite alone, half of that). It measures `lib/` without the vendored `_tomli`
(`[tool.coverage.run]`), and `patch = ["subprocess"]` includes the processes the tests start (workers, launcher,
hooks). The measure is statements, on the Python that runs foreman (a `--python` interpreter is not used here; on 3.9
the branches for older Pythons count, on a newer one they do not). Finding coverage is like finding the quality
tools: `uv run --offline` from uv's cache, else once with the network and the line «coverage: fetching coverage X
(first run)», else this Python if it has that version; `--quality=require` fails when it cannot be had, `auto` (the
default) prints a `SKIP` line. The data files go to a folder under `$TMPDIR`, removed at the end: nothing is written
in the repository. There is no threshold. A report of a change to code gives the total and the figure of each module
it touches (docs/agent.md).

## The hooks module

Beside the classic hooks (`hooks/classic.json`, named by `.claude-plugin/plugin.json` `hooks`), foreman has one hooks
module, `hooks/mod/foreman.ts`, the only entry of `hooks/hooks.json` (`modules`). Claude Code runs it as TypeScript
(no Node, no build). It is a thin adapter: each hook runs `<plugin root>/bin/foreman hook mod OP` with
`$.process.run` (JSON on stdin and stdout) and applies the answer; projects, state, texts and the minimum Claude Code
version stay in Python. **The protocol is written once, in `lib/foreman/hooks_mod.py`'s docstring**: what each op is
sent, what it answers and what the module does with the answer. The module's header keeps only what holds
throughout: no hook throws (a failed call is one status line; only a failed start leaves the module inert); outside
an active project it is inert; no hook awaits `$.prompt.submit` or `$.command.run` (the timer fires them); one tick
at a time, and while a pass it submitted waits or runs each tick still writes the heartbeat (`hold`).

What runs on it, and where each part lives in Python:

- **Start** (`session.start`, and the first tick under a new id after `/clear` or a resume): the session's root and
  Claude Code version (`versions.py`), the minimum version, the move of the orchestrator's mark (`orchestrator.carry`).
- **The loop pass** on the module's timer (60 s, fixed, `heartbeat.TICK_S`): `hook mod tick` reads the mark at every
  tick and runs a due pass there (`loop.py`); the module submits it with `$.prompt.submit`. Without the module (Claude
  Code below the minimum, mods off) there is no loop: `loop_stale` and `foreman session` say so.
- **In-between notices and the version notice** (`loop.notices`, `versions.pending`), handed to the model with the
  session's next own tool result or prompt.
- **Liveness**: every tick writes the session's heartbeat (`heartbeat.py`); `foreman session` (and `--all`), `start`,
  `who`, the panel and the `dead_agents`/`stalled_agents` notices read only that, never Claude Code's own files.
- **The renewal in place** (`renew.py`, docs/orchestrator.md «Renewal»): the module runs `/compact <instructions>`
  from its timer and reports how the call settled. foreman's
  pack comes back after a compaction through the classic SessionStart `compact` hook (`foreman hook resume`).
- **Measures** (`session.measure`, `<state>/measure/<session>.json`): the orchestrator's context for the
  `context_rotate` notice, the renewal and `foreman session`.
- **The chat reviewer** (`plain.review_answer`) at each main-loop `turn.complete`; its reminder rides the owner's
  next prompt.
- **The owner's yes for a `[deny.grants]` group** (`grants.py`, `hook mod grant`): in a project with groups, the
  `tool.call` hook checks each main-loop call before it runs (a Bash command, or any other tool's arguments, which
  Python judges by the places and commands they name) and, when all it lacks is that yes, asks with
  `$.ui.ask` while the call waits (the module's own limit, `ask_minutes`); the answer goes back to Python, which
  writes the grant. The module also keeps the grants it heard given, so a `grants.json` written by hand is asked
  again, and one question per group: a question left drawn after its limit refuses the group's calls until it is
  answered (its late answer grants nothing). `tests/mod/grant.test.ts` answers the question as the person would.
  The same hook asks, in any session's main loop, about a call that writes Claude Code's settings or the registry
  (a file tool, or a Bash command that names one): the group is `file:<class>`, the yes is a one-use grant for that
  `tool_use_id` that the classic guard spends, and the module keeps none of it.
- **The orchestrator's band and `/foreman:status`** (`band.py`): Python gives the parts with their roles and
  priorities and never knows the width; the module's `fit` keeps the parts that fit `bodyColumns`
  (tests/mod/band.test.ts has the table by width) and draws them above the prompt with theme keys. `/foreman:status` is `commands/status.md`
  (namespaced `foreman:`), answered by the module's `command.run` hook, so no model turn runs.

Known limits, accepted: what the module keeps between events (in-between notices and the version notice, which
Python marks said when it hands them over, and the reviewer's reminder) lives only in its memory, so a `/reload-plugins`, a
worker respawn or a relief before the next tool call or prompt loses it; the next pass repeats what is still
pending. A pass that did not reach the model is retried only at the next main-loop `turn.complete`, so in an idle
session nobody types into it waits for the next due pass, up to `[loop] every_min` later.

- **One contract for both sides**: `tests/mod/contract.ts` (comment lines, then `export default` and plain JSON: the
  test kit cannot import `.json`) lists each case: the engine's input, the stdin the module must send, the answer it
  gets and the status line it must show. `tests/mod/*.test.ts` check the module against it with `claude plugin test`
  (every call on `$` stubbed: no process runs); `GoldenModContract` (tests/test_golden.py) feeds the same stdin to
  `foreman hook mod OP`, on the state its `MOD_WORLDS` builds for the case, and requires the same answer byte for
  byte once both are normalised as the golden files are, its transcript in `tests/golden/mod/`. Every answer Python
  can give is checked that way (`python: true`); a fixture changed by hand fails there. A static check fails when
  the file is anything but that.
- **The check** (`lib/foreman/mods.py`, in a thread beside the quality checks): `claude plugin validate --strict`
  on the manifest (it must pass, the module must hook exactly `MODULE_HOOKS`, every gating hook needs a `.catch`)
  and `claude plugin test .`. `--mods=auto` (the default) skips with a `SKIP mods: …` line when there is no Claude
  Code or it is older than foreman's minimum; `--mods=require` fails instead (foreman's own CI, in
  the pass above); `--mods=off` runs nothing, the default with `--python`.
- **Trying it in a session**: `claude --plugin-dir <worktree>` from a scratch folder; never write a mod under
  `~/.claude`.

## Module map

One line per module of `lib/foreman/` (`lib/main.py` is the entry the launcher runs; `lib/foreman/sh/` has the shell
scripts behind `lock`, `time-limit` and `hung-shells`). `tests/test_docs_tied.py` fails when a module is missing here.

| Module | What it holds |
|---|---|
| `cli.py` | the argument parser and the dispatcher of every `foreman` command |
| `config.py`, `toml_reader.py` | `foreman.toml`, the registry and where each project keeps its state; the one TOML reader |
| `commands.py` | the one contract and runner of every shell command taken from the configuration |
| `util.py`, `shell.py`, `pyscript.py` | small shared helpers (JSON, atomic writes, git, pids); reading a shell line as bash does; the guard's gate for a python program that cannot start a process |
| `backlog.py`, `backlog_check.py`, `backlog_text.py`, `backlog_cli.py`, `backlog_list.py` | the backlog: its only writer; its checks; its one-line text (`show`, `get`, `start`, `integrate`); `foreman backlog …`; `foreman backlog list` (read only, one product or all) |
| `notes.py`, `pause.py` | notes, progress, inbox, heartbeats, recurring tasks and the file lock; the panel's pause |
| `identity.py`, `sessionlaunch.py`, `remotecontrol.py`, `orchestrator.py`, `procs.py`, `heartbeat.py` | who an agent is and who orchestrates (`agents.json`, `launches.json`, the marked session); the Claude Code process the mark is tied to (`ps`); whether a session runs (the hooks module's heartbeat); `launch --session`'s `claude --bg` command; the panel's Remote Control on demand for a session agent (`claude attach` on a pseudo-terminal) |
| `agents.py` | the agents' state: agents.json, an agent's one state, its last sign of life, `foreman agents` |
| `loop.py`, `notices.py` | the loop pass's own notices; the notice contract (kinds, render, idle) |
| `hooks.py`, `hooks_mod.py`, `guard.py`, `gitpush.py`, `gitdir.py`, `approved.py`, `grants.py`, `delivery.py`, `versions.py`, `renew.py` | the classic Claude Code hooks; the Python side of the hooks module (`hook mod OP`); the command guard; what a `git push` would do (its folder, remote and refs) for the guard; where git keeps a repo's own files (its checkout, common git directory and the paths its config names), read from the disk without git; the log of the orchestrator-only commands (`approved.jsonl`); the owner's grants for `[deny.grants]` groups (`grants.json`, `foreman permissions grants`); the delivery check; which foreman a session runs; the orchestrator session's memory renewed in place (`/compact` and foreman's pack) |
| `band.py` | the orchestrator's band above the prompt and `/foreman:status`: its parts with role and priority, from the pass heartbeat, the measure, the backlog, the inbox and `agents.json` |
| `lock.py`, `waits.py` | resource locks (with `sh/lock.sh`); `foreman wait` |
| `worktree.py`, `integrate.py`, `review.py`, `holdout.py` | a worktree per agent; a finished branch into main (PR or fast-forward); when a branch needs a review by another agent and whether its review lets it in (integrate's review gate); an issue's hidden acceptance check, sealed and run on a copy of the branch (integrate's hidden gate) |
| `timeline.py`, `transcripts.py` | the product's history (events in time order, what `foreman catchup` reads); the one reader of Claude Code's transcripts |
| `costs.py` | what each agent run cost (list prices, from its transcript), kept beside the backlog; `foreman agents --cost` |
| `start.py`, `plain.py`, `catchup.py`, `permissions.py` | the new orchestrator's start summary; the check of texts for the owner; `foreman catchup`, what the owner missed in four parts; `foreman permissions`, the permissions in bands and a candidate `foreman.toml` tested against the guard table and the orchestrator's lifeline, and `apply`, which puts it in place |
| `init.py` | `foreman init`: a new project's home (minimal `foreman.toml`, starting rules, backlog) and its registry entry, appended |
| `issue_docs.py` | the documents of an issue (plans, reports, branch reviews, decision pages, cited files): the card's list and `backlog show` |
| `panel.py`, `panel_cache.py`, `panel_cards.py`, `panel_locks.py`, `panel_registry.py`, `panel_wiki.py` | the web panel: server and routes, cache, backlog cards, locks, registry, wiki |
| `metrics.py` | the series of the new panel's charts (`/api/metrics`, `panel/next/`): cost, deliveries, reviews, guard refusals and the figures of today, by day |
| `install.py`, `release.py`, `selftest.py`, `quality.py`, `cover.py`, `mods.py` | where foreman is installed and the panel service; version and release checks; the static checks and the test runner; the code quality checks (ruff, pyright); the coverage run; the hooks module's check (claude plugin validate/test) the selftest runs |
| `i18n/` | the texts a person reads, `en.json` and `es.json` (same keys in both: a static check) |

## Golden files

The selftest includes a corpus of everything foreman shows and writes (`tests/test_golden.py`, how it works in
`tests/golden.py`): two fake products (one in Spanish, one in English with a wiki of hand-written pages), fixed
sequences of CLI calls (every subcommand that reads or writes), hook events (each row of `tests/golden_guard.tsv`, then
a session's life: marking, launching an agent, its delivery, the loop's prompts, a pause, a compaction, the orchestrator
gone), every panel route before and after the owner's writes, and the state tree and the products' homes at the end.
Each sequence's transcript (exit codes, stdout, stderr, files) must equal its file in `tests/golden/`, byte for byte, on
every Python foreman runs on; only what varies from run to run (temporary paths, dates near today, clock times, epoch
times, measured durations, pids, the panel's port, launch tokens, the machine's figures, foreman's version and
argparse's per-version layout) is normalised, in one documented function (`Normaliser`). It is how a refactor (moving
code between modules, formatting, a rewrite) proves it changes nothing visible: if the corpus passes before and after,
no output changed.

- A normal `bin/foreman selftest` compares; a mismatch fails with a short unified diff (golden, then now).
- `bin/foreman selftest --update-golden [PATTERN]` runs only the corpus, rewrites its files as the outputs are now
  and says which changed (+/- lines). Without a pattern it also removes a golden file no test writes any more.
- **A change that means to change an output updates the golden files in the same commit**, and its report says why
  each changed (`git diff tests/golden/` is the review of what the user sees). A change that does not mean to change
  any output leaves them untouched: if the corpus fails, the change is wrong, not the golden file.
- Never edit a golden file by hand, and never widen the normalisation to make a diff go away: if something varies
  between runs that should not, fix the source of the variation (or, if it truly must vary, add it to `Normaliser`
  with its reason).
- New user-visible behaviour (a subcommand, a hook case, a panel route, a state file) gets its calls added to a
  sequence of `tests/test_golden.py`, and a new state file a row in `docs/internals.md` («The state»). The developer tool's corpus is
  `dev/tests/test_golden_dev.py` (files in `dev/tests/golden/`), updated by the same `--update-golden`.

## The developer tool (`dev/`)

`dev/foreman-dev` (stats, fill, timeline; dev/README.md) measures foreman's sessions and prints a product's history
for whoever develops foreman. It is not part of the product: no command, hook, skill, agent or doc of the product
names it, and nothing in `lib/foreman` imports `foreman_dev` (a static check of the selftest). It imports only
lib/foreman's readers (config, transcripts, identity, orchestrator, util, i18n, the history collector `timeline.py`).
Its tests (`dev/tests/`, with their golden files in `dev/tests/golden/`) run in `bin/foreman selftest` with foreman's,
and ruff and pyright cover `dev/foreman_dev` as they cover `lib/foreman`, so a change in lib/ that breaks it fails
the selftest. Run it from the working tree; it only reads, from the same registry, state and Claude Code folders
foreman does:

```sh
dev/foreman-dev [--project P] stats [--since 24h] [--by session|agent|issue|type|model|hour] [--all] [--json]
dev/foreman-dev [--project P] fill SESSION [--json]
dev/foreman-dev [--project P] timeline [--since 24h] [--agent NAME] [--issue ID] [--kind K,…] [--json]
```

`stats` is what the Claude Code sessions spent, in tokens; `fill` what fills one session's context, piece by piece;
`timeline` the product's history, one event per line, with each agent's spend and where to dig.

## Internals

How foreman works inside, beyond what a user configures, is docs/internals.md (`foreman docs internals`): the
panel's API and how it follows the installed version, how the launcher picks a Python, the git foreman runs without
hooks, the reserved files, how the guard reads a command and resolves a `git push`, and every file of the state
foreman keeps. The contract of a product's commands (`FOREMAN_API`) is docs/extending.md.

## Developing foreman while using an installed version

Do not run your sessions from the working tree. A marketplace added from a local folder loads its plugin in place, so
every commit (or half-finished edit) reaches every session at once. Install a released version instead, the same way
a user does, and try the working tree in a separate session.

### Install from the `release` branch of your clone

A `git` marketplace pinned to the `release` branch copies the plugin into Claude Code's cache, one folder per
version, so main never reaches your sessions; moving `release` to a new version is what updates them.

1. Declare the marketplace in your Claude Code `settings.json` (user or project scope). `claude plugin marketplace
   add` does not accept a `file://` URL, but `extraKnownMarketplaces` does:

   ```json
   "extraKnownMarketplaces": {
     "foreman": {
       "source": {"source": "git", "url": "file:///absolute/path/to/your/foreman/clone", "ref": "release"},
       "autoUpdate": true
     }
   }
   ```

   The key must equal the `name` in `.claude-plugin/marketplace.json` (`foreman`).
2. Start a new Claude Code session: the marketplace is registered when a session starts, not by the `claude plugin`
   CLI, and editing `settings.json` alone changes nothing until then.
3. `claude plugin install foreman@foreman`, then check with `claude plugin list --json`: `installPath` is under
   `cache/foreman/foreman/<version>` and there is no `readFromFolder`.
4. Open new sessions (or `/reload-plugins`). `bin/` of the installed version is on the PATH of the Bash tool: call
   `foreman`, never `<clone>/bin/foreman`, so that every session runs the same version.

**Updating.** With `autoUpdate`, sessions pick up a new version on their own, after a delay. By hand, both steps are
needed: `claude plugin update` alone does not refresh the marketplace and reports `already at the latest version`.

```sh
claude plugin marketplace update foreman && claude plugin update foreman@foreman
```

Sessions already open keep the version they started with, hooks and `bin/` on the PATH included, until you type
`/reload-plugins` in each one (or resume it). foreman says so: the first start, prompt or tool call of such a session
after the update tells the model (once per installed version) and shows you one line («foreman X in this session, Y
installed: type /reload-plugins»), the marked orchestrator's session too (docs/orchestrator.md, «Reload»). The
record of which session runs which version is `<state root>/install/sessions.json` (docs/internals.md).

**Switching from a folder marketplace.** If the plugin was installed before from a local folder (loaded in place),
changing the source in `settings.json` makes Claude Code use whatever copy is left in its cache, which may be old,
and `plugin update` will not refresh it while the version matches. Remove the old marketplace, then uninstall and
install again: `claude plugin uninstall foreman@<old marketplace> && claude plugin install foreman@foreman`.

### The panel

Run the panel as a service from the installed version, not from the working tree: `foreman panel service install
[--host …] [--port …]` (README, docs/config.md). It follows the version your sessions run: the hooks record their
plugin root and version, and the panel re-executes itself into a new one after a smoke test. A panel only moves when
a session has run the new version (a session start or a prompt in an active project), so after moving `release` it
follows within a minute of the first session that updated. Your clone never wins over an installed copy, even with a
higher version on main or a session still loading it in place (a root with `.git` ranks last), so the panel does not
run unpublished code. To try panel changes, run `bin/foreman panel --port <another port>` from the working tree,
without `--follow-installed`.

### Building the new panel

The new panel (`/next/`, docs/internals.md) is a React and TypeScript app in `panel/next-src/`, built with Vite into
`panel/next/`. The built files are committed: the plugin is the repository, so whoever installs foreman needs only
Python, Claude Code and a browser, never Node. Changing the panel needs Node 22 or later and the network once:

```sh
cd panel/next-src
npm ci              # the exact versions of package-lock.json, into node_modules/ (ignored by git)
npm run build       # type-check (strict), then write panel/next/: index.html, app.js, app.css, LICENSES.txt, BUILD
npm run watch       # rebuild on each change, while trying it on a panel of your own (below)
```

Commit the source and `panel/next/` together. `panel/next/BUILD` holds the sha256 of every source file and of every
built file: the selftest (`test_panel_next`) fails when a source changed without a build or a built file was changed
by hand, without needing Node, and the CI builds again at the lockfile's versions and fails on any difference. The
built files are marked generated in `.gitattributes`, so pull requests fold them. To add a package, pin its exact
version (`npm install --save-exact`), keep the panel under 150 KB gzipped (a test checks it) and check that its
licence lands in `LICENSES.txt`. With Node installed, the selftest also mounts the built `app.js` in a minimal DOM
(`tests/next_dom.mjs`); without it, those tests are skipped.

### Trying the working tree

`claude --plugin-dir /path/to/your/clone` in a separate session loads the working tree for that session only,
replacing the installed plugin there. Tests never need it: `bin/foreman selftest` runs from the tree.

## Cutting a version

The `release` branch is the stable channel: users and contributors install from it. Claude Code only updates an
installed plugin when its version changes, so **`release` only ever moves to a commit that bumps the version in
`.claude-plugin/plugin.json`**. Moving it to any other commit reaches nobody, and nothing warns about it.

1. **Bump** `version` in `.claude-plugin/plugin.json` ([Semantic Versioning](https://semver.org/); `config_schema`
   going up is at least a minor version). It is the only place the version is written: the code reads it.
2. **CHANGELOG**: rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD`, open a new empty `## [Unreleased]` above
   it, and make sure the Config and Upgrade notes sections say what users must do.
3. **Integrate** that commit into main like any other change (the CI's pass on 3.9 green).
4. **Tag** it: `claude plugin validate .`, then `claude plugin tag . --dry-run` and `claude plugin tag .` (without
   `--push`), which creates `foreman--vX.Y.Z`. By hand: `git tag foreman--vX.Y.Z`.
5. **Move `release`** to the tag: `git branch -f release foreman--vX.Y.Z`. `bin/foreman selftest` then checks that
   `release` sits on the tag of its version.
6. Publishing (pushing the tag and `release` to a public remote) is a separate step for whoever owns the repository.
7. Check that a panel running as a service moved to the new version (System tab, or `foreman_version` in
   `/api/projects`) once a session has updated; if it shows «Installed foreman not adopted», the new version failed
   its smoke test and the message says why.
8. **List the sessions left behind**: every session open before the update keeps its old version until
   `/reload-plugins`. Run `foreman session --all --behind` (your own session is one of them: run from its old folder,
   it still finds the new install next to it; «settling» ones may load it by themselves), or look at the panel's
   System tab, and tell the owner of each one to type `/reload-plugins` there before counting on the new
   version. An orchestrator left behind loads it in place: by itself in the desktop app, otherwise its pass asks you,
   once, to type `/reload-plugins` there (docs/orchestrator.md, «Reload»).

Between versions, main keeps the last released version number; the version commit is the one that raises it.

## Cutting the public snapshot

The public repository starts from a snapshot of this one, not from its history: the tracked tree at one commit,
without what only the private repository keeps. `dev/foreman-dev snapshot DEST [--commit REV]` writes it into `DEST`
(absent or empty) and prints its file count and size:

- the tracked files at the commit (`git archive`: nothing untracked or ignored gets in);
- without `ops/` (the project's own home: backlog, notes, reports) and `tests/live/` (probes run by hand against real
  Claude Code sessions, with those sessions' logs; the selftest never runs them). The list is `EXCLUDED` in
  `dev/foreman_dev/snapshot.py`, and the publish-safety tests in `tests/test_config.py` skip the same paths;
- with a short `CHANGELOG.md` instead of the private history: a «first public release» entry under `[Unreleased]`
  and the current version's section, which the release check wants.

`test_public_snapshot_passes_the_publish_checks` exports HEAD into a temporary folder and runs the same scans over
it (no product names, no paths of the owner's machine, no issue ids). The command never creates a remote, pushes
or calls gh: turning the folder into a repository and publishing it is a separate step for whoever owns the
repository.
