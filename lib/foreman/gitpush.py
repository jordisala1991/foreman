"""What a `git push` would do (guard.py's resolved texts): the folder it runs in, that repo's remote and the refs.

A [deny] pattern written for a publish (`git push .*some-org`, `git push … main`) matches the text of a command, and
the text of `git push` alone names neither the remote nor the branch: those come from the folder it runs in. So the
guard compares one more text per `git push` a line runs, behind every text of today, built from what git would do:

    <the segment's assignments and wrappers> git push <push URL> <src>:<dst> [--force] [--delete] [--mirror]

one per destination ref (branches short, `main:main`; `refs/tags/*:refs/tags/* --tags` for --tags, --follow-tags
and push.followTags; a deletion as `:dst --delete`), and a second one with the `https://host/path` form of an ssh or
scp URL, so a pattern written for one form matches both.

The folder. A line starts in the hook event's cwd (Ctx.cwd); a `cd`/`pushd` before the segment moves it, and each
`git -C DIR` applies after. Where a `cd` may or may not have run (inside a group, a branch, after `||`, a pipe) the
segment may run in either folder and both are resolved: the guard blocks when any of them matches. A folder the line
cannot name (`cd "$X"`, `cd -`, `popd`, a folder that does not exist, an event without cwd) or one where git does
not answer in time is UNKNOWN: the push is resolved against every repo of the project (its home and each
`[repos.*] path`) with that repo's main branch as the current one, so an unknown folder only blocks more.

What git says. For each folder, once per check (the Resolver's cache): `rev-parse --absolute-git-dir`,
`symbolic-ref -q HEAD` and `config --list -z`, with no pager, no prompt, no optional locks, no fsmonitor, and
TIMEOUT seconds each. None of them runs a hook or a filter. A folder that is not a repo has no remote: the text keeps
the remote as written (git would fail anyway).

The remote: the first argument (or --repo), else branch.<b>.pushRemote, remote.pushDefault, branch.<b>.remote and
origin; its pushurls (insteadOf applied) or else its urls (pushInsteadOf, else insteadOf); a name that is no
configured remote is a URL or a path, rewritten the same way. The refs: the explicit refspecs (`+`, HEAD and @, `src`
alone, `:dst`, `tag NAME`), else remote.<name>.push, else push.default (simple and current: the current branch to the
same name; upstream: to branch.<b>.merge; matching: the main branch and the current one; nothing: none). --all,
--branches, --mirror and a glob refspec add the main branch; a detached HEAD, or a word the line computes
(`$BRANCH`), counts as the main branch too. The current branch is the one git says, unless a `git switch` or `git
checkout -b` earlier on the line, in the same folder, surely ran before the push (only && between them, outside any
group or compound): then it is that one; one that may not have run adds its branch (branches()).

Config changed on the same line (Changes). What git reads is what the hook reads, plus what the line changes
before the push runs, which the hook cannot read yet: the push's own `git -c KEY=VALUE` and the line's earlier `git
config KEY VALUE`, `git remote add|set-url NAME URL` (a value is added to what git says: both are tried), `git config
--unset`, `git remote remove` (the key is dropped). `--git-dir DIR` and a `GIT_DIR=DIR` in front of the push read the
repo of DIR instead of the folder's. What cannot be read that way makes the push UNKNOWN too, besides its folder: a
value the line computes, `--config-env`, `GIT_CONFIG*`, `GIT_COMMON_DIR`, `HOME` or `XDG_CONFIG_HOME` in front of the
push or exported earlier on the line, an `include.*` key, `git config --file|--edit|--rename-section`, `git remote
rename|set-url --delete`, an upstream set on the line (`git branch -u`, `--track`). `--work-tree` and
`GIT_WORK_TREE` change neither the repo nor its config: they are ignored.

Git aliases. A subcommand that is no git command is looked up in alias.<name> (with the line's changes): an alias
to `push` (through other aliases too) is resolved as that push; one that starts with `!` is a shell command, compared
as a command of its own (guard.py, from the same folder). A subcommand the line computes (`git $CMD`) counts as a
push. The strings of `ssh HOST` run on another machine and are not resolved.
"""

from __future__ import annotations

import copy
import os
import re
import shlex
import subprocess
from pathlib import PurePath
from typing import TYPE_CHECKING, NamedTuple

from .shell import ASSIGN, Tok

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .config import Project
    from .shell import Runs, Seg

UNKNOWN = ""  # a folder the line cannot name: every repo of the project
CURRENT = ""  # in branches(): the branch git says is checked out
DETACHED = "\0"  # in branches(): no branch, or one the line computes (counts as the main branch)


class Failed(Exception):
    """The resolver itself failed: guard.deny lets it through to check(), whose answer is then a deny."""


TIMEOUT = 2.0  # seconds for each git call
OPENERS = ("if", "while", "until", "for", "select", "case", "{")
CLOSERS = ("fi", "done", "esac", "}")
KEYWORDS = (*OPENERS, *CLOSERS, "then", "elif", "else", "do", "function", "!")
LIST_SEPS = (";", "\n", "&", "")
GIT_VALUE_OPTS = ("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env",
                  "--super-prefix", "--attr-source")  # fmt: skip
PUSH_VALUE_OPTS = ("--repo", "--push-option", "--receive-pack", "--exec", "--recurse-submodules", "--signed")
# git's own commands: an alias of the same name is never used, so none of them is looked up (and no git is run)
BUILTINS = frozenset("""
    add am annotate apply archive backfill bisect blame branch bugreport bundle cat-file check-attr check-ignore
    check-mailmap check-ref-format checkout checkout-index cherry cherry-pick citool clean clone column commit
    commit-graph commit-tree config count-objects credential describe diagnose diff diff-files diff-index diff-tree
    difftool fast-export fast-import fetch fetch-pack filter-branch fmt-merge-msg for-each-ref for-each-repo
    format-patch fsck gc get-tar-commit-id grep gui hash-object help hook index-pack init instaweb
    interpret-trailers lfs log ls-files ls-remote ls-tree mailinfo mailsplit maintenance merge merge-base merge-file
    merge-tree mergetool mktag mktree multi-pack-index mv name-rev notes pack-objects pack-refs prune pull range-diff
    read-tree rebase reflog refs remote repack replace replay rerere reset restore rev-list rev-parse revert rm
    send-email shortlog show show-branch show-ref sparse-checkout stash status stripspace submodule switch
    symbolic-ref tag update-index update-ref var verify-commit verify-pack verify-tag version whatchanged worktree
    write-tree
    """.split())  # fmt: skip
EXPORTERS = ("export", "declare", "typeset", "readonly", "local")
UNKNOWN_ENV = ("GIT_COMMON_DIR", "HOME", "XDG_CONFIG_HOME")  # and every GIT_CONFIG*: config git reads elsewhere


class Changes(NamedTuple):
    """What the line changed in git's config before a push: config, [(key, value)] added to what git says (value
    None: the key is dropped; a key ending in ".": its whole section), and unknown, whether a change cannot be
    read (the push is then also resolved as from an unknown folder)."""

    config: tuple[tuple[str, str | None], ...] = ()
    unknown: bool = False

    def plus(self, other: Changes) -> Changes:
        return Changes(self.config + other.config, self.unknown or other.unknown)


NONE = Changes()
LOST = Changes((), True)


class Repo:
    """What git says of one folder: git_dir (None: not a repo), branch (None: detached or unborn) and config
    ({key: [values]}, keys as `git config --list` writes them: section and name lowercase)."""

    __slots__ = ("git_dir", "branch", "config")

    def __init__(self, git_dir: str | None, branch: str | None, cfg: dict[str, list[str]]) -> None:
        self.git_dir, self.branch, self.config = git_dir, branch, cfg

    def get(self, key: str) -> str | None:
        v = self.config.get(key)
        return v[-1] if v else None


def _git(folder: str, *args: str, git_dir: str | None = None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", GIT_PAGER="cat", PAGER="cat", LC_ALL="C")
    env.pop("LANGUAGE", None)  # git's messages in English: "not a git repository" is read
    return subprocess.run(
        [
            "git",
            "-C",
            folder,
            *([f"--git-dir={git_dir}"] if git_dir else []),
            "-c",
            "core.fsmonitor=false",
            "--no-pager",
            *args,
        ],  # fmt: skip
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        env=env,
        stdin=subprocess.DEVNULL,
        check=False,
    )


def read_repo(folder: str, git_dir: str | None = None) -> Repo | None:
    """What git says of the folder (of the repo at git_dir, run from the folder, when given), or None when it cannot
    be known (no such folder, git missing, slow or failing for another reason than "not a repo")."""
    if not os.path.isdir(folder):
        return None
    try:
        r = _git(folder, "rev-parse", "--absolute-git-dir", git_dir=git_dir)
        if r.returncode:
            return Repo(None, None, {}) if "not a git repository" in r.stderr else None
        head = _git(folder, "symbolic-ref", "-q", "HEAD", git_dir=git_dir)
        conf = _git(folder, "config", "--list", "-z", git_dir=git_dir)
    except (OSError, subprocess.SubprocessError):
        return None
    if conf.returncode:
        return None
    cfg: dict[str, list[str]] = {}
    for entry in conf.stdout.split("\0"):
        if entry:
            key, _, value = entry.partition("\n")
            cfg.setdefault(key, []).append(value if "\n" in entry else "true")
    ref = head.stdout.strip() if head.returncode == 0 else ""
    return Repo(r.stdout.strip(), ref[len("refs/heads/") :] if ref.startswith("refs/heads/") else None, cfg)


def _expand(path: str) -> str:
    return os.path.normpath(os.path.expanduser(path))


def _join(base: str, target: str, exists: bool = True) -> str:
    """The folder `cd target` reaches from base (UNKNOWN when it cannot be known or, with exists, does not exist)."""
    if target.startswith(("~", "/")):
        out = _expand(target)
    elif base == UNKNOWN:
        return UNKNOWN
    else:
        out = os.path.normpath(os.path.join(base, target))
    return out if not exists or os.path.isdir(out) else UNKNOWN


def _cd_target(w: Sequence[Tok]) -> str | None:
    """The literal target of `cd|pushd [-L|-P|-e|-@] [DIR]` (w starts at the program), "~" for none, None when it
    is not literal or is `-` / a stack entry (`+N`)."""
    k = 1
    while k < len(w) and w[k].literal and w[k].value[:1] == "-" and w[k].value not in ("-",):
        if w[k].value == "--":
            k += 1
            break
        k += 1
    if k >= len(w):
        return "~"
    t = w[k]
    if not t.literal or t.value == "-" or t.value[:1] == "+":
        return None
    return t.value


def _moves(w: Sequence[Tok], r: Runs) -> str | None:
    """ "cd" when the segment changes the shell's folder (cd, pushd, popd), else None."""
    p = r.prog
    if p >= len(w) or not w[p].literal:
        return None
    return "cd" if w[p].value in ("cd", "pushd", "popd") else None


def folders(segs: Sequence[Seg], words_of: Sequence[Sequence[Tok]], info: Sequence[Runs], start: frozenset[str],
            exists: bool = True, unknown_chain: bool = False) -> list[frozenset[str]]:  # fmt: skip
    """The folders each segment may run in, from start (the module doc). A `cd` replaces the set only when it
    surely ran before the segment: outside any group or compound, and either first in its list (after ; newline &
    or the start) or followed by nothing but && up to the segment; elsewhere its folder is added to the set.
    exists=False: a folder that does not exist (yet) is still named (the guard's reserved files: `mkdir d && cd d`;
    a cd that fails stops its && list). unknown_chain: a cd to a folder the line cannot name, followed by nothing but
    &&, leaves only that unknown folder for the rest of the list (if the cd fails, nothing after it runs: the guard's
    main checkout rule, `cd "$X" && git merge main`), and a cd followed by || leaves the folder before it too (what
    follows runs when the cd failed); a push keeps the folder before the first and reads the second as git does."""
    out: list[frozenset[str]] = []
    sure = set(start)  # where the next list starts
    chain: set[str] | None = None  # where the rest of an all-&& list runs, after a cd inside it
    pure = True  # the current list is all && so far
    first = True  # the next segment starts a list
    parens = kws = 0
    for k, seg in enumerate(segs):
        w = words_of[k]
        lead = w[0].value if w and w[0].literal else ""
        here = chain if (chain is not None and pure) else sure
        out.append(frozenset(here))
        if lead in OPENERS:
            kws += 1
        if lead in CLOSERS:
            kws = max(0, kws - 1)
        if _moves(w, info[k]):
            p = info[k].prog
            target = _cd_target(w[p:]) if w[p].value != "popd" else None
            new = {UNKNOWN} if target is None else {_join(d, target, exists) for d in here}
            plain = parens == 0 and kws == 0 and p == 0 and seg.sep not in ("|", "|&", "&")
            plain = plain and not (unknown_chain and seg.sep == "||")  # `cd X || git …`: it runs where the cd failed
            if plain and first and UNKNOWN not in new:
                sure, chain = set(new), None
            elif plain and pure and seg.sep == "&&" and (unknown_chain or UNKNOWN not in new):
                chain = new
                sure |= new
            else:
                sure |= new
                if chain is not None:
                    chain |= new
        if seg.sep == "(":
            parens += 1
        elif seg.sep == ")":
            parens = max(0, parens - 1)
        if seg.sep in LIST_SEPS:
            first, pure, chain = True, True, None
        elif seg.sep == "(" and not w:
            pass  # an opening parenthesis: the list goes on
        else:
            first = False
            if seg.sep != "&&":
                pure = False
    return out


def switched(w: Sequence[Tok], prog: int) -> tuple[list[str | None], str, bool] | None:
    """(its `git -C` folders, the branch it leaves checked out (DETACHED: none, or a name the line computes),
    whether that is certain) when w[prog:] is `git switch|checkout …` that may change the branch, else None.
    `git checkout NAME` may restore a file NAME instead: not certain; `git checkout … -- …` changes no branch."""
    got = git_command(w, prog, ("switch", "checkout"))
    if got is None:
        return None
    dirs, k = got
    switch = w[k - 1].value == "switch"
    rest = w[k:]
    if any(t.literal and t.value == "--" for t in rest):
        return None
    pos: list[Tok] = []
    j = 0
    while j < len(rest):
        t = rest[j]
        v = t.value if t.literal else None
        j += 1
        if v in ("-c", "-C", "--create", "--force-create", "-b", "-B", "--orphan"):
            nxt = rest[j] if j < len(rest) else None
            return dirs, (nxt.value if nxt is not None and nxt.literal else DETACHED), True
        if v is not None and v.startswith(("--create=", "--force-create=", "--orphan=")):
            return dirs, v.partition("=")[2], True
        if v in ("-d", "--detach"):
            return dirs, DETACHED, True
        if v is not None and v.startswith("-"):
            continue
        pos.append(t)
    if not pos:
        return None
    named: str = pos[0].value if pos[0].literal else DETACHED
    if switch:
        return dirs, named, True
    return (dirs, named, False) if len(pos) == 1 else None


def branches(
    segs: Sequence[Seg], words_of: Sequence[Sequence[Tok]], info: Sequence[Runs], where: Sequence[frozenset[str]]
) -> list[dict[str, frozenset[str]]]:
    """For each segment, {folder: the branches that may be checked out there} after the line's `git switch` and
    `git checkout` before it (CURRENT: the one git says); a folder not in it has only CURRENT. A switch replaces
    the branch only when it surely ran in that one folder: outside any group or compound, followed by nothing but
    && up to the segment (a switch can fail); elsewhere its branch is added."""
    out: list[dict[str, frozenset[str]]] = []
    sure: dict[str, set[str]] = {}
    chain: dict[str, set[str]] | None = None
    pure, parens, kws = True, 0, 0
    for k, seg in enumerate(segs):
        w = words_of[k]
        lead = w[0].value if w and w[0].literal else ""
        state = chain if (chain is not None and pure) else sure
        out.append({d: frozenset(b) for d, b in state.items()})
        if lead in OPENERS:
            kws += 1
        if lead in CLOSERS:
            kws = max(0, kws - 1)
        got = switched(w, info[k].prog)
        if got is not None:
            dirs, name, certain = got
            here = set(where[k])
            for d in dirs:
                here = {UNKNOWN if d is None else _join(x, d) for x in here}
            plain = certain and parens == 0 and kws == 0 and seg.sep == "&&" and len(here) == 1
            new = dict(chain if (chain is not None and pure) else sure)
            new = {d: set(b) for d, b in new.items()}
            for d in here:
                if plain and pure:
                    new[d] = {name}
                else:
                    new[d] = new.get(d, {CURRENT}) | {name}
            for d in here:
                sure[d] = sure.get(d, {CURRENT}) | {name}
            chain = new if plain and pure else (None if chain is None else new)
        if seg.sep == "(":
            parens += 1
        elif seg.sep == ")":
            parens = max(0, parens - 1)
        if seg.sep in LIST_SEPS:
            pure, chain = True, None
        elif not (seg.sep == "(" and not w) and seg.sep != "&&":
            pure = False
    return out


# ---------- the push ----------


class Push:
    """The arguments of one `git push`: positionals (None for a word the line computes) and its options."""

    def __init__(self) -> None:
        self.args: list[str | None] = []
        self.repo: str | None = None
        self.force = self.delete = self.tags = self.all = self.mirror = False


class Call:
    """A `git [global options] SUB …`: the folders of its -C options in order (None: not literal), what its -c and
    --config-env change (Changes), its --git-dir ("" none, None not literal) and the index of SUB (len(w): none)."""

    __slots__ = ("dirs", "changes", "git_dir", "sub")

    def __init__(self) -> None:
        self.dirs: list[str | None] = []
        self.changes = NONE
        self.git_dir: str | None = ""
        self.sub = 0


def _key(key: str) -> str | None:
    """A config key as `git config --list` writes it (section and name lowercase, a subsection as is), None when it
    is no key."""
    head, dot, rest = key.partition(".")
    sub, dot2, name = rest.rpartition(".")
    if not dot or not head or not name:
        return None
    return f"{head.lower()}.{sub}{dot2}{name.lower()}"


def _setting(key: str, value: str) -> Changes:
    k = _key(key)
    if k is None:
        return NONE  # git refuses it
    if k.startswith(("include.", "includeif.")):
        return LOST  # a file of config: not read
    return Changes(((k, value),))


def parse_git(w: Sequence[Tok], prog: int) -> Call | None:
    """The Call when w[prog:] runs git, else None."""
    if prog >= len(w) or not w[prog].literal or PurePath(w[prog].value).name != "git":
        return None
    c = Call()
    k = prog + 1
    while k < len(w) and (w[k].value if w[k].literal else w[k].raw.lstrip("\"'")).startswith("-"):
        if not w[k].literal:  # an option the line computes (`--git-dir=$D`): whatever it changes is unknown
            name, eq, _ = w[k].raw.lstrip("\"'").partition("=")
            k += 2 if name in GIT_VALUE_OPTS and not eq else 1
            if name.startswith("-C"):
                c.dirs.append(None)
            else:
                c.changes = c.changes.plus(LOST)
                c.git_dir = None if name == "--git-dir" else c.git_dir
            continue
        v = w[k].value
        name, eq, val = v.partition("=")
        value: str | None = val
        if name in GIT_VALUE_OPTS and not eq:
            nxt = w[k + 1] if k + 1 < len(w) else None
            value = nxt.value if nxt is not None and nxt.literal else None
            k += 2
        elif name in GIT_VALUE_OPTS:
            k += 1
        elif v.startswith("-C") and len(v) > 2:
            name, value = "-C", v[2:]
            k += 1
        else:
            k += 1
            continue
        if name == "-C":
            c.dirs.append(value)
        elif name == "-c":
            key, has, setting = (value or "").partition("=")
            c.changes = c.changes.plus(LOST if value is None else _setting(key, setting if has else "true"))
        elif name == "--config-env":
            c.changes = c.changes.plus(LOST)
        elif name == "--git-dir":
            c.git_dir = value
    c.sub = k
    return c


def git_command(w: Sequence[Tok], prog: int, sub: Sequence[str]) -> tuple[list[str | None], int] | None:
    """(the folders of its `git -C` options, in order (None: not literal), the index of the word after the
    subcommand) when w[prog:] is `git [global options] SUB …` with SUB one of sub, else None."""
    c = parse_git(w, prog)
    if c is None or c.sub >= len(w) or not (w[c.sub].literal and w[c.sub].value in sub):
        return None
    return c.dirs, c.sub + 1


def env_changes(w: Sequence[Tok]) -> tuple[Changes, str | None]:
    """(what the assignments among w change, the GIT_DIR they set: "" none, None not literal)."""
    out, git_dir = NONE, ""
    for t in w:
        m = ASSIGN.match(t.raw)
        if not m:
            continue
        name = t.raw[: m.end()].partition("[")[0].rstrip("+=")
        value = t.value.partition("=")[2] if t.literal else None
        if name == "GIT_DIR":
            git_dir = value
        elif name.startswith("GIT_CONFIG") or name in UNKNOWN_ENV:
            out = LOST
    return out, git_dir


def _config_change(args: Sequence[Tok]) -> Changes:
    """What `git config ARGS` changes."""
    if any(not t.literal for t in args):
        return LOST
    pos: list[str] = []
    mode, other_file, sub = "set", False, ""
    k = 0
    while k < len(args):
        v = args[k].value
        k += 1
        name, eq, _ = v.partition("=")
        if not pos and not sub and v in ("set", "unset", "get", "list", "edit", "rename-section", "remove-section"):
            sub = v
        elif name in ("--file", "-f", "--blob"):
            other_file = True
            k += 0 if eq else 1
        elif name in ("--type", "--default", "--value", "--comment", "--url"):
            k += 0 if eq else 1
        elif v in ("--unset", "--unset-all"):
            mode = "unset"
        elif v == "--remove-section":
            mode = "remove-section"
        elif v in ("--rename-section", "-e", "--edit"):
            mode = "lost"
        elif v.startswith(("--get", "--list")) or v == "-l":
            mode = "get"
        elif not v.startswith("-") or v == "-":
            pos.append(v)
    mode = sub or mode
    if mode in ("get", "list"):
        return NONE
    if mode in ("edit", "rename-section", "lost"):
        return LOST
    if not pos or (mode == "set" and len(pos) < 2):
        return NONE  # a read (or git refuses it)
    if other_file:
        return LOST  # it may be the repo's own config
    if mode == "remove-section":
        head, _, rest = pos[0].partition(".")
        return Changes(((f"{head.lower()}.{rest + '.' if rest else ''}", None),))
    if mode == "unset":
        key = _key(pos[0])
        return Changes(((key, None),)) if key else NONE
    return _setting(pos[0], pos[1])


def _remote_change(args: Sequence[Tok]) -> Changes:
    """What `git remote ARGS` changes."""
    if any(not t.literal for t in args):
        return LOST
    words = [t.value for t in args]
    pos: list[str] = []
    flags: set[str] = set()
    k = 0
    while k < len(words):
        v = words[k]
        k += 1
        if v in ("-t", "-m"):
            k += 1
        elif v.startswith("-"):
            flags.add(v)
        else:
            pos.append(v)
    what = pos[0] if pos else ""
    if what == "add" and len(pos) >= 3:
        out = Changes(((f"remote.{pos[1]}.url", pos[2]),))
        if "--mirror" in flags or "--mirror=push" in flags:
            out = out.plus(Changes(((f"remote.{pos[1]}.mirror", "true"),)))
        return out
    if what == "set-url" and len(pos) >= 3:
        if "--delete" in flags:
            return LOST
        return Changes(((f"remote.{pos[1]}.{'pushurl' if '--push' in flags else 'url'}", pos[2]),))
    if what in ("remove", "rm") and len(pos) >= 2:
        return Changes(((f"remote.{pos[1]}.", None),))
    if what == "rename":
        return LOST
    return NONE


def changed(w: Sequence[Tok], prog: int) -> Changes:
    """What one segment changes in git's config for the segments after it on the line."""
    if prog >= len(w) or (w[prog].literal and w[prog].value in EXPORTERS):  # assignments, export
        env, git_dir = env_changes(w)
        return LOST if env.unknown or git_dir != "" else NONE
    c = parse_git(w, prog)
    if c is None or c.sub >= len(w):
        return NONE
    if not w[c.sub].literal:
        return LOST  # a subcommand the line computes
    sub, args = w[c.sub].value, w[c.sub + 1 :]
    if sub == "config":
        return _config_change(args)
    if sub == "remote":
        return _remote_change(args)
    if sub in ("branch", "switch", "checkout"):
        upstream = ("-u", "-t", "--track", "--set-upstream", "--unset-upstream")
        if any(
            t.literal and (t.value in upstream or t.value.startswith(("--set-upstream-to", "--track="))) for t in args
        ):
            return LOST
    return NONE


def changes(words_of: Sequence[Sequence[Tok]], info: Sequence[Runs], start: Changes = NONE) -> list[Changes]:
    """For each segment, what the line changed in git's config before it (start: before the line). A change
    counts wherever it is on the line (a group, a branch, after ||): it may have run."""
    out: list[Changes] = []
    cur = start
    for k, w in enumerate(words_of):
        out.append(cur)
        cur = cur.plus(changed(w, info[k].prog))
    return out


def overlay(cfg: dict[str, list[str]], extra: Sequence[tuple[str, str | None]]) -> dict[str, list[str]]:
    """cfg with the changes of a Changes applied."""
    if not extra:
        return cfg
    out = {k: list(v) for k, v in cfg.items()}
    for key, value in extra:
        if value is None:
            for k in [k for k in out if k == key or (key.endswith(".") and k.startswith(key))]:
                del out[k]
        else:
            out.setdefault(key, []).append(value)
    return out


def _tok(value: str) -> Tok:
    t = Tok("word", 0)
    t.value, t.raw = value, shlex.quote(value)
    return t


def expand(w: Sequence[Tok], cfg: Repo) -> list[Tok] | str | None:
    """w (`git …` from its program) as the push it runs, with git's aliases expanded (cfg: the config they come
    from); a shell command for an alias that starts with `!`; None when it runs no push."""
    words = list(w)
    seen: set[str] = set()
    while True:
        c = parse_git(words, 0)
        if c is None or c.sub >= len(words):
            return None
        t = words[c.sub]
        if not t.literal:  # a subcommand the line computes: it may be push
            return [*words[: c.sub], _tok("push"), *words[c.sub + 1 :]]
        name = t.value.lower()
        if t.value == "push":
            return words
        if t.value in BUILTINS or name in seen:
            return None
        seen.add(name)
        value = cfg.get(f"alias.{name}")
        if value is None:
            return None
        if value.startswith("!"):
            return " ".join([value[1:], *(x.raw for x in words[c.sub + 1 :])])
        try:
            parts = shlex.split(value)
        except ValueError:
            return None  # git cannot split it either
        words = [*words[: c.sub], *map(_tok, parts), *words[c.sub + 1 :]]


def push_args(w: Sequence[Tok], prog: int) -> tuple[list[str | None], Push] | None:
    """(the folders of its `git -C` options, its arguments) when w[prog:] is `git [global options] push …`."""
    got = git_command(w, prog, ("push",))
    if got is None:
        return None
    dirs, k = got
    p = Push()
    opts = True
    while k < len(w):
        t = w[k]
        v = t.value if t.literal else None
        k += 1
        if opts and v == "--":
            opts = False
            continue
        if opts and v is not None and v.startswith("--"):
            name, eq, val = v.partition("=")
            if name in PUSH_VALUE_OPTS and not eq and name not in ("--recurse-submodules", "--signed"):
                val = w[k].value if k < len(w) and w[k].literal else ""
                k += 1
            if name == "--repo":
                p.repo = val
            elif name in ("--force", "--force-with-lease"):
                p.force = True
            elif name == "--delete":
                p.delete = True
            elif name in ("--tags", "--follow-tags"):
                p.tags = True
            elif name in ("--all", "--branches"):
                p.all = True
            elif name == "--mirror":
                p.mirror = True
            continue
        if opts and v is not None and v.startswith("-") and len(v) > 1:
            for n, ch in enumerate(v[1:], 1):
                if ch == "f":
                    p.force = True
                elif ch == "d":
                    p.delete = True
                elif ch == "o":
                    if n == len(v) - 1:
                        k += 1
                    break
            continue
        p.args.append(v)
    return dirs, p


def https(url: str) -> str | None:
    """The `https://host/path` form of an ssh or scp-like URL (`git@host:o/r.git`, `ssh://git@host:22/o/r`), else
    None."""
    if "://" in url:
        m = re.match(r"^(?:ssh|git\+ssh|ssh\+git|git)://(?:[^@/]+@)?([^/:]+)(?::\d*)?/(.+)$", url)
        return f"https://{m.group(1)}/{m.group(2)}" if m else None
    head, colon, path = url.partition(":")
    if not colon or not path or "/" in head:
        return None  # a name or a local path (git reads host:path only with a colon before any slash)
    return f"https://{head.rpartition('@')[2]}/{path.lstrip('/')}"


def _rewrite(repo: Repo, url: str, push: bool) -> str:
    """url with the longest url.<base>.insteadOf (pushInsteadOf first, when push) prefix replaced."""
    for kind in ("pushinsteadof", "insteadof") if push else ("insteadof",):
        best = ("", "")
        for key, values in repo.config.items():
            if key.startswith("url.") and key.endswith("." + kind):
                base = key[4 : -len(kind) - 1]
                for prefix in values:
                    if url.startswith(prefix) and len(prefix) > len(best[0]):
                        best = (prefix, base)
        if best[0]:
            return best[1] + url[len(best[0]) :]
    return url


def urls(repo: Repo, remote: str) -> list[str]:
    """The URLs git pushes to for remote (a name or a URL/path), or [remote] when it has none."""
    pushurls = repo.config.get(f"remote.{remote}.pushurl")
    if pushurls:
        return [_rewrite(repo, u, False) for u in pushurls]
    return [_rewrite(repo, u, True) for u in repo.config.get(f"remote.{remote}.url") or [remote]]


def _short(ref: str) -> str:
    return ref[len("refs/heads/") :] if ref.startswith("refs/heads/") else ref


def refspecs(p: Push, specs: Sequence[str | None], repo: Repo, remote: str, main: str) -> list[tuple[str, str]]:
    """[(src:dst, its flags: " --force", " --delete")] the push sends (the module doc)."""
    cur = repo.branch or main

    def src(s: str) -> str:
        return cur if s in ("HEAD", "@") else _short(s)

    out: list[tuple[str, bool, bool]] = []
    words = list(specs)
    if not words:
        if p.all or p.mirror:
            words = [main, cur]
        elif repo.config.get(f"remote.{remote}.push"):
            words = list(repo.config[f"remote.{remote}.push"])
        else:
            mode = (repo.get("push.default") or "simple").lower()
            if mode in ("upstream", "tracking"):
                merge = repo.get(f"branch.{cur}.merge")
                words = [f"{cur}:{_short(merge)}" if merge else cur]
            elif mode == "matching":
                words = [main, cur]
            elif mode != "nothing":
                words = [cur]
    k = 0
    while k < len(words):
        s = words[k]
        k += 1
        if s == "tag" and k < len(words) and words[k] is not None:
            s, k = f"refs/tags/{words[k]}:refs/tags/{words[k]}", k + 1
        if s is None:  # a word the line computes: the main branch, and the current one
            out += [(f"{main}:{main}", True, False), (f"{cur}:{cur}", True, False)]
            continue
        forced = s.startswith("+")
        s = s[1:] if forced else s
        if s == ":":  # matching
            out += [(f"{main}:{main}", forced, False), (f"{cur}:{cur}", forced, False)]
            continue
        a, colon, b = s.partition(":")
        if p.delete and not colon:
            a, b, colon = "", a, ":"
        if not colon:
            b = src(a)
        out.append((f"{src(a) if a else ''}:{_short(b)}", forced, not a))
        if "*" in s:
            out.append((f"{main}:{main}", forced, not a))
    force = p.force or p.mirror
    return [(spec, (" --force" if f or force else "") + (" --delete" if d or p.delete else "")) for spec, f, d in out]


def texts(prefix: str, p: Push, repo: Repo, main: str) -> list[str]:
    """The resolved texts of one push from a folder git described (repo), with main as its main branch."""
    cur = repo.branch or main
    args = list(p.args)
    remote: str | None
    if args:
        remote = args.pop(0)
    elif p.repo:
        remote = p.repo
    else:
        keys = (f"branch.{cur}.pushremote", "remote.pushdefault", f"branch.{cur}.remote")
        remote = next((v for v in map(repo.get, keys) if v), "origin")
    if remote is None:  # computed: every configured remote
        names = sorted({k.split(".")[1] for k in repo.config if k.startswith("remote.") and k.count(".") >= 2})
        remotes = names or ["origin"]
    else:
        remotes = [remote]
    if repo.git_dir is None:
        targets = [(r, r) for r in remotes]  # not a repo: the remote as written
    else:
        targets = [(r, u) for r in remotes for u in urls(repo, r)]
    follow = _true(repo.get("push.followtags"))
    out: list[str] = []
    for name, url in targets:
        q = p
        if not p.mirror and _true(repo.get(f"remote.{name}.mirror")):  # `git remote add --mirror=push`
            q = copy.copy(p)
            q.mirror = True
        tags = q.tags or q.mirror or follow
        mirror = " --mirror" if q.mirror else ""
        for u in (url, https(url)):
            if not u:
                continue
            for spec, flags in refspecs(q, args, repo, name, main):
                out.append(f"{prefix}git push {u} {spec}{flags}{mirror}")
            if tags:
                force = " --force" if q.force else ""
                out.append(f"{prefix}git push {u} refs/tags/*:refs/tags/* --tags{force}{mirror}")
    return out


def _true(value: str | None) -> bool:
    return (value or "").lower() in ("true", "yes", "on", "1")


class Resolver:
    """Resolves the pushes of one check() (guard.py) for a project, from the event's cwd (None: unknown); caches
    what git says of each folder for the whole check."""

    def __init__(self, project: Project, cwd: str | None) -> None:
        self.project = project
        self.start = frozenset({_expand(cwd) if cwd else UNKNOWN})
        self._repos: dict[tuple[str, str], Repo | None] = {}

    def repo(self, folder: str, git_dir: str = "") -> Repo | None:
        if (folder, git_dir) not in self._repos:
            self._repos[folder, git_dir] = read_repo(folder, git_dir or None)
        return self._repos[folder, git_dir]

    def roots(self) -> list[tuple[str, str]]:
        """[(folder, main branch)] of every repo of the project: its home and each [repos.*] path."""
        out = [(str(self.project.home), "main")]
        for r in self.project.repos.values():
            if isinstance(r, dict) and r.get("path"):
                out.append((str(self.project.path(r["path"])), str(r.get("main") or "main")))
        return list(dict(out).items())

    def main_of(self, folder: str) -> str:
        """The main branch of the project repo that holds folder (the deepest), "main" for any other folder."""
        best = ("", "main")
        for root, main in self.roots():
            if (folder == root or folder.startswith(root.rstrip("/") + "/")) and len(root) > len(best[0]):
                best = (root, main)
        return best[1]

    def resolve(
        self,
        w: Sequence[Tok],
        prog: int,
        where: frozenset[str],
        heads: dict[str, frozenset[str]] | None = None,
        before: Changes = NONE,
        shells: list[tuple[str, frozenset[str], Changes]] | None = None,
    ) -> list[tuple[str, str]]:
        """[(text, folder)] for a segment that runs `git push` from one of the folders where, [] for any other
        segment. folder is "?" + a repo of the project when it stands for a folder the line cannot name. heads: the
        branches the line may have checked out in a folder before (branches()); before: what the line changed in
        git's config before the segment (changes()); shells: gets (command, its folders, its changes) for each
        alias that runs a shell command."""
        call = parse_git(w, prog)
        if call is None or call.sub >= len(w):
            return []
        sub = w[call.sub]
        if sub.literal and sub.value != "push" and sub.value in BUILTINS:
            return []  # no git is run for them
        env, env_dir = env_changes(w[:prog])
        mine = before.plus(env).plus(call.changes)
        git_dir = call.git_dir if call.git_dir != "" else env_dir
        prefix = "".join(t.raw + " " for t in w[:prog])  # assignments and wrappers (a mark, foreman time-limit)
        here = set(where)
        for d in call.dirs:
            here = {UNKNOWN if d is None else _join(x, d) for x in here}
        if mine.unknown or git_dir is None:
            here.add(UNKNOWN)
        out: list[tuple[str, str]] = []

        def one(r: Repo, folder: str, label: str, main: str) -> None:
            cmd = expand(w[prog:], r)
            if isinstance(cmd, str):
                if shells is not None:
                    shells.append((cmd, frozenset({folder}), mine))
                return
            got = push_args(cmd, 0) if cmd is not None else None
            if got is not None:
                out.extend([(t, label) for t in texts(prefix, got[1], r, main) if (t, label) not in out])

        for folder in sorted(here):
            repo = None
            if folder != UNKNOWN and not git_dir:
                repo = self.repo(folder)
            elif folder != UNKNOWN and git_dir:
                gd = _join(folder, git_dir)  # UNKNOWN when it does not exist (yet)
                repo = None if gd == UNKNOWN else self.repo(folder, gd)
            if repo is not None:
                main = self.main_of(folder)
                cfg = overlay(repo.config, mine.config)
                for b in sorted((heads or {}).get(folder, {CURRENT})):
                    head = repo.branch if b == CURRENT else None if b == DETACHED else b
                    one(Repo(repo.git_dir, head, cfg), folder, folder, main)
                continue
            for root, main in self.roots():
                r = self.repo(root) or Repo(None, None, {})
                one(Repo(r.git_dir, main, overlay(r.config, mine.config)), UNKNOWN, "?" + root, main)
        return out

    def blind(self) -> list[tuple[str, str]]:
        """[(text, folder)] for a line the lexer cannot read that runs `git … push`: every branch and tag, from
        the event's folder and from every repo of the project."""
        w = [_tok(x) for x in ("git", "push", "--all", "--follow-tags")]
        return self.resolve(w, 0, self.start | {UNKNOWN})
