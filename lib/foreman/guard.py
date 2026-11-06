"""The command guard (Claude Code PreToolUse hook): what a session of a registered project may not run or reach.

Forked from an earlier in-house version (a fixed list of build and engine commands). Two lists, both from
foreman.toml:
- per resource, `guard`: command prefixes that must go through `foreman lock RESOURCE run …` in the same call (or
  match one of its `allow` regexes, e.g. a CI script that takes the lock itself);
- `[deny] patterns`: regexes of what is never run without the owner (deploys, releases, sends to real users…);
- `[deny.grants]`: groups of regexes of what only the marked orchestrator runs, after the owner's yes (below).

How the deny regexes read a command. A lexer reads it the way bash does: words with their quotes, escapes and
`\\`+newline, comments, redirections, heredocs, and the separators && || ; | |& & newline ( ). Every segment (one
simple command) is compared as its raw text, quotes included (so `bash -c "x"` is still seen), and every command
substitution ($(…), backquotes, <(…), >(…), also inside double quotes and inside an unquoted heredoc) is compared
again as a command of its own, so an anchored regex finds the command at its start. The only text left out is the
value of a data argument of a closed list of commands that never run their arguments (TEXT_ARGS: foreman note,
progress, guard, inbox, backlog, the messages of integrate, the label of `lock … run`, and git commit -m; and
data_tokens(): every argument of echo and printf but `printf -v`, the pattern of grep, egrep, fgrep, rg and git grep
with their options in a closed list, gh pr|issue --title/--body and git tag -m), when the segment does not pipe into
anything but a plain filter: those values are compared as "". A printer may also write to files and pipe into tee
while every file the line writes is a literal path, not a device, /proc or .git (written()); when the line may run
a file it writes (read_back()), what printers print is compared as a command.
What runs inside a command is a command of its own too, read again by the same check (runs()): the string of
`sh|bash|zsh… -c` (behind env, sudo, nice, timeout, foreman lock … run…), the words of eval, ssh
HOST, watch, su -c and env -S, the command of xargs and of find -exec, a heredoc or here-string a shell (or eval,
source, xargs, ssh) reads, and what a pipe feeds into one (its heredocs, and its arguments as a command: `echo "x" |
sh`); a $(…) in the program's place or a <(…) given to a shell runs its output; so does `trap ACTION`, and a command
inside `{ …; }`. Two cases reach past one segment (unbounded()). When the output or input of a group, a compound, a
function or alias defined in the line, >(…) or a coprocess reaches something that reads it as a program (`{ …; } |
sh`, `if …; fi | sh`, `echo x | if …; then sh; fi`, `{ sh; } <<< x`, `f(){ sh; }; echo x | f`), the whole line is
read as if it went to a shell: no exemption, and every segment's arguments, inputs and assigned values are compared
as commands. When a variable runs (in the program's place, or in sh -c, eval, xargs, a heredoc or pipe into a shell
or a substitution, with a value the line can give it), the values the line gives its variables are compared as
commands: assignments (also `export`, `local`, `${x:=…}`, arrays), `printf -v`, `for|select … in`, the input of
`read|mapfile` (and what pipes into it), `set` and the arguments of a function defined in the line, and for `$_` the
last argument of each command; their substitutions run. Data values keep their exemption there (they reach a
variable only through `$_` or `| read`, both compared). A heredoc to an interpreter that is not a shell (python,
perl, node…) is compared raw, except a python program read from one heredoc that cannot start a process (pyscript:
an allow-list gate of imports and names), compared with its comments and its prose literals blanked, one-word
literals kept; raw again when its output runs, the line writes to something else than a plain file, the program
writes a file it cannot name or a module it would import, or the line may run a file it writes (_python()). Any
other heredoc body is text (only its substitutions run when the delimiter is unquoted), except a heredoc to cat or
tee writing a file the line may run (runs_written()): then it is compared as a command, or raw when only an
interpreter that is not a shell runs it. If the lexer cannot
read the command or a string inside it (an unclosed quote, $'…', $((…)), nesting deeper than 4, a control character)
the whole text is compared with no exemption, raw and with quotes and parentheses turned into spaces, so a failure
only blocks more. check() never raises: a hook that raised would let the command through.
A `git push` is also compared as git would do it (gitpush.py): for each segment that runs one, from the folders it
may run in (ctx cwd, then the line's cd/pushd and git -C), a text `<assignments and wrappers> git push <URL>
<src>:<dst> [--force] [--delete]` per ref it would push, after every other text, so a command that already matched
keeps its pattern and where; where = resolved, or unknown when the folder cannot be named (every repo of the project
is tried: it only blocks more). Config the line changes before the push (`git -c`, `--git-dir`, GIT_DIR, an
earlier `git config` or `git remote set-url`) and git aliases are resolved too; what cannot be read counts as an
unknown folder; a `!` alias is compared as a command of its own. A line the lexer cannot read that has `git … push`
is compared as a push of every branch and tag from the event's folder and every repo of the project. All three lists
see it (deny(…, at)). A failure of the resolver is a deny. A ctx without the cwd key compares only the text; a table
run gives each row its @DIR, else the project's home.
One reading per check (_Check): those texts are read once with the pushes resolved and once without (for the two
built-in lists), and each list is compared with them (an empty list is not); each line the command runs is read once
(_read) for those texts and for the rules below (_walk: the command and each line nested in it as sh -c, eval, $(…),
a heredoc or a pipe into a shell, xargs and find -exec run it, not what ssh runs; each segment with the folders it may
run in, gitpush.folders from the event's cwd, else the process's, with the values the line gives its variables); the
registry, whether the caller is an agent and what git says of a folder (the Resolver, shared by the pushes and the
main-checkout rule), once each. A rule below that fails compares the line's text only (_either): a failure of
foreman blocks only what that text names.
What the guard does not see: a script written to a file and run later (or read as code by another program by its
own convention, a shell's startup file), a forbidden command built inside a program
(python -c, `os.system("x")` in a heredoc to python, for an anchored pattern; a python program that passes the gate
and still reaches a process through a path the gate does not know, or a module file left in the folder by an
earlier call), shell aliases and functions from
outside the command, a variable whose value comes from outside the line (the environment, an earlier call) and a
command assembled from pieces (`a=gi; b="t x"; $a$b`).

Who calls (check()'s ctx, from the hook event). A command that matches a `[deny.grants]` group (and no `patterns`,
which win for everyone) passes only for the main thread of the marked orchestrator's session (the event's session_id
is the one in <state>/session and it carries no agent_id: never a subagent, which shares its parent's session id, nor
another session). It must run under the marked session's own `claude` process, the one that marked itself
(procs.py: the nearest `claude` above the hook is the mark's pid with the same start time; without a recorded
process, or without `ps`, it does not pass): a copy started with the session's id (`claude -p --resume ID`) sends the
same session_id and no agent_id. And it needs a live grant of its group (grants.py: the owner's own yes to the
question foreman's hooks module asks, for that session and process, for [deny] grant_minutes); a command of two
groups needs both.
Otherwise the verdict is ("orchestrator", hit) with hit.why = agent | session | process | grant (checked in that
order; the process after the session, as it runs `ps`, and the grant last, so the module asks the owner only about a
call that nothing else refuses; hit.group names the group without a grant). A table run and `guard --as` compare no
process and take every group as granted. No ctx, or one with only cwd (a table's agent run), is an agent. A command
that passes leaves the hit in ctx["passed"] (hit.grant: {group, via, at, answer} of each live grant it used): the
hook writes it down (approved.py: a line in approved.jsonl and a note).

A built-in rule, before `[deny] patterns` and for everyone (the orchestrator included): a Bash call that runs
`foreman hook mod` is refused (HOOK_MOD). Its ops are for foreman's hooks module, which runs them as a process the
guard never sees; through Bash, a caller could hand `hook mod grant` an answer the owner never gave. Likewise a Bash
call that reaches the panel's endpoint approving a pending grant request (PANEL_GRANT, `api/grants/approve` in a
URL): the owner approves from the page, never a command.
One built-in rule for the orchestrator only (PERMISSIONS_APPLY, after those two): `foreman permissions apply`, which
rewrites the product's foreman.toml and guard table (permissions.py), passes only for the marked orchestrator's main
thread in its own process (the agent, session and process checks of a group, without a grant), whatever the
product's lists say; the owner's yes is in the chat. When it carries `--opens` (the change allows something that was
not allowed before) it is also in the internal group `permissions` (grants.INTERNAL), so it needs the owner's own yes
to the hooks module's question, like any group; so does `foreman integrate … --opens` (a branch that changes a live
foreman.toml or guard table that way, integrate.py). The internal group is compared only for a command that names
them (names_internal).

Three built-in rules for an agent only (AGENT_ONLY, after `[deny] patterns` and before the groups; _agent_only), each
("orchestrator", Hit) with why=agent; the orchestrator and the other sessions of the product (the owner's chats) pass,
and only a caller that _who calls an agent (no process is compared) is checked. INTEGRATE: a segment whose program is
foreman (shell.foreman_call: bare, a path, `python3 bin/foreman`, --project P, behind the wrappers runs() skips) running
`integrate` in any line _walk gives (the regex for a text the lexer cannot read), in any folder and any product, but
with --help, -h or --check as a word of its own (argparse takes no abbreviation; --check stops before anything is
written); the text of a note, a commit message, a sed script or a heredoc to an interpreter is never such a call.
MAIN_CHECKOUT: a git that rewrites a main checkout of any product of the registry: the checkout (the nearest folder up
with a `.git` entry, a stat per level, never git) of its live config or guard table, and that of each of its [repos.*]
paths that exists (a home outside its code repo leaves that repo in), for an agent only (main_checkouts; a worktree
outside it, one nested in .claude/worktrees and a submodule have their own `.git` file, so they are not it). The folder
of each git, in every line _walk gives, is its segment's with its -C applied (gitpush.moved: one the line cannot name
keeps the one before), plus a --work-tree or GIT_WORK_TREE the line gives. Only a folder it cannot name passes unless
the line names such a checkout as text (_names_top). What rewrites it (_git_rewrites): merge, pull, rebase, cherry-pick,
revert, am, switch, clean, filter-branch and checkout-index always; bisect but log|view|visualize; read-tree -u|--reset;
sparse-checkout but list; submodule update; apply but --check|--stat|--numstat|--summary|--cached; reset
--hard|--merge|--keep; stash with no subcommand, push without paths, save, pop, apply, branch; checkout of a branch or a
commit (-b, -B, --orphan, --detach, -f alone, `X --`, one word that is no path in that folder); and, by paths, when one
reaches a live file (_reaches: the file, a folder that holds it, a .claude folder or settings file of the checkout; a
path with a variable or a wildcard, when a live file lies under the folder of its literal start; a `:` pathspec):
checkout -- PATHS (or TREE PATHS), restore (but --staged alone), rm (but --cached), mv (sources and destination), stash
push -- PATHS. Everything else passes: reading (log, diff, show, status, stash list|show…), commit (a commit on main in
a main checkout is not seen: the projects' rules keep it), add, branch, worktree, fetch, reset --soft and a mixed reset
(the index only). A git alias is read in that folder's config (and the line's -c; gitpush.aliased): to a git command, as
that command; `!`, as a shell command from the same folder; a subcommand the line computes (`git $C`) rewrites, but a
function's "$@" (gitpush.called). A line the lexer cannot read is refused when it has `git [options] SUB` with SUB one
of those (GIT_REWRITE_TEXT) and names such a checkout or runs in one. A failure of the rule refuses only a line that has
such a `git SUB` and names such a checkout as text. Not seen: a git command that writes the shared .git/config or
.git/hooks (`git config`, `git remote add`; their writes by name are the git class of the reserved files, below), a
branch moved without touching files (update-ref, branch -f: the copy is then dirty and integrate stops), patch, tar or
unzip over it, git run by a program or a script, a variable from outside the line.
HOLDOUT (_holdout, _holdout_tool): the folders of hidden acceptance checks ([paths] holdout of every product of the
registry that exists: none, and only `foreman holdout` is compared), for an agent holdout_reader refuses (every type
but a planner's: the ctx's agent_type, else <state>/agents.json; an unknown one is refused). Refused: `foreman
holdout` as INTEGRATE reads it but with --help or -h; a line that names such a folder as text (`~`, $HOME); a word
of a segment (also a redirection's file, the value of `--opt=VALUE`, in every line _walk gives) that, expanded
and joined to each folder the segment may run in, reaches one (_holdout_reach: the real path in or at the folder; a
wildcard that may match it, `**` any depth; with a program that walks, _walks: WALKERS or WALK_FLAGS, a folder that
holds it, and given no existing path, the folder it runs in); a segment that runs inside one. Any other tool: its
places as `cat PLACE` (so a Read, Write or Edit in it), and a Grep whose path (else the call's cwd) holds one or a
Glob whose pattern may reach one. Not seen: a path a program builds, a variable from outside the line, a link to a
folder above walked by a program that follows links.

For the resource prefixes quoted strings are dropped (a note that mentions `make up` is not a command); the
command is split at && || ; | newlines and $(; leading VAR=value, env, time, nice, exec, command, sudo and the
time-limit wrappers are skipped. Exit 2 = rejected, with the reason on stderr for the agent.

Any other tool (check_tool, the same hook on every tool but Bash; check_call dispatches; a FILE_TOOLS call after
check_file, so writing a secret gets the verdict of reading it, as `echo x > .env` does in Bash): one rule,
with no list of tools, judges what the call names. tool_texts walks its input (TOOL_DEPTH levels, TOOL_STRINGS
strings): a string under a field whose name ends, split at `_` and camelCase, in one of PLACE_WORDS (file_path,
notebook_path, path, url, cwd, root, files, glob, targetPath…), or inside a list or map under one, is a place; a
string under `command` or `cmd` is a command. A command gets the verdict check() gives it in Bash (the pause of the
hook too); a place, the one check() gives `cat PLACE`, and for an absolute path under $HOME also `cat ~/…` (Read
names absolute paths, rules are often written with `~`), never a lock (a place runs nothing). The first refusal wins;
a place's Hit has where=tool and part `<tool> <place>`, and every Hit of such a call its tool and call. Every other
string (content, old_string, prompt, message, query, pattern…) is free text and is not compared, as the text of a
note is not. Reading a reserved file stays free. A failure of check() on a place compares its raw text (_whole);
any other failure lets the call through: a failure of foreman never stops a Read. Not seen: a tool that runs code
under another key (script, code), a glob's `pattern`.

The reserved files (check_file, the same hook on Write, Edit, MultiEdit and NotebookEdit): the files no tool call
writes, the same for every product and computed at each call (reserved()): config, the live foreman.toml of every
project of the registry; table, the live guard table of each ([check] guard_table); state, the whole state root and
the [paths] state of a project that lies outside it; registry, the registry file (with the default one, its whole
folder ~/.config/foreman, which holds the lock's busy scripts); claude, Claude Code's own folder (~/.claude, and
$CLAUDE_CONFIG_DIR) but its projects/ (transcripts and memory) and plans/, ~/.claude.json, and any settings.json or
settings.local.json of a folder named .claude; git (AGENT_KINDS, git_entries), what git runs or reads as
instructions in a protected repo (protected_repos: the checkout, gitdir.checkout_of, of each live config and guard
table, and each [repos.*] path of every project), found on the disk without git (gitdir.common_dir: the `.git`
folder, or a worktree's `.git` file and its commondir, so a worktree outside or nested reaches the same one): its
common git directory's hooks/ and info/, config and config.worktree, every worktree's worktrees/*/config.worktree (a
pattern, WORKTREE_CONFIG), and what those configs name (an absolute core.hooksPath, include.path and
includeIf.*.path, followed into the included files, gitdir.config_paths); and the machine's git config:
~/.gitconfig, $XDG_CONFIG_HOME/git/ (else ~/.config/git/) and the file each link in it reaches, $GIT_CONFIG_GLOBAL
and $GIT_CONFIG_SYSTEM when set, /etc/gitconfig, /opt/homebrew/etc/gitconfig and /usr/local/etc/gitconfig, with what
they name. Its files are reserved as the file they reach and as the link itself. Not objects/, refs/, logs/, HEAD,
index or the rest of worktrees/, which a commit or `git worktree add` writes. A path is compared as the file it
reaches: `~` expanded, joined to the ctx's cwd when relative, os.path.realpath (symbolic links, also of the folders
of a path that does not exist yet, and `..`), case-folded on macOS (APFS ignores case); a file that exists with more
than one hard link is also compared by (device, inode) with each reserved file. Nobody passes, the orchestrator
neither (`foreman permissions apply` changes the config and the table, and Python writes them, which the guard does
not see), with two exceptions: the git class refuses only an agent (_who says agent; the orchestrator and the
owner's other sessions write it, as they run git), and the product's own board (Project.board) for the marked
orchestrator's session and main thread (no process is compared: it is its own slate). The verdict is ("file", Hit)
with the Hit "file:<class>", where "file", kind the class, path the file as written, name the project it belongs to
(git: the repo's product, "" for the machine's) and why agent | session | "" (the orchestrator). If the check itself
fails, it denies only a path that looks reserved as text (under one of the reserved folders, or named foreman.toml,
guard.tsv, projects.toml, settings.json or settings.local.json), never one of the git class (O8: a failure of
git_entries gives no entry): a failure of foreman never stops the agents' writes.
A Bash command that writes one of them by name gets the same verdict (_bash_file, in check() after the built-in rules
and PERMISSIONS_APPLY, before [deny] patterns), with hit.bash set and hit.part the command around the name: the files
shell.writes() reads in the line (redirections, tee, the destination of cp/mv/ln/install/rsync/ditto/scp and the sources
of mv, sed -i, gsed -i, perl -i, rm, rmdir, unlink, truncate, touch, shred, chmod, chown, chgrp, chflags, dd of=, curl
-o, wget -O, a find that deletes), behind the wrappers runs() skips, in every line _walk gives, and the files the code
of an interpreter (python -c, node -e, a heredoc to perl…) may write (_code_writes: Python that passes pyscript's gate
by what it writes, other code by the quoted literals in the arguments of a call that writes; from the segment's folders
and from HOME, `~` expanded). A target is expanded (`~` when unquoted, $HOME and the line's own literal assignments) and
joined to each folder the segment may run in, then compared as check_file does, also as the link itself (rm, mv, ln -sf
change a link); a copy into a folder also as each source's name in it; a recursive one (rm -r, chmod -R, cp -R, rsync
-a, ditto, the source of mv) also when a reserved file or folder lies under it. A target still not known (an expansion,
a glob, a folder the line cannot name) is reserved only when its text names a reserved path (_names); a text the lexer
cannot read, only when it also looks like a write (WRITE_WORDS). A failure of the rule denies only such a text. Not
seen: a script file that writes it (python x.py), code piped into an interpreter (`echo … | python3`), git checkout or
restore in the main checkout (an agent's: MAIN_CHECKOUT above), an archive unpacked over it, `find … -exec rm {}` below
a folder that is not itself reserved, a variable from outside the line or set by a loop.
The owner's one-off yes: a call of the main thread (no agent_id) of any session of the product that writes
a file of ONCE_KINDS (claude, registry) passes when grants.json holds the owner's one-use yes for that very call
(grants.take_once: the ctx's session and tool_use_id, and the same real path, or the same command for Bash), which it
spends. foreman's hooks module asks for it before the call (`hook mod grant`, hooks_mod.py); an agent's call is never
asked and never passes. config and table change only through `foreman permissions apply`; state, never.
"""

from __future__ import annotations

import fnmatch
import functools
import json
import os
import re
import shlex
import sys
from collections.abc import Callable, Generator, Iterable, Iterator, Sequence
from pathlib import Path, PurePath
from typing import TYPE_CHECKING, Any, TypedDict

from . import gitdir, gitpush, pyscript
from .shell import (
    ASSIGN,
    CODE_OPTS,
    RAW_INTERPRETERS,
    Runs,
    Seg,
    Tok,
    Unparsed,
    Write,
    data_tokens,
    feeders,
    filters_only,
    foreman_call,
    heredoc_files,
    heredoc_writer,
    inputs,
    lex,
    may_run,
    piece,
    pipelines,
    printer,
    quoted_segments,
    read_back,
    runs,
    runs_written,
    segments,
    strip_heredocs,
    strip_quotes,
    unbounded,
    values,
    words,
    writes,
    written,
)

if TYPE_CHECKING:
    from .config import Project, Table
    from .util import PathLike

# The built-in rules (module doc): any form of `foreman hook mod` (bin/foreman, a path, --project P, quoted words);
# the panel's endpoint that approves a pending grant request (any URL that names it).
HOOK_MOD = r"""(^|[\s/'"])foreman\S*['"]?\s+(--project[\s=]\S+\s+)?['"]?hook['"]?\s+['"]?mod\b"""
PANEL_GRANT = r"api/grants/approve"
BUILTIN = {HOOK_MOD: "guard_hook_mod", PANEL_GRANT: "guard_panel_grant"}
# The built-in rule for the orchestrator only: `foreman permissions apply` (any word order after `permissions`)
# passes only for the marked orchestrator's main thread in its own process, with no group (module doc); the grant
# question comes from the internal group of grants.INTERNAL when it carries --opens.
PERMISSIONS_APPLY = (
    r"""(^|[\s/'"])foreman\S*['"]?\s+(--project[\s=]\S+\s+)?['"]?permissions['"]?\s(.*\s)?['"]?apply['"]?(\s|$)"""
)

Verdict = tuple[str, Any]  # check()'s answer: ("deny" | "orchestrator", Hit) or ("lock", (resource, segment));
# check_file()'s: ("file", Hit)


class Ctx(TypedDict, total=False):
    """Who runs a command (check()'s ctx): the hook event's session_id and agent_id (None: the main thread). marked:
    the orchestrator's session to compare with, given only by a table run or `guard --as` (else <state>/session; with
    it no process is compared and every [deny.grants] group counts as granted). cwd: the folder the command runs in
    (the event's cwd; `guard --cwd`): with the key, each `git push` is also compared as git would do it (gitpush.py;
    None: an unknown folder); without it, only as written (a table run gives each row its @DIR or the home).
    passed: set by check(), the orchestrator-only pattern of a command it let through. agent_type: the event's (a
    subagent's type; else <state>/agents.json's, holdout_reader). tool_use_id: the call's, given
    only by the guard hook: with it, a main-thread write of Claude Code's settings or the registry may spend the
    owner's one-use yes for that call (grants.take_once)."""

    session: str | None
    agent: str | None
    agent_type: str | None
    marked: str | None
    cwd: str | None
    passed: Hit
    tool_use_id: str | None


TABLE_SESSION = "(guard table)"


def as_role(role: str, cwd: str | None = None) -> Ctx | None:
    """The ctx of a table run or `guard --as`: None for an agent, the marked orchestrator's main thread otherwise;
    with cwd, a ctx that carries it (an agent's has no session)."""
    ctx: Ctx = {"session": TABLE_SESSION, "agent": None, "marked": TABLE_SESSION} if role == "orchestrator" else {}
    if role == "planner":  # an agent of the planner type (it may reach the hidden acceptance checks)
        ctx = {"agent": "(guard --as planner)", "agent_type": "planner"}
    if cwd is not None:
        ctx["cwd"] = cwd
    return ctx or None


# ---------- the deny check ----------

# Where a pattern matched (Hit.where), each with its sentence in the refusal (i18n guard_where_*): the text of a
# command, inline code of an interpreter (python -c, node -e), a sed or awk script, a loop or a test, the script an
# interpreter reads (heredoc, pipe), what a shell runs from a pipe or a $(…), a value given to a variable that runs,
# text written to a file the line then runs (a printer or a heredoc to cat|tee), the whole text when the lexer
# cannot read it, and a `git push` as git would do it from its folder (resolved) or from a repo of the project when
# the folder cannot be known (unknown; gitpush.py; i18n guard_how_resolved says how to name the push). The refusal
# says how to write code apart for these (i18n guard_how_code):
CODE_WHERE = ("code", "sed", "loop", "script", "written")
EDITORS = ("sed", "gsed", "awk", "gawk", "mawk", "nawk")
EXPECTS = ("allow", "deny", "orchestrator")  # a table row's expectation, besides lock:RESOURCE
LOOPS = ("for", "select", "case", "if", "elif", "while", "until", "[", "[[", "test")
EXCERPT = 80  # characters of the matched text the refusal quotes


class Hit(str):
    """The [deny] pattern a command runs (it compares equal to the pattern), with where it matched (one of the
    kinds above) and part, the text that matched, shortened around the match."""

    where: str
    part: str
    why: str = ""  # a [deny.grants] hit check() refuses: agent | session | process | grant
    group: str = ""  # with why=grant: the [deny.grants] group without a live grant
    dir: str = ""  # where = resolved | unknown: the folder the push was resolved from
    grant: list[dict[str, Any]] | None = None  # on a pass: the live grants it used, [{group, via, at, answer}]
    kind: str = ""  # where = file: the reserved class (config | table | state | registry | claude | git)
    path: str = ""  # where = file: the file as the call names it
    name: str = ""  # where = file: the project the file belongs to (config, table, state), else ""
    bash: bool = False  # where = file: a Bash command writes it (part: the command around its name)
    tool: str = ""  # a call of another tool than Bash (check_tool): its name
    call: str = ""  # with tool: the place or the command of that call that matched (or passed)

    def __new__(cls, pattern: str, where: str = "command", text: str = "") -> Hit:
        hit = super().__new__(cls, pattern)
        hit.where, hit.part = where, _excerpt(pattern, text)
        return hit


def _flat(text: str) -> str:
    return " ".join(text.split())


def _excerpt(pattern: str, text: str) -> str:
    flat = _flat(text).replace("`", "'")
    if len(flat) <= EXCERPT:
        return flat
    m = re.search(pattern, flat)
    a = max(0, (m.start() if m else 0) - EXCERPT // 4)
    return ("…" if a else "") + flat[a : a + EXCERPT] + ("…" if a + EXCERPT < len(flat) else "")


Text = tuple[str, str, str, tuple[str, ...]]  # one text a command is compared as: (the text, where, the folder of a
# resolved push, the marks of the lines it is nested in, innermost first)


def _items(texts: Iterable[tuple[str, str]], dirs: dict[str, str] | None = None) -> Iterator[Text]:
    """The (text, where) pairs of one line as Texts, in order, each text once. dirs: {resolved text: its folder}."""
    seen: set[str] = set()
    for raw, where in texts:
        text = _flat(raw)
        if text and text not in seen:
            seen.add(text)
            yield text, where, (dirs or {}).get(raw, "") if where in ("resolved", "unknown") else "", ()


def _scan(pats: Sequence[str], texts: Iterable[Text]) -> Hit | None:
    """The first pattern that matches one of the texts, in order (text by text)."""
    for text, where, folder, marks in texts:
        for pat in pats:
            if re.search(pat, text):
                hit = Hit(pat, where, text)
                hit.dir = folder
                for mark in marks:  # a text written to a file the line runs says so, but a resolved push
                    hit = Hit(hit, mark, hit.part) if hit.where not in ("resolved", "unknown") else hit
                return hit
    return None


def _where(w: Sequence[Tok], prog: int) -> str:
    """What a segment's own text is, for the refusal: a loop or test, a sed/awk script, inline code, a command."""
    first = w[0].value if w else ""
    name = w[prog].value.rsplit("/", 1)[-1] if prog < len(w) else ""
    if first in LOOPS or name in LOOPS:
        return "loop"
    if name in EDITORS:
        return "sed"
    if RAW_INTERPRETERS.fullmatch(name):
        return "code"
    return "command"


def _python(segs: Sequence[Seg], info: Sequence[Runs], words_of: Sequence[Sequence[Tok]], k: int) -> str | None:
    """What is compared of segs[k] when it is python reading its program from one heredoc that passes pyscript's
    gate: the program with its prose blanked. None (compared raw) when the delimiter is not quoted and the body holds
    an expansion or an escape, the line writes somewhere that is not a plain file (written()), the program writes a
    file it cannot name (pyscript.writes()) or one an allowed import may load instead (re.py, a folder difflib/), or
    another segment may run a file the program or the line writes (may_run())."""
    seg, r = segs[k], info[k]
    if r.strings or r.cmds or not pyscript.program(words_of[k], r.prog):
        return None
    if any(o in ("<(", ">(") for t in seg.toks for o, _ in t.subs):
        return None
    ins = [t for t in seg.toks if t.kind == "redir" and "<" in t.raw]
    if len(ins) != 1 or ins[0].body is None or "<<-" in ins[0].raw:
        return None
    body = ins[0].body
    if not re.search(r"['\"\\]", ins[0].raw) and re.search(r"[$`\\]", body):
        return None
    got = pyscript.read(body)
    files = written(segs, info) if got else None
    if got is None or got.writes is None or files is None:
        return None
    paths = [*got.writes, *(str(t.value) for t in files)]
    if any(p.removesuffix(".py") in pyscript.ALLOWED_MODULES for x in paths for p in PurePath(x).parts):
        return None
    names = {PurePath(x).name or x for x in paths}
    if len(segs) > 1:
        rest = "\n".join(
            f"{t.raw}\n{t.value if isinstance(t.value, str) else ''}\n{t.body or ''}"
            for j, other in enumerate(segs)
            if j != k
            for t in other.toks
        )
        if may_run(segs, info, {n for n in names if n in rest}, k):
            return None
    return got.text


At = tuple[gitpush.Resolver, frozenset[str], gitpush.Changes]  # the resolver of a check, the folders a command
# starts in and what the line around it changed in git's config before it


@functools.lru_cache(maxsize=256)
def _read(cmd: str, depth: int) -> tuple[list[Seg], Any, list[list[Tok]], list[Runs]]:
    """(segments, the lexer, the words of each, what each runs) of a line (shell.lex, pipelines, runs): read once
    for _texts and _walk alike (nothing changes them once read). Raises Unparsed."""
    segs, lx = lex(cmd, depth)
    segs = pipelines(segs)
    words_of = [[t for t in s.toks if t.kind == "word"] for s in segs]
    return segs, lx, words_of, [runs(w) for w in words_of]


def _texts(cmd: str, depth: int, exempt: bool, feeds: bool = False, at: At | None = None) -> Generator[Text, None, str]:
    """The texts the command is compared as, in order (module doc); returns the command with its data values blanked.
    feeds: the output of the command is run by a shell (it is $(…) in a program's place, or a pipe into one). at:
    resolve its pushes (gitpush.py), after every other text of their line."""
    segs, lx, words_of, info = _read(cmd, depth)
    here = gitpush.folders(segs, words_of, info, at[1]) if at else []
    fn = gitpush.called(segs, words_of, info, cmd) if at and lx.redefines else {}
    heads = gitpush.branches(segs, words_of, info, here, fn) if at else []
    before = gitpush.changes(words_of, info, at[2], segs, here, fn) if at else []

    def sub(k: int, more: bool = False) -> At | None:  # what a command nested in segment k starts in
        if not at:
            return None
        w = words_of[k]
        env = gitpush.env_changes(w[: info[k].prog])
        lost = gitpush.LOST if env[1] != "" else gitpush.NONE  # a GIT_DIR it is given
        return (at[0], here[k] | ({gitpush.UNKNOWN} if more else set()), before[k].plus(env[0]).plus(lost))

    reach: dict[int, str] = {}  # segment index -> what runs its output: "shell" | "raw"
    for j, r in enumerate(info):
        if r.stdin:
            for k in feeders(segs, j):
                reach[k] = max(reach.get(k, ""), r.stdin)  # "shell" wins over "raw"
    vals = values(segs, info)
    mode = unbounded(segs, info, vals, reach)
    feeds = feeds or mode == "shell"  # what runs cannot be bounded: read all of it as if it went to a shell
    var = mode == "var"  # a variable runs: the values the line gives its variables are commands (a data value
    exempt = exempt and not lx.redefines and not feeds  # reaches one only through $_ or `| read`, both compared)
    if feeds:
        reach = dict.fromkeys(range(len(segs)), "shell")
    printers = {k for k, w in enumerate(words_of) if printer(w)}
    writers = {k for k, (s, r) in enumerate(zip(segs, info)) if heredoc_writer(s, r)}  # cat|tee <<EOF
    files = written(segs, info) if printers or writers else []
    back: set[int] = set()  # segments whose output goes to a file the line may run (where = "written")
    if printers and (files is None or files) and read_back(segs, info, files or []):  # it may run what one prints
        back |= {k for k in printers if k not in reach}
        reach.update(dict.fromkeys(back, "shell"))
    run_by, ran = runs_written(segs, info, files or []) if writers and (files is None or files) else (None, None)
    if run_by:  # a heredoc written to a file the line runs: its text is a command (or a script, for python…)
        for k in writers - reach.keys():
            mine = heredoc_files(segs, info, k)
            if ran is None or mine is None or mine & ran:
                reach[k] = run_by
                back.add(k)
    texts: list[tuple[str, str]] = []  # (text, where it comes from)
    if var:
        reach.update(dict.fromkeys(range(vals.fed), "shell"))  # `echo "x" | read v; $v`
        texts += [(x, "variable") for v in vals.texts for x in (v, *segments(v))]
    nested: list[tuple[str, bool, bool, str, At | None]] = [(c, False, False, "", at) for c in lx.body_subs]
    resolved: dict[str, str] = {}  # a push as git would do it -> its folder ("?" + a repo: the folder is unknown)
    repl: list[tuple[int, int, str]] = []
    for k, seg in enumerate(segs):
        w, r = words_of[k], info[k]
        prints = k in printers  # a printer keeps its data when the line writes only to plain files (also a group's)
        ex = exempt and k not in reach and not (prints and files is None) and filters_only(segs, k, prints)
        data = (data_tokens(w) if ex else None) or {}
        swap: dict[Tok, str] = {}
        prog = w[r.prog] if r.prog < len(w) else None
        remote = prog is not None and prog.literal and PurePath(prog.value).name == "ssh"  # runs elsewhere
        for tok, text, blankable in r.strings:  # sh -c S, eval…: S is a command of its own
            b = yield from _texts(text, depth + 1, ex and blankable, at=None if remote else sub(k))
            if blankable and b != text and tok is not None and tok.oneword:  # only its data values differ
                q = tok.raw[0] if tok.raw[:1] in ("'", '"') else ""
                swap[tok] = q + b + q
        execdir = any(t.literal and t.value in ("-execdir", "-okdir") for t in w)  # runs in each file's folder
        nested += [(" ".join(t.raw for t in toks), False, False, "", sub(k, execdir)) for toks in r.cmds]
        mark = "written" if k in back else ""
        py = None  # a python program that cannot start processes (pyscript): compared with its prose blanked
        if (
            r.stdin == "raw"
            and exempt
            and k not in reach
            and len(cmd) <= pyscript.MAX_LINE
            and filters_only(segs, k, True)
        ):
            py = _python(segs, info, words_of, k)
        for kind, own in ((r.stdin, True), (reach.get(k), False)):
            for text in inputs(seg) if kind else ():
                if kind == "shell":
                    nested.append((text, ex and own, False, "" if own else mark, None if remote else sub(k)))
                else:
                    read = py if own and py is not None else text
                    texts += [(x, ("" if own else mark) or "script") for x in (read, *read.split("\n"))]
        if k in reach:  # what it prints is run: its arguments (and every value it assigns) are commands
            run = [" ".join(t.value for t in w[r.prog + 1 :]), " ".join(t.value for t in w)]
            run += [t.value.partition("=")[2] for t in w if ASSIGN.match(t.raw)]
            texts += [(x, mark or "piped") for v in run for x in (v, *segments(v))]
        if at and prog is not None and prog.literal and PurePath(prog.value).name == "git":
            try:
                shells: list[tuple[str, frozenset[str], gitpush.Changes]] = []
                for v, j in gitpush.variants(w, r.prog, fn.get(k)):  # a function's body runs where it is called,
                    dirs = here[k] if j is None else here[k] | here[j]  # with the branch and config of the call
                    at_k = k if j is None else j  # (before[j] holds the body's own changes, as may-not-have-run)
                    for t, d in at[0].resolve(v, r.prog, dirs, heads[at_k], before[at_k], shells):
                        resolved.setdefault(t, d)
                nested += [(c, False, False, "", (at[0], dirs, ch)) for c, dirs, ch in shells]  # `!` aliases
            except Exception as e:  # a push it cannot resolve is never let through: check() denies
                raise gitpush.Failed(type(e).__name__) from e
        parts: list[str] = []
        for t in seg.toks:
            off = data.get(t)
            for o, c in t.subs:
                fd = k in reach or t is prog or (o == "<(" and r.stdin == "shell") or (var and t in vals.toks)
                nested.append((c, off is not None and not fd, fd, "", sub(k)))
            if t in swap:
                parts.append(swap[t])
                repl.append((t.start, t.end, swap[t]))
            elif off is None:
                parts.append(t.raw)
            else:
                parts.append(t.raw[:off] + '""')
                repl.append((t.start, t.end, parts[-1]))
        text, where = " ".join(parts), _where(w, r.prog)
        texts += [(text, where), (text.replace("\\\n", ""), where)]
    blanked = cmd
    for a, b, new in sorted(repl, reverse=True):
        blanked = blanked[:a] + new + blanked[b:]
    texts += [(x, "command") for x in quoted_segments(strip_heredocs(blanked))]  # the old guard's texts, minus data
    texts += [(t, "unknown" if d.startswith("?") else "resolved") for t, d in resolved.items()]  # after all of them
    yield from _items(texts, {t: d.lstrip("?") for t, d in resolved.items()})
    for content, ex, fd, mark, inner in nested:
        for text, where, folder, marks in _texts(content, depth + 1, ex, fd, inner):
            yield text, where, folder, (*marks, mark) if mark else marks
    return blanked


def _whole(cmd: str) -> Iterator[Text]:
    """The texts of a command the lexer cannot read: all of it, no exemption."""
    spaced = re.sub(r"[\"'`()$\\]", " ", cmd)
    texts = [
        *quoted_segments(strip_heredocs(cmd)),
        *quoted_segments(cmd),
        *segments(cmd),
        *segments(spaced),
        *cmd.split("\n"),
        *spaced.split("\n"),
        cmd,
        spaced,
    ]
    return _items((x, "whole") for x in texts)


def deny(
    pats: Sequence[str], cmd: str, at: gitpush.Resolver | None = None, texts: Iterable[Text] | None = None
) -> Hit | None:
    """The first [deny] pattern the command runs, or None (see the module doc); an empty list is not compared. at:
    also compare each `git push` as git would do it from the resolver's folder (gitpush.py). texts: the command's,
    already read for at (_Check.texts)."""
    if not pats:
        return None
    try:
        return _scan(pats, texts if texts is not None else _texts(cmd, 0, True, at=_start(at)))
    except gitpush.Failed:
        raise
    except Exception:  # noqa: BLE001 (the pattern check falls back to the whole-line check on any parser error)
        hit = _scan(pats, _whole(cmd))
    if hit is None and at is not None and re.search(r"\bgit\b.*\bpush\b", cmd, re.S):
        try:  # it cannot say which push: every branch and tag, from the event's folder and every repo
            blind = at.blind()
        except Exception as e:
            raise gitpush.Failed(type(e).__name__) from e
        hit = _scan(pats, _items(((t, "unknown") for t, _ in blind), {t: d.lstrip("?") for t, d in blind}))
    return hit


def _start(at: gitpush.Resolver | None) -> At | None:
    return (at, at.start, gitpush.NONE) if at else None


LAUNCHERS = ("xcrun", "npx", "bunx", "pnpm", "yarn", "bundle", "uv", "uvx", "mise")


def matches_prefix(w: Sequence[str], pattern: str) -> bool:
    """True if the command starts with the pattern's words, the program compared by basename (`/usr/bin/xcodebuild`
    matches "xcodebuild"); a launcher in front is skipped (`xcrun simctl boot` matches "simctl boot", and
    `npx expo start` matches "expo start"). Only the program position counts: `ls build` is not `build`."""
    p = pattern.split()
    if not p or not w:
        return False
    starts = [0] + ([1] if w[0].rsplit("/", 1)[-1] in LAUNCHERS and len(w) > 1 else [])
    for s in starts:
        cand = w[s : s + len(p)]
        if len(cand) == len(p) and cand[0].rsplit("/", 1)[-1] == p[0] and cand[1:] == p[1:]:
            return True
    return False


def uses_lock(cmd: str, name: str, res: Table) -> bool:
    """True if the call goes through the resource's lock (foreman lock NAME, or an allow regex)."""
    if re.search(rf"(^|[\s/])foreman\s+(--project\s+\S+\s+)?lock\s+{re.escape(name)}\b", cmd):
        return True
    return any(re.search(a, cmd) for a in res.get("allow", []))


def names_apply(cmd: str) -> bool:
    """Whether a command may run `foreman permissions apply`: its text, without quotes, backslashes and line
    continuations, has both words (PERMISSIONS_APPLY is compared only then)."""
    flat = re.sub(r"\\\n|[\\'\"]", "", cmd)
    return "permissions" in flat and "apply" in flat


def names_internal(cmd: str) -> bool:
    """Whether a command may run one of foreman's own group (grants.INTERNAL): `foreman permissions apply`, or
    `foreman integrate … --opens` (a branch that changes a live foreman.toml or guard table so that it allows
    something new, integrate.py). The same loose reading as names_apply; the group's patterns decide."""
    flat = re.sub(r"\\\n|[\\'\"]", "", cmd)
    return names_apply(cmd) or ("integrate" in flat and "--opens" in flat)


def _who(project: Project, ctx: Ctx | None, process: bool = True) -> str:
    """Why the caller is not the marked orchestrator's main thread in its own process (agent | session | process),
    or "" when it is (a table run or `guard --as` compares no process; process=False compares none either)."""
    from . import orchestrator

    if not ctx or not ctx.get("session") or ctx.get("agent"):
        return "agent"
    simulated = "marked" in ctx  # a table run or `guard --as`: no process to compare
    marked = ctx["marked"] if simulated else (orchestrator.current(project) or {}).get("session")
    if ctx.get("session") != marked:
        return "session"
    if process and not simulated and not orchestrator.runs_here(project):  # the only check that runs `ps`
        return "process"
    return ""


class _Check:
    """One check() of a Bash command: what its rules share, each read once when first asked and kept (an exception
    too, raised again to each rule that asks): the registry, _who without the process, the folder it runs in, the
    lines the command runs (_walk), its texts with and without its pushes resolved (_texts) and what git says of each
    folder (the Resolver)."""

    def __init__(self, project: Project, cmd: str, ctx: Ctx | None, projects: Sequence[Project] | None) -> None:
        self.project, self.cmd, self.ctx = project, cmd, ctx
        cwd = (ctx or {}).get("cwd")
        self.given = cwd
        self.git = gitpush.Resolver(project, cwd)
        self.at = self.git if ctx is not None and "cwd" in ctx else None  # without the key: the text only
        self.kept: dict[Any, tuple[Any, Exception | None]] = {}
        if projects is not None:
            self.kept["projects"] = (_projects(project, projects), None)

    def once(self, key: Any, make: Callable[[], Any]) -> Any:
        if key not in self.kept:
            try:
                self.kept[key] = (make(), None)
            except Exception as e:  # noqa: BLE001 (kept, and raised to each caller)
                self.kept[key] = (None, e)
        value, err = self.kept[key]
        if err is not None:
            raise err
        return value

    @property
    def cwd(self) -> str:  # read by the rules (the process's folder may be gone: a rule's failure, not check()'s)
        return self.once("cwd", lambda: os.path.normpath(os.path.expanduser(self.given or os.getcwd())))

    def projects(self) -> Sequence[Project]:
        return self.once("projects", lambda: _projects(self.project, None))

    def who(self) -> str:
        return self.once("who", lambda: _who(self.project, self.ctx, process=False))

    def lines(self) -> list[Line | str]:
        return self.once("lines", lambda: _walk(self.cmd, frozenset({self.cwd}), {"HOME": [str(Path.home())]}, 0))

    def deny(self, pats: Sequence[str], resolve: bool = True) -> Hit | None:
        at = self.at if resolve else None
        return deny(pats, self.cmd, at, self.texts(at))

    def texts(self, at: gitpush.Resolver | None) -> Iterator[Text]:
        """The command's texts (_texts), read once for each at, up to the end or to the exception that stopped the
        reading, raised again in the same place: a list only answers what one read in order would have."""
        if at not in self.kept:
            items: list[Text] = []
            try:
                for t in _texts(self.cmd, 0, True, at=_start(at)):
                    items.append(t)
                self.kept[at] = (items, None)
            except Exception as e:  # noqa: BLE001 (raised to each reader after the texts before it)
                self.kept[at] = (items, e)
        items, err = self.kept[at]
        yield from items
        if err is not None:
            raise err


def _why(chk: _Check, hit: Hit) -> str:
    """Why a command of a [deny.grants] group does not pass (agent | session | process | grant), or "" when it does.
    For grant, hit.group is the first group without a live grant; on a pass, hit.grant lists those used."""
    from . import grants

    who = _who(chk.project, chk.ctx)
    if who or chk.ctx is None:
        return who or "agent"
    used = []
    for group, pats in grants.groups(chk.project, True).items() if "marked" not in chk.ctx else ():
        if not chk.deny(pats):
            continue
        g = grants.live(chk.project, group)
        if g is None:
            hit.group = group
            return "grant"
        used.append({"group": group, **{k: g.get(k) for k in ("via", "at", "answer")}})
    hit.grant = used or None
    return ""


def check(
    project: Project, cmd: str, ctx: Ctx | None = None, projects: Sequence[Project] | None = None
) -> Verdict | None:
    """None if allowed, or (kind, detail) with kind "deny" (detail = the pattern, a Hit that says where it matched),
    "orchestrator" (a [deny.grants] Hit whose why says why this caller may not: module doc) or "lock"
    (detail = (resource, the matching segment)). ctx: who calls (None: an agent). projects: the registry's, if
    already loaded. Never raises: anything unexpected is a deny."""
    try:
        from . import grants

        chk = _Check(project, cmd, ctx, projects)
        hit = chk.deny(list(BUILTIN), resolve=False)
        if hit:
            return "deny", hit
        hit = chk.deny([PERMISSIONS_APPLY], resolve=False) if names_apply(cmd) else None
        if hit:
            hit.why = _who(project, ctx)
            if hit.why:
                return "orchestrator", hit
        hit = _bash_file(chk)
        if hit:
            return "file", hit
        hit = chk.deny(project.section("deny").get("patterns", []))
        if hit:
            return "deny", hit
        hit = _agent_only(chk)
        if hit:
            return "orchestrator", hit
        hit = chk.deny(grants.patterns(project, internal=names_internal(cmd)))
        if hit:
            hit.why = _why(chk, hit)
            if hit.why:
                return "orchestrator", hit
            if ctx is not None:
                ctx["passed"] = hit
        no_heredoc = strip_heredocs(cmd)
        plain = strip_quotes(no_heredoc)
        for name, res in project.resources.items():
            pats = res.get("guard", [])
            if not pats or uses_lock(no_heredoc, name, res):
                continue
            for seg in segments(plain):
                w = words(seg)
                for pat in pats:
                    if matches_prefix(w, pat):
                        return "lock", (name, " ".join(w)[:80])
        return None
    except Exception as e:  # noqa: BLE001 (the guard reports any failure as a deny, never a crash)
        return "deny", f"(the guard could not check this command: {type(e).__name__})"


def _either(rule: Callable[[], Hit | None], text_only: Callable[[], Hit | None]) -> Hit | None:
    """rule(); when it fails, text_only() (the rule on the command's text alone); when that fails too, None: a failure
    of foreman blocks only what the text names (O7)."""
    try:
        return rule()
    except Exception:  # noqa: BLE001
        try:
            return text_only()
        except Exception:  # noqa: BLE001
            return None


# ---------- the reserved files (module doc) ----------

FILE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")  # the tools whose call names the file it writes
FILE_KINDS = ("config", "table", "state", "registry", "claude", "git")
AGENT_KINDS = ("git",)  # the classes only an agent may not write (the orchestrator and the owner's sessions may)
GIT_FOLDERS = ("hooks", "info")  # of a protected repo's common git directory (_git_entries)
GIT_FILES = ("config", "config.worktree")
WORKTREE_CONFIG = os.sep + os.path.join("worktrees", "*", "config.worktree")  # each worktree's, as a pattern (_match)
SYSTEM_GITCONFIG = ("/etc/gitconfig", "/opt/homebrew/etc/gitconfig", "/usr/local/etc/gitconfig")
CLAUDE_FREE = ("projects", "plans")  # what Claude Code writes for itself under its folder: transcripts, memory, plans
CLAUDE_SETTINGS = ("settings.json", "settings.local.json")  # in any folder named .claude
LOOKS_RESERVED = ("foreman.toml", "guard.tsv", "projects.toml", *CLAUDE_SETTINGS)  # a failed check denies these names
ASK_KINDS = ("registry", "claude")  # what the owner may let one main-thread call write (grants.ONCE_KINDS)

Reserved = tuple[str, str, bool, str]  # (class, the real path case-folded on macOS, a folder?, project name or "")


def _fold(path: str) -> str:
    return path.lower() if sys.platform == "darwin" else path


def _real(path: PathLike) -> str:
    return _fold(os.path.realpath(path))


def _under(path: str, folder: str) -> bool:
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


def _claude_dirs() -> list[Path]:
    """Claude Code's own folders: ~/.claude, and $CLAUDE_CONFIG_DIR when set."""
    dirs = [Path.home() / ".claude"]
    extra = os.environ.get("CLAUDE_CONFIG_DIR")
    if extra:
        dirs.append(Path(os.path.expanduser(extra)))
    return dirs


def _machine(real: Any = _real) -> list[Reserved]:
    """The reserved entries that need no project: the state root, the registry, Claude Code's. real: how a path is
    made comparable (the real path; the failure path only normalizes the text)."""
    from . import config

    reg = config.registry_path()
    default = not os.environ.get("FOREMAN_REGISTRY")  # the default registry: its whole folder (the busy scripts)
    out: list[Reserved] = [
        ("state", real(config.state_root()), True, ""),
        ("registry", real(reg.parent if default else reg), default, ""),
    ]
    out += [("claude", real(d), True, "") for d in _claude_dirs()]
    out.append(("claude", real(Path.home() / ".claude.json"), False, ""))
    return out


def _projects(project: Project, projects: Sequence[Project] | None) -> Sequence[Project]:
    """The projects of the registry (`projects`, else config.registered_projects()), with `project` among them."""
    from . import config

    if projects is None:
        projects = config.registered_projects()
    if not any(p.name == project.name for p in projects):
        projects = [*projects, project]
    return projects


def _live(project: Project, projects: Sequence[Project]) -> list[tuple[str, str, str]]:
    """[(class, real path, project name)] of the config and the guard table of each project, then those of
    `project`'s own home (reserved())."""
    out: list[tuple[str, str, str]] = []
    for p in projects:
        out.append(("config", os.path.realpath(p.config_file or p.home / "foreman.toml"), p.name))
        table = p.section("check").get("guard_table")
        if table:
            out.append(("table", os.path.realpath(p.path(table)), p.name))
    out.append(("config", os.path.realpath(project.home / "foreman.toml"), project.name))
    table = project.section("check").get("guard_table")
    if table:
        out.append(("table", os.path.realpath(project.path(table)), project.name))
    return out


def reserved(project: Project, projects: Sequence[Project] | None = None) -> list[Reserved]:
    """Every reserved file and folder (module doc), the most precise first: the config and the guard table of each
    project of the registry (`projects`, else config.registered_projects()), and those of `project`'s own home too
    (its home/foreman.toml, not a candidate's file: a check run from a copy of the home, as `foreman integrate` does in
    a worktree, judges its rows as the live home would; the hook always passes the registered project), each
    project's state, then the machine's."""
    projects = _projects(project, projects)
    live = _live(project, projects)
    out: list[Reserved] = [(kind, _fold(path), False, name) for kind, path, name in live]
    out += [("state", _real(p.state), True, p.name) for p in projects]
    git = [("git", _fold(path), folder, name) for path, folder, name in git_entries(project, projects, live)]
    return out + _machine() + git


def _both(path: str, reals: dict[str, str]) -> list[str]:
    """A path as the file it reaches (its real path) and as itself (a link: its folder's real path and its name).
    reals: the real path of each folder already resolved in this call (one realpath per folder, an lstat per file)."""
    head, tail = os.path.split(path)
    head = head or "."
    if head not in reals:
        reals[head] = os.path.realpath(head)
    itself = os.path.join(reals[head], tail)
    return [os.path.realpath(itself), itself] if os.path.islink(itself) else [itself]


def protected_repos(
    project: Project, projects: Sequence[Project], live: Sequence[tuple[str, str, str]] | None = None
) -> dict[str, str]:
    """{the real common git directory: the project} of each repo whose files the git class reserves: the checkout
    of each live config and guard table of the registry (live: _live()'s, real paths), and each [repos.*] path of
    every project (walked up as written, then one realpath per checkout)."""
    if live is None:
        live = _live(project, projects)
    places = [(os.path.dirname(path), name, True) for _kind, path, name in live]
    for p in projects:
        places += [(str(p.path(r["path"])), p.name, False) for r in p.repos.values() if r.get("path")]
    out: dict[str, str] = {}
    tops: set[str] = set()
    for place, name, real in places:
        top = gitdir.checkout_of(place, resolve=False)
        if top is None or top in tops:
            continue
        tops.add(top)
        common = gitdir.common_dir(top if real else os.path.realpath(top))
        if common and common not in out:
            out[common] = name
    return out


def _machine_git() -> list[tuple[str, bool]]:
    """[(path, a folder?)] of the machine's git config: ~/.gitconfig, $XDG_CONFIG_HOME/git/ (or ~/.config/git/) and
    the real file of each link in it, $GIT_CONFIG_GLOBAL and $GIT_CONFIG_SYSTEM when set, and the system configs."""
    xdg = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "git")
    files = [os.path.expanduser("~/.gitconfig"), os.path.join(xdg, "config")]
    files += [
        os.path.expanduser(v) for v in (os.environ.get(k) for k in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM")) if v
    ]
    out = [(f, False) for f in [*files, *SYSTEM_GITCONFIG]] + [(xdg, True)]
    try:
        names = os.listdir(xdg)
    except OSError:
        names = []
    for n in names:
        f = os.path.join(xdg, n)
        if os.path.islink(f):
            out.append((os.path.realpath(f), os.path.isdir(f)))
    for f in [*files, *SYSTEM_GITCONFIG]:
        out += gitdir.config_paths(f)
    return out


def git_entries(
    project: Project, projects: Sequence[Project], live: Sequence[tuple[str, str, str]] | None = None
) -> list[tuple[str, bool, str]]:
    """[(path, a folder?, the project or "")] of the git class (module doc), real paths not case-folded: of each
    protected repo's common git directory, hooks/, info/, config, config.worktree, each worktree's config.worktree
    (a pattern) and what those configs name (an absolute core.hooksPath, included files); then the machine's git
    config. A failure gives [] (O8: it never stops a write)."""
    try:
        out: list[tuple[str, bool, str]] = []
        reals: dict[str, str] = {}
        for common, name in protected_repos(project, projects, live).items():  # common: a real path
            listed: list[tuple[str, bool]] = []
            for f in (*GIT_FOLDERS, *GIT_FILES):
                path = os.path.join(common, f)
                folder = f in GIT_FOLDERS
                if os.path.islink(path):  # also the file it reaches
                    out.append((os.path.realpath(path), folder, name))
                out.append((path, folder, name))
                if not folder:
                    listed += gitdir.config_paths(path)
            out += [(x, folder, name) for path, folder in listed for x in _both(path, reals)]
            out.append((common + WORKTREE_CONFIG, False, name))
        out += [(x, folder, "") for path, folder in _machine_git() for x in _both(path, reals)]
        return list(dict.fromkeys(out))
    except Exception:  # noqa: BLE001 (O8: a failure of the git class stops nothing)
        return []


def git_spelled(project: Project, projects: Sequence[Project]) -> dict[str, str]:
    """{the comparable path (case-folded on macOS): the path as on disk} of the git class (permissions' band)."""
    return {_fold(path): path for path, _folder, _name in git_entries(project, _projects(project, projects))}


def _match(path: str, entries: Iterable[Reserved]) -> Reserved | None:
    """The first entry that holds the (comparable) path: the file itself, or a folder above it; Claude Code's
    projects/ and plans/ are left out of its folder; any .claude/settings*.json is claude; a worktree's
    config.worktree matches its pattern (WORKTREE_CONFIG)."""
    for e in entries:
        kind, where, folder, _ = e
        if kind == "git" and where.endswith(WORKTREE_CONFIG):
            wts = where[: -len(WORKTREE_CONFIG)] + os.sep + "worktrees"
            if os.path.basename(path) == "config.worktree" and os.path.dirname(os.path.dirname(path)) == wts:
                return e
            continue
        if (folder and _under(path, where)) or path == where:
            if kind == "claude" and folder and any(_under(path, os.path.join(where, x)) for x in CLAUDE_FREE):
                continue
            return e
    if os.path.basename(path) in CLAUDE_SETTINGS and os.path.basename(os.path.dirname(path)) == ".claude":
        return ("claude", path, False, "")
    return None


def reserved_entry(path: PathLike, entries: Iterable[Reserved]) -> Reserved | None:
    """The entry of reserved() that holds a path (its real path, case-folded on macOS), or None: what `foreman
    integrate` asks of each file a branch changes in a main checkout."""
    return _match(_real(path), entries)


def _linked(path: str, entries: Iterable[Reserved]) -> Reserved | None:
    """The reserved file a path is a hard link of (an existing file with more than one link), else None."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    if st.st_nlink < 2:
        return None
    for e in entries:
        if not e[2]:
            try:
                other = os.stat(e[1])
            except OSError:
                continue
            if (other.st_dev, other.st_ino) == (st.st_dev, st.st_ino):
                return e
    return None


def _looks_reserved(path: str, cwd: str | None) -> bool:
    """For a failed check (O7): whether the path, as text only, is reserved (under a reserved folder of the
    machine, or named like a reserved file)."""
    try:
        text = _fold(os.path.normpath(os.path.join(cwd or "", os.path.expanduser(path))))
        if os.path.basename(text) in LOOKS_RESERVED:
            return True
        return _match(text, _machine(lambda p: _fold(os.path.normpath(p)))) is not None
    except Exception:  # noqa: BLE001 (not even the text can be read: let it pass, as any failure of an unreserved path)
        return False


def real_path(path: str, cwd: str | None = None) -> str:
    """The comparable form of the file a call names (as check_file takes it): `~` expanded, joined to cwd (else the
    process's), its real path, case-folded on macOS."""
    return _real(os.path.join(cwd or os.getcwd(), os.path.expanduser(str(path))))


def _spend(project: Project, kind: str, who: str, ctx: Ctx | None, path: str | None, command: str | None) -> bool:
    """Whether a main-thread call writing a class of ASK_KINDS spends the owner's one-use yes for it (grants.py)."""
    if kind not in ASK_KINDS or who == "agent" or not ctx or not ctx.get("tool_use_id"):
        return False
    from . import grants

    return grants.take_once(project, ctx.get("session"), ctx.get("tool_use_id"), path, command) is not None


def check_file(
    project: Project, path: str, ctx: Ctx | None = None, projects: Sequence[Project] | None = None
) -> Verdict | None:
    """None if a Write/Edit/MultiEdit/NotebookEdit call may write the file at path, else ("file", Hit) (module doc).
    ctx: who calls (None: an agent), its cwd for a relative path (else the process's). projects: the registry's, if
    already loaded. Never raises: a failure denies only a path that looks reserved ("deny", the failure's text)."""
    try:
        if not path:
            return None
        full = os.path.join((ctx or {}).get("cwd") or os.getcwd(), os.path.expanduser(str(path)))
        real = _real(full)
        entries = reserved(project, projects)
        hit = _match(real, entries) or _linked(full, entries)
        if hit is None:
            return None
        who = _who(project, ctx, process=False)
        if hit[0] in AGENT_KINDS and who != "agent":  # the git class refuses an agent only; the rest still applies
            rest = [e for e in entries if e[0] not in AGENT_KINDS]
            hit = _match(real, rest) or _linked(full, rest)
            if hit is None:
                return None
        if hit[0] == "state" and not who and real == _real(project.board):
            return None  # the orchestrator's own board
        if _spend(project, hit[0], who, ctx, real, None):
            return None  # the owner's yes for this one call
        out = Hit(f"file:{hit[0]}", "file", str(path))
        out.kind, out.path, out.name, out.why = hit[0], str(path), hit[3], who
        return "file", out
    except Exception as e:  # noqa: BLE001 (a failure never stops a write that does not look reserved: O7)
        if _looks_reserved(str(path), (ctx or {}).get("cwd")):
            return "deny", f"(the guard could not check this write: {type(e).__name__})"
        return None


# ---------- the reserved files a Bash command writes (module doc) ----------

# A line the lexer cannot read is denied only when it has one of these and names a reserved path as text.
WRITE_WORDS = re.compile(
    r">|(^|[\s/;&|(`'\"])(cp|mv|ln|install|rsync|ditto|scp|sed|gsed|perl|rm|rmdir|unlink|truncate|touch|shred|"
    r"chmod|chown|chgrp|chflags|dd|tee|curl|wget|find)(\s|$)"
)
# Code of an interpreter that is not Python passing pyscript's gate (_code_writes): a call that writes, renames,
# copies or removes a file, or opens one; the quoted literals of its arguments may be what it writes (an open, only
# with a mode that writes: a literal of its own, or `>` in front of the file's name, as perl has it)
CODE_WRITES = re.compile(
    r"(?i)\b(open|write|append|rename|unlink|remove|rm|rmdir|copy|move|mkdir|truncate|symlink|link|chmod|chown|"
    r"touch|replace|save)\w*"
)
OPEN_MODE = re.compile(r"\s*(\+?[<>]{1,2}|[wax]\+?b?|r\+b?)\s*")
CODE_LITERAL = re.compile(r"'([^'\n]*)'|\"([^\"\n]*)\"|`([^`\n]*)`")
MAX_LITERAL = 4096  # a longer quoted literal is not a file name
ADJACENT = re.compile(r"\s*([,/+])\s*")  # between two literals of one path written in pieces
PIECE = re.compile(r"/?[\w.~-]+(/[\w.~-]+)*/?")  # a literal that may be a piece of a path
STATEMENT = re.compile(r"(^|[;{}\n])\s*\Z")  # the start of a statement, before a call
OS_DOT = re.compile(r"\bos\.\Z")
MAX_PIECES = 8
VAR = re.compile(r"\$\{([A-Za-z_]\w*)\}|\$([A-Za-z_]\w*)")
NOT_LITERAL = re.compile(r"[$`*?\[]")  # what is left after expanding: the path is not known, only its text
TEXT_STOP = re.compile(r"[\s'\"`;|&()<>]")  # where a path written in a text ends
TILDE = re.compile(r"~(?=/)|(?<![^\s='\"])~(?![^\s'\"])")  # the home in a text: before a `/`, or a word
SETTINGS_TEXT = re.compile(r"(^|/)\.claude/settings(\.local)?\.json$")
DECLARERS = ("export", "local", "declare", "typeset", "readonly")
MAX_VALUES = 8  # the values one word may take from the line's variables, at most
SKIP_STDIN = ("xargs", "ssh")  # their input is not a script of this machine's shell


class _Target:
    """A file a line writes (shell.Write) as the guard reads it: the texts it may be (variables expanded), the
    folders it may be relative to, and the Write."""

    __slots__ = ("texts", "dirs", "write", "raw")

    def __init__(self, texts: list[str], dirs: frozenset[str], write: Write, raw: str) -> None:
        self.texts, self.dirs, self.write, self.raw = texts, dirs, write, raw


def _expand(text: str, literal: bool, env: dict[str, list[str]], tilde: bool) -> list[str]:
    """The texts a word may be: $NAME and ${NAME} replaced by each value the line (or HOME) gives them, a leading
    `~` expanded when tilde; a word with no expansion is itself."""
    if tilde and (text == "~" or text.startswith("~/")):
        text = os.path.expanduser(text)
    if literal:
        return [text]
    out = [text]
    for _ in range(4):  # one known variable more each time (a value may name another)
        nxt: list[str] = []
        for t in out:
            m = next((m for m in VAR.finditer(t) if (m.group(1) or m.group(2)) in env), None)
            if m is None:
                nxt.append(t)
            else:
                nxt += [t[: m.start()] + v + t[m.end() :] for v in env[m.group(1) or m.group(2)]]
        nxt = list(dict.fromkeys(nxt))[:MAX_VALUES]
        if nxt == out:
            break
        out = nxt
    return out


def _assigned(words_of: Sequence[Sequence[Tok]], info: Sequence[Runs], env: dict[str, list[str]]) -> None:
    """Adds to env the values the line assigns to its variables (VAR=…, export VAR=…), in order."""
    for w, r in zip(words_of, info):
        decl = r.prog < len(w) and w[r.prog].literal and w[r.prog].value in DECLARERS
        for i, t in enumerate(w):
            if not ASSIGN.match(t.raw) or not (i < r.prog or decl or r.prog >= len(w)):
                continue
            name = re.split(r"[\[+=]", t.raw, maxsplit=1)[0]
            raw_value = t.raw.partition("=")[2]
            env[name] = _expand(str(t.value).partition("=")[2], t.literal, env, raw_value.startswith("~"))


def _cd_known(
    words_of: Sequence[Sequence[Tok]], info: Sequence[Runs], env: dict[str, list[str]]
) -> list[Sequence[Tok]]:
    """The words of each segment, with the folder of a cd or pushd made literal when the line's variables give it one
    value (`D=~/x; cd $D`)."""
    out: list[Sequence[Tok]] = []
    for seg_words, r in zip(words_of, info):
        w, p = list(seg_words), r.prog
        if p + 1 < len(w) and w[p].literal and w[p].value in ("cd", "pushd") and not w[p + 1].literal:
            t = w[p + 1]
            got = _expand(str(t.value), False, env, t.raw.startswith("~"))
            if len(got) == 1 and not NOT_LITERAL.search(got[0]):
                w[p + 1] = piece(t, got[0])
                w[p + 1].literal = True
        out.append(w)
    return out


Line = tuple[str, list[Seg], list[list[Tok]], list[Runs], list[frozenset[str]], dict[str, list[str]]]  # one line
# a command runs (_walk): its text, segments, their words and what each runs, the folders each may run in, the
# values the line gives its variables


def _walk(cmd: str, start: frozenset[str], env: dict[str, list[str]], depth: int) -> list[Line | str]:
    """Every line the command runs, in order, from the folders each segment may run in (the line's cd and pushd,
    gitpush.folders, with the values the line gives its variables): the command itself, then what is nested in it
    (sh -c, eval, $(…), a heredoc or a pipe into a shell, xargs and find -exec; not what ssh runs); a text the lexer
    cannot read (the command itself, or one nested) is given as its str."""
    try:
        segs, lx, words_of, info = _read(cmd, depth)
    except Unparsed:
        return [cmd]
    env = dict(env)
    _assigned(words_of, info, env)
    known = _cd_known(words_of, info, env)
    here = gitpush.folders(segs, known, info, start, exists=False)
    out: list[Line | str] = [(cmd, segs, words_of, info, here, env)]
    nested: list[tuple[str, frozenset[str]]] = [(c, start) for c in lx.body_subs]
    printers = {k for k, w in enumerate(words_of) if printer(w)}
    for k, (seg, r) in enumerate(zip(segs, info)):
        w = words_of[k]
        name = PurePath(str(w[r.prog].value)).name if r.prog < len(w) and w[r.prog].literal else ""
        if name != "ssh":  # what ssh runs, runs elsewhere
            nested += [(text, here[k]) for _, text, _ in r.strings]
        nested += [(" ".join(t.raw for t in toks), here[k]) for toks in r.cmds]
        nested += [(c, here[k]) for t in seg.toks for _, c in t.subs]
        if r.stdin == "shell" and name not in SKIP_STDIN:
            nested += [(text, here[k]) for text in inputs(seg)]
            for f in feeders(segs, k) if printers else ():  # `echo "cp a b" | sh`: a printer piped into a shell
                if f in printers:  # (once: a long pipeline of shells would read it once per shell)
                    printers.discard(f)
                    fw = words_of[f]
                    nested.append((" ".join(str(x.value) for x in fw[info[f].prog + 1 :]), here[k]))
    for text, dirs in nested:
        out += _walk(text, dirs, env, depth + 1)
    return out


def _code_writes(seg: Seg, w: Sequence[Tok], prog: int) -> list[str]:
    """The files the code an interpreter runs (RAW_INTERPRETERS: its -c/-e word, its heredoc or here-string) may
    write, as written: for Python that passes pyscript's gate, what it writes (pyscript.writes(): with the literals of
    a target it cannot name); for other code, the quoted literals of the arguments of each call that writes
    (CODE_WRITES: up to its closing parenthesis, or to the end of the statement without one)."""
    name = PurePath(str(w[prog].value)).name if prog < len(w) and w[prog].literal else ""
    if not RAW_INTERPRETERS.fullmatch(name):
        return []
    opts = (*CODE_OPTS, "-r") if name.startswith("php") else CODE_OPTS
    codes = inputs(seg) + [str(b.value) for a, b in zip(w[prog + 1 :], w[prog + 2 :]) if a.literal and a.value in opts]
    out: list[str] = []
    for text in codes:
        tree = pyscript.gate(text) if pyscript.PYTHON.fullmatch(name) else None
        if tree is not None:
            named, unnamed = pyscript.writes(tree)
            out += [*named, *(unnamed or ())]
            continue
        for m in CODE_WRITES.finditer(text):
            rest, depth = text[m.end() :].lstrip(), 0
            args = re.split(r"[;\n]", rest, maxsplit=1)[0]
            for i, c in enumerate(rest if rest[:1] == "(" else ()):  # up to its closing parenthesis
                depth += (c == "(") - (c == ")")
                if not depth:
                    args = rest[: i + 1]
                    break
            lits = [x for g in CODE_LITERAL.finditer(args) for x in g.groups() if x]
            if m.group(1).lower() == "open" and not any(
                OPEN_MODE.fullmatch(x) or re.match(r"\s*\+?[<>]", x) for x in lits
            ):
                continue  # it reads: open(f, "w"), open my $f, ">", "f" and open(F, ">f") write
            dot, bare = text[m.start() - 1 : m.start()] == ".", CODE_LITERAL.sub("", args)
            start = dot or STATEMENT.search(text, 0, m.start())  # `File.write p, …`, `open my $f, …`: no parentheses
            called = rest[:1] == "(" or (start and re.match(r"[ \t]+[A-Za-z_$@]", text[m.end() :]))
            if (
                dot
                and m.group(0) in ("replace", "replaceAll")
                and "," in bare
                and not OS_DOT.search(text, 0, m.start())
            ):
                called = False  # a string's replace(pattern, value), not os.replace: its own literals only
            if (called and re.search(r"[A-Za-z_$]", bare)) or (dot and m.group(0) in pyscript.WRITES_SELF):
                args = text  # a target it cannot name (a variable, an expression, a Path's object): any of the code's
            out += [x for x in _pieces(args, args is text) if not OPEN_MODE.fullmatch(x)]
    return list(dict.fromkeys(out))


def _pieces(code: str, spread: bool = False) -> list[str]:
    """The quoted literals of code (perl's `>` in front of a file dropped), and each run of literals written next to
    each other also joined as one path, the way a path is built in pieces (`'a', 'b'` and `"a" / "b"`: a/b; `'a' +
    'b'`: ab; runs of up to MAX_PIECES); with spread (a target the code cannot name), literals that look like a
    path's pieces also run on across statements (`base = home / "a"` … `base / "b"`: a/b)."""
    out: list[str] = []
    run: list[tuple[str, str]] = []
    last = None
    for m in CODE_LITERAL.finditer(code):
        lit = re.sub(r"^\s*\+?[<>]{1,2}\s*", "", next(x for x in m.groups() if x is not None))
        gap = ADJACENT.fullmatch(code, last.end(), m.start()) if last else None
        joins = spread and run and PIECE.fullmatch(lit) and PIECE.fullmatch(run[-1][1])
        sep = gap.group(1) if gap else "/" if joins else ""
        run, last = ([*run, ("" if sep == "+" else "/", lit)] if sep else [("", lit)]), m
        run = run[-MAX_PIECES:]
        out += [run[j][1] + "".join(s + x for s, x in run[j + 1 :]) for j in range(len(run))]
    return [x for x in out if x and len(x) <= MAX_LITERAL]


def _targets(lines: Iterable[Line | str]) -> Iterator[_Target]:
    """Every file the lines write (shell.writes, _code_writes), from the folders each may run in; a text the lexer
    cannot read, as text only."""
    for line in lines:
        if isinstance(line, str):
            yield _Target([line], frozenset(), Write(-1, Tok("word", 0)), line)
            continue
        _cmd, segs, words_of, info, here, env = line
        for wr in writes(segs, info):
            t = wr.tok
            yield _Target(_expand(str(t.value), t.literal, env, t.raw.startswith("~")), here[wr.seg], wr, t.raw)
        for k, (seg, w) in enumerate(zip(segs, words_of)):
            for path in _code_writes(seg, w, info[k].prog):  # also from HOME: Path.home() / "x", os.homedir()
                tok = Tok("word", 0)
                tok.value, tok.raw = path, path
                yield _Target(_expand(path, True, env, True), here[k] | {str(Path.home())}, Write(k, tok), path)


def _spelled(text: str) -> str:
    """A text as the rules find paths in it (_names, _names_top, _holdout_named): $HOME, ${HOME} and `~` (before a
    `/`, or a word of its own) read as the home, case-folded on macOS."""
    home = str(Path.home())
    return _fold(TILDE.sub(lambda _m: home, text.replace("${HOME}", home).replace("$HOME", home)))


def _tails(t: str, key: str) -> Iterator[str]:
    """The path written in a text (_spelled) at each place key appears in it: up to a space, a quote or an operator."""
    i = t.find(key)
    while i >= 0:
        yield TEXT_STOP.split(t[i:], maxsplit=1)[0]
        i = t.find(key, i + 1)


def _names(text: str, entries: Sequence[Reserved]) -> Reserved | None:
    """The reserved entry a text names, as text only (a path that is not known: `$X/.claude/settings.json`,
    `${D:-~/.local/state/foreman}/x`): one of the reserved paths, or something under a reserved folder, appears in
    it; or it ends in .claude/settings.json or settings.local.json."""
    t = _spelled(text)
    for e in entries:
        if any(_match(os.path.normpath(tail), [e]) for tail in _tails(t, e[1])):
            return e
    return next((("claude", w, False, "") for w in TEXT_STOP.split(t) if SETTINGS_TEXT.search(w)), None)


def _path_hit(full: str, entries: Sequence[Reserved], tree: bool) -> Reserved | None:
    """The reserved entry a known path reaches (check_file's comparison: the real path, case-folded), also as the
    link itself (rm, mv and ln -sf change a symbolic link, not what it points to); with tree, also a reserved file or
    folder under it, or a .claude folder's settings."""
    head, tail = os.path.split(full)
    reals = list(dict.fromkeys([_real(full), _fold(os.path.join(os.path.realpath(head or "."), tail))]))
    for real in reals:
        e = _match(real, entries)
        if e is not None:
            return e
    e = _linked(full, entries)
    if e is not None:
        return e
    if tree:
        for real in reals:
            for e in entries:
                if _under(e[1], real) and real != e[1]:
                    return e
            if os.path.basename(real) == ".claude":
                for s in CLAUDE_SETTINGS:
                    if os.path.exists(os.path.join(full, s)):
                        return ("claude", os.path.join(real, s), False, "")
    return None


def _target_hits(t: _Target, entries: Sequence[Reserved]) -> Iterable[tuple[Reserved, str]]:
    """(the reserved entry, the real path or the text) of each file a target may be."""
    w = t.write
    if w.seg < 0:  # a nested text the lexer cannot read: only if it looks like a write and names a reserved path
        if WRITE_WORDS.search(t.texts[0]):
            e = _names(t.texts[0], entries)
            if e is not None:
                yield e, t.texts[0]
        return
    for text in t.texts:
        if not text:
            continue
        if NOT_LITERAL.search(text):
            e = _names(text, entries)
            if e is not None:
                yield e, text
            continue
        dirs = [""] if os.path.isabs(text) else list(t.dirs)
        for d in dirs:
            if not d and not os.path.isabs(text):  # relative to a folder the line cannot name: its text only
                e = _names(text, entries)
                if e is not None:
                    yield e, text
                continue
            full = os.path.normpath(os.path.join(d, text))
            fulls = [(full, w.tree)]
            if w.names is not None and os.path.isdir(full):  # a copy into a folder: each source's name in it
                fulls = [(full, False)] + [(os.path.join(full, n), w.tree or not n) for n in w.names]
            for f, tree in fulls:
                e = _path_hit(f, entries, tree)
                if e is not None:
                    yield e, _real(f)


def _bash_file(chk: _Check) -> Hit | None:
    """The reserved file a Bash command writes by name (module doc), as check_file's Hit with bash set, or None.
    The orchestrator's own board passes for its main thread. Never raises: a failure denies only a line that looks
    like a write and names a reserved path as text."""

    def rule() -> Hit | None:
        out = list(_targets(chk.lines()))
        if not out:
            return None
        entries = reserved(chk.project, chk.projects())
        who = chk.who()
        if who != "agent":  # the git class: the orchestrator and the owner's sessions write it
            entries = [e for e in entries if e[0] not in AGENT_KINDS]
        board = _real(chk.project.board)
        spent: bool | None = None
        for t in out:
            for e, where in _target_hits(t, entries):
                if e[0] == "state" and not who and where == board:
                    continue  # the orchestrator's own board
                if e[0] in ASK_KINDS and who != "agent":
                    if spent is None:
                        spent = _spend(chk.project, e[0], who, chk.ctx, None, chk.cmd)
                    if spent:
                        continue  # the owner's yes for this one command
                return _file_hit(e, t.raw, who, chk.cmd)
        return None

    def text_only() -> Hit | None:
        e = _names(chk.cmd, _machine(lambda p: _fold(os.path.normpath(p)))) if WRITE_WORDS.search(chk.cmd) else None
        return _file_hit(e, "", chk.who(), chk.cmd) if e is not None else None

    return _either(rule, text_only)


SHORT_PATH = 30  # in the refusal's quote of the command, a longer path (the refusal names it whole) is …/its name


def _file_hit(e: Reserved, path: str, who: str, cmd: str) -> Hit:
    """The Hit of a Bash command that writes a reserved file (path: its target as written, "" when unknown)."""
    out = Hit(f"file:{e[0]}", "file", "")
    flat, short = _flat(cmd), _flat(path)
    if len(short) > SHORT_PATH and "/" in short.rstrip("/"):
        short = "…/" + short.rstrip("/").rsplit("/", 1)[1]
        flat = flat.replace(_flat(path), short)
    out.part = _excerpt(re.escape(short), flat) if path else flat[:EXCERPT]
    out.kind, out.path, out.name, out.why, out.bash = e[0], path, e[3], who, True
    return out


# ---------- an agent's git in a main checkout (a repo or a live home), and `foreman integrate` (module doc) ----------

# The pattern of an agent's refused git (Hit: kind the subcommand, dir the main checkout, name its project) and the
# regex of `foreman integrate` (bin/foreman, a path, --project P, quoted words), but with --help, -h or --check.
MAIN_CHECKOUT = "git:main-checkout"
INTEGRATE = (
    r"""(^|[\s/'"])foreman\S*['"]?\s+(--project[\s=]\S+\s+)?['"]?integrate['"]?"""
    r"""(?![^;&|\n]*\s(?P<q>['"]?)(--help|-h|--check)(?P=q)(\s|$))(\s|$)"""
)
HOLDOUT = "holdout:hidden-checks"  # the Hit of an agent reaching the hidden acceptance checks (_holdout)
AGENT_ONLY = (MAIN_CHECKOUT, INTEGRATE, HOLDOUT)  # the built-in rules that refuse an agent only (permissions.rules)
# What rewrites the main checkout: always (the whole copy) ...
GIT_WHOLE = frozenset(
    "merge pull rebase cherry-pick revert am switch clean filter-branch checkout-index".split()
)  # fmt: skip
# ... or by its options and paths (_git_rewrites)
GIT_REWRITES = GIT_WHOLE | frozenset(
    "bisect read-tree sparse-checkout submodule apply reset stash checkout restore rm mv".split()
)  # fmt: skip
GIT_SAFE_APPLY = ("--check", "--stat", "--numstat", "--summary", "--cached")  # apply that changes no file
GIT_HARD_RESET = ("--hard", "--merge", "--keep")
GIT_WORDS = re.compile(r"\bgit\b")
# A text the lexer cannot read: `git [options] SUB` with SUB one of GIT_REWRITES
GIT_REWRITE_TEXT = re.compile(
    r"\bgit(\s+(-[Cc]\s*\S+|--?[\w-]+(=\S+)?))*\s+(" + "|".join(map(re.escape, sorted(GIT_REWRITES))) + r")(?![\w-])"
)
INTEGRATE_FREE = ("--help", "-h", "--check")  # what an agent may run of `foreman integrate`


class _Args:
    """The arguments of a git subcommand: opts, the option names it gives (`-x` for each letter of a cluster,
    `--name`, "?" for one the line computes); pos, the other words (Toks); paths, those after `--` (None: no `--`)."""

    __slots__ = ("opts", "pos", "paths")

    def __init__(self, args: Sequence[Tok], short_vals: str = "", long_vals: Sequence[str] = ()) -> None:
        self.opts: set[str] = set()
        self.pos: list[Tok] = []
        self.paths: list[Tok] | None = None
        k = 0
        while k < len(args):
            t = args[k]
            k += 1
            if self.paths is not None:
                self.paths.append(t)
                continue
            if not t.literal:
                if t.raw.lstrip("\"'").startswith("-"):
                    self.opts.add("?")
                else:
                    self.pos.append(t)
                continue
            v = str(t.value)
            if v == "--":
                self.paths = []
            elif v.startswith("--"):
                name, eq, _ = v.partition("=")
                self.opts.add(name)
                k += 1 if name in long_vals and not eq else 0
            elif v.startswith("-") and len(v) > 1:
                for i, ch in enumerate(v[1:]):
                    self.opts.add("-" + ch)
                    if ch in short_vals:
                        k += 0 if v[i + 2 :] else 1  # its value: the rest of the cluster, else the next word
                        break
            else:
                self.pos.append(t)


def _git_rewrites(sub: str, args: Sequence[Tok], folder: str) -> bool | list[Tok]:
    """Whether `git SUB ARGS` run in folder rewrites the files of its checkout: True when it changes the whole copy
    (another branch or commit, a merge, an undo, the stash taken back), the paths it changes when it goes by paths
    (_reaches decides), False when it changes no file (it reads, or touches only the index)."""
    if sub in GIT_WHOLE:
        return True
    if sub == "bisect":
        a = _Args(args)
        return not (a.pos and a.pos[0].literal and a.pos[0].value in ("log", "view", "visualize"))
    if sub == "read-tree":
        return bool(_Args(args, "", ("--prefix", "--index-output")).opts & {"-u", "--reset"})
    if sub == "sparse-checkout":
        a = _Args(args)
        return not (a.pos and a.pos[0].literal and a.pos[0].value == "list")
    if sub == "submodule":
        a = _Args(args)
        return any(not t.literal or t.value == "update" for t in a.pos)
    if sub == "apply":
        return not (_Args(args, "pC", ("--directory", "--exclude", "--include")).opts & set(GIT_SAFE_APPLY))
    if sub == "reset":
        return bool(_Args(args).opts & set(GIT_HARD_RESET))
    if sub == "stash":
        first = args[0] if args else None
        if first is not None and not first.literal:
            return True
        sub2, rest = ("push", list(args))
        if first is not None and not str(first.value).startswith("-"):
            sub2, rest = str(first.value), list(args[1:])
        if sub2 in ("pop", "apply", "branch", "save"):
            return True
        if sub2 != "push":
            return False  # list, show, drop, clear, create, store: no file of the copy
        a = _Args(rest, "m", ("--message", "--pathspec-from-file"))
        paths = a.pos + (a.paths or [])
        return True if "--pathspec-from-file" in a.opts or "?" in a.opts or not paths else paths
    if sub == "checkout":
        a = _Args(args, "bB", ("--orphan", "--conflict", "--pathspec-from-file"))
        if a.opts & {"-b", "-B", "--orphan", "--detach", "--pathspec-from-file", "?"}:
            return True
        if a.paths is not None:
            return a.pos + a.paths if a.paths else bool(a.pos)  # `checkout X --` switches to X
        if not a.pos:
            return bool(a.opts & {"-f", "--force"})  # `checkout -f` throws away what changed
        if len(a.pos) == 1:
            t = a.pos[0]
            here = folder and os.path.lexists(os.path.join(folder, os.path.expanduser(str(t.value))))
            return [t] if t.literal and here else True  # one word that is no path: a branch or a commit
        return a.pos  # the first one is the tree; reading it as a path too only blocks more
    a = _Args(args, "s" if sub == "restore" else "", ("--source", "--conflict", "--pathspec-from-file"))
    if "--pathspec-from-file" in a.opts or "?" in a.opts:
        return True
    if sub == "restore" and a.opts & {"-S", "--staged"} and not a.opts & {"-W", "--worktree"}:
        return False
    if sub == "rm" and "--cached" in a.opts:
        return False
    return a.pos + (a.paths or [])  # restore, rm, mv (its sources and its destination)


Top = tuple[str, str, list[str]]  # a main checkout: (its path, the project, its live files folded)


def main_checkouts(project: Project, projects: Sequence[Project] | None = None) -> dict[str, Top]:
    """{the folded path of a checkout: Top} of each main checkout of a project of the registry: the one that holds
    a live config or guard table (reserved()), and the one of each [repos.*] path that exists (a home outside its
    repo), with its live files: those configs and tables, and the checkout's .claude/settings*.json."""
    out: dict[str, Top] = {}

    def add(top: str, name: str) -> list[str]:
        key = _fold(top)
        if key not in out:
            out[key] = (top, name, [_fold(os.path.join(top, ".claude", s)) for s in CLAUDE_SETTINGS])
        return out[key][2]

    projects = _projects(project, projects)
    for _kind, path, name in _live(project, projects):
        top = gitdir.checkout_of(os.path.dirname(path))
        if top is not None:
            add(top, name).append(_fold(path))
    for p in projects:
        for r in p.repos.values():
            where = str(p.path(r["path"])) if isinstance(r, dict) and r.get("path") else ""
            top = gitdir.checkout_of(where) if where and os.path.isdir(where) else None
            if top is not None:
                add(top, p.name)
    return out


def _reaches(t: Tok, folder: str, top: str, live: Sequence[str]) -> bool:
    """Whether a path git is given, in folder, may reach a live file of the checkout top (folded): the file, a
    folder that holds it, a .claude folder or settings file, or a pathspec the guard cannot read."""
    v = str(t.value) if t.literal else re.sub(r"[\\'\"]", "", t.raw)
    if not folder or v.startswith(":"):
        return True
    cut = re.search(r"[$`*?\[]", v)
    if cut:  # a variable or a wildcard: what its literal start may reach (the folder it lies in, and all under it)
        head = v[: cut.start()]
        base = head if head.endswith("/") or not head else os.path.dirname(head) + "/"
        real = _real(os.path.normpath(os.path.join(folder, os.path.expanduser(base or "."))))
        return any(_under(f, real) for f in live)
    real = _real(os.path.normpath(os.path.join(folder, os.path.expanduser(v))))
    if any(f == real or _under(f, real) for f in live):
        return True
    return _under(real, top) and (os.path.basename(real) == ".claude" or bool(SETTINGS_TEXT.search(real)))


def _names_top(text: str, tops: dict[str, Top]) -> str | None:
    """The folded top a text names (a path written in it whose checkout is that top), or None."""
    t = _spelled(text)
    tails = ((key, gitdir.checkout_of(os.path.normpath(tail))) for key in tops for tail in _tails(t, key))
    return next((key for key, got in tails if got is not None and _fold(got) == key), None)


def _git_hit(sub: str, top: Top, part: str) -> Hit:
    hit = Hit(MAIN_CHECKOUT, "command", "")
    hit.part = _excerpt(r"\bgit\b", part)
    hit.kind, hit.dir, hit.name, hit.why = sub, top[0], top[1], "agent"
    return hit


def _main_checkout(chk: _Check) -> Hit | None:
    """The git an agent's line runs that rewrites a main checkout (main_checkouts; module doc), as a Hit, or None.
    Never raises: a failure refuses only a line that names git and such a checkout as text."""

    def tops() -> dict[str, Top]:
        return chk.once("tops", lambda: main_checkouts(chk.project, chk.projects()))

    found: list[Hit] = []

    def one(w: Sequence[Tok], prog: int, dirs: frozenset[str], env: dict[str, list[str]], line: str) -> Hit | None:
        c = gitpush.parse_git(w, prog)
        if c is None or c.sub >= len(w):
            return None
        t = w[c.sub]
        sub = str(t.value) if t.literal else None
        if sub is not None and sub in gitpush.BUILTINS and sub not in GIT_REWRITES:
            return None  # it reads, or changes no file of the copy
        here = gitpush.moved(dirs, c.dirs, exists=False)
        for wt in [*env.get("GIT_WORK_TREE", []), *c.trees]:  # the work tree it is given, besides the folder
            here |= gitpush.moved(here, [wt], exists=False)  # (whose repo it still changes)
        all_tops = tops()
        if not all_tops:
            return None
        top: Top | None = None
        folder = ""
        for f in sorted(x for x in here if x != gitpush.UNKNOWN):
            got = gitdir.checkout_of(f)
            if got is not None and _fold(got) in all_tops:
                top, folder = all_tops[_fold(got)], f
                break
        if top is None and gitpush.UNKNOWN in here:
            key = _names_top(line, all_tops)
            top = all_tops[key] if key else None
        if top is None:
            return None
        part = " ".join(x.raw for x in w)
        args: Sequence[Tok] = w[c.sub + 1 :]
        if sub is None:  # a subcommand the line computes
            return _git_hit(t.raw, top, part)
        if sub not in gitpush.BUILTINS:  # an alias (with the config of that folder and the line's -c)
            repo = chk.git.repo(folder or top[0])
            for cfg in gitpush.overlay(repo.config if repo else {}, c.changes.config):
                ex = gitpush.aliased(w[prog:], gitpush.Repo(None, None, cfg))
                if isinstance(ex, str):  # a shell command, from the same folder
                    each(_walk(ex, frozenset({folder or gitpush.UNKNOWN}), env, 1), folder or top[0])
                elif ex is not None:  # a git command, or one the line computes, from the same folders
                    found.extend(h for h in [one(ex, 0, dirs, env, line)] if h)
            return found[0] if found else None
        got = _git_rewrites(sub, args, folder)
        if got is True or (isinstance(got, list) and any(_reaches(x, folder, _fold(top[0]), top[2]) for x in got)):
            return _git_hit(sub, top, part)
        return None

    def each(lines: Iterable[Line | str], cwd: str) -> None:  # cwd: where a text the lexer cannot read runs
        for line in lines:
            if found:
                return
            if isinstance(line, str):  # a text the lexer cannot read: git and a checkout it names, as text
                if GIT_REWRITE_TEXT.search(line):
                    key = _names_top(line, tops())
                    if key is None:
                        got = gitdir.checkout_of(cwd)
                        key = _fold(got) if got is not None and _fold(got) in tops() else None
                    if key is not None:
                        found.append(_git_hit("?", tops()[key], line))
                continue
            text, segs, words_of, info, here, env = line
            fn = gitpush.called(segs, words_of, info, text) if any(s.sep == "(" for s in segs) else {}
            for k, w in enumerate(words_of):
                for v, j in gitpush.variants(w, info[k].prog, fn.get(k)):  # a body runs where it is called too
                    dirs = here[k] if j is None else here[k] | here[j]
                    hit = None if found else one(v, info[k].prog, dirs, env, text)
                    if hit is not None:
                        found.append(hit)

    def rule() -> Hit | None:
        each(chk.lines(), chk.cwd)
        return found[0] if found else None

    def text_only() -> Hit | None:
        key = _names_top(chk.cmd, tops()) if GIT_REWRITE_TEXT.search(chk.cmd) else None
        return _git_hit("?", tops()[key], chk.cmd) if key is not None else None

    return _either(rule, text_only)


def main_checkout_hit(project: Project, cmd: str, cwd: str | None) -> Hit | None:
    """The MAIN_CHECKOUT Hit of a command an agent runs in cwd (permissions.touches: the rows of the rule)."""
    return _main_checkout(_Check(project, cmd, {"cwd": cwd} if cwd else None, None))


def holdout_hit(project: Project, cmd: str, cwd: str | None) -> Hit | None:
    """The HOLDOUT Hit of a command an agent (not a planner) runs in cwd (permissions.touches: the rows of the
    rule)."""
    return _holdout(_Check(project, cmd, {"cwd": cwd} if cwd else None, None))


def names_integrate(cmd: str) -> bool:
    """Whether a command may run `foreman integrate` (the loose reading of names_apply; INTEGRATE decides)."""
    return "integrate" in re.sub(r"\\\n|[\\'\"]", "", cmd)


def _agent_only(chk: _Check) -> Hit | None:
    """The built-in rules for an agent only (module doc): its `foreman integrate` (but --help, -h, --check), its
    git that rewrites a main checkout (a repo or a live home), and what reaches the hidden acceptance checks
    (_holdout: a planner passes). Only an agent (_who, no process) is compared."""
    integrate, git = names_integrate(chk.cmd), bool(GIT_WORDS.search(chk.cmd))
    if chk.who() != "agent":
        return None
    hit = _integrates(chk) if integrate else None
    if hit:
        hit.why = "agent"
        return hit
    hit = _main_checkout(chk) if git else None
    return hit or _holdout(chk)


def _integrates(chk: _Check) -> Hit | None:
    """The INTEGRATE Hit of a line that runs `foreman integrate` without --help, -h or --check as a word of its own:
    a segment whose program is foreman (shell.foreman_call), also nested (_walk); a text the lexer cannot read, by the
    regex (also when the walk fails). Values of foreman note, git commit -m, a heredoc to an interpreter are never
    commands here."""

    def whole(text: str) -> Hit | None:
        return Hit(INTEGRATE, "whole", text) if re.search(INTEGRATE, text) else None

    def rule() -> Hit | None:
        for line in chk.lines():
            if isinstance(line, str):
                if whole(line):
                    return whole(line)
                continue
            for w, r in zip(line[2], line[3]):
                got = foreman_call([str(t.value) for t in w[r.prog :]]) if r.prog < len(w) else None
                if got and got[1] == "integrate" and not set(got[2]) & set(INTEGRATE_FREE):
                    return Hit(INTEGRATE, "command", " ".join(t.raw for t in w))
        return None

    return _either(rule, lambda: whole(chk.cmd))


# ---------- the hidden acceptance checks: what an agent but a planner may not reach (module doc) ----------

HOLDOUT_FREE = ("--help", "-h")  # what an agent may run of `foreman holdout`
# Programs that walk the folders they are given (or the one they run in, given none) ...
WALKERS = frozenset("rg ag ack find fd du tar gtar bsdtar rsync tree ditto zip".split())
# ... and those that walk with one of these short options (or --recursive)
WALK_FLAGS = {
    "grep": "rR", "egrep": "rR", "fgrep": "rR", "ggrep": "rR", "ls": "R", "gls": "R", "cp": "rRa", "gcp": "rRa",
    "scp": "r", "chmod": "R", "chown": "R", "chgrp": "R", "rm": "rR",
}  # fmt: skip
WALK_TOOLS = ("Grep", "Glob")  # tools that walk the folder they are given (their `path`, else the call's cwd)
GLOB_CHARS = re.compile(r"[*?\[]")
# A text the lexer cannot read: `foreman holdout` (bin/foreman, a path, --project P, quoted words), and its help
HOLDOUT_CALL = r"""(^|[\s/'"])foreman\S*['"]?\s+(--project[\s=]\S+\s+)?['"]?holdout['"]?(\s|$)"""
HOLDOUT_HELP = r"""['"]?holdout['"]?\s+['"]?(-h|--help)['"]?(\s|$)"""
HoldoutDir = tuple[str, str, str]  # (the folded real path, the folded path as configured, the product's name)


def holdout_dirs(project: Project, projects: Sequence[Project] | None = None) -> list[HoldoutDir]:
    """The folder of hidden acceptance checks of each product of the registry ([paths] holdout) that exists: none
    exists, the rule costs nothing and refuses nothing."""
    out: list[HoldoutDir] = []
    for p in _projects(project, projects):
        d = p.holdout_dir
        if d.is_dir():
            out.append((_real(d), _fold(os.path.normpath(d)), p.name))
    return out


def holdout_reader(project: Project, ctx: Ctx | None) -> bool:
    """Whether the caller may reach the hidden acceptance checks: everyone _who does not call an agent (the
    orchestrator, the owner's other sessions) and an agent of a planner type (the event's agent_type, else its entry in
    <state>/agents.json); any other agent, also one whose type is not known, may not."""
    from . import identity, util

    if _who(project, ctx, process=False) != "agent":
        return True
    kind = (ctx or {}).get("agent_type")
    aid = (ctx or {}).get("agent")
    if not kind and aid:
        entry = (util.read_json(project.state / "agents.json", {}) or {}).get(aid)
        kind = entry.get("type") if isinstance(entry, dict) else None
    return identity.is_planner_type(project, kind)


def _holdout_hit(d: HoldoutDir, where: str, text: str) -> Hit:
    hit = Hit(HOLDOUT, where, text)
    hit.why, hit.name, hit.path = "agent", d[2], d[1]
    return hit


def _holdout_named(text: str, dirs: Sequence[HoldoutDir]) -> HoldoutDir | None:
    """The folder a text names as text (_spelled): its path, or one under it."""
    t = _spelled(text)
    return next(
        (d for d in dirs for form in dict.fromkeys(d[:2]) if any(_under(x, form) for x in _tails(t, form))), None
    )


def _holdout_reach(path: str, dirs: Sequence[HoldoutDir], walk: bool) -> HoldoutDir | None:
    """The folder an absolute path reaches: the folder or a path under it; with walk (the program or tool walks
    it), also a folder that holds it. A path with a wildcard: when it may match one of those (`**` any depth)."""
    norm = os.path.normpath(path)
    m = GLOB_CHARS.search(norm)
    if m is None:
        real = _real(norm)
        return next((d for d in dirs if _under(real, d[0]) or (walk and _under(d[0], real))), None)
    cut = norm.rfind(os.sep, 0, m.start())
    head = _real(norm[:cut] or os.sep).rstrip(os.sep)
    parts = (head + _fold(norm[cut:])).split(os.sep)
    for d in dirs:
        target = d[0].split(os.sep)
        for k, part in enumerate(parts):
            if part == "**" or k >= len(target):
                return d  # it may reach any depth below, or it names something under the folder
            if not fnmatch.fnmatchcase(target[k], part):
                break
        else:
            if len(parts) == len(target) or walk:
                return d  # the folder itself, or one that holds it walked
    return None


def _walks(name: str, args: Sequence[str]) -> bool:
    """Whether a program with these arguments walks the folders it is given (WALKERS, WALK_FLAGS)."""
    if name in WALKERS:
        return True
    flags = WALK_FLAGS.get(name)
    if not flags:
        return False
    for k, a in enumerate(args):
        if a == "--":
            break
        if a in ("--recursive", "--dereference-recursive", "--directories=recurse"):
            return True
        if a == "-d" and args[k + 1 : k + 2] == ["recurse"]:
            return True
        if a.startswith("-") and not a.startswith("--") and set(a[1:]) & set(flags):
            return True
    return False


def _holdout(chk: _Check) -> Hit | None:
    """The HOLDOUT Hit of an agent's line (module doc), or None: `foreman holdout` but --help or -h; a line that names
    a folder of hidden checks as text; a word (also a redirection's file, `--opt=VALUE`'s value, nested commands)
    that is, resolved from each folder the segment may run in, such a folder or a path in it, or a folder that holds
    one for a program that walks it (_walks; given no existing path, the folder it runs in); a segment that runs in
    one. Only for a caller holdout_reader refuses, and only while such a folder exists. Never raises: a failure
    compares the line's text only."""
    project, cmd = chk.project, chk.cmd
    try:
        dirs = holdout_dirs(project, chk.projects())
    except Exception:  # noqa: BLE001 (a registry that does not read: this product's folder alone)
        dirs = holdout_dirs(project, [project]) if project.holdout_dir.is_dir() else []
    if not dirs and "holdout" not in cmd:
        return None
    if holdout_reader(project, chk.ctx):
        return None

    def line_hit(segs: list[Seg], words_of: list[list[Tok]], info: list[Runs], here: Any, env: Any) -> Hit | None:
        for k, (seg, w, r) in enumerate(zip(segs, words_of, info)):
            values = [str(t.value) for t in w[r.prog :]]
            got = foreman_call(values) if r.prog < len(w) else None
            if got and got[1] == "holdout" and not set(got[2]) & set(HOLDOUT_FREE):
                return _holdout_hit(("", "", project.name), "command", " ".join(t.raw for t in w))
            if not dirs:
                continue
            folders = [f for f in here[k] if f != gitpush.UNKNOWN]
            walk = r.prog < len(w) and _walks(PurePath(values[0]).name, values[1:])
            named = False
            for t in seg.toks:
                if t.kind not in ("word", "target"):
                    continue
                for text in _expand(str(t.value), t.literal, env, t.raw.startswith("~")):
                    for v in (text, text.partition("=")[2] if text.startswith("-") else ""):
                        for full in ([v] if os.path.isabs(v) else [os.path.join(f, v) for f in folders]) if v else ():
                            named = named or (walk and os.path.exists(full))
                            d = _holdout_reach(full, dirs, walk)
                            if d is not None:
                                return _holdout_hit(d, "command", " ".join(t.raw for t in w))
            for f in folders:  # where it runs: in such a folder, or one above it that it walks given no path
                d = _holdout_reach(f, dirs, walk and not named)
                if d is not None:
                    return _holdout_hit(d, "command", " ".join(t.raw for t in w))
        return None

    def rule() -> Hit | None:
        d = _holdout_named(cmd, dirs)
        if d is not None:
            return _holdout_hit(d, "command", cmd)
        for line in chk.lines():
            if isinstance(line, str):  # a text that names the folder was refused before the walk
                hit = None
                if re.search(HOLDOUT_CALL, line) and not re.search(HOLDOUT_HELP, line):
                    hit = _holdout_hit(("", "", project.name), "whole", line)
            else:
                hit = line_hit(*line[1:])
            if hit is not None:
                return hit
        return None

    def text_only() -> Hit | None:
        d = _holdout_named(cmd, dirs)
        return _holdout_hit(d, "whole", cmd) if d is not None else None

    return _either(rule, text_only)


def _holdout_tool(
    project: Project, tool: str | None, tool_input: Any, ctx: Ctx | None, projects: Sequence[Project] | None = None
) -> Hit | None:
    """The HOLDOUT Hit of a Grep or Glob call that walks a folder holding hidden checks (Grep: its `path`, else the
    call's cwd; Glob: its pattern from there), for a caller holdout_reader refuses; a place in or under such a folder is
    check()'s (`cat PLACE`). Never raises."""
    try:
        if tool not in WALK_TOOLS or not isinstance(tool_input, dict):
            return None
        dirs = holdout_dirs(project, projects)
        if not dirs or holdout_reader(project, ctx):
            return None
        cwd = (ctx or {}).get("cwd") or os.getcwd()
        base = os.path.join(cwd, os.path.expanduser(str(tool_input.get("path") or cwd)))
        pattern = tool_input.get("pattern")
        places = [base]  # Grep walks its folder; Glob, what its pattern reaches from it
        if tool == "Glob" and isinstance(pattern, str) and pattern:
            places = [os.path.join(base, os.path.expanduser(pattern))]
        for place in places:
            d = _holdout_reach(place, dirs, True)
            if d is not None:
                hit = _holdout_hit(d, "tool", f"{tool} {place}")
                hit.tool, hit.call = str(tool), place
                return hit
        return None
    except Exception:  # noqa: BLE001 (a failure of foreman never stops a call of another tool)
        return None


def tool_path(tool: str | None, tool_input: dict[str, Any] | None) -> str | None:
    """The file a call of a FILE_TOOLS tool writes (file_path, or notebook_path), "" when it names none; None for
    any other tool."""
    if tool not in FILE_TOOLS:
        return None
    ti = tool_input or {}
    return str(ti.get("file_path") or ti.get("notebook_path") or "")


# ---------- any other tool: the places and commands its call names (module doc) ----------

# The last word of a key (split at `_` and camelCase) that makes its strings places: what the call reads or reaches.
PLACE_WORDS = frozenset(
    {"path", "paths", "file", "files", "dir", "directory", "folder", "cwd", "root", "url", "urls", "uri", "glob"}
)
COMMAND_KEYS = frozenset({"command", "cmd"})  # a key whose string a tool runs as a command
TOOL_DEPTH = 4  # how deep tool_texts walks the call's input
TOOL_STRINGS = 64  # the strings it looks at, at most (an Agent prompt or a browser batch stays cheap)
KEY_WORD = re.compile(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])")
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")  # a key that names a field (a map's own keys, like paths, do not)


def _key_kind(key: str) -> str:
    """place, command or "" (free text) for a field name of a tool's input."""
    if key.lower() in COMMAND_KEYS:
        return "command"
    found = KEY_WORD.findall(key)
    return "place" if found and found[-1].lower() in PLACE_WORDS else ""


def tool_texts(tool: str | None, tool_input: Any) -> tuple[list[str], list[str]]:
    """(places, commands) a call of a tool names (module doc): a string under a field whose name ends in one of
    PLACE_WORDS, or inside one (a list or a map under it), is a place; a string under `command` or `cmd` is a command
    (not for Bash, whose command check() reads). Every other string is free text and is not compared. Walks at most
    TOOL_DEPTH levels and TOOL_STRINGS strings."""
    places: list[str] = []
    commands: list[str] = []
    seen = [0]

    def walk(value: Any, kind: str, inside: bool, depth: int) -> None:
        if seen[0] >= TOOL_STRINGS or depth > TOOL_DEPTH:
            return
        if isinstance(value, str):
            seen[0] += 1
            if kind == "command" and tool != "Bash":
                commands.append(value)
            elif (kind == "place" or inside) and value.strip():
                places.append(value)
            return
        if isinstance(value, dict):
            items: Iterable[tuple[Any, Any]] = value.items()
        elif isinstance(value, list):
            items = ((None, v) for v in value)
        else:
            return
        for key, v in items:
            k = kind
            if isinstance(key, str) and IDENT.match(key):
                k = _key_kind(key)
            walk(v, k, inside or kind == "place", depth + 1)

    walk(tool_input if isinstance(tool_input, (dict, list)) else {}, "", False, 0)
    return places, commands


def _home_form(path: str) -> str | None:
    """`~/…` for an absolute path under $HOME (a rule is often written with `~`), else None."""
    home = os.path.expanduser("~").rstrip("/")
    if not home or home == "~" or not path.startswith(home + "/"):
        return None
    rest = path[len(home) + 1 :]
    return "~/" + (shlex.quote(rest) if rest else "")


def check_tool(
    project: Project,
    tool: str | None,
    tool_input: Any,
    ctx: Ctx | None = None,
    projects: Sequence[Project] | None = None,
) -> Verdict | None:
    """The verdict of a call of any tool but Bash by what it names (tool_texts): each command as check() would give
    it in Bash, each place as check() gives `cat PLACE` (and `cat ~/…` for a path under $HOME), the first refusal
    winning; a place's Hit says where=tool, part `<tool> <place>`, tool and call. A place never gets a lock verdict
    (it runs nothing). A failure of check() on a place compares its raw text with the deny lists (_whole), so a
    failure of foreman never refuses a call that names nothing they match. Never raises."""
    try:
        places, commands = tool_texts(tool, tool_input)
        before = (ctx or {}).get("passed")
        if projects is None and (places or tool in WALK_TOOLS):
            projects = _registry()  # once for every place
        hit = _holdout_tool(project, tool, tool_input, ctx, projects)
        if hit is not None:
            return "orchestrator", hit
        for cmd in commands:
            v = check(project, cmd, ctx, projects)
            if v is not None:
                if isinstance(v[1], Hit):
                    v[1].tool, v[1].call = str(tool), cmd
                return v
            _mark_passed(ctx, before, tool, cmd)
        for place in places:
            texts = ["cat " + shlex.quote(place)]
            home = _home_form(place)
            if home:
                texts.append("cat " + home)
            for text in texts:
                v = check(project, text, ctx, projects)
                if v is None or v[0] == "lock":
                    _mark_passed(ctx, before, tool, place, where="tool")
                    continue
                if not isinstance(v[1], Hit):  # check() itself failed
                    v = _raw_place(project, place)
                    if v is None:
                        continue
                hit = v[1]
                hit.where, hit.part, hit.tool, hit.call = "tool", _excerpt(hit, f"{tool} {place}"), str(tool), place
                return v
        return None
    except Exception:  # noqa: BLE001 (a failure of foreman never stops a call of another tool)
        return None


def _mark_passed(ctx: Ctx | None, before: Any, tool: str | None, call: str, where: str = "") -> None:
    """Names the tool and the place or command in a group's hit that check() let through for this call."""
    hit = (ctx or {}).get("passed")
    if hit is None or hit is before or hit.tool:
        return
    hit.tool, hit.call = str(tool), call
    if where:
        hit.where, hit.part = where, _excerpt(hit, f"{tool} {call}")


def _raw_place(project: Project, place: str) -> Verdict | None:
    """A place's raw text against the deny lists, when check() failed on it: ("deny", Hit where=whole) or None."""
    try:
        from . import grants

        pats = [*BUILTIN, *project.section("deny").get("patterns", []), *grants.patterns(project, internal=False)]
        hit = _scan(pats, _whole(place))
        return ("deny", hit) if hit else None
    except Exception:  # noqa: BLE001
        return None


def check_call(
    project: Project,
    tool: str | None,
    tool_input: Any,
    ctx: Ctx | None = None,
    projects: Sequence[Project] | None = None,
) -> Verdict | None:
    """The guard's verdict on a call of any tool (the hook and a table's `tool:` row): Bash by its command
    (check()); a FILE_TOOLS tool by the file it writes (check_file), then as any other tool, so writing a file gets
    the verdict of reading it; any other tool by what it names (check_tool)."""
    ti = tool_input if isinstance(tool_input, dict) else {}
    if tool in (None, "Bash"):
        cmd = ti.get("command")
        return check(project, cmd, ctx) if isinstance(cmd, str) and cmd else None
    path = tool_path(tool, ti)
    if path is not None:
        projects = _registry() if projects is None else projects  # once for the file and its places
        v = check_file(project, path, ctx, projects)
        if v is not None:
            return v
    return check_tool(project, tool, tool_input, ctx, projects)


def _registry() -> Sequence[Project] | None:
    """The registry's projects, or None when it does not read (each rule then reads it and fails its own way)."""
    from . import config

    try:
        return config.registered_projects()
    except Exception:  # noqa: BLE001
        return None


def row_path(project: Project, row: str, cwd: str) -> str:
    """The file of a table row `file:PATH`: {state}, {state_root} and {registry} replaced, `~` expanded, joined to
    cwd (the row's @DIR or the home) when relative."""
    return os.path.join(cwd, os.path.expanduser(row_text(project, row[len("file:") :].strip())))


def row_text(project: Project, text: str) -> str:
    """A table row's text with {state}, {state_root} and {registry} replaced (a file row's path, a Bash row's
    command)."""
    from . import config

    for key, value in (
        ("{state}", project.state),
        ("{state_root}", config.state_root()),
        ("{registry}", config.registry_path()),
    ):
        text = text.replace(key, str(value))
    return text


def _how(where: str) -> str:
    """The i18n key of how to avoid a refusal that matched in where."""
    if where in ("resolved", "unknown"):
        return "guard_how_resolved"
    if where == "tool":
        return "guard_how_tool"
    return "guard_how_code" if where in CODE_WHERE else "guard_how_text"


def message(project: Project, verdict: Verdict) -> str:
    from . import i18n

    s = i18n.for_project(project)
    kind, detail = verdict
    if kind == "file":
        how = {"config": "perms", "table": "perms", "state": "state", "git": "git"}.get(detail.kind, "owner")
        if how == "perms" and not detail.why:
            how = "perms_orchestrator"
        elif how == "owner" and detail.why != "agent":
            how = "owner_ask"  # a main thread: the owner's yes for one change (hooks_mod, grants.give_once)
        fields = {"project": detail.name or project.name, "owner": project.owner}
        machine = "_machine" if detail.kind == "git" and not detail.name else ""  # the machine's git config
        what = s[f"guard_file_what_{detail.kind}{machine}"].format(**fields)
        agent = "_agent" if detail.kind in AGENT_KINDS else ""  # refused to an agent only
        head = (
            s[f"guard_file_bash{agent}"].format(path=detail.path, what=what, part=detail.part)
            if detail.bash
            else s[f"guard_file{agent}"].format(path=detail.path, what=what)
        )
        return " ".join([head, s[f"guard_file_how_{how}"].format(**fields)])
    if kind == "orchestrator":
        from . import orchestrator

        o = orchestrator.current(project) or {}
        if isinstance(detail, Hit) and detail == MAIN_CHECKOUT:
            return " ".join(
                [
                    s["guard_main_checkout"].format(sub=detail.kind, dir=detail.dir, project=detail.name),
                    s["guard_where_command"].format(part=detail.part),
                ]
            )
        if isinstance(detail, Hit) and detail == HOLDOUT:
            return s["guard_holdout"].format(project=detail.name or project.name, part=detail.part)
        if isinstance(detail, Hit) and detail == INTEGRATE:
            return " ".join(
                [
                    s["guard_integrate_agent"].format(project=project.name, orchestrator=o.get("name") or "?"),
                    s[f"guard_where_{detail.where}"].format(part=detail.part, dir=detail.dir),
                ]
            )
        if isinstance(detail, Hit) and detail == PERMISSIONS_APPLY:
            return " ".join(
                [
                    s["guard_permissions_apply"].format(
                        owner=project.owner, project=project.name, orchestrator=o.get("name") or "?"
                    ),
                    s[f"guard_where_{detail.where}"].format(part=detail.part, dir=detail.dir),
                ]
            )
        out = [
            s[f"guard_orchestrator_{detail.why}"].format(
                pattern=detail,
                owner=project.owner,
                project=project.name,
                orchestrator=o.get("name") or "?",
                group=detail.group,
            ),
            s[f"guard_where_{detail.where}"].format(part=detail.part, dir=detail.dir),
        ]
        if detail.why == "agent":
            out.append(s[_how(detail.where)])
        return " ".join(out)
    if kind == "deny" and isinstance(detail, Hit) and detail in BUILTIN:
        return " ".join([s[BUILTIN[detail]], s[f"guard_where_{detail.where}"].format(part=detail.part)])
    if kind == "deny":
        out = [s["guard_deny"].format(pattern=detail, owner=project.owner, project=project.name)]
        where = getattr(detail, "where", None)  # a Hit; a plain string when the guard itself failed
        if where:
            out.append(s[f"guard_where_{where}"].format(part=detail.part, dir=detail.dir))
            out.append(s[_how(where)])
        extra = project.section("deny").get("message")
        return " ".join(out + ([extra] if extra else []))
    name, what = detail
    return s["guard_lock"].format(resource=name, what=f"`{what}`")


def parse_table(text: str, base: PathLike | None = None) -> list[tuple[int, str, str, str | None]]:
    """A guard table: one `EXPECT<TAB>COMMAND` per line, EXPECT = allow | deny | orchestrator | lock:RESOURCE, or
    `EXPECT@DIR` for a row checked as if it ran in DIR (`~` expanded; a relative DIR is joined to `base`, the table's
    folder); `#` lines and blank lines are skipped; `\\n` in COMMAND is a newline (heredocs), but in a tool row.
    COMMAND `file:PATH` is a write of that file with the Write or Edit tool, and `tool:NAME JSON` a call of tool NAME
    with that input (a JSON object, with its own escapes; row_tool); both table_verdicts. Returns [(line number,
    expect, command, DIR or None)]."""
    rows: list[tuple[int, str, str, str | None]] = []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        head, sep, cmd = line.partition("\t")
        expect, at, folder = head.strip().partition("@")
        if not sep or not cmd.strip() or not (expect in EXPECTS or expect.startswith("lock:")) or (at and not folder):
            raise ValueError(
                f"line {n}: expected 'allow|deny|orchestrator|lock:RESOURCE[@DIR]<TAB>command', got {line[:60]!r}"
            )
        where = None
        if at:
            where = os.path.expanduser(folder)
            if base is not None and not os.path.isabs(where):
                where = os.path.join(os.fspath(base), where)
            where = os.path.normpath(where)
        if cmd.startswith("tool:"):
            try:
                row_tool(cmd)
            except ValueError as e:
                raise ValueError(f"line {n}: {e}") from None
        else:
            cmd = cmd.replace("\\n", "\n")  # a tool row's JSON keeps its own escapes
        rows.append((n, expect, cmd, where))
    return rows


def row_tool(row: str) -> tuple[str, dict[str, Any]]:
    """(tool name, input) of a table row `tool:NAME JSON`; ValueError when it is not one."""
    name, _, text = row[len("tool:") :].strip().partition(" ")
    try:
        ti = json.loads(text) if text.strip() else {}
    except ValueError:
        raise ValueError(f"expected 'tool:NAME {{JSON object}}', got {row[:60]!r}") from None
    if not name or not isinstance(ti, dict):
        raise ValueError(f"expected 'tool:NAME {{JSON object}}', got {row[:60]!r}")
    return name, ti


def _got(v: Verdict | None) -> str:
    return "allow" if v is None else ("deny" if v[0] in ("deny", "orchestrator", "file") else f"lock:{v[1][0]}")


# table_verdicts(): (line, expect, command, DIR or None, the verdict as an agent, as the orchestrator)
Row = tuple[int, str, str, "str | None", str, str]


def table_verdicts(project: Project, path: Path, base: PathLike | None = None) -> list[Row]:
    """Every row of a guard table with what the project says of it: (line, expect, command, DIR or None, the verdict
    as an agent, the verdict as the marked orchestrator's main thread with every group granted), each verdict allow,
    deny or lock:R. The orchestrator's is computed only when the project has [deny.grants] groups or the row may run
    `foreman permissions apply` or `foreman integrate … --opens` (names_internal; else it is the agent's: nothing else
    tells them apart). Each row runs in its @DIR (a relative one joined to `base`, else to the table's folder: a copy
    of a table read from git keeps the live one's), else in the project's home (so a `git push` is compared as git
    would do it from there, gitpush.py). A row `file:PATH` is a write of a Write/Edit call to that file (row_path,
    check_file), and a row `tool:NAME JSON` a call of that tool (row_tool, check_call; its JSON text with the same
    placeholders), both in both roles. Raises OSError or ValueError (parse_table)."""
    from . import config, grants

    rows = parse_table(path.read_text(), base if base is not None else path.resolve().parent)
    groups = bool(grants.patterns(project, internal=False))
    projects: list[Project] | None = []
    if any(r[2].startswith(("file:", "tool:")) for r in rows):
        try:
            projects = config.registered_projects()  # once for every file row
        except config.ConfigError:
            projects = None  # each row tries again, and a failure denies what looks reserved
    out: list[Row] = []
    for n, expect, cmd, folder in rows:
        cwd = folder or str(project.home)
        if cmd.startswith("file:"):  # a reserved file (check_file): always both roles
            f = row_path(project, cmd, cwd)
            agent = _got(check_file(project, f, as_role("agent", cwd), projects))
            orch = _got(check_file(project, f, as_role("orchestrator", cwd), projects))
            out.append((n, expect, cmd, folder, agent, orch))
            continue
        if cmd.startswith("tool:"):  # a call of any tool (check_call): always both roles
            name, ti = row_tool(row_text(project, cmd))
            agent = _got(check_call(project, name, ti, as_role("agent", cwd), projects))
            orch = _got(check_call(project, name, ti, as_role("orchestrator", cwd), projects))
            out.append((n, expect, cmd, folder, agent, orch))
            continue
        text = row_text(project, cmd)
        v = check(project, text, as_role("agent", cwd))
        agent = _got(v)
        both = (
            groups
            or names_internal(cmd)
            or (v is not None and v[0] == "file")  # the board is the orchestrator's
            or (v is not None and v[0] == "orchestrator" and v[1] in AGENT_ONLY)  # an agent's only
        )
        orch = _got(check(project, text, as_role("orchestrator", cwd))) if both else agent
        out.append((n, expect, cmd, folder, agent, orch))
    return out


def run_table(project: Project, path: Path) -> tuple[dict[str, int], list[str]]:
    """Checks every row of a guard table against the project (table_verdicts): allow, deny and lock:R must come out
    the same for an agent and the orchestrator, orchestrator is deny for an agent and allow for the orchestrator.
    Returns (counts by expect kind, failures), each failure a line `path:N: want X, got Y: command`."""
    counts: dict[str, int] = {}
    failures: list[str] = []
    for row in table_verdicts(project, path):
        kind = "lock" if row[1].startswith("lock:") else row[1]
        counts[kind] = counts.get(kind, 0) + 1
        bad = row_failure(path.name, row)
        if bad:
            failures.append(bad)
    return counts, failures


def row_failure(name: str, row: Row) -> str | None:
    """The failure line of a row of table_verdicts() (`name:N: want X, got Y: command`), or None when it gets its
    expected verdict."""
    n, expect, cmd, folder, agent, orch = row
    want = ("deny", "allow") if expect == "orchestrator" else (expect, expect)
    if (agent, orch) == want:
        return None
    got = agent if agent == orch else f"{agent} as an agent and {orch} as the orchestrator"
    head = f"{expect}@{folder}" if folder else expect
    return f"{name}:{n}: want {head}, got {got}: {cmd[:90]!r}"
