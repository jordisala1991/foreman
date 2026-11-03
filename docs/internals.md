# foreman's internals

For contributors, and for whoever wants to know exactly what foreman does: how the panel works, which Python it runs,
how the guard reads a command and what it never lets an agent write, and the state foreman keeps. A project's
settings are docs/config.md; nothing here needs configuring.

## The panel

`foreman panel [--port 8766] [--host 127.0.0.1] [--token T]` serves one page for every registered project: backlog,
agents, the common locks, branches and open PRs of each repo, decisions and inbox, recurring tasks, progress and the
notes of the last hour. Its key is `--token`, `$FOREMAN_PANEL_TOKEN` or `<state root>/panel/token` (created once).
The panel writes the inbox (messages, decision answers, PR approvals that the orchestrator passes to `foreman
integrate --approved`), the answer to a decision (one of its options, `/api/decide?id=…&op=…`, or the owner's own
words, `/api/decide?id=…&free=1` with the text as the body, at most 2,000 characters, marked `free`: it resolves the
decision in the backlog at once, under the backlog's lock, with `by` «Owner, YYYY-MM-DD HH:MM (panel)», and leaves an
inbox message for the orchestrator to apply it; if the backlog cannot be saved, the inbox message alone, 202; an
answer the owner gives in the chat, closed with `foreman backlog decision resolve ID --option N` or `--answer …
--free`, gets `by` «Owner, YYYY-MM-DD HH:MM (chat)» and no inbox message),
backlog moves, the owner's mark on a triage issue of an active product («I want it
done», `/api/want`: the issue's `wanted` field and an inbox message) or its discard («I don't want it»,
`/api/reject`: canceled with the panel's reason and an inbox message), the pause of an active product (`<state>/pause`, which the loop pass, the guard
and the `pause-agents` hook respect) the owner's turn on a lock an active product uses (not one with `slots`), and the owner's approval of a pending
`[deny.grants]` request (docs/config.md, «`[deny.grants]`»).
`FOREMAN_PANEL_PRS=0` turns off the `gh pr list` calls.
A resource with `busy` shows what that command prints (its users outside the lock) under its row in Resources, at
most 6 lines (all of it, up to 4000 bytes, in its detail), as plain text; nothing when it prints nothing. The panel
runs it as `foreman lock` does (`bash -c`, the resource's `FOREMAN_LOCK_*` environment, only its stdout counts), in
the registry's folder for a machine resource, else in the home of the project whose declaration it uses, in a
background thread: a request never waits for it, gets the
last result (`checking…` until the first one) and starts a new run once that is 5 s old. A run is killed after 5 s
and the card says it did not answer in time; a non-zero exit with nothing printed says it failed. The API carries it
in each lock as `busy` (`state` ok, pending, timeout or error; `output`; `truncated`; `since`, the local time its
result last changed, shown as «unchanged since»), absent for a resource without `busy`.
The header's title picks the product (a sheet with every registered project and «all products», the joint view: every
list of every project, each row tagged with its project). The panel names a product by its `[panel] title` (picker,
header, browser tab, the tags of the joint view, the report header, locks), else by its registry name, which stays its
id in the API (`name`, `?p=`); the picker and the System tab also show the panel language. A project with `[panel] wiki` gets a read-only Wiki tab with
search (`/api/wiki?p=`, `/api/wiki/page?p=&n=`, `/api/wiki/search?p=&q=`); `/api/state?p=*` answers for all projects.
`/api/state` (polled every 5 s) carries no clocks: times are epoch seconds (`activity_ts`, `start_ts`, a lock's
holders' and queue's `since_ts`, a branch's `committed_ts`, `report.ts`, a recurring task's `last_ts`), never ages, so
an unchanged state keeps its ETag and the poll is a 304; the page counts the ages from the response's `Date` header and
repaints only those labels (and the marks that change with time: a lock held over 3 h turns red, an agent running
over 2 h gets its warning). The machine's stats (`mac`) come with `/api/projects` (polled every 30 s).
The backlog is its own request, `/api/backlog?p=NAME` (`p=*`: every project), polled with the state: each project's
cards (`projects`, `issues`, `archive`, `counts`) and `backlog_error`. Its body is serialized once per
change of the backlog file or a reports folder and its ETag is the body's hash, so an unchanged poll is a 304. The
state carries no cards: what is live about an issue comes in `issue_live` (`{ID: {agent, agent_status, branch,
production, status, no_agent}}`, only what applies), which the page lays over the open cards.
A closed card (done, canceled, duplicate, or archived) comes short there, `"short": true` with only what its row shows
(id, title, status, priority, plan, a `blocked` reason, the `outcome.aliases` a link may name,
and its sub-issues' id, title, status, `ref` and `doc`); open cards come whole. `/api/issue?p=NAME&id=ID` gives the
whole card of any issue (`{"issue": card, "version"}`, 404 when there is no such product or issue), which the page
loads when a short card is opened and keeps until the backlog changes.
`/api/backlog?p=NAME&closed=recent` (also with `p=*`) keeps the first load from growing with old closed issues: open
cards whole, each product's 15 most recent closed issues that are not archived short as above (newest `closed` date
first; without a date, the later in the backlog file first), and every other closed or archived card a stub in its
place, `"stub": true` with only `id`, `status`, `archived` and its `project` when archived, and the
`outcome.aliases` a link may name. Stubs keep every id where it was, so counts, the progress of each project, and
links still find them; `/api/issue` gives the whole card. `counts` are the same in both bodies, and
each body has its own ETag. Without `closed`, the body is the one above.
The page's version is the newest mtime of `index.html`, `app.js` and `app.css`: the server writes it into the page
(`<html data-v>`, `?v=` on both assets, which are then cached as immutable) and into every JSON answer (`version`);
`/api/version` answers it alone. The page reloads itself (once per server version, sessionStorage) when an answer
carries another version than its own, and on a failed poll it asks `/api/version`, so code from an older version
(a restored tab) does not stay stuck on an error.

**Following the installed version.** Python code is loaded once, so a panel keeps running the version it started
with. The hooks record where Claude Code runs foreman from: on each session start and prompt in an active project,
the plugin root (`CLAUDE_PLUGIN_ROOT`) and its version go to `<state root>/install/installs.json`, and
`<state root>/install/root` lists the usable roots, the chosen one first: the highest version that still exists and
is not marked `.orphaned_at` (a cache folder Claude Code replaced and will delete), the one recorded last between
equal versions. A git working copy (the root has `.git`, a folder or a worktree's file) goes behind every installed
copy whatever its version, and is chosen only when nothing else is usable: a clone loaded in place runs unpublished
code, and a session opened from it before switching to an installed copy keeps recording it. Sessions loaded with
`--plugin-dir` are not recorded, and a root no hook reported for 14 days is forgotten. `foreman panel
--follow-installed` checks that file every 30 s; when the chosen root or its version differ from what it runs, it smoke-tests the new root (`bin/foreman --version` and importing its panel module) and, if that
passes, re-executes itself as `<root>/bin/foreman panel` with the same arguments (same process, same port; the page
reloads itself). If the smoke test fails it keeps running the old version and says so in the System tab (and in
`/api/projects`: `foreman_version`, `foreman_root`, `foreman_follows`, `foreman_warning`). The System tab shows the
running version and folder.

**A session's own version.** Claude Code keeps a session on the plugin version it started with (its hooks and the
`foreman` on the Bash tool's PATH) until the owner types `/reload-plugins` in it or resumes it; after an update it
marks the replaced cache folder `.orphaned_at`. The same hooks record each session in
`<state root>/install/sessions.json` (`{session: {version, root, kind, project, cwd, seen, last, told}}`; written only
when something changes or once an hour, records not seen for 2 days dropped, at most 200) and, when a session runs an
installed copy older than the installed one (or its folder is marked `.orphaned_at`), tell it once per installed
version: the model hears which version it runs, which one is installed, that only the owner can type
`/reload-plugins` and where the CHANGELOG is; the owner gets one line as the hook's `systemMessage`. Working copies,
`--plugin-dir` sessions and subagents are never told. A session that gets no other prompt (an agent launched with
a single one) hears it from foreman's hooks module: its tick stats the plugin folder's `.orphaned_at` once a minute
and the text rides the result of the session's next tool call. A session with no record (it started on a foreman older than this) gets an estimate:
the highest installed version first seen (`seen` in `installs.json`) before the session started, or before the last
`/reload-plugins` typed in it when its transcript has one (a user line whose content is that command; read once, then
only what was appended), shown as «≈X». A `/reload-plugins` typed before any session recorded the new version is
estimated one version short; once a session runs a foreman that keeps the record, its next prompt replaces the
estimate. `foreman session --all [--behind] [--json]` lists the live sessions of every registered product with their
version (docs/orchestrator.md). The `foreman` CLI of such a session, run from an `.orphaned_at` folder, says the same
on stderr for commands that act («this is foreman X, the one this session started with…»), once per root and version:
once per session when `CLAUDE_CODE_SESSION_ID` is set, otherwise at most once every 10 minutes
(`<state root>/install/orphan-told.json`, `{root: {version, at, sessions}}`).

**The panel as a service.** `foreman panel service install|uninstall|status|print [--label L] [--host
127.0.0.1] [--port 8766] [--replace] [--platform macos|linux]`: a LaunchAgent (macOS) or a systemd user unit (Linux) running
`<state root>/install/panel-launcher`, a small script that execs the first usable root of `install/root` that is not a working copy (else the first
usable one) as `panel --follow-installed`. The unit names no plugin folder (they are versioned and deleted after an update) and no
key: the panel reads `<state root>/panel/token` (created with mode 600). `install` also writes the launcher, the
trace if there is none yet (the foreman running the command), and loads the unit (`launchctl bootout` and
`bootstrap gui/<uid>`; `systemctl --user daemon-reload`, `enable`, `restart`). launchd unloads asynchronously, so
`install` waits up to 10 s for `launchctl print` to stop finding the label and retries `bootstrap` a few times; if it
still fails it prints the commands to load it by hand. The PATH of the unit is the installer's (the panel runs git,
gh, Python and the projects' lock scripts from wherever they are installed), without relative entries, missing or
temporary folders and plugin folders, plus the system folders; install the service again after moving those tools.
**Which unit.** There is no fixed default label: without `--label`, `install`, `uninstall` and `status` take
`foreman.panel` when this state root has no panel unit or has one by that name, the only panel unit of this state
root when it has another name (and say so: `label     the only panel service here is X …: reinstalling it`), and
with several of them refuse to guess (exit 2; `status` lists them). Reinstalling a unit without `--host` or `--port`
keeps its own address (else 127.0.0.1:8766). Before writing anything, `install` looks at the other panel units:
one of this state root on the same port (same port and the same host, or either a wildcard) makes it exit 2,
naming that unit and the two ways out (`--label` of that unit to reinstall it, or `--replace`); `--replace` unloads
and removes those units first (on macOS waiting for launchd to unload them) and leaves the launcher. A clashing unit
of another state root is refused even with `--replace` (another foreman's). Units on other ports get a `note` line;
a port held by a process that is no panel unit (a panel started by hand) only a `note` with its pid. `print` reads
no unit: it shows what `install` writes for the given values (default label and address).
`uninstall` unloads it and removes the unit, and the launcher unless another panel unit in the service folder still
runs it (it says which; the key stays).
`status` exits 0 when the unit is installed and running; it also shows the unit's address (`port`; when the unit
is not running and something answers there, the pid `lsof` gives and which panel unit it is, or `not a foreman
service`) and one `other` line per other panel unit in the service folder (its port and state; a unit of another
state root says so), which do not change the exit code. `print` writes nothing (the unit on
stdout, the launcher on stderr). Logs: `<state root>/panel/panel.log` on macOS, the user journal on Linux.
FOREMAN_SERVICE_DIR, FOREMAN_LAUNCHCTL, FOREMAN_SYSTEMCTL and FOREMAN_LSOF replace the unit folder and the
executables, FOREMAN_LAUNCHD_WAIT and
FOREMAN_LAUNCHD_PAUSE the unload wait limit and the pause between attempts, in seconds (tests).

## Python: how the launcher picks one

foreman needs Python 3.9 or newer (standard library only; on 3.9 and 3.10 it reads TOML with its own copy of
tomli, the parser that became 3.11's tomllib), so the Python that macOS ships with its Command Line Tools (3.9, which
git on a Mac already requires) is enough. `bin/foreman`, the command every hook and every CLI call
runs, is a plain POSIX sh launcher; foreman's Python entry is `lib/main.py`, and the launcher execs it with a Python
that fits. Without `FOREMAN_PYTHON` it runs the PATH's `python3` without asking its version first (as cheap as a
`python3` shebang); the entry checks the version the launcher passes (`FOREMAN_PYTHON_MIN`) and, when that Python is
older, runs the launcher again (`FOREMAN_PYTHON_PROBE=1`), which then asks each candidate (`python -V`) in this order:
`$FOREMAN_PYTHON`, `python3` and `python3.N` on the PATH, mise (`$MISE_DATA_DIR` or `~/.local/share/mise`,
`installs/python/*`), Homebrew (`/opt/homebrew/bin`, `/usr/local/bin`) and `/usr/bin/python3`. A `FOREMAN_PYTHON`
that is too old is skipped like any other. The minimum is written once, as `min_python` at the top of `bin/foreman`.
Run `bin/foreman`, never `python bin/foreman` (it is not Python); code that already runs in a Python that fits (the
tests) may run `lib/main.py` with it. `foreman selftest` checks that the launcher holds no Python and that nothing in
`lib/` or `tests/` runs it through a Python interpreter.

## The git foreman runs: without hooks, except a push

Every git command foreman runs itself (`foreman integrate`, `foreman worktree`, the release checks, and the reads of
the panel, the loop and the other commands) goes with `-c core.hooksPath=/dev/null -c core.fsmonitor=false`
(`util.GIT_QUIET`): the repo's git hooks (`post-merge`, `post-checkout`, `reference-transaction`,
`pre-merge-commit`, `post-rewrite`…) and an fsmonitor command do not run, whatever the repo's config or
`core.hooksPath` say. A worktree shares `hooks/` and the config with its main checkout, so a hook an agent (or a
script of its) left there would otherwise run with the orchestrator's power when it integrates. The one exception is
`git push` (the branch of a PR, and deleting it after the merge): it keeps its hooks, because a `pre-push` is where
tools like git-lfs upload their objects; the `reference-transaction` of the refs it updates runs then too. A step
that must run after a merge goes in `post_merge` (docs/config.md), not in a git hook. Not covered by these options: what the
config itself makes git run (`filter.*`, `diff.*.textconv`, `merge.*.driver`), which no single option turns off,
nor the `local_ci` and `main_ci` the branch brings (code in its diff). The git commands an agent or the owner run by hand keep their
hooks as usual.

## The reserved files

On Write, Edit, MultiEdit and NotebookEdit the guard hook (it runs on every tool: «Any other tool») refuses a call that
writes a reserved file, the same for every product and for anyone in the session: the live foreman.toml and guard
table (`[check] guard_table`) of every project of the registry, foreman's state (the state root, and a `[paths]
state` outside it), the registry (with the default one, its whole folder `~/.config/foreman/`), and Claude Code's own
files (`~/.claude/` but its `projects/` and `plans/`, `~/.claude.json`, and `settings.json` or `settings.local.json`
in any `.claude` folder). The one exception is the product's board, which its marked orchestrator's main thread
writes. A path is compared as the file it reaches (relative to the session's folder, symbolic links and `..`
resolved, case ignored on macOS, a hard link by its inode). The configuration and the table change only with
`foreman permissions apply` (it tests the change, keeps a copy and asks the owner when it opens something); the
registry and Claude Code's settings, only by the owner: by hand, or with the owner's yes in the chat that asks, for
that one change. foreman's hooks module asks it in the main thread of any session of the product (never for an
agent's call, which stays refused): a Write, Edit, MultiEdit or NotebookEdit of such a file, or a Bash command of at
most 300 characters that writes one by name, shown whole. The yes is for that call only (its `tool_use_id`): the
guard lets it through once and the next write asks again; a no, a dismissal or no answer in time refuses it, with no
cooldown; a session nobody can answer (`claude -p`) is refused. The module tests the path or command as text first
(`.claude`, `.config/foreman`, `projects.toml`), so a file reached otherwise (a link, a custom `$FOREMAN_REGISTRY` or
`$CLAUDE_CONFIG_DIR`) is refused with no question. A refusal of an agent or another session reaches the
orchestrator (`orchestrator_only_denied`); every refusal is a line of `<state>/guard.jsonl` with the path, never what
the call would write. If the check itself fails it refuses only a path that looks reserved. `foreman guard --file
PATH [--as agent|orchestrator] [--cwd DIR]` says what it would do; a guard table checks it with `file:PATH` rows.
`foreman permissions` lists them in their own band (the paths of each class for this product, who may write
it, and its refusals of the last 7 days), and `foreman permissions --refused` counts them as `file:<class>`.
A Bash command that writes one by name is refused the same way, before `[deny] patterns` (and after foreman's
built-in rules): the target of an output redirection (`>`, `>>`, `&>`, `<>`; not `/dev/null`, a descriptor) and of
`tee`, the destination of `cp`, `mv`, `ln`, `install`, `rsync`, `ditto` and `scp` (each source's name inside it when
it is a folder; the sources of `mv` too), the files of `sed -i`, `gsed -i` and `perl -i`, the operands of `rm`,
`rmdir`, `unlink`, `truncate`, `touch`, `shred`, `chmod`, `chown`, `chgrp` and `chflags`, `dd of=`, `curl -o`, `wget
-O`, and the starting folders of a `find` that deletes or runs one of those; behind `env`, `sudo`, `timeout`,
`foreman lock … run` and the other wrappers, and inside `sh -c`, `eval`, `$(…)`, `xargs`, `find -exec`, and a heredoc
or a pipe into a shell. A recursive one (`rm -r`, `chmod -R`, `cp -R`, `rsync -a dir/`) is refused when the folder
holds a reserved file. A target is read with `~`, `$HOME` and the line's own variables (`f=…; echo x > "$f"`)
expanded, relative to the session's folder and the line's `cd` and `pushd` (as for a `git push`: a `cd` it cannot
follow keeps the session's folder as one of them). A target it still cannot know is refused only when its text names
a reserved path (`$X/.claude/settings.json`, `${D:-~/.local/state/foreman}/x`); a line the lexer cannot read, only
when it looks like a write and names one. Reading them (`cat`, `grep`, `diff`, `cp foreman.toml /tmp/cand.toml`) is
free, and so is text that only mentions a write (a quoted heredoc to `cat`, `foreman note`, `git commit -m`). The
refusal is `file:<class>` too, with the command around the name. A guard table's Bash rows get the same `{state}`,
`{state_root}` and `{registry}`. Not seen: a program that writes them by itself (`python -c "open(…).write(…)"`, a
script written earlier), `git checkout` or `git restore` of them in the main checkout by the orchestrator (an
agent's is refused: «The main checkout», below; a merge by `foreman integrate` tests them first: docs/config.md, «Seeing the
permissions and testing a change»), an archive unpacked over them, `find -exec rm {}` below a folder that is not
itself reserved, and a variable whose value comes from outside the line.

One more class, `git`, is refused to an agent only (a subagent, or a call with no session): the orchestrator and the
owner's other sessions of the product write it, as they run git. It holds what git runs or reads as instructions in a
protected repo, the repo of each live home's checkout (the nearest folder up with a `.git` entry from each live
foreman.toml and guard table of the registry) and of each `[repos.*] path` of every product: in the git directory
the repo's worktrees share (its `.git` folder; a worktree's `.git` file leads there through its `commondir`), its
`hooks/` and `info/` folders, `config`, `config.worktree` and every `worktrees/*/config.worktree`, plus an absolute
`core.hooksPath` those configs give and the files they include (`include.path`, `includeIf.*.path`). It also holds
the machine's git configuration: `~/.gitconfig`, `$XDG_CONFIG_HOME/git/` (or `~/.config/git/`), `$GIT_CONFIG_GLOBAL`
and `$GIT_CONFIG_SYSTEM` when set, `/etc/gitconfig`, `/opt/homebrew/etc/gitconfig` and `/usr/local/etc/gitconfig`,
what they include, and the real file behind each link. A hook or a setting an agent left there would run in no diff,
with the power of whoever runs git next in that repo: the orchestrator when it integrates, or the owner. Everything
else git writes stays free (`objects/`, `refs/`, `logs/`, `HEAD`, `index`, `COMMIT_EDITMSG`, the rest of
`worktrees/`), and so does reading the class. It is found on the disk, without running git; a failure to read it
refuses nothing. For one command an agent passes the setting itself, `git -c KEY=VALUE …`. The refusal says
`file:git` and reaches the orchestrator like the others. Not seen yet: a git command that writes them (`git config`,
`git remote add`, a hook installer), and a program or script that writes them by itself; `foreman integrate`, the
worktrees and a release run git with hooks off (but `git push`), so what such a writer leaves does not run there.

## The main checkout and `foreman integrate`: the orchestrator's

Two built-in rules refuse an agent (a subagent, or a call with no session) what only the orchestrator does; its
refusal reaches the orchestrator (`orchestrator_only_denied`), and the orchestrator and the owner's other sessions of
the product pass, in every product:

- `foreman integrate` run as a command (`bin/foreman`, `--project P`, `python3 bin/foreman`, behind `timeout` or
  `foreman lock … run`, inside `sh -c` or `$(…)`), in any folder, except with `--help`, `-h` or `--check` (which stops
  before it writes anything); its mention in a note, a commit message, a `sed` script or a heredoc is not. An agent
  delivers with `foreman progress --done`.
- A git that rewrites the files of a main checkout holding a live home: the checkout (the nearest folder up with a
  `.git` entry) of the live foreman.toml or guard table of any product of the registry (a home inside its repo, such
  as `ops/`, or at its top). A worktree outside it or nested in `.claude/worktrees/` has its own `.git` file, so it is
  not that checkout. What is refused there: `merge`, `pull`, `rebase`, `cherry-pick`, `revert`, `am`, `switch`,
  `clean`, `filter-branch`, `checkout-index`, `bisect` (but `log`/`view`), `read-tree -u`, `sparse-checkout` (but
  `list`), `submodule update`, `apply` (but `--check`, `--stat`, `--numstat`, `--summary`, `--cached`), `reset --hard`,
  `--merge` or `--keep`, `stash` (no subcommand, `push` without paths, `save`, `pop`, `apply`, `branch`), a `checkout`
  of a branch or a commit (`-b`, `-B`, `--orphan`, `--detach`, `-f` alone, one word that is no path); and, when one of
  their paths reaches a live file (the file, a folder that holds it, the checkout's `.claude/settings*.json`, a
  `:` pathspec, a path with a variable or a wildcard whose literal start is a folder holding one), `checkout --
  PATHS`, `restore` (but `--staged` alone), `rm` (but `--cached`), `mv` and `stash push -- PATHS`. Reading (`log`, `diff`, `show`, `status`, `stash list`), `commit`, `add`, `branch`,
  `worktree`, `fetch`, `reset --soft` and a mixed `reset` pass, and so do the path forms on other files
  (`git checkout -- README.md`, `git stash push -- tools/x.py`).

The folder of each git is the session's, moved by the line's `cd` and `pushd` and its `-C`, plus a `--work-tree` or
`GIT_WORK_TREE` it is given; also inside `sh -c`, `$(…)` and the other nested commands. A `cd` that may not have run
(after `;`, `||`, in a subshell) keeps the folder before it too, but `cd DIR && git …` runs only in DIR, also when the
line cannot name it (`cd "$X" && git merge main` passes; `cd "$X"; git merge main` from the main checkout does not). A
folder the line cannot name passes unless the line names a main checkout as text. A git alias is read as the command
it stands for. Not seen: a git command that writes the `.git` its worktree shares with the main checkout (its writes
by name are the `git` class, «The reserved files»), a
branch moved without touching files (`update-ref`, `branch -f`: the copy is then dirty and `foreman integrate` stops),
`patch` or an archive over the copy, and git run by a program or a script. `foreman guard --as agent --cwd DIR
"git merge main"` says what it would do; `foreman permissions` shows both rules as foreman's own.

## How the guard reads a command (`[deny] patterns`)

A rule the patterns cannot say goes in a `PreToolUse` hook of the product's own, where a `deny` always wins
(docs/extending.md, «Hooks»).

The guard reads a Bash command the way bash does (quotes, escapes, comments, redirections, heredocs, the separators
`&&` `||` `;` `|` `&` newline and parentheses) and compares each pattern with:

- **each command of the line**, as its raw text with its quotes (so `(^|[\s/'"=<])\.env` and a `bash -c "…"` still
  match);
- **each command substitution as a command of its own**: `$(…)`, backquotes, `<(…)` and `>(…)`, also inside double
  quotes, inside the arguments of a text command and inside a heredoc whose delimiter is not quoted, so a pattern
  anchored with `(^|\s)` finds the command at its start;
- with the **values of data arguments blanked** (compared as `""`): every argument of `foreman note`, `progress`
  and `guard`, every argument after the action of `foreman inbox` and after the subcommand of `foreman backlog`
  (option names are still compared), the values of `--title`, `--body`, `--note`, `--result`, `--verdict`,
  `--approved` and the merge message of `foreman integrate`, the "what" of `foreman lock R run "what" …` (the
  command after it is compared as a command), and `git commit -m` / `--message`. foreman is recognized by any path, `python… bin/foreman`, with `--project X` and
  `FOREMAN_*=` in front. There is no exemption when the command pipes into anything but a plain filter (`head`,
  `tail`, `grep`, `wc`, `cat`…), writes into `>(…)`, or the line defines a function, an alias, or runs `source` or
  `eval`. A pattern that needs the text of a note will not find it there.
- the same for **text that only prints, searches or describes**: every argument of `echo` and `printf` (not
  `printf -v`, which sets a variable), the pattern of `grep`, `egrep`, `fgrep`, `rg` and `git [-C DIR] grep` (the
  value of `-e`/`--regexp`, or the first operand; their paths are still compared), the values of `--title`/`-t`
  and `--body`/`-b` of `gh pr|issue create|edit|comment|review`, and `git tag -m`. The program must be named bare
  or by a system path (`./echo` may be any script), no word may be one bash splits or globs (`$x`, `*.md`), and
  the options of a search are a closed list: any other one (`rg --pre CMD`, `git grep -O`, `git -c … grep`) leaves
  no exemption. A printer may also write to a file, or pipe into `tee`, while every file the line writes is a
  literal path that is not a device (other than `/dev/null`, stdout and stderr), not under `/proc` and not inside
  a `.git` folder, and no descriptor above 2 is written (`>&3`). When the line may run a file it writes (the
  file's name appears again, as in `sh x.sh` or `chmod +x x.sh`, more than 8 files are written, a program is not a
  plain word, or a shell, interpreter, `source`, `xargs` or `find -exec` takes a word that is not literal or reads
  a pipe), what the printers print is compared as a command.

- **what runs inside a command, as a command of its own** (read again the same way, to a depth of 4): the string
  of `sh|bash|zsh|dash|ksh|fish… -c` (also `-lc`, `-ec`, and behind `env`, `sudo`, `nice`, `timeout`,
  `foreman time-limit` and `foreman lock … run`), the words of `eval`, `ssh HOST …`, `watch …`, `su -c` and
  `env -S`, the command of `xargs …` and of `find … -exec … ;`, the body of a heredoc or here-string that a shell
  (or `eval`, `source`, `xargs`, `ssh HOST`) reads, directly or through a pipe (`cat <<EOF | sh`), and the
  arguments of a command that pipes into one (`echo "x" | bash`). A `$(…)` in the program's place and a `<(…)`
  given to a shell or `source` run their output, so what they print is compared as a command. A note inside
  `bash -c "foreman note …"` keeps its exemption; piped into a shell it does not.

A heredoc to an interpreter that is not a shell (`python`, `perl`, `ruby`, `node`, `osascript`…) is compared raw: a
pattern anchored with `(^|\s)` does not see `os.system("x")`. One exception: a Python program that cannot start a
process is text. `python3` or `python3 -` (named bare or by a system path, only flags that take no value such as
`-I`, no `PYTHON*=` in front) reading one heredoc, quoted or without `$`, backquotes and backslashes, of at most
64 KB, that parses and passes an allow-list gate: no `__` anywhere, imports only from `re`, `pathlib`, `json`,
`textwrap`, `difflib`, `collections`, `datetime`, `string`, `math`, `itertools`, `functools`, `glob`, `fnmatch` and
`tomllib`, no attribute that starts with `_`, and no name such as `os`, `sys`, `subprocess`, `importlib`, `system`,
`Popen`, `exec`, `eval`, `compile`, `getattr`, `vars`, `globals` or `input` (lib/foreman/pyscript.py has the lists).
Its comments and its string literals that hold whitespace are blanked; a one-word literal (`".env"`, a file name)
is still compared. It is read raw again when its output is run (`| sh`, `$(…)`), when the line writes somewhere
that is not a plain file, writes a module the program would import instead (`re.py`), or may run a file the program
may write (a one-word literal of it, as in `sh x.sh` or `./run`, or a runner that takes a word that is not
literal). A file written in one call and run in a later one, or
read as code by another program by its own convention (a shell's startup file), is not seen. Any other heredoc body is
text (`cat > f <<EOF`, `git commit -F - <<EOF`; only its substitutions run when the delimiter is unquoted), unless
the same line runs the file a heredoc to `cat` or `tee` writes (or a copy of it: `cp`, `mv`, `ln`): named in what a
shell, `source`/`.`, `eval`, `xargs`, `find -exec`, `crontab` or `make` gets (or a Makefile with `make`; the string
of `sh -c` or `ssh HOST` is read again), run as the program (`./x.sh`), or possibly reached by a runner that takes a
word that is not literal or reads a pipe, a program that is not a plain word, or more than 8 written files. Then the
body of the heredoc that writes that file is compared as a command (raw, when only an interpreter such as
`python3 x.py` runs it; for an interpreter only its script operand, `-c`/`-e` code and input count, so
`python3 check.py rows.tsv` does not run rows.tsv). A file only read or handed to another tool (`git add f.md`,
`$EDITOR f.md`, `tar -T list.txt`) keeps its text. A command the lexer cannot read,
or a string inside it that it cannot read (an unclosed quote, `$'…'`, `$((…))`, nesting deeper than 4, a control
character) is compared whole, with no exemption, raw and with quotes and parentheses turned into spaces: it can only
block more. Keep a guard table (`[check] guard_table`) with the
commands that must and must not pass, and run `foreman check` after changing the patterns or foreman.

### git push: what a push resolves to

How a push is written does not say where it goes: `git push` alone, inside a repo whose remote is a public
organisation, names neither the remote nor the branch. So for every command of the line that runs `git push`, the
guard also compares, after every other text, the push **as git would do it** from the folder it runs in:

```
<the command's own VAR=… and wrappers> git push <push URL> <src>:<dst> [--force] [--delete] [--mirror]
```

one text per ref it would push, with branches in short form (`main:main`), tags as `refs/tags/v1:refs/tags/v1`,
`--tags`, `--follow-tags` and `push.followTags` as `refs/tags/*:refs/tags/* --tags`, a deletion as `:dst --delete`,
and `+`, `-f`, `--force-with-lease` and `--mirror` as `--force`. An ssh or scp URL (`git@host:org/repo.git`) gives a
second text with its `https://host/org/repo.git` form. The patterns you already have match it as they are:
`git push .*some-org` now sees the URL, and `git\s+push\s+(-\S+\s+)*\S+\s+(\S+:)?main(\s|$)` sees the branch,
whatever was typed. A mark in front of the command (`OWNER_OK="…" git push …`) stays in front of the resolved
text, so a pattern with a negative lookahead for the mark works the same. `patterns` and the `[deny.grants]` groups
see it alike.

- **The folder**: the hook event's `cwd`; each `cd DIR` or `pushd DIR` earlier on the line (relative to the folders
  before it), then each `git -C DIR`. A `cd` that surely ran (outside any group, branch or loop; first in its list,
  or followed only by `&&` up to the push) replaces the folder; one that may not have run (`(cd X)`, `a || cd X`,
  inside `if`) adds its folder, and the push is checked from each. A string run by `sh -c`, `xargs` or a
  substitution starts in its command's folders; one run by `ssh HOST` is not resolved.
- **An unknown folder blocks**: when the line does not name the folder literally (`cd "$X"`, `cd -`, `popd`), the
  folder does not exist, the event has no cwd, or git does not answer within 2 s, the push is checked against every
  repo of the project (its home and each `[repos.*] path`, with that repo's `main` as the current branch). The
  refusal says so, and how to avoid it: write the folder literally.
- **What git says**: `rev-parse`, `symbolic-ref HEAD` and `config --list` in that folder (no hooks, no pager, no
  optional locks, no fsmonitor), once per folder and check. The remote is the one written (or `--repo`), else
  `branch.<b>.pushRemote`, `remote.pushDefault`, `branch.<b>.remote`, `origin`; its URL is `pushurl` or `url` with
  `insteadOf` and `pushInsteadOf` applied (a worktree shares its repo's config). The refs are the refspecs written
  (`HEAD` and `@` are the current branch), else `remote.<name>.push`, else `push.default` (`simple`, `current`,
  `upstream`, `matching`, `nothing`). A detached HEAD and a ref the line computes (`$B`) count as the main branch;
  `--all`, `--mirror` and a glob refspec add it. A folder that is not a repo keeps the remote as written.
- **A branch switched on the same line**: `git switch [-c] NAME` or `git checkout -b NAME` in the same folder
  earlier on the line is the current branch for the push when nothing but `&&` separates them (outside any group or
  branch): `git switch -c fix/x && git commit -qam … && git push -u origin HEAD` pushes `fix/x`. Otherwise (a `;`
  or `|` between them, `git checkout NAME`, which may restore a file) both the branch before and NAME are checked.
- **Config changed on the same line**: the push's own `git -c KEY=VALUE`, and an earlier `git config KEY VALUE`,
  `git config set`, `git remote add NAME URL` or `git remote set-url [--push] NAME URL` anywhere before it on the line
  (it may have run), add their value to what git says: both the old and the new value are checked.
  `git config --unset|unset` and `git remote remove` drop the key (so a `pushurl` dropped on the line leaves the
  `url`). `--git-dir DIR` or `GIT_DIR=DIR` in front of the push reads the repo at DIR (relative to the folder).
  `git remote add --mirror=push` (`remote.<name>.mirror`) makes a plain push to that remote a `--mirror`.
  `--work-tree` and `GIT_WORK_TREE` change neither the repo nor its config and are ignored.
- **What cannot be read blocks**: the push is also checked as from an unknown folder (above) when its config comes
  from something the guard does not read: a value the line computes (`git -c "k=$V"`, `GIT_DIR=$D`,
  `git remote set-url origin $U`), `--config-env`, `GIT_CONFIG*`, `GIT_COMMON_DIR`, `HOME` or `XDG_CONFIG_HOME` in
  front of the push or assigned/exported earlier on the line, an `include.*` or `includeIf.*` key, `git config
  --file|--blob|--edit|--rename-section`, `git remote rename` or `set-url --delete`, an upstream set on the line
  (`git branch -u|--set-upstream-to`, `git checkout|switch --track`), `git $SUB`.
- **Git aliases**: a subcommand that is not one of git's own is looked up in `alias.<name>` (the repo's, the global
  one and the line's `-c alias.*`). An alias that expands to `push` (through other aliases too) is checked as that
  push with the arguments typed after it; one that starts with `!` is a shell command, checked as a command of its
  own from the same folder (patterns and resolved pushes alike). A subcommand the line computes (`git $CMD …`) counts
  as a push. Git's own commands are never looked up, so `git status` runs no git in the hook.
- **A line the lexer cannot read** (an unclosed quote) that has `git … push` and matches nothing as text is checked
  as a push of every branch and tag (`--all --follow-tags`) from the event's folder and from every repo of the
  project.
- **Not read**: config written on the same line some other way (an editor, `>> .git/config`, a script), an alias
  that runs `git config`, and the strings of `ssh HOST`.

`foreman guard --cwd DIR "command"` checks a command as if it ran in DIR (default: the current folder). In a guard
table (`foreman check`, `guard --table`), a row runs in the project's home, or in the folder its expectation names
after `@`: `deny@~/Projects/lib<TAB>git push` checks a bare push from that repo, `allow@../own<TAB>git push` one from
a folder relative to the table's own folder (`~` is expanded). The home is the default so that a table gives the same
verdicts on any machine where the home has no remote; a folder that does not exist counts as unknown (every repo of
the project), so a row that names a repo checks it only where the repo is. A failing row shows its folder:
`guard.tsv:12: want deny@/…/lib, got allow: 'git push'`.

## Any other tool

The guard hook runs before every tool call (hooks/classic.json has no matcher for it): Bash as above, and every other
tool, foreman's own, Claude Code's (Read, Grep, Glob, WebFetch, Monitor, Agent…) and any plugin's, with one rule and no
list of tools. It walks the call's input (4 levels, 64 strings at most, so a long Agent prompt or a browser batch stays
cheap) and sorts each string by the name of the field that holds it, split at `_` and at camelCase:

- **a place**: the last word is `path`, `paths`, `file`, `files`, `dir`, `directory`, `folder`, `cwd`, `root`, `url`,
  `urls`, `uri` or `glob` (`file_path`, `notebook_path`, Grep's `path`, WebFetch's `url`, a plugin's `targetPath`), or
  the string sits inside a list or map under such a field (an Artifact's `files`). It gets the verdict `cat PLACE`
  would get in Bash, and an absolute path under `$HOME` also that of `cat ~/…` (Read names absolute paths; rules are
  often written with `~`). A place never waits for a lock: it runs nothing.
- **a command**: a string under `command` or `cmd` in any tool but Bash (Monitor, a plugin's terminal). It gets the
  verdict of that command in Bash, the resource prefixes and the pause included.
- **free text**: everything else (`content`, `old_string`, `new_string`, `prompt`, `message`, `query`, `pattern`) is
  not compared, as the text of a note is not; a brief that mentions `.env` passes.

The first refusal wins; it says the tool and the place or command that matched. A write tool's call is first checked
for the reserved files (above), then by this rule, so writing a secret is refused as reading it. Reading a reserved
file stays free. If the check fails on a place, its raw text is compared whole; any other failure of foreman lets the
call through (it never stops a Read). A call that names neither a place nor a command leaves at once. Not seen: a
tool that runs code under another field (`script`, `code`) and a glob's `pattern`. Without a Python foreman can use,
every tool call is refused while a registry exists, as a Bash call is (docs/config.md, «Python»).

A guard table checks it with `tool:NAME JSON` rows, judged in both roles like a command:
`orchestrator<TAB>tool:Read {"file_path": "~/.ssh/id_ed25519"}`, `allow<TAB>tool:mcp__x__y {"message": "cat .env"}`.
`guard.jsonl` and `approved.jsonl` record the tool and the place or command, never the rest of its input.

## The state

What foreman writes outside a product's home: one folder per product and a few shared files, under the **state
root**, `~/.local/state/foreman` (or `$FOREMAN_STATE_HOME`). None of it is configuration and none of it is the
product's memory (backlog, notes, reviews and pages live in the home, in its repo): it can be lost without losing
work, though the hooks and the loop pass then start again from nothing (no live agents known, no marked
orchestrator, every once-only notice said again).

Every file is plain text: JSON (written whole, `indent=1`, through a temporary file), JSON lines (appended) or a line
of text. A `NAME.lock` beside a file is the `flock` its writers take; it is empty. Times are local and ISO
(`2026-10-31T09:30:00`) unless the name says `_ts` (epoch seconds) or the text says otherwise. Readers tolerate a
missing or broken file (they take it as empty), so deleting one is always safe.

The golden corpus of the selftest (tests/golden.py, CONTRIBUTING.md «Golden files») writes down the whole state tree
after its fixed sequences: tests/golden/*.txt show every file below with real content.

### `<state>`: one folder per product

`<state root>/<name>`, or `[paths] state` of its foreman.toml.

| file | what it is | written by |
|---|---|---|
| `session` | the id of the session marked as the product's orchestrator (one line) | `foreman session ID` |
| `orchestrator.json` | `{session, name, since, pid, pid_start, process}` of that session; `name` the one given with `--name` (kept when the same session is marked again without one); `pid` and `pid_start` (its `ps` start time, in UTC) the nearest `claude` process above the call when the session marks itself (its own `CLAUDE_CODE_SESSION_ID`), kept when the same session is marked again by hand, null otherwise, with `process` saying why (`marked by hand`, `ps unavailable (…)`, `no claude process above this call`). A mark of the same session from another `claude` process is refused while that pid still runs with that start time; orchestrator-only commands pass only under that process | `foreman session ID [--name N]` |
| `board.md` | the orchestrator's board, shown again after a compaction | the orchestrator, by hand; read by `hook resume` |
| `agents.json` | `{agent_id: {start, type, model_asked, model, transcript, name, issue, sub, branch, production, launch, named_by, tool, activity, delivered, outcome, report, kind, end, final, final_chars, delivery_blocks, blocked_at, delivery_incomplete}}`: the subagents seen in the last 24 h; `model_asked` is `foreman launch --model`, `model` the one its transcript says ran (read at its stop, absent if the transcript has none); `final_chars` the length of its whole final message (`final` keeps the first 600); `outcome` the `--outcome` of its `progress --done` and `report` its `--report` (relative to the home), `kind` (`measure`) when it said `--kind measure` (all three go when it is resumed); the `delivery_*` keys and `blocked_at` are the delivery check's (lib/foreman/delivery.py: refusals so far, the last one, `{at, missing}` once let go incomplete) | `hook agents` (SubagentStart, PostToolUse, SubagentStop); `foreman agents --name/--stop` |
| `launches.json` | `{token: {name, issue, sub, repo, branch, worktree, type, production, [model,] at, session, agent}}`: a launch recorded before its agent exists; `agent` once one takes it (pending ones go after 24 h) | `foreman launch`, `foreman worktree`; `hook agents` ties them |
| `progress.jsonl` | one line per `foreman progress`: `{time, agent, text}`, plus `agent_id` when the caller is known by it and `done: true` with `outcome` (`ok`, `partial`, `failed`) for a delivery, `report` (relative to the home) when it named one with `--report`, and `kind: "measure"` when it said `--kind measure` (it brings no code for main) | `foreman progress` |
| `inbox.json` | the owner's messages and the orchestrator's replies, the last 200: `{id, time, from: owner\|orchestrator, text, read}` and, for the panel's writes, `decision`, `move` or `approve`, and `want: on\|off\|reject` with `move` (the owner's mark on a triage issue, or its discard); an answer that resolved its decision in the backlog also has `did` (the decision's id), `resolved: true` and `freed` (the lines `decision resolve` prints); an answer in the owner's own words (the panel's «Other answer») has `free: true` | `foreman inbox`, the panel (`/api/message`, `/api/decide`, `/api/approve`, `/api/move`, `/api/want`, `/api/reject`) |
| `loop.jsonl` | one line per loop pass: `{time, session, skipped, notices, actionable, duration_s}` (`notices` and `actionable` are kinds, in order; `skipped`: an idle pass, left as one line on screen and not sent to the model); cut to the last 30 days once it passes 512 KB. Read by `foreman catchup` | the loop pass (`hook mod tick`, the hooks module's timer) |
| `catchup.json` | `{last, session}`: when the owner was last caught up (the `until` of the last `foreman catchup` run without `--since`, local time) and the session that ran it (`CLAUDE_CODE_SESSION_ID`, or null); the next run without `--since` counts from it (none: 24 h; older than 7 days: 7 days). The only file `foreman catchup` writes | `foreman catchup` without `--since` |
| `heartbeats.json` | `{name: time}`: the last beat of each heartbeat (`loop` is the loop pass) | `foreman recurring done`, the loop pass |
| `pause` | present while the product is paused from the panel («paused at HH:MM») | the panel (`/api/pause`); read by the loop pass, the guard and `hook pause-agents` |
| `notices-last` | the keys of the actionable notices said between passes, one per line (a case is said once while it lasts) | `hook loop` (prompts), `hook mod tick` (the hooks module, between prompts) |
| `inbox-notified` | the newest inbox id already announced | the loop pass, `hook loop` |
| `approved.jsonl` | the orchestrator-only commands (the `[deny.grants]` groups): one line per decision of the guard, `{at, verdict: allow\|deny, why: agent\|session\|process\|grant\|null, session, agent_id, agent, orchestrator, pattern, command (300 characters), tool_use_id}` (a reserved file an agent or another session tried to write: `pattern: file:<class>` and `file`, its path, instead of `command`; a call of any other tool: `tool`, its name, and `place`, the path or URL that matched, or `command`, the one it runs, never the rest of its input) (plus `marked_pid`, the mark's pid or null, when `why` is `process`; `group` when `why` is `grant`; `grant`, `[{group, via, at, answer}]`, on an allow that used grants; an allow is a line of today's notes too), one per answer of the owner to the hooks module's question, `{at, verdict: grant\|deny\|late, why: grant, group, answer: yes\|no\|dismissed\|timeout\|unavailable, surfaces, session, orchestrator, command, tool_use_id}` (plus `until` on a grant, `via: panel` and `asked` (the request's time) on a grant approved from the panel, `cooldown_until` after a no, `pending` after a timeout, `refused: session\|process` for a yes that came where it grants nothing), and one per group `foreman permissions grants off` removed, `{at, verdict: revoke, why: grant, group, session}`; cut to the last 30 days once it passes 512 KB (lines of foreman 0.43 or older may carry `why: wrapper`, `approved` and the `{tool_use_id, exit, seconds}` of the old `foreman approved`) | `hook guard`, `hook mod grant`, `foreman permissions grants off` |
| `guard.jsonl` | one line per refusal of the guard hook: `{at, kind: builtin\|deny\|orchestrator\|lock\|file\|error, rule, where, who: agent\|orchestrator\|other}`, plus `group` and `why` for an orchestrator-only refusal, `resource` for a lock, and `file` (the path) and `why` for a reserved file (`rule: file:<class>`, `where: file`), `tool` (its name) for a call of any tool but Bash (`where: tool` for a place it named) (`rule`: the pattern, foreman's own rule or the resource's matching prefix; `where`: where it matched, as the refusal says; `who`: an agent_id, the marked session, or any other); never the command's text. A failure to write it leaves the refusal as it is; past 2 MB it keeps its last 10,000 lines. Read by `foreman permissions` (each rule's count of the last 7 days) and `foreman permissions --refused` | `hook guard` |
| `grants.json` | the owner's grants for `[deny.grants]` groups: `{grants: {group: {session, pid, pid_start, orchestrator, at, until, via, answer, surfaces, tool_use_id}}, cooldowns: {group: {session, until}}, pending: {group: {at, session, pid, pid_start, orchestrator, command, tool_use_id}}, once: {tool_use_id: {session, kind, path, command, at}}}` (a grant from the panel: `via: panel` and `sig`, an HMAC with `panel/grant.key`). `once` holds the owner's yes to one call that writes Claude Code's settings or the registry (`kind` claude or registry; `path` the real path of the file a file tool writes, or `command` the Bash command): the guard spends it on that call (same session and `tool_use_id`, never an agent's) and drops it 10 min after `at`. A grant is live while `until` is ahead and the mark is the same session, pid and start time; a cooldown (10 min after the owner's no) stops the question for that group in that session; a pending request is the last question of a group left unanswered, for the panel, until `grant_minutes` after `at` and while the mark is the session and process that asked. Read by the guard (`why=grant`), `foreman permissions grants` and the panel | `hook mod grant`; the panel's approval; `foreman permissions grants off` removes grants and requests |
| `permissions/AAAAMMDD-HHMMSS.toml`, `.tsv` | the product's `foreman.toml` (and its guard table, when the change replaced it) as they were before each `foreman permissions apply`: the copy that undoes it (the command it prints applies them back). Never removed by foreman | `foreman permissions apply` |
| `orchestrator-denied-notified` | the time of the last refusal of an agent, another session or another process of the marked one the loop pass said (`orchestrator_only_denied`) | the loop pass |
| `lock-notified` | the lock cases already announced (keys) | the loop pass, `hook loop` |
| `config-warnings-notified`, `context-rotate-notified`, `disk-low-notified`, `foreman-behind-notified`, `nameless-notified`, `sessions-behind-notified` | the case each once-only notice of the loop pass was last given for (one value; gone when the case clears) | the loop pass (notices.changed_mark) |
| `who-gone-notified.json` | `{session: case}`: the «the orchestrator is gone or silent» notice already given to a session | `hook who`, `hook loop` |
| `measure/<session>.json` | `{tokens, window, percent, cost_usd, rate_limits, at}`: Claude Code's last measure of a session's context (`tokens`: uncached + cache read + cache written input of the last response, null until the window's first response; `percent` of `window`), its cost so far and its rate-limit windows (`[{kind, percent_used, resets_at}]`); files untouched for 7 days go. Read by the `context_rotate` notice, the renewal and `foreman session` | `hook mod measure` (the hooks module's `session.measure`, when the context moved) |
| `sessions/<session>.json` | `{at, started, busy, version, claude_code, cwd, interactive?, ended?, agents?}`: the heartbeat of each session of the product: its last tick (once a minute), when foreman's hooks module started in it, whether a main-loop turn ran, the foreman and Claude Code versions, its folder, `interactive` (whether a person is at the prompt, as the module's start said: information only), `ended` at its `session.end`, and in the marked orchestrator's session `agents` (`{agent id: status}` from `$.agent.list()`). Alive while the last tick is at most 2 ticks old and it did not end; files untouched for 7 days go. Read by `foreman session`, `foreman start`, `foreman session --all`, the loop pass (`dead_agents`, `stalled_agents`), `who` and the panel | `hook mod start`, `hook mod tick`, `hook mod end` (the hooks module's `session.start`, timer and `session.end`) |
| `renew.json` | `{session, at, now, note?}`: the marked session's request to renew its memory in place («Renewal» in docs/orchestrator.md); removed when the tick starts the attempt | `foreman renew` |
| `renewals.jsonl` | the orchestrator session's renewals: `{at, session, id, state: "asked", why: "request" \| "now" \| "safety", tokens_before, note?}` written before the hooks module runs `/compact`, then lines for the same `id`: `{ran_at, text?}` when the call settled, `{state: "compacted", compacted_at}` at the SessionStart `compact` hook, `{state: "ok", tokens_after}` at the first measure after it, or `{state: "failed", error}`; an attempt still `asked` after 10 min (2 min after its call settled) counts as failed. Read by the tick, the loop pass (`context_rotate`, the cooldown, two failures in a row and a renewal that freed too little), the band and `foreman session` | `hook mod tick`, `hook mod renewed`, `hook mod measure`, `hook resume` |
| `renewal-pack.md` | the whole pack of the last compaction of the marked session (what the SessionStart `compact` hook prints, before it is cut to fit) | `hook resume` |
| `locks-notes/YYYY-MM-DD.md.lock` | the lock file of the day's notes (`foreman note`), kept here so no `.lock` file appears among the notes in the product's home; empty, safe to delete when no `foreman note` runs | `foreman note` (notes.file_lock) |
| `integrate/BRANCH-ci.log` | the output of each local CI run of `foreman integrate` for that branch (`/` written `-`), with its start, place, exit code and time | `foreman integrate` |

### `<state root>`: shared by every product

| file | what it is | written by |
|---|---|---|
| `callers.json` | `{tool_use_id: {at, session, agent, state, calls, background}}`: the foreman calls of a Bash command the guard let through, so `foreman progress` and `foreman lock` know which agent runs them (the last 200, at most 2 h) | `hook guard` (PreToolUse); `hook agents` drops the entry when the call ends |
| `locks/NAME/held/` | the lock of resource NAME, taken with `mkdir`; `owner` («who: what (date, pid N)») and, with slots, `slot`. With slots, `held-SLOT/` per slot | `foreman lock` (sh/lock.sh), the panel (the owner's turn) |
| `locks/NAME/queue/` | one ticket per waiter, named `<epoch.micro>-<pid>`, «who\|time\|pid\|what\|short» | `foreman lock` |
| `locks/NAME/next` | whose turn it is when someone holds it explicitly (the panel's owner) | the panel |
| `locks/NAME/jumped` | a short job just jumped the queue | `foreman lock` |
| `locks/NAME/uses.jsonl` | one line per use: `{requested, event, who, what, taken, released, rc, waits}` | `foreman lock` |
| `install/installs.json` | `{root: {version, seen, last}}`: the plugin roots the hooks ran from | `hook who`, `hook loop` (UserPromptSubmit) |
| `install/root` | the usable roots, the chosen one first (the panel service follows it) | the same hooks |
| `install/sessions.json` | `{session: {version, root, kind, project, cwd, seen, last, told, claude_code, entrypoint}}`: which foreman each session runs (`entrypoint`: its host, information only) | the same hooks |
| `install/orphan-told.json` | `{root: {version, at, sessions}}`: sessions already told their foreman was replaced | `foreman` (any command run from an orphaned root) |
| `install/panel-launcher` | the script the panel service runs (every panel unit of this state root shares it; the last `uninstall` removes it) | `foreman panel service install` |
| `panel/token` | the panel's key (mode 600) | `foreman panel`, the first time |
| `panel/grant.key` | the key that signs a `[deny.grants]` grant approved from the panel (mode 600; the hooks module's check trusts a panel grant only when it verifies) | the panel, at its first approval |
| `panel/panel.log` | the panel service's output (macOS) | launchd |

A machine resource (`[resources.NAME]` in the registry) may put its lock elsewhere (`dir`, `queue`, `next`,
`jumped`, `log`); docs/config.md says how, docs/lock-protocol.md how the lock works.

### In the home (the product's repo)

Not state, but also written by foreman: `backlog.json` and the pages generated from it (`[backlog] page`,
`archive_page`, `decisions_page` or its `decisions_section`), with `.backlog.lock` beside it (`foreman backlog`, the
panel's moves, `foreman integrate`); `notes/YYYY-MM-DD.md`, append only (`foreman note`, `foreman integrate` and
foreman's own notes).
