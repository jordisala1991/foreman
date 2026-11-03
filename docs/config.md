# Configuration

The settings of a project: the registry of projects on the machine, and the project's own `foreman.toml`, every
key with its default. Your own commands at foreman's points, agent types and hooks: docs/extending.md. How foreman
works inside (how the guard reads a command, the reserved files, the state it keeps, the panel's API):
docs/internals.md.

Words used below:

- **product** or **project**: one piece of software foreman runs, with its own backlog; **owner**: the person who
  decides about it.
- **home**: the project's foreman folder, with its `foreman.toml`, rules, backlog, notes and reports (often `ops/`
  inside the repo).
- **registry**: `~/.config/foreman/projects.toml`, the list of set-up projects on this machine.
- **state root**: `~/.local/state/foreman/` (or `$FOREMAN_STATE_HOME`), where foreman keeps what it can rebuild
  (agents seen, locks, the inbox); `<state>` is one project's folder in it.
- **triage**: the backlog status of what arrived and is not decided yet.

Sections: the registry («The registry», «Starting a project», «Machine resources»), «Python», `foreman.toml`
(«`foreman.toml` (in the home)», the full example, then one section for each key that needs more than a comment:
`owner_knows`, `[[recurring]]`, local CI, `main_ci`, `post_merge`, `[deny] patterns`, «Seeing the permissions and
testing a change», `[deny.grants]`, `[loop] checks`, `[strings]`), «Compatibility» and «The backlog model».

## The registry: `~/.config/foreman/projects.toml` (or `$FOREMAN_REGISTRY`)

```toml
[[project]]
name = "example"
home = "~/Projects/example/ops"   # the directory with foreman.toml
mode = "active"                   # "inactive": set up, hooks do nothing
config = ""                       # optional: its foreman.toml lives elsewhere (a home foreman must not touch)

[panel]                           # optional: the user's defaults for `foreman panel`
language = "en"                   # interface language: en | es
who = "owner"                     # the name written when the owner takes a lock from the panel

[resources.gpu]                   # optional: a machine resource, declared once for every product (below)
dir = "~/.gpu-lock"               # where the one lock lives (also queue, next, jumped, log)
busy = "~/.config/foreman/bin/gpu-busy.sh"   # its users outside the lock, outside any product's repo

[machine.project]                 # optional: settings every product inherits (below), shaped like a foreman.toml
owner = "owner"

[machine.deny]
patterns = ["npm publish"]
```

`foreman panel [--port 8766] [--host 127.0.0.1] [--token T]` serves one page for every registered project: backlog,
agents, the common locks, branches and open PRs of each repo, decisions and inbox, recurring tasks, progress and the
notes of the last hour. From it the owner answers decisions, writes to the inbox, approves pull requests, moves
backlog issues, marks a triage issue as wanted or discards it, pauses a product and takes a turn on a lock. Its key is
`--token`, `$FOREMAN_PANEL_TOKEN` or `<state root>/panel/token` (created once, mode 600); `--host 0.0.0.0` reaches it
from a phone on the same network (the key still applies). `FOREMAN_PANEL_PRS=0` turns off its `gh pr list` calls.

`foreman panel service install [--host H] [--port P] [--label L] [--replace]` runs it as a user service (a
LaunchAgent on macOS, a systemd user unit on Linux) that moves to a new installed version of foreman by itself;
`foreman panel service status|uninstall|print` shows it, removes it, or prints what `install` would write. Logs:
`<state root>/panel/panel.log` on macOS, the user journal on Linux. How the panel works inside (its API, how it
follows the installed version, which unit `install` picks): docs/internals.md, «The panel».

Hooks find a session's project by its cwd: inside the home, a configured repo, the worktrees folder or the workspace
(the deepest home wins). A worktrees folder that holds the home or a repo (a shared folder of projects, like
`~/Projects`) is not the project's as a whole: only the paths that start with a worktree prefix (each repo's `worktree`
template cut at `{name}`, and `[agents] production_worktree`) are. The workspace is the home's parent (home `X/ops`,
repos `X/app`, `X/api` → `X`) when it holds every repo, the worktrees folder is not shared, it is not `~` or above, and
it holds no home or repo of another registered project. Outside every `active` project they do nothing. An `inactive`
project is ready (the CLI reads and writes it with `--project`), but no
hook acts for it until its mode is switched to `active`. CLI commands pick the project with `--project NAME`, `$FOREMAN_HOME` (a home),
`$FOREMAN_PROJECT` (a name), the cwd, or the nearest `foreman.toml` above the cwd.

### Starting a project: `foreman init`

`foreman init HOME --name NAME` makes a new project's home and its registry entry in one go, without questions:

```
foreman init HOME --name NAME [--owner TEXT] [--language en|es] [--repo KEY=PATH]… [--local-ci [KEY=]CMD]…
             [--slots N] [--every-min N] [--sample [--prefix PX]] [--mode active|inactive] [--no-register]
             [--dry-run]
```

- **`HOME/foreman.toml`**, minimal: `[project]` with `name` and `config_schema` (and `owner`, `language` when given),
  `[agents] production_slots` and `[loop] every_min` when they differ from the defaults, and one `[repos.KEY]` per
  `--repo` with only what differs from the defaults, read from git: `main` (the branch checked out), `remote`
  (`origin`, else the first one, else `"none"` with `remote_checks = "none"`), `github` for a GitHub remote, and
  `local_ci` (`--local-ci`; `KEY=` may be left out with one repo). A repo that holds the home is written relative
  to it (`path = ".."`). The rest stays at its default: `foreman docs config` and `foreman config` show it.
- **The agents' worktrees never go inside a repo.** When the default folder (`<home>/../wt`) would fall inside one
  (a home inside its repo, `<repo>/ops`, as one repo usually has), `[paths] worktrees` is written as `<repo>-wt`
  beside it.
- **`HOME/rules.md`**, starting rules in the project's language (what the product is, to fill in; what never
  happens without the owner; how the code is checked, from `--local-ci`); `notes/` and `reviews/`.
- **The backlog** and its pages, when the home has none; `--sample` adds one issue in triage, `<PX>-1`, where PX is the first two
  letters of the name in capitals (or `--prefix`), so the board is not empty.
- **The registry**: a `[[project]]` entry (`name`, `home`, `mode`, `--mode` default `active`) appended to the file,
  everything already there kept byte for byte (comments included); written atomically under a lock and read back.
  `--no-register` leaves the registry alone: the CLI still finds the home from inside it, the hooks do not act.

Every step is checked before anything is written. A step whose result is already there says «already there» (an
existing `rules.md` is always kept: it is the owner's), so running the same command again changes nothing. A
`foreman.toml` with other text is kept as it is when it names the same project and no option that shapes it
(`--owner`, `--language`, `--repo`, `--local-ci`, `--slots`, `--every-min`) is given: `foreman init HOME --name NAME`
then makes only what the home lacks (its backlog, say). Otherwise a `foreman.toml` with other text, a name registered with another home or mode, a home registered under another name,
or a registry that does not read stops it with exit 2 and nothing written. `--dry-run` writes nothing and prints
each step, the two files and the registry entry whole. At the end it prints `foreman check` of the new project.
It installs nothing: the panel service is `foreman panel service install`. `/foreman:setup` (skills/setup) is the
guided way for someone new: it asks the questions, runs `foreman init` (with `--dry-run` first), installs the panel
when wanted, explains `foreman check` and walks the owner through the first pass.

### Machine resources: `[resources.NAME]` in the registry

A resource several products share on one machine (a GPU, a simulator pool, a port) is one lock. Declare it **once**,
in the registry, with everything that defines that lock; each product that uses it declares `[resources.NAME]` in
its foreman.toml with only what is its own.

| Where | Keys |
|---|---|
| registry `[resources.NAME]` (the machine's) | `dir`, `queue`, `next`, `jumped`, `log`, `busy`, `busy_wait`, `short_max`, `slots` |
| foreman.toml `[resources.NAME]` (each product's) | `guard`, `allow` (an empty table is fine) |

- **By name, no new key.** A product uses the machine resource `gpu` by declaring `[resources.gpu]`; `config_schema`
  does not change. When a project loads, its table is resolved once: the machine's keys plus the product's `guard`
  and `allow` (`config.machine_resources` and `merge_machine`). `foreman lock`, the guard, `foreman wait lock` and the
  loop pass all read that merged table, and `foreman check` says where each resource comes from («resource gpu:
  machine (registry), …, busy …, guard 15» or «…: this project»).
- **The machine wins.** Machine keys a product repeats in a machine resource are ignored, with a warning that names
  them and says to remove them: `foreman check` fails (strict, like an unknown key), the loop pass says it once
  (`config_warnings`), and the product keeps loading, so its guard keeps guarding.
- **Paths** in the machine table expand `~`, and a relative one is relative to the registry's folder. Empty paths are
  the defaults under `<state root>/locks/NAME/`.
- **`busy`** is a shell command (`bash -c`) printing the resource's users outside the lock; keep it outside every
  product's repo, e.g. `~/.config/foreman/bin/NAME-busy.sh` next to the registry, so no product depends on another's
  files. It must not depend on the working directory: lock.sh runs it in the caller's, the panel in the registry's
  folder.
- **In the panel** a machine resource is one row with the machine's table whatever the registry order (its lock
  paths, `busy`, the owner's take, release and +30 min), listing the products that declare it; one no product
  declares is listed too, and, like any resource of no active product, cannot be taken from the panel.
  The `locks` rows of `/api/projects` and `/api/state` carry `"machine": true|false`; the page looks the same.
- **Without a product.** `foreman lock NAME …` works with a machine resource from a product that does not declare it
  and from outside any product (the guard of a product that does not declare it does not ask for the lock).
- **Mistakes.** `guard` or `allow` in the machine table are each product's own: a registry warning, ignored.
  An unknown key: a registry warning. A bad value (`busy_wait` not a whole number ≥ 0, `slots` not a list of strings,
  a path or `busy` not a string, a name that is not lowercase letters, digits and `-`) leaves that machine resource
  out: `foreman check` shows the error, and each product that declares it loads with its own keys and a warning that
  says so (its check fails). The registry's warnings fail nobody's check by themselves.
- **Migrating from copied declarations.** When several products copy the same `dir`, `busy`… into their foreman.toml,
  move them to one registry table first (the products keep their lines, and get the warning), then remove the lines
  from each product. Use a foreman that knows machine resources everywhere before removing them (see the CHANGELOG's
  upgrade notes): an older one ignores the registry table («unknown section [resources]») and, without the product's
  lines, would use the default paths, a second queue.

**Examples.** `examples/machine/projects.toml` is a registry with two products and a machine `[resources.gpu]` (lock
paths, `busy`, `busy_wait`, `short_max`); `examples/tidepool.toml` is one of those products, using that GPU with only
its own `guard` next to resources of its own (a simulator with `slots`, an emulator, containers).
`examples/machine/gpu-busy.sh` is a `busy` command to copy next to the registry (`~/.config/foreman/bin/gpu-busy.sh`)
and adapt: it lists the processes of the executables in its `EXES` (or `$GPU_BUSY_EXES`), skipping command lines
that match `IGNORE`, one line each (pid, elapsed time, working directory, command); stdout is the only contract (any
output: busy), and its exit code (0 busy, 1 free) is for people. The selftest loads and validates all of them.

### Machine settings: `[machine]` in the registry

What holds for every product on a machine (the owner's name, the language, the commands nobody runs without the
owner) is written **once**, in the registry's `[machine]`, with the shape of a foreman.toml: `[machine.project]`,
`[machine.agents]`, `[machine.loop]`, `[machine.deny]`, `[machine.deny.grants]`, `[machine.strings]`. Every product
inherits it when it loads (`config.machine_settings`, `config.merge_settings`), so the guard, the grants, the loop
pass and the panel read the merged values without knowing where each came from.

| Section | Keys the machine may set |
|---|---|
| `[machine.project]` | `owner`, `language`, `owner_knows` (`config_schema`: checked as a foreman.toml's, not inherited) |
| `[machine.agents]` | `production_slots`, `ready_target`, `production_types`, `stalled_min` |
| `[machine.loop]` | `every_min`, `context_rotate`, `disk_low_gb`, `lock_long_min` |
| `[machine.deny]` | `patterns`, `message`, `grants`, `grant_minutes`, `ask_minutes` |
| `[machine.strings]` | any text, as `[strings]` |

Everything else is each product's own: `[paths]`, `[backlog]`, `[ids]`, `[panel]`, `[publish]`, `[check]`,
`[repos.*]`, `[[recurring]]`, `[project] name` and `rules`, `[agents] production_worktree`, `[loop] prompt` (it
names each product's orchestrator) and `[loop] checks` (commands foreman runs: they never arrive through the
registry), and `[deny] drop`. Machine resources keep their own `[resources.NAME]` (above).

- **How they merge** (defaults < machine < product). A scalar the product writes wins over the machine's (`owner`,
  `deny.message`, `grant_minutes`…). Lists add up, the machine's first and without repeats (`deny.patterns`,
  `owner_knows`, `production_types`). Tables merge key by key with the same rule: a `[deny.grants]` group both
  declare has the patterns of both; in `[strings]` each text is the product's when it writes one.
- **Removing an inherited rule: `[deny] drop`**, in the product's foreman.toml only. Each entry is a pattern exactly
  as the machine writes it (removed from the inherited `patterns` and from every inherited group) or `"group:NAME"`
  (the whole inherited group NAME; the product's own patterns in a group of that name stay). It removes only what
  the machine gives, never the product's own rules. To turn a machine «never» into a group of the product's: drop it
  and add it to the group. An entry that removes nothing inherited is a warning.

  ```toml
  [deny]
  drop = ["npm publish", "group:machine"]
  ```

- **Where each value comes from.** `foreman config --json` says `registry` for a value the machine gives and
  `registry+foreman.toml` for a list or table both add to (docs/extending.md); `foreman check` prints «inherited
  from the machine (registry [machine]): N settings, M deny rules», and how many inherited rules `[deny] drop`
  removes; `foreman permissions` marks each deny rule the machine gives and lists what `drop` removed (below).
- **Mistakes.** A section or key that is each product's own, or unknown, is ignored with a warning. A bad value (a
  regex that does not compile, a group with a bad name or no patterns, `context_rotate` below 0, a list key that is
  not a list) leaves out **only that value**: one bad regex of `patterns` keeps the others, one bad group keeps the
  other groups. Every product still loads (a product that does not load has no guard) and carries the warning, so
  `foreman check` fails in each of them until the registry is fixed. A pattern denied at one level and granted in a
  group at the other is the warning of `[deny.grants]` (below), naming both places.
- **Who can change it.** The registry is a reserved file (docs/internals.md, «The reserved files»): only with the
  owner's own yes. An older foreman ignores `[machine]` («unknown section [machine]»): keep a rule in the products
  too until every session runs a foreman that knows it (the merge does not repeat it).
- **Grants are per product.** A `[machine.deny.grants]` group is granted in each product apart: the owner's yes in
  one product's panel does not open it in another.

## Python: which one foreman runs, and what happens without one

foreman needs Python 3.9 or newer, standard library only, so the Python that macOS ships with its Command Line Tools
(3.9, which git on a Mac already requires) is enough. `bin/foreman` finds one by itself: `$FOREMAN_PYTHON`, then
`python3` and `python3.N` on the PATH, mise, Homebrew and `/usr/bin/python3` (docs/internals.md, «Python»). Run
`bin/foreman` (or `foreman` on the Bash tool's PATH), never `python bin/foreman`: it is a shell launcher.

Without a Python that fits (none at all, as on a Mac without the Command Line Tools, or only older ones), foreman says
so instead of switching itself off in silence:

- **The guard** (`hook guard`, before every Bash call) denies the call (exit 2) when a foreman registry exists
  (`$FOREMAN_REGISTRY` or `~/.config/foreman/projects.toml`, as above): what is never done without the owner cannot be
  checked, so nothing runs. The reason says which Python is missing and how to install it. Without a registry (the
  plugin installed, no project set up) the call passes.
- **Session start** (`hook who`): the owner sees one line (`systemMessage`) and the model a note with the same reason,
  at startup and resume (not after compacting).
- **The other hooks** exit 0 with a line on stderr: they never break a session.
- **The CLI** prints the reason on stderr and exits 1.

The fix is the owner's: install a Python that fits (macOS: `xcode-select --install`, `brew install python@3.13` or
`mise use -g python@3.13`; Linux: the package manager) or point `FOREMAN_PYTHON` at one, then restart the Claude Code session.

## `foreman.toml` (in the home)

Relative paths are relative to the home; `~` is expanded. Every key but `[project] name` has a default.

```toml
[project]
name = "example"            # required: lowercase, digits, . _ -
owner = "owner"             # who decides; named in pages and guard messages
language = "en"             # en | es: generated pages, notes heading, guard and resume texts
rules = "rules.md"          # the project's rules, read by the orchestrator and every agent
config_schema = 19          # the foreman.toml schema the file is written for (below); default: this foreman's
owner_knows = []            # terms the owner knows, never flagged by those checks: ["CI", "PR"]

[paths]
notes = "notes"             # YYYY-MM-DD.md, append only
reviews = "reviews"         # plans and reports
state = ""                  # default ~/.local/state/foreman/<name> (or $FOREMAN_STATE_HOME/<name>)
worktrees = ""              # default <home>/../wt

[backlog]
file = "backlog.json"
page = "pages/backlog.md"
archive_page = "pages/backlog-archive.md"
decisions_page = "pages/decisions.md"
decisions_section = ""      # if set, only that "## …" section of decisions_page is rewritten

[ids]
project = "^[A-Z]\\d+$"
issue = "^[A-Z]{2}-\\d+$"
legacy = ""                 # ids allowed only in the archive and as aliases (e.g. "^H\\d+$")
decision = "^[A-Z][A-Za-z0-9-]* [A-Z]\\d+$"  # decision add without --id: «<issue> J1», else «<issue prefix> J1», else «DEC J1»: the first the pattern accepts
criterion_required = []     # issue prefixes that need an acceptance criterion before needs_criterion statuses
needs_criterion = ["backlog", "ready"]
report_prefixes = ["reviews/"]  # where reports live: the delivery check, the pass (`ready_to_integrate`) and integrate
                            # look for one there, and `progress --done --report` must name one there; [] = the
                            # project keeps no reports

[agents]
production_slots = 1        # production agents the pipeline notice asks for
ready_target = 2            # unblocked ready issues it asks for
production_types = ["dev"]  # agent types that count as production (fallback; `dev` always does; a plugin-qualified
                            # type matches its bare name: `foreman:dev` counts for "dev")
production_worktree = ""    # fallback: or a path prefix in the brief (default: the worktrees folder)
stalled_min = 20            # quiet minutes without a lock before an agent is reported (stalled_agents,
                            # the panel's «maybe stuck»): since its last tool call or transcript write

[loop]
prompt = "Orchestrator loop"   # the loop pass's prompt starts with this ("": no loop pass)
every_min = 60              # minutes between passes (foreman's hooks module checks every minute)
checks = []                 # shell commands run in the home; each output line is a notice of the pass (below)
disk_low_gb = 30
context_rotate = 200000     # tokens of context of the orchestrator's session that ask to renew it in place
                            # (docs/orchestrator.md «Renewal»); 0: off
lock_long_min = 60          # minutes a lock is held before the lock_long notice (the panel's marks stay fixed)

[repos.app]                 # one table per repo
path = "~/src/app"          # the main checkout
github = "owner/app"        # for import-gh and «Closes #n»
main = "main"
remote = "origin"           # the git remote; "none": no PR, integrate merges by fast-forward after the local CI
merge = "squash"            # squash | merge | rebase
local_ci = ""               # run in the worktree before pushing ({wt}, {main}, {branch}, {ci})
ci_runs = [""]              # one run of local_ci per value, put in {ci}, in order (integrate --ci=ARGS replaces it)
ci_summary = ""             # command: each run's output on stdin, its stdout a bullet in the PR comment
# main_ci = ""              # no remote: run on main after the fast-forward instead of local_ci (unset: local_ci)
remote_checks = "required"  # required | none
post_create = []            # run in a new worktree, new or existing branch ({main}, {wt}, {repo}, {name}, {branch})
publish_on_merge = false    # merging publishes: always the owner's approval
require_approval = false    # every merge needs --approved
risky_paths = []            # "dir/", "file", "glob*", or "file:text" (a changed line contains text)
public = false              # no internal IDs in title, body, comment and the branch's commit messages
privacy_scan = ""           # extra command: each text on stdin, non-zero exit stops
pre_merge_report = ""       # command whose output is shown before merging
post_merge = ""             # run in the main checkout after the merge ({main}, {branch}, {before}, {head})
plan_check_heading = ""     # default: "## How to check" or "## Cómo se comprueba", a number before allowed
worktree = "{worktrees}/{repo}-{name}"

[resources.sim]             # one table per shared resource (common to every project by name); for a machine
                            # resource (declared in the registry, above) only guard and allow go here
guard = ["simctl boot"]     # command prefixes that must go through `foreman lock sim run …`
allow = []                  # regexes of calls that take the lock themselves
busy = ""                   # command printing users outside the lock (also shown in the panel)
busy_wait = 600
slots = []                  # one holder per slot; run exports FOREMAN_SLOT
dir = ""                    # where the lock lives (also queue, next, jumped, log); default <state root>/locks/NAME/
short_max = 300
# every project that declares the same resource shares one lock: dir, queue, next, jumped, log, busy, busy_wait,
# short_max and slots should agree (after defaults); `foreman check` and the loop pass warn when they do not and
# suggest declaring it once in the registry (a machine resource is never warned about: the machine's keys win)

[deny]
patterns = []               # regexes of commands never run without the owner
message = ""                # added to the rejection
grant_minutes = 30          # how long the owner's yes to a [deny.grants] group lasts (below)
ask_minutes = 5             # how long the question for that yes waits; then it is a no for that call (0: no limit)
drop = []                   # rules inherited from the registry's [machine.deny] this product removes: a pattern as
                            # written there, or "group:NAME" (above, «Machine settings»)

[deny.grants]               # groups of orchestrator-only commands that also need the owner's own yes
# deploy = ["make deploy", "(^|[\\s/])kamal([\\s:]|$)"]

[publish]
means = []                  # what publishing means here (for the rules and the panel)

[panel]
title = ""                  # the name the panel shows for this product (default: its registry name)
wiki = ""                   # a folder of Markdown pages under the home: the panel's Wiki tab (read-only)
wiki_first = []             # pages (file names without .md) listed first in the wiki's general group

[strings]                   # override any text of lib/foreman/i18n/<language>.json (an unknown key: a warning)

[check]
guard_table = ""            # a table of commands and the guard's expected verdict, run by `foreman check`:
                            # one `allow|deny|orchestrator|lock:RESOURCE<TAB>command` per line, `#` comments,
                            # `\n` for newlines (`orchestrator`: denied to an agent, let through for the orchestrator);
                            # `EXPECT@DIR` runs the row as if in DIR, else in the home (docs/internals.md, «git push»);
                            # `file:PATH` is a Write/Edit call writing PATH (relative to DIR or the home; `{state}`,
                            # `{state_root}`, `{registry}` replaced, in a command too): docs/internals.md, «The reserved files»

[[recurring]]
name = "Orchestrator loop"
every = "hourly"
sources = [{ heartbeat = "loop", max_h = 2 }, { label = "ran", glob = "reviews/*-health.md", max_h = 26 }]
# a source is a heartbeat, the newest file of a glob, or { log = "word", file = "log.md" }: the last «## [date] … word»
# entry of a log page; it is late after max_h hours (default 24), or daily at a time:

[[recurring]]
name = "Watch"
every = "daily at 09:30"
sources = [{ label = "ran", glob = "reviews/*-watch.md", at = "09:30" }, { label = "read", heartbeat = "read-watch", at = "09:30" }]
```

## `[project] owner_knows`: what the owner reads

`foreman plain` checks a text written for the owner against docs/owner.md (how to check one is there).
foreman's own checks run where it writes for the owner: `backlog
decision add` refuses a question that fails the check, and issue titles, result lines, progress lines, inbox replies
and the PR title and body of `foreman integrate` get a warning, and the chat reviewer reminds the orchestrator of
what its last message to the owner left unexplained (docs/owner.md). `foreman plain` itself always checks: it is an
explicit request. `owner_knows` lists terms this owner knows
(`["CI", "PR", "main_ci"]`): whole words, any case, never flagged.

## `[[recurring]]` sources: every few hours or daily at a time

A source reads one thing (exactly one of `heartbeat`, `glob` or `log`; `label` names it in the panel and in
`foreman recurring`) and is late in one of two ways:

- `max_h` (hours, default 24): late when its last run is more than `max_h` hours old. A daily task with
  `max_h = 26` drifts later every day (it may run up to two hours after the previous one, plus the loop's wait).
- `at = "HH:MM"` (local time, `00:00` to `23:59`): late once today's HH:MM has passed and the source has nothing after
  it; before HH:MM, late only if yesterday's HH:MM still has nothing after it (a missed day stays late until it is
  done, also past midnight). «Done» is what the source already records, so a daily task is done when its report
  (`glob`) is written or its heartbeat (`foreman recurring done NAME`) is beaten after HH:MM. A `log` entry has only a
  date, so with `at` it counts as done for its whole day. `at` and `max_h` do not go together in one source.

Never run is late either way. Late recurring tasks are an actionable `recurring_late` notice in the loop pass (the
loop's own task, named as `[loop] prompt`, never counts). A bad `at`, a source with no kind or with two, or a
`max_h` that is not a positive number is an error; an unknown source key is a warning.

## `[repos.*]` local CI: runs and summary

A product's own commands at foreman's points, and an example `ci_summary`: docs/extending.md, examples/extend/.

`foreman integrate` runs `local_ci` once per value of `ci_runs`, in order, with the value in `{ci}`; the first run
that fails stops the integration. The default `[""]` is one run with an empty `{ci}`, so a `local_ci` without the
placeholder works as before. `foreman integrate … --ci=ARGS`, repeatable, replaces `ci_runs` for one integration
(write it with `=` when ARGS starts with `-`: `--ci=--quick --ci=--full`); `--ci`, or a `ci_runs` with arguments,
without `{ci}` in `local_ci` is exit 2 (arguments are never appended blindly). `--check` lists the runs. With
`remote = "none"` the same runs repeat in the main checkout after the fast-forward, unless `main_ci` is set (below).

Every run's output (stdout and stderr) is written as it comes to `<state>/integrate/<branch>-ci.log` (a `/` in the
branch becomes `-`; one file per integration, the runs on main appended), so a long CI can be followed with
`foreman wait`. The PR comment is «Local CI passed on SHA in N s.» for the single default run, or «Local CI passed on
SHA; runs: `ARGS` (N s), …» otherwise. With `ci_summary` (a command run where the CI ran, with the same placeholders,
each run's output on stdin), its stdout goes under that line as one bullet per run; the comment passes the public
scan (`public`, `privacy_scan`) before anything is pushed. Without a remote there is no comment and `ci_summary` does
not apply; the note gets the first line.

## `[repos.*] main_ci`: what runs on main after the fast-forward

Without a remote, the fast-forward leaves in main exactly the commit the local CI just passed on in the worktree, so
running the whole `local_ci` there again mostly repeats it; what can differ is what main's checkout has that the
commit does not (uncommitted files, such as a home inside the repo). `main_ci` is the command run in the main checkout
after the fast-forward instead, with the same placeholders as `local_ci` (`{wt}` is then the main checkout): once
per run of the local CI if it uses `{ci}`, once otherwise. Its output goes to the same log; the note says «On main:
main_ci passed on SHA in N s.» If it fails, main goes back as with the local CI.

- **Unset** (the default): `local_ci`, the same runs, as before.
- **Main is not the commit the CI passed on** (the branch moved after its CI, say): the full `local_ci` runs on main
  whatever `main_ci` says, and the note adds «full local CI, not main_ci».
- **`""`**: nothing runs on main; the note says «On main: nothing run (main_ci is empty; SHA is the commit the local
  CI passed on)». The risk: whatever main's checkout has that the commit lacks (uncommitted or untracked files, a home
  inside the repo, the environment of the main checkout) is never checked before the branch counts as integrated.
  A `main_ci` that checks exactly that (`foreman check` on the home, for instance) is usually the better choice.

With a remote it does not apply (`--check` says so): the merge happens on the remote, after its checks. A
placeholder it does not know is exit 2 before anything is done. `--no-ci` skips both, as before.

## `[repos.*] post_merge`: a step after the merge

`post_merge` is a command `foreman integrate` runs in the main checkout once the branch is in main: after the PR's
merge and the pull, or after the fast-forward and the local CI on main without a remote; always before the note and
the backlog close. Its placeholders are `{main}` (the main checkout), `{branch}`, `{before}` (main's commit before
the merge) and `{head}` (main's commit after it), both full SHAs, so `git diff {before}..{head}` is what the
integration brought (for instance, regenerate a file when a public surface changed). Its output is shown and its last
non-empty line goes into the note. If it fails, the merge stands (it is already in main, maybe published): a
warning, a `foreman progress` line from the orchestrator, the note says so, the backlog is closed as usual and the
exit is 0; rerun it by hand. It runs with a fixed time limit of 600 s (no key changes it; see the contract below):
past it the command's whole process group is stopped and it counts as failed, with «post_merge cut after 600 s» in
the warning, the progress line and the note. An unknown placeholder is exit 2 before anything is done. `--check`
prints the command with `{before}` and `{head}` unreplaced. Its variables, `FOREMAN_BEFORE` and `FOREMAN_HEAD`, need no
quoting (docs/extending.md).

## `[deny] patterns`: what the regexes see

Each pattern is a Python regex. The guard splits a Bash line the way bash does (`&&`, `;`, pipes, `$(…)`, `bash -c
"…"`, a heredoc a shell reads, `xargs`, `find -exec`…) and compares each pattern with each command it finds, so a
pattern anchored with `(^|\s)` finds a command wherever it starts. The values of data arguments are blanked first
(the text of `foreman note` and `progress`, `git commit -m`, what `echo` prints, the pattern of a `grep`), so a note
that only mentions a command does not match. A `git push` is also compared as git would run it from its folder (its
remote and branch), so a bare `git push` is caught too. The full reading, with what it does not see:
docs/internals.md, «How the guard reads a command».

A rule the patterns cannot say goes in a `PreToolUse` hook of your own, where a `deny` always wins
(docs/extending.md, «Hooks»). Keep a guard table (`[check] guard_table`) with the commands that must and must not
pass, and run `foreman check` after changing the patterns or foreman.

## Seeing the permissions and testing a change

`foreman permissions` shows what the guard refuses, in plain bands: foreman's own rules (for everyone), `[deny]
patterns` (never without the owner), each `[deny.grants]` group (only the orchestrator, after the owner's yes: with
`grant_minutes` and the group's state as `foreman permissions grants` says it, followed by foreman's own rule for
`foreman permissions apply`, below) and each resource's `guard` prefixes (they wait their turn in `foreman lock`). Each
rule comes with how many rows of the guard table (`[check] guard_table`, or `--table T`) it touches, read as the guard
reads a command, and up to two of them; a rule of the product with no row is a warning, since nothing checks that it
does what it should. Each rule says where it comes from: a rule the registry's `[machine.deny]` gives is marked «(from
the machine)», one both the machine and the product write «(machine and product)»; a line near the top counts them,
and another lists the inherited rules this product removes with `[deny] drop`. In `--json` each rule has `from`
(`foreman` for foreman's own, `machine`, `product` or `both`; a resource's `guard` prefixes are always the product's)
and `inherited` gives `{rules, dropped}`. Then the reserved files (docs/internals.md, «The reserved files»): one line per class with this product's paths (a
folder of the class holds what lies under it), who may write it and its refusals of the last 7 days, and how many other
registered products have theirs reserved too. It ends with the lifeline: the commands the orchestrator runs in every
pass (`foreman start`, `loop`, `check`, `backlog`, `note`, `launch`, `integrate`, `permissions grants`, `permissions`,
and `git -C HOME status|log|add|commit`, `cat HOME/foreman.toml`, `ls HOME`), which must pass for it without any group.
`--json` gives all of it.

Every refusal of the guard hook is also written down in `<state>/guard.jsonl` (docs/internals.md, «The state»): which rule refused,
where it matched and who ran it (an agent, the orchestrator, another session), never the command itself, since some
rules match commands that carry passwords. The view adds each rule's refusals of the last 7 days, and
`foreman permissions --refused [--since 7d|24h|DATE] [--json]` counts them by rule, with who ran into each.

A change to `[deny]` takes effect at the very next call, as the guard reads the file each time, and a `foreman.toml`
that does not load lets everything through. So test it first on a copy:

```
cp foreman.toml /tmp/candidate.toml          # edit the copy (and a copy of the table, with rows for the change)
foreman permissions --candidate /tmp/candidate.toml --table /tmp/guard.tsv
```

It loads the copy strictly with the real home (a load error: exit 2, its message and nothing else), and says the
rules added, removed or moved to another band, each row of the table (`--table`, else the live one) whose verdict
changes for an agent or the orchestrator (before → after), the rows of that table that fail with the copy, and the
lifeline with the copy; with a `--table` other than the live one, also how many rows it adds and removes (a copy
that only adds rows says so, not «nothing changes»). Exit 1 when a row fails or the orchestrator would lose a lifeline command (a rule refuses it,
or puts it in a group: the owner would be asked every time), else 0. A row that becomes allowed is listed, never
hidden. Neither form writes anything, in the home or in the state folder.

Once tested, the orchestrator puts it in place with `apply`, after the owner's yes in the chat:

```
foreman permissions apply /tmp/candidate.toml --table /tmp/guard.tsv [--opens]
```

- **The base.** `foreman permissions` shows a base, a hash of the live `foreman.toml`. The copy names it with a line
  `# foreman permissions base: HASH` (or `apply --base HASH`); if `foreman.toml` changed after the copy, or the copy
  names no base, nothing is written (exit 2): copy it again. The line is left out of what is written.
- **The test.** The same as `--candidate`, with the same exits: nothing is written unless it passes.
- **Opening needs the owner's own yes.** A change that allows something that was not allowed before (a rule removed,
  or moved out of «never»; a table row whose verdict gets looser for an agent or the orchestrator; a longer
  `grant_minutes`; a resource `allow` added; a command foreman runs on its own added or changed; a brake of the
  integration taken off, below) is refused (exit 2, nothing written) without `--opens`. With `--opens`,
  the command belongs to foreman's own `[deny.grants]` group `permissions`, present in every product, so the guard
  lets it through only after the owner answers yes to the hooks module's question (or approves it in the panel),
  for `grant_minutes`. A change that only restricts, or changes nothing that is allowed (rows added to the table),
  needs no question.
- **Commands and brakes count as opening** (for `apply` and for a branch at `foreman integrate`). The commands
  foreman runs on its own run with the orchestrator's power and the guard never sees them: `[loop] checks`,
  `[resources.*] busy` and each repo's `local_ci`, `ci_runs`, `main_ci`, `ci_summary`, `privacy_scan`,
  `pre_merge_report`, `post_merge` and `post_create` (every point of docs/extending.md, «The
  contract»). One added, changed or emptied (a CI that no longer runs) opens. So does a brake of an integration that
  comes off: `require_approval`, `publish_on_merge` or `public` from true to false, `remote_checks` from `required`
  to `none`, an entry removed from `risky_paths`. A brake put on, a `risky_paths` entry added, or a repo or resource
  removed (nothing of it runs any more) opens nothing: `foreman integrate` shows it («Also changes») and goes on.
- **Who.** Whatever the product's lists say, a built-in rule of the guard lets only the marked orchestrator's main
  thread, in its own process, run `foreman permissions apply`: an agent, a subagent or another session is refused.
- **What it writes.** A copy of the live `foreman.toml` (and of the table it replaces) in
  `<state>/permissions/AAAAMMDD-HHMMSS.toml` (`.tsv`); then the table (where the candidate's `[check] guard_table`
  says) and `foreman.toml`, each from a temporary file in its own folder with an atomic rename, so the guard never
  reads half a file; a strict load of the result (on a failure the copies go back, exit 1); and a note with what
  changed. It prints the command that undoes it: the copies applied as one more candidate.

**A branch that changes them.** A product whose home lives inside one of its repos (foreman's own) has its live
`foreman.toml` and guard table in the code it integrates: once merged, the branch's copies are the live ones. So
`foreman integrate` makes the same test as `apply` when a branch changes the live `foreman.toml` or guard table of a
registered product (as files of the main checkout; a copy under `examples/`, in a test or in a worktree is not
live), on what main will hold after the merge (with main's own home commits merged in), with the installed foreman,
and before any CI or push:

- the new `foreman.toml` does not load (a bad value, or the branch deletes it): exit 2, nothing merged, no way to
  force it (the product would load with no guard). A key the installed foreman does not know is only a warning (the
  branch may bring its code; a `[repos.*]` one is refused by the next integration until a foreman that knows it is
  installed);
- a row of the table fails, or the orchestrator would lose a lifeline command: exit 1, nothing merged;
- it allows something that was not allowed before (as for `apply`): exit 3, nothing merged, a progress line; rerun
  with `--opens` once the owner said yes in the chat, and the guard asks the owner yes/no first (the same group
  `permissions` and the same yes as `apply --opens`). `--approved` does not stand for it: with risky paths too,
  both are needed;
- it only restricts, or changes nothing that is allowed (rows added or removed: «N rows added, M removed»): the
  integration goes on.

The note of the integration says what changed and how to see the previous files (`git show BEFORE:PATH`, to undo it
with `permissions apply`). `--check`
shows the same section and says whether it would stop or need `--opens`. The other reserved files inside the repo
(Claude Code's settings, a state, the registry) count as risky paths: exit 3, `--approved`.

If a change ever leaves the orchestrator unable to run foreman, the copy in `<state>/permissions/` is still there,
and the owner can copy it back by hand in a terminal (no session may: the guard refuses that write).

## `[deny.grants]`: what only the orchestrator runs, after the owner's own yes

Some commands are for the orchestrator only, once the owner has said yes: a deploy, a release tag, a login to a
server. Put them in a named group of `[deny.grants]` (a name of lowercase letters, digits and `-`, and a list of
regexes read the same way as `patterns`, above):

```toml
[deny]
patterns = ["(^|\\s)rm -rf /", "secrets\\.production"]
grant_minutes = 30          # how long a yes lasts
ask_minutes = 5             # how long the question waits (0: until the owner answers)

[deny.grants]
deploy = ["make deploy", "(^|[\\s/])kamal([\\s:]|$)", "(^|\\s)git push .*(v[0-9]|--tags)"]
stores = ["fastlane (release|deliver)"]
```

A command that matches a group's pattern passes only when all of this holds:

- no pattern of `patterns` matches it (those win for everyone, the orchestrator too);
- it comes from the **main thread of the marked orchestrator's session**: the hook event's `session_id` is the one
  `foreman session` marked, and it carries no `agent_id`. A subagent shares its parent's session id but always has
  an `agent_id`, so no agent passes, whatever text it copies; another session does not pass either;
- it runs in the **orchestrator's own Claude Code process**: the nearest `claude` process above the guard's hook is
  the one the session recorded when it marked itself (pid and start time, read with `ps`). A copy of the session
  (`claude -p --resume ID`) sends the same `session_id` and no `agent_id`, but runs in another process. A mark
  made by hand from another session records no process, and without `ps` nothing passes: the session marks itself
  again (`foreman session` shows the process). Only the native `claude` binary is recognised (the one the
  installer, the desktop app and other hosts run), not Claude Code run by `node`;
- it has a **live grant** of its group: the owner's own yes, given to that session and that Claude Code process,
  for `grant_minutes`. A command of two groups needs both. The guard checks the grant last, so the owner is never
  asked about a call refused anyway.

The command is written as it is, with nothing in front: who approved it and when is foreman's own record of the
owner's yes, not a text the orchestrator writes. The guard writes each decision on such a command into
`<state>/approved.jsonl` (docs/internals.md, «The state»: an allow carries the grants it used), and each one it lets through is also
a line of today's notes. An agent or another session refused gets the reason, and the next loop pass tells the
orchestrator (`orchestrator_only_denied`).

In the guard table, a row `orchestrator<TAB>command` must be denied to an agent and let through for the
orchestrator; with groups set, `allow`, `deny` and `lock:R` rows are checked for both. `foreman guard --as
orchestrator "command"` shows what the orchestrator would get.

The yes comes only from the owner: foreman's hooks module asks in the orchestrator's chat (Claude Code's own
question, which the model cannot answer) when the orchestrator runs a command of a group with no live grant, and
records the answer through `foreman hook mod grant` (hooks_mod.py). The question shows wherever the owner follows
that chat: the terminal, the desktop app, an SDK host's chat, and the phone through Remote Control (the phone does not
notify: open the session to see it). Its options are «Yes, for N min» and «No»; anything but the yes is a no, and
closing it (Esc, «Chat about this») refuses the call without counting as a no. Where nobody can be asked (`claude
-p`) the call is refused. The command waits while the question is open, and the orchestrator with it. No command
gives a grant: the guard refuses any Bash call that runs `foreman hook mod`, for everyone. A no blocks that call and
the group is not asked again in that session for 10 minutes; a question left unanswered for `ask_minutes` is a no
for that call, and the request waits in `<state>/grants.json` for the owner to approve it later from foreman's
panel. Claude Code cannot close a question, so it stays on screen: until the owner answers or closes it, the
group's commands are refused at once with no new question, and an answer that comes then is written down and grants
nothing (when the group was approved from the panel meanwhile, it says so and changes nothing).

**Approving from the panel.** The panel's You tab lists each pending request (the group, the command, when it was
asked) with an «Approve for N min» button. Approving gives exactly the grant a yes in the chat gives: the group for
`grant_minutes`, to the marked session in its own Claude Code process. It is refused when that is no longer who
asked (another session marked, another process, the process gone, or `ps` unreadable), when the group is already
granted, and when the request is older than `grant_minutes` (a stale request expires: the orchestrator asks again).
One request grants once. The approval is a line of `approved.jsonl` (`via: panel`), a note, and an inbox message
that tells the orchestrator it may run the command again. A grant from the panel carries a signature made with the
panel's own key (`<state root>/panel/grant.key`, mode 600), so the hooks module lets it through without asking
again, while a `grants.json` written by hand is still asked. Like every write of the panel, the approval needs the
panel's key; on top of that, the guard refuses any Bash call that reaches the approval's address
(`api/grants/approve`), for everyone, the orchestrator included: the owner approves from the page, by hand. A host that answers questions by itself (an SDK program with its own permission callback) would answer this
one too: run the orchestrator where a person answers. The grant ends at `until`, when the mark moves to another session
or process, or with `foreman permissions grants off [GROUP]` (anyone may run it: it only takes away). `foreman
permissions grants` lists each group's state. Every answer is a line of `<state>/approved.jsonl`, and a yes a line of
today's notes.

**Without the hooks module** (Claude Code below its minimum, modules off), there is no grant, and every command of a
group is refused with `why=grant`: the groups stay closed. The module keeps the grants it heard given: a grant in
`grants.json` it did not hear (written by hand, or from before a reload or a `/clear`) is asked again. A group
pattern also in `patterns` is a warning of `foreman check` (`patterns` wins). In the guard table and with `guard --as
orchestrator`, every group counts as granted.

## `[loop] checks`: a project's own notices

Each command runs in the home (`bash -c`, 30 s at most, as the contract below says) on every loop pass, never between
passes (an example that reads `FOREMAN_STATE`: examples/extend/ops/checks/state-size.sh, docs/extending.md). Each non-empty
output line is one notice of the pass:

- A line that is a JSON object with a non-empty string `text` says its own notice: `{"kind": "...", "actionable":
  true|false, "text": "..."}`. Without `kind` it is `check`; without `actionable` (or with anything but a JSON
  boolean) it is actionable. An optional `key` names the case when the text changes from one pass to the next for
  the same case (a count, a size); by default the case is the text.
- Any other line (plain text, or JSON without `text`) is a `check` notice, actionable, as before.
- Exit code 0 or 1 is normal (1 may mean «something found»); a timeout or any other code adds a `check_failed`
  notice, actionable.

`foreman check --run-checks` runs each check once, as the loop does, and prints the notice of every line with what
the loop would read differently from what the line seems to say, as a warning: JSON that does not parse or has no
`text` (a plain `check` notice), a kind of foreman's own (reserved or not: a `check` notice), an `actionable` that is
not `true` or `false` (actionable), a `key` that is not a string (ignored). Warnings do not fail it; a check that
would be `check_failed` (another exit code, or cut at 30 s) does (exit 1, with its stderr). Nothing is marked: the
loop's state is untouched. A check that needs a value of foreman.toml asks foreman for it (`"$FOREMAN_BIN" config
--json`, below) instead of parsing the file.

A pass whose only notices are not actionable is skipped (not sent to the model): a check that wants to inform without
waking the orchestrator prints `"actionable": false`. Example, a check that only informs about booted simulators and
asks for action when there are too many:

```bash
#!/bin/bash
n=$(xcrun simctl list devices booted | grep -c Booted)
[ "$n" -eq 0 ] && exit 0
act=false; [ "$n" -gt 2 ] && act=true
printf '{"kind": "simulators", "actionable": %s, "key": "simulators:%s", "text": "%s simulators booted"}\n' "$act" "$act" "$n"
```

## `[strings]`: a product's own wording

`[strings]` overrides the texts foreman writes for a person (lib/foreman/i18n/en.json lists the keys; a table such as
`start` is overridden key by key), in the project's language. The panel's texts (panel/strings.<language>.json) are
foreman's: a product does not override them.

```toml
[strings]
notes_heading = "# Log {date}"

[strings.start]
title = "Start"
```

- An unknown key is a warning (ignored; `foreman check` exits 1 naming it), so a misspelt key is caught instead of
  silently doing nothing. A value that is not a text (or a table of texts where foreman has a table) is an error.
- A plural pair (`x_1` for one, `x_n` for any other number) set in half is a warning.
- Keep the placeholders (`{n}`, `{name}`…) of the text you replace: foreman fills only those.

## Compatibility: tolerant and strict reading, `config_schema`

A foreman.toml (or registry entry) may be read by an older or newer foreman than the one it was written for, so
reading is tolerant where a product must not disappear, and strict where mistakes are caught:

- **Warnings** (the project loads, ignoring them): a section or key this foreman does not know, a
  `config_schema` higher than the one it knows («written for a newer foreman (config schema N, this foreman knows
  M): update foreman»), machine keys repeated in a machine resource, a machine resource of the registry that does not load, and
  a problem of the registry's `[machine]` (each bad value is left out alone; every product carries the warning). **Errors** (the project does not load): a bad value of a known key (a regex that does not
  compile, `merge` outside its list, an invalid name, `config_schema` not a whole number ≥ 1, a section that is not a table).
- **Tolerant** (warnings are shown, the product keeps working): the panel (the product stays in the picker with a
  «N config warnings» tag, each warning a notice in its Now tab), the hooks (the guard keeps guarding; the `who`
  SessionStart line names the warnings) and the CLI (the loop pass starts with a «Config warnings» line).
- **Strict** (a warning fails): `foreman check` (exit 1, naming each key and the schema), the selftest (the
  examples and the repo's own home validate with no warning), and `foreman integrate`, which refuses unknown keys in
  `[repos.*]`, where a typo (`require_aproval`) could merge something unguarded.
- **A product that does not load** (an error, or a registry entry with an unknown `mode` or without name or home)
  is left out, alone: the other products load. It fails open but visibly: no hook acts for it, so the guard lets
  every command through, and its sessions are told so by `who`, its orchestrator by the loop pass; the panel lists
  it in red («does not load») and, when picked, shows its error instead of the tabs. `foreman check` says what to
  fix.

`config_schema` goes up by one with every new (or removed) key or section, so a file that uses a newer key also says
it is from a newer foreman, which tells «update foreman» from «a typo». A file without it is read as this foreman's
schema. The selftest keeps the keys of each schema in `tests/config_schema.json` and fails when the keys change
without a new schema.


## The backlog model: product, projects, issues, sub-issues

| Linear | foreman |
|---|---|
| Workspace | the registry (`~/.config/foreman/projects.toml`) |
| Team | a product: a registered project with its own home and backlog |
| Sub-team | a repo of the product (`[repos.*]`); issues and projects name it with `repo` / `repos` |
| Project | `projects` (groups of work `E1`…): status (backlog, planned, in progress, paused, completed, canceled), priority, other `repos` |
| Issue | an issue (`XX-n`): Linear's workflow states by category (triage; backlog; refining and ready = unstarted; in progress = started; done; canceled and duplicate), priority (urgent, high, medium, low, none), assignee, relations, project optional (`issues` holds those without project), `wanted` (`YYYY-MM-DD HH:MM`: the owner marked it in triage from the panel; kept once taken in, cleared by «no longer»); plus the acceptance criterion and verdict, result, plan, reports, branch and commit |
| Issue relations | `relations`: `blocks` (blocked by is its inverse), `related`, `duplicate_of` (and the status duplicate). An issue is blocked while it has a free-text `blocked` reason or an open issue `blocks` it; closing the blocker (done, canceled, duplicate) frees it with nothing written (the relation stays as history), and `integrate` / `backlog status` say which issues are free now. `foreman check` warns of a `blocks` cycle among open issues |
| Sub-issue | sub-issues `T0`, `T1`…; the last one done closes the issue |
| Triage | the status `triage` |
| Initiatives, milestones, project updates and health, cycles | not modelled |

CLI: `foreman backlog add [PROJECT] --title …` (`--id` optional: the next free id, printed; no `PROJECT`, `""` or
`-`: no project), `project add|edit|remove` with the project fields, `relate`, `edit ID --priority P --assignee A
--repo R` (an empty value clears it), `move ID --project -` (no project). The progress of a project (`done/total` in `show EN` and the panel) counts, as Linear does, the issues
neither canceled nor in triage: a project whose open issues are all in triage shows as complete. The panel's Backlog
tab shows Issues (with a project filter) and Projects, each with its progress.

`foreman backlog list` reads without writing: one line per issue (id, status, project or `-`, title; `*` after the
project for an archived one) and the total, in the file's order. Filters, all of which must hold: `--status S …`
(any of them; the aliases of `backlog status`), `--open` (not done, canceled or duplicate), `--group EN …` (any of
these projects, `-` for none), `--has FIELD` (set) or `--has FIELD=VALUE` (equal, or one of its items: `priority=high`),
repeatable, `--sub-status S` (a sub-issue in that status), `--archived` (the archive too). `--count` prints only the
total; `--by status|group|product` (implies `--count`) one count per status in workflow order, project (`-` for
none) or product. `--json`: the issues with `product`, `group`
and `archived`, or `{"total", "by", "counts"}`. `--all-products` reads every product of the registry that has a
backlog, each from its own `[backlog] file`, in the registry's order, its projects named `product/EN`; a product whose registry entry or backlog does not load is said on stderr and left out (it does not
go with `--project`). Bad filters: exit 2.

The backlog file, `backlog.json`, is JSON of schema 5, written only by `foreman backlog …` (never by hand: the pages
are generated from it). A file of an older schema is refused with what to do (`foreman check` names it). Old ids
stay valid through `ids.legacy`.

`foreman check` validates the file, the rules file, the guard table and the backlog (`foreman guard --table FILE`
runs a table alone), and prints the registry's own warnings as «(registry) …» (machine resources included); each
resource line says whether it comes from the machine (registry) or this project. It also warns, without failing,
when another registry project declares one of this project's resources with a different `dir`, `queue`, `next`,
`jumped`, `log`, `busy`, `busy_wait`, `short_max` or `slots` (values after defaults, so an omitted key equal to its
default agrees), suggesting to declare it once in the registry; never for a machine resource. With `--run-checks` it
also runs each `[loop] checks` command once and validates its lines («[loop] checks» above).
`examples/tidepool.toml` is a full project.
