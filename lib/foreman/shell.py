"""Reading a shell line the way bash does, with no policy and no configuration.

A lexer for the words of a command (quotes, escapes, comments, redirections, heredocs, the separators && || ; | |& &
newline and parentheses, command substitutions) and the readers built on it: the segments of a line, the data
arguments of the commands that never run them (TEXT_ARGS, printers, search patterns, messages: data_tokens()), the
files a line writes and whether it may run them (written(), read_back(), runs_written() for a heredoc to cat or
tee), the files a line names as what it writes, whatever they are (writes(): redirections, copies, in-place editors,
removals…), and what runs inside a command (runs()).
The guard (guard.py) applies the deny and resource lists to what this module reads; identity.py and pause.py read
`foreman` calls through it. How the guard uses it is in guard.py's module doc.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Iterable, Sequence
from pathlib import PurePath
from typing import Any

WRAPPERS = ("time", "nice", "exec", "env", "command", "sudo", "nohup", "(", "{", "then", "do", "else")

# The foreman commands whose data arguments are not compared with [deny] (their values never run). "all" = every
# argument after the subcommand that is not an option name; "after-action" = the same after the first argument (inbox
# ACTION, backlog SUB); integrate: the values of these options and the 4th positional (the merge message); lock:
# only the "what" of `lock RESOURCE run "what" command…` (the command after it is compared as it is). git commit and
# tag: the values of -m / --message (also -am, -m=…, --message=…).
TEXT_ARGS = {
    "note": "all", "progress": "all", "guard": "all", "inbox": "after-action", "backlog": "after-action",
    "integrate": ("title", "body", "note", "result", "verdict", "approved"), "lock": "what",
}  # fmt: skip
INTEGRATE_VALUES = tuple("title body body-file sub note report result verdict rebase-from approved ci".split())
INTEGRATE_FLAGS = ("no-ci", "check", "opens")
# Commands whose data is not a TEXT_ARGS value. Printers: every argument is data (not `printf -v`, which assigns a
# variable). Search commands: the pattern (the value of -e/--regexp, or the first operand without -e/-f) is data, its
# paths are not; their options are a closed list (flags; letters and names that take a value), and any other option
# (`rg --pre CMD`, `git grep -O`, which run a program) leaves no exemption. Messages: the values of these options.
# The program is named bare or by a system path: `./echo` or `/tmp/grep` may be a script of anything.
PRINTERS = ("echo", "printf")


def _long(names: str) -> tuple[str, ...]:
    return tuple("--" + n for n in names.split())


# A search command's options: (short flags, short with value, long flags, long with value)
Search = tuple[str, str, tuple[str, ...], tuple[str, ...]]
SEARCHES: dict[str, Search] = {
    "grep": (
        "0123456789abcEFGHhIiLlnoPqRrsTUvwxZz",
        "ABCefm",
        _long(
            "line-number ignore-case no-ignore-case recursive dereference-recursive files-with-matches "
            "files-without-match count invert-match word-regexp line-regexp extended-regexp fixed-strings basic-regexp "
            "perl-regexp no-messages quiet silent no-filename with-filename only-matching text null null-data "
            "byte-offset initial-tab color colour"
        ),
        _long("max-count after-context before-context context include exclude exclude-dir regexp file color colour"),
    ),
    "rg": (
        "0.acFHhIiLlNnoPpqSsUuvwx",
        "ABCdEefgjMmrTt",
        _long(
            "hidden no-ignore no-ignore-vcs files-with-matches files-without-match count count-matches fixed-strings "
            "ignore-case smart-case case-sensitive word-regexp line-regexp line-number no-line-number heading "
            "no-heading with-filename no-filename only-matching quiet text follow multiline pcre2 vimgrep json files "
            "stats unrestricted invert-match null trim column no-messages glob-case-insensitive"
        ),
        _long(
            "glob iglob type type-not max-count max-depth after-context before-context context regexp file color "
            "colors sort sortr replace max-columns threads encoding"
        ),
    ),
    "git grep": (
        "0123456789acEFGHhIiLlnopqvWwz",
        "ABCefm",
        _long(
            "cached untracked no-index exclude-standard no-exclude-standard line-number ignore-case "
            "files-with-matches name-only files-without-match count invert-match word-regexp extended-regexp "
            "fixed-strings basic-regexp perl-regexp full-name column heading break show-function function-context "
            "only-matching null all-match and or not quiet text"
        ),
        _long("max-count max-depth threads context after-context before-context regexp file"),
    ),
}
SEARCHES["egrep"] = SEARCHES["fgrep"] = SEARCHES["grep"]
NO_PATTERN = ("--files",)  # rg lists the files it would search: every operand is a path
MESSAGES = {("gh", "pr"), ("gh", "issue")}
MESSAGE_ACTIONS = ("create", "edit", "comment", "review")
MESSAGE_OPTS = re.compile(r"--title|-t|--body|-b")
COMMIT_OPTS = re.compile(r"--message|-[asnvqe]*m")  # git commit|tag: -m, -am…
SYSTEM_BIN = ("/bin/", "/usr/bin/", "/usr/local/bin/", "/opt/homebrew/bin/")
# Where a printer may write and keep its exemption: a literal path that is not a device (/dev/fd/3, /dev/tcp/…)
# other than these, nor under /proc or inside a .git folder (its hooks and config run).
SAFE_DEVICES = ("/dev/null", "/dev/stdout", "/dev/stderr")
MAX_WRITTEN = 8  # read_back(): more files written in one line than this counts as "may run one of them"
# What may come before a text command: these assignments and bare wrappers (with options they could run anything).
SAFE_ASSIGN = re.compile(r"(FOREMAN_\w*|AGENT|LANG|LC_\w+|TZ|NO_COLOR)=")
PREFIX_WORDS = ("env", "command", "exec", "time", "nohup", "{", "then", "do", "else")
# What a text command may pipe into and keep its exemption (they print, they never run their input).
SAFE_CONSUMERS = ("head", "tail", "grep", "egrep", "fgrep", "wc", "cat", "sort", "uniq", "cut", "tr")
# A command that redefines what runs, or runs text: no exemption anywhere in the line.
REDEFINERS = ("source", ".", "alias", "eval", "function", "enable")
MAX_DEPTH = 4
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
REDIR = re.compile(r"(\d+|&)?(<<<|<<-|<<|<>|<&|>>|>&|>\||<|>)")


class Unparsed(Exception):
    """The lexer cannot read the command the way bash would: the caller compares the whole text instead."""


HEREDOC = re.compile(r"<<-?[ \t]*(['\"]?)(\w+)\1")


def strip_heredocs(cmd: str) -> str:
    """The command without its heredocs (from `<<` to the end line, both included, become a newline). Linear: the
    regex of before took seconds on a few KB of `<<`."""
    out: list[str] = []
    i = 0
    while True:
        m = HEREDOC.search(cmd, i)
        if not m:
            break
        nl = cmd.find("\n", m.end())
        if nl < 0:
            break
        j, end = nl + 1, None
        while j <= len(cmd):
            k = cmd.find("\n", j)
            k = len(cmd) if k < 0 else k
            if cmd[j:k].strip() == m.group(2):
                end = k + 1
                break
            j = k + 1
        if end is None:
            out.append(cmd[i : m.end()])
            i = m.end()
            continue
        out.append(cmd[i : m.start()] + "\n")
        i = end
    out.append(cmd[i:])
    return "".join(out)


def strip_quotes(cmd: str) -> str:
    cmd = re.sub(r"'[^']*'", "''", cmd)
    return re.sub(r'"(?:[^"\\]|\\.)*"', '""', cmd)


def segments(cmd: str) -> list[str]:
    return [s.strip() for s in re.split(r"&&|\|\||[;|\n]|\$\(|`", cmd) if s.strip()]


def quoted_segments(cmd: str) -> list[str]:
    """Like segments(), but a separator inside quotes does not split: `git commit -m "a; b"` is one segment (so its
    text stays text) and `bash -c "x; make deploy"` is one segment that the deny regexes still read."""
    out: list[str] = []
    cur: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(cmd):
        ch = cmd[i]
        if quote:
            if ch == "\\" and quote == '"' and i + 1 < len(cmd):
                cur.append(cmd[i : i + 2])
                i += 2
                continue
            if ch == quote:
                quote = None
            cur.append(ch)
        elif ch in "'\"":
            quote = ch
            cur.append(ch)
        else:
            sep = next((t for t in ("&&", "||", "$(", ";", "|", "\n", "`") if cmd.startswith(t, i)), None)
            if sep:
                out.append("".join(cur))
                cur = []
                i += len(sep)
                continue
            cur.append(ch)
        i += 1
    out.append("".join(cur))
    return [s.strip() for s in out if s.strip()]


def words(seg: str) -> list[str]:
    try:
        w = shlex.split(seg, posix=True)
    except ValueError:
        w = seg.split()
    while w and (re.match(r"^\w+=", w[0]) or w[0] in WRAPPERS):
        w = w[1:]
    if w[:2] == ["perl", "-e"] and len(w) > 2:  # perl -e 'alarm N; exec @ARGV' command...
        w = w[3:]
    if w and w[0].rsplit("/", 1)[-1] in ("time_limit.sh", "timeout", "gtimeout") and len(w) > 2:
        w = w[2:]
    if w[:2] and w[0].rsplit("/", 1)[-1] == "foreman" and len(w) > 2 and w[1] == "time-limit":
        w = w[3:]
    # A lock wrapper runs the command after it: check that command too (it may need another resource).
    if len(w) > 1 and w[0] in ("bash", "sh") and w[1].endswith(".sh"):
        w = w[1:]
    if len(w) > 5 and w[0].rsplit("/", 1)[-1] == "foreman" and w[1] == "lock" and w[3] == "run":
        return words(" ".join(shlex.quote(x) for x in w[5:]))
    if len(w) > 3 and w[0].rsplit("/", 1)[-1].endswith("lock.sh") and w[1] == "run":
        return words(" ".join(shlex.quote(x) for x in w[3:]))
    return w


def foreman_call(w: list[str]) -> tuple[str | None, str, list[str]] | None:
    """(project given with --project or None, subcommand, the rest) if the words (from words()) are a foreman call:
    bare `foreman`, any path to it, `python… bin/foreman`; else None."""
    if len(w) > 1 and PurePath(w[0]).name.startswith("python") and PurePath(w[1]).name == "foreman":
        w = w[1:]
    if not w or PurePath(w[0]).name != "foreman":
        return None
    rest, name = w[1:], None
    if rest[:1] == ["--project"] and len(rest) > 1:
        name, rest = rest[1], rest[2:]
    return (name, rest[0], rest[1:]) if rest else None


# ---------- the lexer ----------


class Tok:
    """A word as bash reads it. kind: word | redir (an operator; a heredoc's includes its delimiter) | target (the
    word a redirection names) | comment. subs: [(opener, content)] of the substitutions inside it. oneword: bash
    keeps it one word (no unquoted expansion, glob or brace). literal: no expansion at all (value is what bash
    sees)."""

    __slots__ = ("kind", "start", "end", "raw", "subs", "oneword", "literal", "value", "body")

    def __init__(self, kind: str, start: int) -> None:
        self.kind, self.start, self.end, self.raw, self.oneword, self.literal = kind, start, start, "", True, True
        self.subs: list[tuple[str, str]] = []
        self.value: Any = []  # the characters read (a list) while lexing, then the word (a str)
        self.body: str | None = None  # a heredoc operator: the text of its body


class Seg:
    __slots__ = ("toks", "sep")

    def __init__(self, toks: list[Tok], sep: str) -> None:
        self.toks, self.sep = toks, sep


class Lexer:
    def __init__(self, s: str, depth: int = 0) -> None:
        if depth > MAX_DEPTH:
            raise Unparsed(f"nested deeper than {MAX_DEPTH}")
        self.s, self.n, self.depth = s, len(s), depth
        self.pending: list[
            tuple[str, bool, Tok]
        ] = []  # heredocs waiting for the next newline: (delimiter, quoted, their operator token)
        self.body_subs: list[str] = []  # substitutions inside unquoted heredoc bodies (they run)
        self.redefines = False  # a function definition, alias, source, eval…: no exemption

    def parse(self, i: int = 0, closer: str | None = None) -> tuple[list[Seg], int]:
        """The segments from i to the end (closer None) or to the closing ')' (closer ')'): (segments, the index
        after the closer)."""
        s, n = self.s, self.n
        segs: list[Seg] = []
        toks: list[Tok] = []
        parens, target = 0, False

        def end(sep: str) -> None:
            nonlocal toks
            if target:
                raise Unparsed("redirection without a target")
            first = next(
                (t.raw for t in toks if t.kind == "word" and not re.match(r"\w+=", t.raw) and t.raw not in WRAPPERS),
                None,
            )
            if first in REDEFINERS:
                self.redefines = True
            segs.append(Seg(toks, sep))
            toks = []

        while True:
            if i >= n:
                if closer or parens or self.pending:
                    raise Unparsed("unclosed")
                end("")
                return segs, n
            c = s[i]
            if c in " \t":
                i += 1
                continue
            if s.startswith("\\\n", i):
                i += 2
                continue
            if c == "\n":
                end("\n")
                i = self.read_bodies(i + 1)
                continue
            if c == "#":
                j = s.find("\n", i)
                j = n if j < 0 else j
                t = Tok("comment", i)
                t.end, t.raw = j, s[i:j]
                toks.append(t)
                i = j
                continue
            if c == "(":
                if toks:
                    self.redefines = True  # name ( ) { …; }: a function definition
                parens += 1
                end("(")
                i += 1
                continue
            if c == ")":
                if parens:
                    parens -= 1
                    end(")")
                    i += 1
                    continue
                if closer == ")":
                    if self.pending:
                        raise Unparsed("heredoc inside a substitution not closed before it")
                    end(")")
                    return segs, i + 1
                raise Unparsed("unbalanced )")
            m = None if s.startswith(("<(", ">("), i) else REDIR.match(s, i)
            if m and (m.group(1) != "&" or m.group(2) in (">", ">>")) and (c in "<>&" or m.group(1)):
                if target:
                    raise Unparsed("redirection without a target")
                i = self.redir(m, toks)
                target = m.group(2) not in ("<<", "<<-")
                continue
            sep = next((op for op in ("&&", "||", "|&", ";;&", ";;", ";&", ";", "|", "&") if s.startswith(op, i)), None)
            if sep:
                end(sep)
                i += len(sep)
                continue
            t = self.word(i, "target" if target else "word")
            if t.end == i:
                raise Unparsed("unexpected character")
            target = False
            toks.append(t)
            i = t.end

    def redir(self, m: re.Match[str], toks: list[Tok]) -> int:
        t = Tok("redir", m.start())
        j = m.end()
        if m.group(2) in ("<<", "<<-"):
            while j < self.n and self.s[j] in " \t":
                j += 1
            if j >= self.n or self.s[j] in " \t\n;&|()<>":
                raise Unparsed("heredoc without a delimiter")
            d = self.word(j, "word")
            if not d.literal or d.subs or not d.value:
                raise Unparsed("heredoc delimiter with an expansion")
            self.pending.append((d.value, any(q in d.raw for q in "'\"\\"), t))
            j = d.end
        t.end, t.raw = j, self.s[m.start() : j]
        toks.append(t)
        return j

    def read_bodies(self, i: int) -> int:
        s, n = self.s, self.n
        for delim, quoted, op in self.pending:
            start = i
            while True:
                j = s.find("\n", i)
                line = s[i : n if j < 0 else j]
                if line.strip() == delim:  # laxer than bash (it ends no later): more is compared, never less
                    body, i = s[start:i], (n if j < 0 else j + 1)
                    op.body = body
                    break
                if j < 0:
                    raise Unparsed("heredoc without its end line")
                i = j + 1
            if not quoted:
                sub = Lexer(body, self.depth)
                t = Tok("body", 0)
                sub.body(t)
                self.body_subs += [c for _, c in t.subs]
        self.pending = []
        return i

    def body(self, t: Tok) -> None:
        """An unquoted heredoc body: only $… and backquotes mean something (as inside double quotes)."""
        s, n, i = self.s, self.n, 0
        while i < n:
            c = s[i]
            if c == "\\":
                i += 2
            elif c == "$":
                i = self.dollar(i, t, True)
            elif c == "`":
                i = self.backquote(i + 1, t, True)
            else:
                i += 1

    def word(self, i: int, kind: str) -> Tok:
        s, n = self.s, self.n
        t = Tok(kind, i)
        while i < n:
            c = s[i]
            if c in "<>" and s.startswith("(", i + 1):
                content, i = self.paren_sub(i + 2)
                t.subs.append((c + "(", content))
                t.oneword = t.literal = False
                continue
            if c in " \t\n;&|()<>":
                break
            if ord(c) < 32 or c == "\x7f":
                raise Unparsed("control character")
            if c == "\\":
                if i + 1 >= n:
                    raise Unparsed("trailing backslash")
                if s[i + 1] != "\n":
                    t.value.append(s[i + 1])
                i += 2
                continue
            if c == "'":
                j = s.find("'", i + 1)
                if j < 0:
                    raise Unparsed("unclosed '")
                if CONTROL.search(s, i + 1, j):
                    raise Unparsed("control character")
                t.value.append(s[i + 1 : j])
                i = j + 1
                continue
            if c == '"':
                i = self.dquote(i + 1, t)
                continue
            if c == "$":
                i = self.dollar(i, t, False)
                continue
            if c == "`":
                i = self.backquote(i + 1, t, False)
                continue
            if c in "*?[{":
                t.oneword = t.literal = False
            t.value.append(c)
            i += 1
        t.end, t.raw, t.value = i, s[t.start : i], "".join(t.value)
        return t

    def dquote(self, i: int, t: Tok) -> int:
        s, n = self.s, self.n
        while True:
            if i >= n:
                raise Unparsed('unclosed "')
            c = s[i]
            if c == '"':
                return i + 1
            if c == "\\":
                if i + 1 >= n:
                    raise Unparsed('unclosed "')
                nx = s[i + 1]
                if nx != "\n":
                    t.value.append(nx if nx in '"\\$`' else "\\" + nx)
                i += 2
                continue
            if c == "$":
                i = self.dollar(i, t, True)
                continue
            if c == "`":
                i = self.backquote(i + 1, t, True)
                continue
            if (ord(c) < 32 and c not in "\t\n") or c == "\x7f":
                raise Unparsed("control character")
            t.value.append(c)
            i += 1

    def dollar(self, i: int, t: Tok, quoted: bool) -> int:
        s = self.s
        nx = s[i + 1 : i + 2]
        if s.startswith(("$((", "$["), i):
            raise Unparsed("arithmetic expansion")
        if nx in ("'", '"') and not quoted:
            raise Unparsed("$'…' or $\"…\"")
        if nx == "(":
            content, j = self.paren_sub(i + 2)
            t.subs.append(("$(", content))
        elif nx == "{":
            j = s.find("}", i + 2)
            if j < 0 or re.search(r"[$`'\"\\(){}\n]", s[i + 2 : j]):
                raise Unparsed("parameter expansion with quotes or substitutions")
            j += 1
        elif nx and (nx.isalnum() or nx in "_@*#?$!-"):
            j = i + 1
        else:
            t.value.append("$")
            return i + 1
        t.literal = False
        if not quoted:
            t.oneword = False
        t.value.append(s[i:j])
        return j

    def backquote(self, i: int, t: Tok, quoted: bool) -> int:
        s, n = self.s, self.n
        buf: list[str] = []
        while True:
            if i >= n:
                raise Unparsed("unclosed `")
            c = s[i]
            if c == "\\" and i + 1 < n:
                nx = s[i + 1]
                buf.append(nx if nx in "`\\$" or (quoted and nx == '"') else c + nx)
                i += 2
                continue
            if c == "`":
                break
            buf.append(c)
            i += 1
        content = "".join(buf)
        Lexer(content, self.depth + 1).parse()  # it must read too
        t.subs.append(("`", content))
        t.literal = False
        if not quoted:
            t.oneword = False
        return i + 1

    def paren_sub(self, i: int) -> tuple[str, int]:
        inner = Lexer(self.s, self.depth + 1)
        _, j = inner.parse(i, ")")
        return self.s[i : j - 1], j


def lex(cmd: str, depth: int = 0) -> tuple[list[Seg], Lexer]:
    """(segments, the lexer) of a command line; raises Unparsed."""
    lx = Lexer(cmd, depth)
    return lx.parse()[0], lx


# ---------- what is data ----------


def _lit(toks: Sequence[Tok], k: int) -> str | None:
    """The value of toks[k] if it is a plain literal word, else None."""
    return toks[k].value if k < len(toks) and toks[k].literal else None


def _program_at(toks: Sequence[Tok]) -> int:
    """Where a text command's program is: past SAFE_ASSIGN assignments and PREFIX_WORDS."""
    i = 0
    while i < len(toks) and (SAFE_ASSIGN.match(toks[i].raw) or toks[i].raw in PREFIX_WORDS):
        i += 1
    return i


def data_tokens(toks: Sequence[Tok]) -> dict[Tok, int] | None:
    """{token: offset} of the data arguments of a text command (offset: how much of the raw token stays, 0 = all
    of it is data), or None if the words are not a TEXT_ARGS command."""
    i = _program_at(toks)
    path = _lit(toks, i)
    if path is None:
        return None
    prog = PurePath(path).name
    system = "/" not in path or path.startswith(SYSTEM_BIN)  # not a script of the same name
    nxt = _lit(toks, i + 1)
    if prog.startswith("python") and nxt and PurePath(nxt).name == "foreman":
        i, prog = i + 1, "foreman"
    if prog == "foreman":
        j = i + 1
        if _lit(toks, j) == "--project":
            if _lit(toks, j + 1) is None:
                return None
            j += 2
        spec = TEXT_ARGS.get(_lit(toks, j) or "")
        if spec == "all":
            return _all_data(toks, j + 1)
        if spec == "after-action":
            return _all_data(toks, j + 2) if _lit(toks, j + 1) is not None else None
        if spec == "what":  # lock RESOURCE run "what" command…
            if (
                _lit(toks, j + 1) is not None
                and _lit(toks, j + 2) == "run"
                and j + 3 < len(toks)
                and toks[j + 3].oneword
            ):
                return {toks[j + 3]: 0}
            return None
        if spec:
            return _integrate(toks, j + 1, spec)
        return None
    if prog == "git":
        j = i + 1
        while _lit(toks, j) in ("-C", "-c"):
            j += 2
        sub = _lit(toks, j)
        if sub in ("commit", "tag"):  # the values of -m/--message
            return _valued(toks, j + 1, COMMIT_OPTS, ("-m=", "--message="))
        if sub == "grep" and system and all(_lit(toks, n) == "-C" for n in range(i + 1, j, 2)):  # not -c (config)
            return _search(toks, j + 1, SEARCHES["git grep"])
        return None
    if not system:
        return None
    if prog in PRINTERS:
        return _printed(toks, i + 1, prog)
    if prog in SEARCHES:
        return _search(toks, i + 1, SEARCHES[prog])
    if (prog, _lit(toks, i + 1)) in MESSAGES and _lit(toks, i + 2) in MESSAGE_ACTIONS:
        return _valued(toks, i + 3, MESSAGE_OPTS, ("--title=", "--body="))
    return None


def printer(toks: Sequence[Tok]) -> bool:
    """True if the words (word tokens of a simple command) run echo or printf, named as data_tokens() reads them."""
    path = _lit(toks, _program_at(toks))
    return path is not None and PurePath(path).name in PRINTERS and ("/" not in path or path.startswith(SYSTEM_BIN))


def _printed(toks: Sequence[Tok], k: int, prog: str) -> dict[Tok, int] | None:
    """Every argument of echo or printf is data; None for `printf -v` (or a first argument that is an option or not
    a literal, which could be -v) and for a word bash would split or glob."""
    if any(not t.oneword for t in toks[k:]):
        return None
    first = toks[k] if k < len(toks) else None
    if prog == "printf" and first and (not first.literal or first.value[:1] == "-") and first.value != "--":
        return None
    return dict.fromkeys(toks[k:], 0)


def _search(toks: Sequence[Tok], k: int, spec: Search) -> dict[Tok, int] | None:
    """The pattern of a search command (spec: an entry of SEARCHES) read like its option parser: the values of
    -e/--regexp, or else the first operand. None at an option outside the closed list, at a word bash would split
    or glob, and at -e/-f after an operand (GNU grep reads them as options, BSD grep as paths)."""
    flags, valued, long_flags, long_valued = spec
    if any(not t.oneword for t in toks[k:]):
        return None
    out: dict[Tok, int] = {}
    given, first, dash, m = False, None, False, k
    while m < len(toks):
        t = toks[m]
        v = t.value
        if dash or v[:1] != "-" or v == "-":
            first = m if first is None else first
            m += 1
            continue
        if not t.literal:
            return None
        if v == "--":
            dash, m = True, m + 1
            continue
        if v.startswith("--"):
            name, eq, _ = v.partition("=")
            if name in NO_PATTERN and not eq:
                given = True
            if name in long_flags and not eq:
                m += 1
                continue
            if name not in long_valued or (not eq and m + 1 >= len(toks)):
                return None
            if name in ("--regexp", "--file"):
                if first is not None:
                    return None
                given = True
                if name == "--regexp":
                    if eq and t.raw.startswith(name + "="):
                        out[t] = len(name) + 1
                    elif not eq:
                        out[toks[m + 1]] = 0
            m += 1 if eq else 2
            continue
        step = 1
        for n, ch in enumerate(v[1:], 1):
            if ch in valued:
                if ch in "ef":
                    if first is not None:
                        return None
                    given = True
                attached = len(v) > n + 1
                if not attached and m + 1 >= len(toks):
                    return None
                if ch == "e":
                    if not attached:
                        out[toks[m + 1]] = 0
                    elif t.raw.startswith(v[: n + 1]):
                        out[t] = n + 1
                step = 1 if attached else 2
                break
            if ch not in flags:
                return None
        m += step
    if not given and first is not None:
        out[toks[first]] = 0
    return out


def _valued(toks: Sequence[Tok], k: int, opts: re.Pattern[str], given: tuple[str, ...]) -> dict[Tok, int] | None:
    """The values of the options opts matches (the next word; or after `=`, the options in given): the messages of
    `gh pr|issue create|edit|comment|review` (--body-file is a path) and of `git commit|tag`; every other word is
    still compared."""
    if any(not t.oneword for t in toks[k:]):
        return None
    out: dict[Tok, int] = {}
    m = k
    while m < len(toks):
        t = toks[m]
        v = t.value if t.literal and t.raw == t.value else None
        if v == "--":
            break
        if v is not None and opts.fullmatch(v) and m + 1 < len(toks):
            out[toks[m + 1]] = 0
            m += 2
            continue
        if t.raw.startswith(given):
            out[t] = t.raw.index("=") + 1
        m += 1
    return out


def _all_data(toks: Sequence[Tok], k: int) -> dict[Tok, int]:
    return {t: 0 for t in toks[k:] if not t.raw.startswith("-")}


def _integrate(toks: Sequence[Tok], k: int, data_opts: Iterable[str]) -> dict[Tok, int] | None:
    """The data of `foreman integrate REPO BRANCH ISSUE [MESSAGE] [--opt value]…` read like its argparse; None at
    anything it would not read the same way (an unknown or abbreviated option, a word bash would split)."""
    if any(not t.oneword for t in toks[k:]):
        return None
    out: dict[Tok, int] = {}
    pos, m, dash = 0, k, False
    while m < len(toks):
        t = toks[m]
        if not dash and (t.raw.startswith("-") or t.value.startswith("-")):  # a quoted option is still one
            if not t.literal:
                return None
            if t.value == "--":
                dash, m = True, m + 1
                continue
            if not t.value.startswith("--"):
                return None
            name, eq, _ = t.value[2:].partition("=")
            if name in INTEGRATE_FLAGS and not eq:
                m += 1
                continue
            if name not in INTEGRATE_VALUES:
                return None
            if eq:
                if not t.raw.startswith(f"--{name}="):
                    return None
                if name in data_opts:
                    out[t] = len(name) + 3
                m += 1
                continue
            if m + 1 >= len(toks):
                return None
            if name in data_opts:
                out[toks[m + 1]] = 0
            m += 2
            continue
        if pos == 3:
            out[t] = 0
        elif pos > 3:
            return None
        pos, m = pos + 1, m + 1
    return out


def filters_only(segs: Sequence[Seg], k: int, writes: bool = False) -> bool:
    """True if segment k does not feed anything that could run its output: only plain filters after it in its
    pipeline, no >(…) in it or in them, and its output not written to a file (a script run later). writes: a printer
    may write to a file and pipe into tee too (written() and read_back() judge where it writes, for the whole line)."""

    def has_procsub(seg: Seg) -> bool:
        return any(o == ">(" for t in seg.toks for o, _ in t.subs)

    toks = segs[k].toks
    if has_procsub(segs[k]) or (
        not writes
        and any(
            a.kind == "redir" and ">" in a.raw and b.raw not in ("/dev/null", "1", "2", "-")
            for a, b in zip(toks, toks[1:])
        )
    ):
        return False
    consumers = (*SAFE_CONSUMERS, "tee") if writes else SAFE_CONSUMERS
    while segs[k].sep in ("|", "|&") and k + 1 < len(segs):
        k += 1
        w = words(" ".join(t.raw for t in segs[k].toks if t.kind != "comment"))
        if not w or PurePath(w[0]).name not in consumers or has_procsub(segs[k]):
            return False
    return True


def written(segs: Sequence[Seg], info: Sequence[Runs]) -> list[Tok] | None:
    """The files the line writes: the targets of its output redirections (> >> >| &> <> and >& a name) and the
    operands of tee. None if any write may reach something else than a plain file: a target that is not a literal
    word ($T/x could be /dev/fd/3), a device other than /dev/null, stdout and stderr (/dev/fd/N, /dev/tcp/…),
    /proc, a .git folder (its hooks and config run), or a descriptor above 2 (>&3). info: runs() of each segment."""
    out: list[Tok] = []
    for seg, r in zip(segs, info):
        toks = seg.toks
        for a, b in zip(toks, toks[1:]):
            if a.kind != "redir" or ">" not in a.raw or b.kind != "target":
                continue
            if a.raw.endswith("&") and (b.value.isdigit() or b.value == "-"):  # >&N, 2>&1, >&-: a descriptor
                if b.value not in ("1", "2", "-"):
                    return None
                continue
            out.append(b)
        w = _words(seg)
        if r.prog < len(w) and PurePath(w[r.prog].value).name == "tee":  # also behind sudo, env…
            out += [t for t in w[r.prog + 1 :] if not (t.literal and t.value.startswith("-"))]
    for t in out:
        v = t.value
        if not t.literal or not v or (v.startswith("/dev/") and v not in SAFE_DEVICES):
            return None
        if v.startswith("/proc/") or ".git" in PurePath(v).parts:
            return None
    return [t for t in out if t.value not in SAFE_DEVICES]


def read_back(segs: Sequence[Seg], info: Sequence[Runs], files: Sequence[Tok]) -> bool:
    """True if the line may run a file it writes (files: written(), [] when it is not known where it writes): the
    name of one appears in any other word, target or heredoc body of the line (`sh x.sh`, `./x.sh`, `chmod +x x.sh`,
    `mkfifo p`), it writes more than MAX_WRITTEN files, a program is not a plain word ($f, $(…), a glob), or
    something that runs what it gets (a shell or an interpreter, eval, source, xargs, find -exec, sh -c…) takes a
    word that is not literal (`sh *.sh`, `sh "$f"`) or reads a pipe (`ls | xargs sh`). Approximate on the side of
    yes; a file another program reads as code by its own convention (a Makefile) is not seen, as for any file written
    and run later."""
    names = {PurePath(t.value).name or t.value for t in files}
    if len(names) > MAX_WRITTEN:  # bounded work: more files than this, and the answer is yes
        return True
    own = set(files)
    rest = "\n".join(  # raw and value: an escaped space (sh a\ b.sh) names the file "a b.sh"
        f"{t.raw}\n{t.value if isinstance(t.value, str) else ''}\n{t.body or ''}"
        for seg in segs
        for t in seg.toks
        if t not in own
    )
    if any(n in rest for n in names):
        return True
    for j, (seg, r) in enumerate(zip(segs, info)):
        w = _words(seg)
        if r.prog < len(w) and not w[r.prog].literal:
            return True
        name = PurePath(w[r.prog].value).name if r.prog < len(w) else ""
        runner = r.stdin or r.strings or r.cmds or name in ("source", ".")
        if runner and (any(not t.literal for t in w[r.prog :]) or (j and segs[j - 1].sep in PIPES)):
            return True
    return False


WRITERS = ("cat", "tee")  # what a heredoc writes to a file through (heredoc_writer())
FILE_RUNNERS = ("crontab", "at", "batch", "make", "gmake")  # they run a file named in their arguments
MAKES, MAKEFILES = ("make", "gmake"), {"Makefile", "makefile", "GNUmakefile"}  # and make its Makefile by name
COPIERS = ("cp", "mv", "ln", "install", "rsync", "ditto")  # a copy of a file written in the line may be run
PLAIN_PARAM = re.compile(r"\$\{?([A-Za-z_]\w*)\}?")
# A variable the line sets to one of these (or to a shell or an interpreter), in the program's place, may run a file.
RUNNER_NAMES = ("eval", "source", ".", "xargs", "find", "ssh", "env", "sudo", "doas", "exec", "command", "builtin")
RUNNER_NAMES += ("nohup", "time", "nice", "timeout", "gtimeout", "watch", "su", "trap", *FILE_RUNNERS)
CODE_OPTS = ("-c", "-e", "-E", "-m", "-p", "--eval", "--print")  # interpreters: the next word is the code (or module)


def heredoc_writer(seg: Seg, r: Runs) -> bool:
    """True if the segment is cat or tee (also behind sudo, env…) reading a heredoc or a here-string."""
    w = _words(seg)
    return (
        r.prog < len(w)
        and _lit(w, r.prog) is not None
        and PurePath(w[r.prog].value).name in WRITERS
        and bool(inputs(seg))
    )


def heredoc_files(segs: Sequence[Seg], info: Sequence[Runs], k: int) -> set[str] | None:
    """The names of the files the heredoc writer segs[k] writes: its own output targets and those of the tee its
    pipeline goes through; None if one is not known (written())."""
    j = k
    while j + 1 < len(segs) and segs[j].sep in PIPES:
        j += 1
    files = written(segs[k : j + 1], info[k : j + 1])
    return None if files is None else {PurePath(t.value).name or t.value for t in files}


def runs_written(
    segs: Sequence[Seg], info: Sequence[Runs], files: Sequence[Tok], depth: int = 0
) -> tuple[str | None, set[str] | None]:
    """Whether and how the line may run a file it writes (files: written(), [] when it is not known where it
    writes): (kind, names). kind is "shell" when a shell (or anything that runs commands: eval, source, xargs,
    find -exec, sh -c, ssh, crontab, make…) or the file itself as the program may run it, "raw" when only an
    interpreter that is not a shell (python…) may, else None; names are the written files it may run, None for any.
    Narrower than read_back(): the name of a written file (or of a copy: cp, mv, ln…) must be the program
    (`./x.sh`), the Makefile of make, or appear in a word, target or heredoc of a command that runs files (for an
    interpreter that is not a shell, only its script operand, its -c/-e code or its input: `python3 run.py data.txt`
    does not run data.txt; a command string, `sh -c S` or `ssh HOST S`, is read again). Any written file may run when
    the line writes more than MAX_WRITTEN files, a program is not a plain word ($(…), a glob, a variable the line may
    set to a runner), or a runner takes a word that is not literal or reads a pipe. A file only read or handed to
    another tool (`git add f.md`, `$EDITOR f.md`) is not run."""
    names = {PurePath(t.value).name or t.value: {PurePath(t.value).name or t.value} for t in files}
    return _scan(segs, info, names, set(files), depth)


def may_run(segs: Sequence[Seg], info: Sequence[Runs], names: Iterable[str], skip: int) -> bool:
    """True if the line may run a file of one of these names (runs_written()'s reading, by name: a file a python
    program writes, guard.py), not counting segs[skip] (the program itself)."""
    found = {n: {n} for n in names}
    return _scan(segs, info, found, set(), 0, skip)[0] is not None


def _scan(
    segs: Sequence[Seg],
    info: Sequence[Runs],
    names: dict[str, set[str]],
    own: set[Tok],
    depth: int,
    skip: int = -1,
) -> tuple[str | None, set[str] | None]:
    """runs_written() on the segments of a line; names: name -> the written files it is a copy of; skip: a segment
    left out (may_run())."""
    if len(names) > MAX_WRITTEN:  # bounded work: more files than this, and any may run
        return "shell", None

    def text(toks: Iterable[Tok]) -> str:  # raw and value: an escaped space (sh a\ b.sh) names the file "a b.sh"
        return "\n".join(
            f"{t.raw}\n{t.value if isinstance(t.value, str) else ''}\n{t.body or ''}" for t in toks if t not in own
        )

    def hit(seen: str) -> set[str]:
        return {root for n, roots in names.items() if n in seen for root in roots}

    words_of = [_words(seg) for seg in segs]
    setters: set[str] = set()  # variables the line may set to something not literal (read, for, printf -v…)
    assigned: dict[str, list[str]] = {}  # variable -> the program names its literal assignments give it
    for seg in segs:
        for t in seg.toks:
            if t.kind != "word":
                continue
            m = DEFAULT_ASSIGN.search(t.raw)
            if m:
                setters.add(m.group(1))
            if ASSIGN.match(t.raw):
                var = re.split(r"[\[+=]", t.raw, maxsplit=1)[0]
                v = t.value.partition("=")[2] if t.literal else None
                if v is None:
                    setters.add(var)
                    continue
                first = PurePath(v.split()[0]).name if v.split() else ""
                if first == "foreman" and {"run", "time-limit"} & set(v.split()):
                    first = "nohup"  # foreman lock … run, time-limit: wrappers that run the rest
                assigned.setdefault(var, []).append(first)
            elif t.literal and re.fullmatch(r"(-v)?[A-Za-z_]\w*", t.value):
                setters.add(t.value.removeprefix("-v"))  # read X, for X in, export X, printf -v X, mapfile X…
    copies = [
        (w[r.prog + 1 :], text(seg.toks))
        for seg, r, w in zip(segs, info, words_of)
        if r.prog < len(w) and PurePath(_lit(w, r.prog) or "").name in COPIERS
    ]
    while names and copies and len(names) <= MAX_WRITTEN:  # copies of a written file are written files too
        before = len(names)
        for args, seen in copies:
            roots = hit(seen)
            if roots:
                if any(not t.literal for t in args):
                    return "shell", None
                for t in args:
                    if t.value[:1] != "-":
                        names.setdefault(PurePath(t.value).name or t.value, set()).update(roots)
        if len(names) == before:
            break
    if len(names) > MAX_WRITTEN:
        return "shell", None
    mode: str | None = None
    ran: set[str] | None = set()
    for j, (seg, r, w) in enumerate(zip(segs, info, words_of)):
        if r.prog >= len(w) or j == skip:
            continue
        prog = w[r.prog]
        if (
            not prog.literal
        ):  # $(…), a glob, or a variable: what the line may set it to ($EDITOR from outside: not seen)
            m = PLAIN_PARAM.fullmatch(prog.raw)
            var = m.group(1) if m else ""
            if not m or var in setters:
                return "shell", None
            for v in assigned.get(var, []):
                if v in RUNNER_NAMES or v in SHELLS or RAW_INTERPRETERS.fullmatch(v):
                    return "shell", None
                if v in names:
                    ran = None if ran is None else ran | names[v]
                    mode = "shell"
            continue
        name = PurePath(prog.value).name
        if name in names:  # ./x.sh, /tmp/x.sh
            mode, ran = "shell", None if ran is None else ran | names[name]
            continue
        if name in MAKES:  # make reads its Makefile by name
            for n in MAKEFILES & names.keys():
                mode, ran = "shell", None if ran is None else ran | names[n]
        group = any(t.raw == "{" for t in w[: r.prog])  # runs() reads a group's input as a shell's: its program's
        stdin = runs(w[r.prog :]).stdin if group else r.stdin
        piped = bool(j) and segs[j - 1].sep in PIPES
        if stdin == "raw" and not (r.strings or r.cmds):
            seen, unknown = _script(seg, w, r.prog, piped)
            roots = hit(seen)
            if unknown or roots:
                mode = mode or "raw"
                ran = None if unknown or ran is None else ran | roots
            continue
        if not (r.strings or r.cmds or stdin == "shell" or name in FILE_RUNNERS):
            continue
        if piped or any(not t.literal for t in w[r.prog + 1 :]):
            return "shell", None
        roots = hit(text(seg.toks))
        if not roots:
            continue
        if r.strings and not r.cmds and name not in FILE_RUNNERS:  # sh -c S, ssh HOST S, eval S: read S again
            strung = {tok for tok, _, _ in r.strings}
            if None not in strung and hit(text(t for t in seg.toks if t not in strung)):  # sh -c S < x.sh
                return "shell", None if ran is None else ran | roots
            for _tok, string, _b in r.strings:
                try:
                    inner = pipelines(lex(string, depth + 1)[0])
                except Unparsed:
                    return "shell", None
                k, got = _scan(inner, [runs(_words(s)) for s in inner], dict(names), own, depth + 1)
                if k:
                    mode = "shell" if "shell" in (k, mode) else k
                    ran = None if got is None or ran is None else ran | got
            continue
        return "shell", None if ran is None else ran | roots
    return mode, ran


def _script(seg: Seg, w: Sequence[Tok], prog: int, piped: bool) -> tuple[str, bool]:
    """What an interpreter that is not a shell runs as code: (the text of its script operand, its -c/-e code and its
    input, and whether that is unknown: a script operand whose name is not literal, or a pipe into its stdin)."""
    toks = seg.toks
    out = [t.body or "" for t in toks if t.kind == "redir"]  # its input: heredoc, here-string, < FILE
    out += [b.value for a, b in zip(toks, toks[1:]) if a.kind == "redir" and "<" in a.raw and b.kind == "target"]
    k = prog + 1
    while k < len(w):
        t = w[k]
        if t.literal and t.value in CODE_OPTS:
            return "\n".join([*out, *(x.raw for x in w[k + 1 : k + 2])]), False
        if t.literal and t.value.startswith("-") and t.value != "-":
            k += 1
            continue
        if t.literal and t.value != "-":
            return "\n".join([*out, t.value]), False
        tail = t.raw.rsplit("/", 1)[-1]
        if t.value == "-" or re.search(r"[$`*?\[(]", tail):
            return "\n".join(out), t.value != "-" or piped
        return "\n".join([*out, tail]), False
    return "\n".join(out), piped


def commands(cmd: str, depth: int = 0) -> list[str]:
    """The text of every command the line runs: each segment and, recursively, each substitution (falls back to
    quoted_segments when the lexer cannot read the line)."""
    try:
        segs, lx = lex(cmd, depth)
        out: list[str] = []
        for seg in segs:
            text = " ".join(t.raw for t in seg.toks if t.kind != "comment")
            if text.strip():
                out.append(text)
            for t in seg.toks:
                for _, c in t.subs:
                    out += commands(c, depth + 1)
        for c in lx.body_subs:
            out += commands(c, depth + 1)
        return out
    except Exception:  # noqa: BLE001 (a parser bug falls back to the quoted-segments scan)
        return quoted_segments(strip_heredocs(cmd))


# ---------- what runs inside a command ----------

PIPES = ("|", "|&")
SHELLS = ("sh", "bash", "zsh", "dash", "ksh", "mksh", "yash", "ash", "fish", "csh", "tcsh")
# Interpreters that are not a shell: what they read is compared raw (a pattern anchored with (^|\s) does not see
# `os.system("x")`; accepted).
RAW_INTERPRETERS = re.compile(
    r"(python|pypy)[\d.]*|perl[\d.]*|ruby|node|nodejs|deno|bun|php|lua|osascript|"
    r"Rscript|tclsh|swift"
)
# Words that come before the program: keywords and wrappers that run the rest; the letters are their short options
# that take a value (getopt style: attached or the next word).
PREFIX = {
    "!": "", "if": "", "elif": "", "while": "", "until": "", "then": "", "do": "", "else": "", "{": "", "time": "",
    "builtin": "", "nohup": "", "command": "", "exec": "a", "env": "uCSP", "sudo": "ugCDhpRrTtU", "doas": "uC",
    "nice": "n", "stdbuf": "ioe", "caffeinate": "tw", "setsid": "", "chronic": "", "unbuffer": "", "busybox": "",
}  # fmt: skip
LONG_WITH_VALUE = _long(
    "user group chdir unset split-string adjustment prompt host role type signal kill-after input output error "
    "arg-file delimiter eof replace max-lines max-args max-procs max-chars process-slot-var interval command shell "
    "rcfile init-file"
)
ASSIGN = re.compile(r"[A-Za-z_]\w*(\[[^]]*\])?\+?=")


class Runs:
    """What a simple command runs besides itself. prog: index of the program word; strings: [(token or None, text,
    blankable)] command strings (`sh -c S`, eval, ssh, watch, su -c, env -S); cmds: [word tokens] commands given
    as words (xargs, find -exec); stdin: "shell" | "raw" | None, what it does with its standard input."""

    __slots__ = ("prog", "strings", "cmds", "stdin")
    prog: int
    strings: list[tuple[Tok | None, str, bool]]
    cmds: list[Sequence[Tok]]
    stdin: str | None

    def __init__(self) -> None:
        self.prog, self.strings, self.cmds, self.stdin = 0, [], [], None


def _skip_opts(
    w: Sequence[Tok], i: int, with_value: Iterable[str], strings: list[tuple[Tok | None, str, bool]] | None = None
) -> int:
    """The index of the first word after the options from w[i] (`--` included); with strings, `-S`/`--split-string`
    values (env) are added to it as commands."""
    while i < len(w):
        t = w[i]
        if strings is not None and ASSIGN.match(t.raw):
            i += 1
            continue
        v = t.value if t.literal or t.raw.startswith("-") else None  # -I{} is an option too
        if v is None or not v.startswith("-") or v == "-":
            return i
        if v == "--":
            return i + 1
        if v.startswith("--"):
            name, eq, val = v.partition("=")
            takes = name in LONG_WITH_VALUE and not eq
            if strings is not None and name == "--split-string":
                strings.append((None, w[i + 1].value if takes and i + 1 < len(w) else val, False))
            i += 2 if takes else 1
            continue
        step = 1
        for n, ch in enumerate(v[1:], 1):
            if ch in with_value:
                val = v[n + 1 :]
                if not val:
                    step = 2
                    val = w[i + 1].value if i + 1 < len(w) else ""
                if strings is not None and ch == "S":
                    strings.append((None, val, False))
                break
        i += step
    return i


def _program(w: Sequence[Tok], runs: Runs) -> tuple[int, str | None]:
    """The index of the program word of a simple command (word tokens) and its name ("" for none, None when it is
    not a plain word, e.g. "$SHELL" or $(…)), past assignments, keywords, wrappers, timeout, foreman time-limit
    and foreman lock … run."""
    i = 0
    while i < len(w):
        t = w[i]
        if ASSIGN.match(t.raw):
            i += 1
            continue
        if not t.literal:
            return i, None
        name = t.value if t.value in PREFIX or t.value == "." else PurePath(t.value).name  # PurePath(".").name is ""
        if name in PREFIX:
            i = _skip_opts(w, i + 1, PREFIX[name], runs.strings if name == "env" else None)
            continue
        if name in ("timeout", "gtimeout"):
            i = _skip_opts(w, i + 1, "sk") + 1
            continue
        if name == "time_limit.sh":
            i += 2
            continue
        nxt = _lit(w, i + 1)
        if name == "foreman" or (name.startswith("python") and nxt and PurePath(nxt).name == "foreman"):
            j = i + (1 if name == "foreman" else 2)
            if _lit(w, j) == "--project":
                j += 2
            if _lit(w, j) == "time-limit":
                i = j + 2
                continue
            if _lit(w, j) == "lock" and _lit(w, j + 2) == "run":
                i = j + 4
                continue
            return i, "foreman"
        if name.endswith("lock.sh") and nxt == "run":
            i += 3
            continue
        if name in SHELLS and nxt and PurePath(nxt).name.endswith("lock.sh"):
            i += 1
            continue
        if name == "perl" and nxt == "-e" and len(w) > i + 3 and "exec" in w[i + 2].value:
            i += 3  # perl -e 'alarm N; exec @ARGV' command…
            continue
        return i, name
    return i, ""


def runs(w: Sequence[Tok]) -> Runs:
    """Runs of a simple command given as its word tokens."""
    r = Runs()
    p, name = _program(w, r)
    if p < len(w) and w[p].raw == "{":  # `{` is not a literal word: read the command inside the group, and keep
        inner = runs(w[p + 1 :])  # its input read as a shell's, as for any program that is not a plain word
        r.prog, r.strings, r.cmds, r.stdin = p + 1 + inner.prog, inner.strings, inner.cmds, "shell"
        return r
    r.prog = p

    def joined(k: int) -> str:
        return " ".join(t.value for t in w[k:])

    if name is None or name in SHELLS:  # a program that is not a plain word may be a shell
        c, k = False, p + 1
        while k < len(w) and w[k].literal and w[k].value[:1] in "-+" and w[k].value not in ("-", "+"):
            v = w[k].value
            if v == "--":
                k += 1
                break
            if v in ("-o", "+o", "-O", "+O", "--rcfile", "--init-file"):
                k += 2
                continue
            if not v.startswith("--") and "c" in v[1:]:
                c = True
            k += 1
        if c and k < len(w):
            r.strings.append((w[k], w[k].value, True))
        r.stdin = "shell"
    elif name == "eval":
        r.strings.append((None, joined(p + 1), False))
        r.stdin = "shell"
    elif name in ("source", "."):
        r.stdin = "shell"
    elif name == "xargs":
        k = _skip_opts(w, p + 1, "adEILnPsR")
        if k < len(w):
            r.cmds.append(w[k:])
        r.stdin = "shell"
    elif name == "find":
        k = p + 1
        while k < len(w):
            if _lit(w, k) in ("-exec", "-execdir", "-ok", "-okdir"):
                e = k + 1
                while e < len(w) and _lit(w, e) not in (";", "+"):
                    e += 1
                if e > k + 1:
                    r.cmds.append(w[k + 1 : e])
                k = e
            k += 1
    elif name == "ssh":
        k = _skip_opts(w, p + 1, "BbcDEeFIiJLlmOoPpQRSWw")
        if k + 1 < len(w):
            r.strings.append((None, joined(k + 1), False))
        else:
            r.stdin = "shell"
    elif name == "watch":
        k = _skip_opts(w, p + 1, "nq")
        if k < len(w):
            r.strings.append((None, joined(k), False))
    elif name == "su":
        k = p + 1
        while k < len(w):
            v = _lit(w, k) or ""
            if v in ("-c", "--command") and k + 1 < len(w):
                r.strings.append((None, w[k + 1].value, False))
            elif v.startswith("--command="):
                r.strings.append((None, v.partition("=")[2], False))
            k += 1
        if not r.strings:
            r.stdin = "shell"
    elif name == "trap":  # trap ACTION SIGNAL…: the action runs (not `trap -p`, `trap -l`, `trap - SIG`)
        k = p + 2 if _lit(w, p + 1) == "--" else p + 1
        if k < len(w) and not (w[k].literal and w[k].value.startswith("-")):
            r.strings.append((None, w[k].value, False))
    elif name and RAW_INTERPRETERS.fullmatch(name):
        r.stdin = "raw"
    return r


def inputs(seg: Seg) -> list[str]:
    """The texts a simple command gets on its standard input from its heredocs and here-strings."""
    out: list[str] = []
    toks = seg.toks
    for n, t in enumerate(toks):
        if t.kind != "redir":
            continue
        if t.body is not None:
            out.append(t.body)
        elif "<<<" in t.raw and n + 1 < len(toks) and toks[n + 1].kind == "target":
            out.append(toks[n + 1].value)
    return out


def feeders(segs: Sequence[Seg], j: int) -> list[int]:
    """The indexes of the segments whose output reaches segs[j] through a pipe (also a ( … ) group before it)."""
    if j == 0 or segs[j - 1].sep not in PIPES:
        return []
    out: list[int] = []
    depth, k = 0, j - 1
    while k >= 0:
        s = segs[k]
        if s.sep == "(":
            depth -= 1
            if depth < 0:
                break
        elif depth == 0 and s.sep not in PIPES and s.sep != ")":
            break
        else:
            if s.sep == ")":
                depth += 1
            out.append(k)
        k -= 1
    return out


# Words that open or close a group or a compound command; a parameter expansion and the name it reads ($x, ${x},
# ${!x}, $1, $@, $_…); a ${x:=value} that assigns.
OPENERS = ("{", "if", "while", "until", "for", "select", "case")
CLOSERS = ("}", "fi", "done", "esac")
PARAM = re.compile(r"\$(\{!?)?([A-Za-z_]\w*|[0-9@*#?!$-])")
DEFAULT_ASSIGN = re.compile(r"\$\{([A-Za-z_]\w*):?=([^}]*)\}")
SPECIAL = re.compile(r"[0-9@*_]")  # positional parameters and $_: their values come from words of the line
READERS = ("read", "mapfile", "readarray")
LAST = re.compile(r"\$\{?_(?!\w)")


def _words(seg: Seg) -> list[Tok]:
    return [t for t in seg.toks if t.kind == "word"]


def _reads_input(seg: Seg) -> bool:
    return any(t.kind == "redir" and "<" in t.raw for t in seg.toks)


class Values:
    """Where a variable of the line gets its value. names: the names assigned; texts: the values (compared as
    commands when a variable runs); toks: the words that are values (their substitutions run); fed: the segments
    before the last `read` or `mapfile` a pipe feeds (what it reads can come from any of them; over-counted so the
    line is read once); defined: the functions and aliases defined in the line."""

    __slots__ = ("names", "texts", "toks", "fed", "defined")
    names: set[str]
    texts: list[str]
    toks: set[Tok]
    fed: int
    defined: set[str]

    def __init__(self) -> None:
        self.names, self.texts, self.toks, self.fed, self.defined = set(), [], set(), 0, set()

    def add(self, toks: Sequence[Tok]) -> None:
        self.toks.update(toks)
        if toks:
            self.texts += [" ".join(t.value for t in toks), *(t.value for t in toks)]


def values(segs: Sequence[Seg], info: Sequence[Runs]) -> Values:
    """The values the variables of a line can get from the line itself (see Values). info: runs() of each segment's
    words."""
    v = Values()
    for j, (seg, r) in enumerate(zip(segs, info)):
        w = _words(seg)
        for t in w:
            if ASSIGN.match(t.raw):
                v.names.add(re.split(r"[\[+=]", t.raw, maxsplit=1)[0])
                v.texts.append(t.value.partition("=")[2])
                v.toks.add(t)
            for m in DEFAULT_ASSIGN.finditer(t.raw):
                v.names.add(m.group(1))
                v.texts.append(m.group(2).strip("\"'"))
                v.toks.add(t)
        if seg.sep == "(" and w:
            if w[-1].raw.endswith("=") and j + 1 < len(segs):  # a=( … ): the words up to ) are values
                v.names.add(w[-1].raw.rstrip("+="))
                v.add(_words(segs[j + 1]))
            else:  # name ( ) { …; }
                v.defined.add(w[-1].value)
        if not w or r.prog >= len(w):
            continue
        name, args = w[r.prog].value, w[r.prog + 1 :]
        if name == "function" and args:
            v.defined.add(args[0].value)
        elif name == "alias":
            v.defined.update(a.value.partition("=")[0] for a in args)
        elif name in ("for", "select") and args:
            v.names.add(args[0].value)
            v.add(args[2:] if len(args) > 1 and args[1].value == "in" else args[1:])
        elif name == "printf" and args and args[0].value.startswith("-v"):  # printf -v NAME or -vNAME
            k = 2 if args[0].value == "-v" else 1
            v.names.add(args[0].value[2:] if k == 1 else args[1].value if len(args) > 1 else "")
            v.add(args[k:])
        elif name in READERS:
            v.names.update(a.value for a in args if a.literal and re.fullmatch(r"[A-Za-z_]\w*", a.value))
            v.texts += inputs(seg)
            if j and segs[j - 1].sep in PIPES:
                v.fed = j
        elif name == "set" or name in v.defined:  # positional parameters
            v.add(args)
    if any(LAST.search(t.raw) for s in segs for t in s.toks) or any(LAST.search(x) for s in segs for x in inputs(s)):
        v.add([w[-1] for w in map(_words, segs) if len(w) > 1])  # $_: the last argument of the command before
    return v


def unbounded(segs: Sequence[Seg], info: Sequence[Runs], vals: Values, fed: Iterable[int]) -> str | None:
    """How far what the line runs reaches past one segment: "shell" when output or input of a group, a compound, a
    function or alias defined in the line, >(…) or a coprocess reaches something that reads it as a program (the
    guard then reads all of the line as if it went to a shell); "var" when a variable runs, in the program's place or
    inside a command that runs (sh -c "$x", eval, xargs, a heredoc or pipe into a shell, a substitution) and its value
    can come from the line (vals.names, positional parameters, $_); None otherwise. info: runs() of each segment's
    words; vals: values(); fed: the segments whose output a pipe takes to something that reads it (feeders())."""
    readers = [j for j, r in enumerate(info) if r.stdin]
    var = False

    def from_line(text: str) -> bool:
        return any(
            "!" in (m.group(1) or "") or SPECIAL.fullmatch(m.group(2)) or m.group(2) in vals.names
            for m in PARAM.finditer(text)
        )

    for j, (seg, r) in enumerate(zip(segs, info)):
        w = _words(seg)
        if any(o == ">(" for t in seg.toks for o, _ in t.subs) or (w and w[0].raw == "coproc"):
            return "shell"
        prog = w[r.prog].value if r.prog < len(w) and w[r.prog].literal else None
        if prog in vals.defined and ((j and segs[j - 1].sep in PIPES) or _reads_input(seg)):
            return "shell"  # input into a function or alias defined in the line
        if readers:
            if seg.sep in PIPES and j + 1 < len(segs):  # a pipe into a group or compound with a reader in it
                nw = [t.raw for t in _words(segs[j + 1])]
                while nw and nw[0] in ("!", "time"):
                    nw = nw[1:]
                if ((nw and nw[0] in OPENERS) or (not nw and segs[j + 1].sep == "(")) and readers[-1] > j:
                    return "shell"
            if (not w or w[0].raw in CLOSERS) and _reads_input(seg):
                return "shell"  # `{ sh; } <<< x`, `( sh ) < f`: the input of a group reaches the reader inside it
        var = (
            var
            or (r.prog < len(w) and not w[r.prog].literal and PARAM.search(w[r.prog].raw) is not None)
            or any(from_line(text) for _, text, _ in r.strings)
            or any(from_line(t.raw) for c in r.cmds for t in c)
            or (r.stdin == "shell" and any(map(from_line, inputs(seg))))
            or any(from_line(c) for t in seg.toks for _, c in t.subs)
        )
    for k in fed:
        fw = _words(segs[k])
        if fw and fw[0].raw in CLOSERS:
            return "shell"
        var = var or any(from_line(t.raw) for t in segs[k].toks) or any(map(from_line, inputs(segs[k])))
    return "var" if var else None


def pipelines(segs: Iterable[Seg]) -> list[Seg]:
    """The segments with an empty one after a pipe dropped (`a |` newline `b` is one pipeline)."""
    out: list[Seg] = []
    for s in segs:
        if s.sep == "\n" and not any(t.kind != "comment" for t in s.toks) and out and out[-1].sep in PIPES:
            continue
        out.append(s)
    return out


# ---------- the files a line writes by name (guard.py: the reserved files) ----------

COPY_PROGS = ("cp", "mv", "ln", "install", "rsync", "ditto", "scp", "gcp", "gmv", "gln", "ginstall")
OPERAND_PROGS = ("rm", "grm", "rmdir", "unlink", "truncate", "gtruncate", "touch", "gtouch", "shred")
OWNER_PROGS = ("chmod", "chown", "chgrp", "chflags")  # their first operand is the mode, owner or flags
INPLACE_PROGS = ("sed", "gsed", "perl")
FIND_WRITERS = ("rm", "mv", "cp", "ln", "tee", "truncate", "chmod", "chown", "sed", "gsed", "perl", "dd", "shred")
# Short options that take a value (attached or the next word), per program; long ones that take one without "=".
TAKES = {
    "cp": "tS", "mv": "tS", "ln": "tS", "install": "mogtSBfNT", "rsync": "efBTM", "scp": "cFiJlOoPS",
    "truncate": "sr", "touch": "tdrA", "sed": "efl", "perl": "eE", "curl": "", "wget": "",
}  # fmt: skip
LONG_TAKES = (
    "--target-directory", "--suffix", "--mode", "--owner", "--group", "--exclude", "--include", "--filter",
    "--rsh", "--temp-dir", "--partial-dir", "--backup-dir", "--files-from", "--exclude-from", "--include-from",
    "--log-file", "--chmod", "--chown", "--compare-dest", "--copy-dest", "--link-dest", "--rsync-path",
    "--size", "--reference", "--date", "--expression", "--file", "--line-length",
)  # fmt: skip
# The rest of a cluster after these letters is their value (sed -i.bak, perl -i.orig -Mstrict -I lib).
REST = {"sed": "iI", "gsed": "iI", "perl": "iIMmxCdDlF0"}
CHMOD_MODE = re.compile(r"[-+=][rwxXstugoa]*")
REMOTE = re.compile(r"[^/]*[^/\\]:")  # host:path of scp and rsync


class Write:
    """A file a command names as what it writes (writes()). seg: the segment's index; tok: the word or redirection
    target (a Tok, possibly an attached value: piece()); names: the names of the sources a copy puts inside tok when
    it is a folder ("" for a source's content: `rsync a/ b`); tree: everything under it may change (rm -r, chmod -R, a
    recursive copy, and the source of mv)."""

    __slots__ = ("seg", "tok", "names", "tree")

    def __init__(self, seg: int, tok: Tok, names: tuple[str, ...] | None = None, tree: bool = False) -> None:
        self.seg, self.tok, self.names, self.tree = seg, tok, names, tree

    def __repr__(self) -> str:
        return f"Write({self.seg}, {self.tok.raw!r}, {self.names!r}, {self.tree})"


def piece(t: Tok, text: str) -> Tok:
    """A word made of part of t (an option's attached value, dd's of=…): literal when t is."""
    out = Tok("word", t.start)
    out.raw, out.value, out.literal, out.oneword, out.end = text, text, t.literal, t.oneword, t.end
    return out


def _args(
    w: Sequence[Tok], start: int, takes: str = "", rest: str = ""
) -> tuple[list[Tok], set[str], dict[str, list[Tok]]]:
    """(operands, the option letters and long names given, {option: its values}) of w[start:] (getopt style, options
    anywhere before `--`). takes: short options with a value (attached or the next word); rest: short options whose
    value is the rest of their cluster, maybe empty (sed -i.bak); LONG_TAKES take the next word without "="."""
    ops: list[Tok] = []
    flags: set[str] = set()
    vals: dict[str, list[Tok]] = {}
    i, end = start, False
    while i < len(w):
        t = w[i]
        v = t.value if t.literal else None
        if end or v is None or not v.startswith("-") or v == "-":
            ops.append(t)
            i += 1
            continue
        if v == "--":
            end = True
            i += 1
            continue
        if v.startswith("--"):
            name, eq, val = v.partition("=")
            flags.add(name)
            if eq:
                vals.setdefault(name, []).append(piece(t, val))
            elif name in LONG_TAKES and i + 1 < len(w):
                vals.setdefault(name, []).append(w[i + 1])
                i += 1
            i += 1
            continue
        for n, ch in enumerate(v[1:], 1):
            flags.add(ch)
            if ch in rest:
                vals.setdefault(ch, []).append(piece(t, v[n + 1 :]))
                break
            if ch in takes:
                if v[n + 1 :]:
                    vals.setdefault(ch, []).append(piece(t, v[n + 1 :]))
                elif i + 1 < len(w):
                    vals.setdefault(ch, []).append(w[i + 1])
                    i += 1
                break
        i += 1
    return ops, flags, vals


def _copy(k: int, name: str, w: Sequence[Tok], p: int) -> list[Write]:
    """What cp, mv, ln, install, rsync, ditto or scp writes: the last operand (or each -t DIR), with the sources'
    names for a folder; mv also its sources, whole."""
    base = name[1:] if name in ("gcp", "gmv", "gln", "ginstall") else name
    ops, flags, vals = _args(w, p + 1, TAKES.get(base, ""))
    if base == "install" and "d" in flags:
        return [Write(k, t) for t in ops]  # install -d DIR…: folders it creates
    dests = vals.get("t", []) + vals.get("--target-directory", [])
    srcs = ops if dests else ops[:-1]
    if not dests and len(ops) >= 2:
        dests = ops[-1:]
    if base in ("rsync", "scp"):  # host:path is elsewhere
        dests = [t for t in dests if not (t.literal and REMOTE.match(t.value))]
    contents = base in ("rsync", "cp", "ditto")  # `rsync a/ b`, `cp -R a/ b` (BSD): a's content goes into b
    names = tuple("" if contents and t.value.endswith("/") else PurePath(t.value).name for t in srcs)
    tree = base == "ditto" or (bool(flags & {"r", "R", "a", "--recursive", "--archive"}) and base != "ln")
    out = [Write(k, d, names, tree) for d in dests]
    if base == "mv":
        out += [Write(k, t, None, True) for t in srcs]  # a moved file (or folder) is gone from where it was
    return out


def _inplace(k: int, name: str, w: Sequence[Tok], p: int) -> list[Write]:
    """The files sed -i (gsed, BSD's -i '' and -I) or perl -i edits in place: its operands but the script (the first
    one, when no -e or -f gives it)."""
    base = "sed" if name == "gsed" else name
    words = list(w[: p + 1])
    for j in range(p + 1, len(w)):  # BSD sed -i '' / -i .bak: the extension is the next word
        prev = w[j - 1]
        ext = w[j].value == "" or (w[j].value.startswith(".") and "/" not in w[j].value)
        if base == "sed" and prev.literal and prev.value in ("-i", "-I") and w[j].literal and ext:
            continue
        words.append(w[j])
    ops, flags, vals = _args(words, p + 1, TAKES[base], REST[name])
    if not (flags & {"i", "--in-place"} or (base == "sed" and "I" in flags)):
        return []
    scripted = vals.keys() & {"e", "E", "f", "--expression", "--file"}
    return [Write(k, t) for t in (ops if scripted else ops[1:])]


def _operands(k: int, name: str, w: Sequence[Tok], p: int) -> list[Write]:
    """What rm, rmdir, unlink, truncate, touch, shred, chmod, chown, chgrp or chflags changes: its operands (but the
    mode or owner), whole with -r/-R."""
    base = name[1:] if name in ("grm", "gtruncate", "gtouch") else name
    ops, flags, vals = _args(w, p + 1, TAKES.get(base, ""))
    if base in OWNER_PROGS:
        mode = base == "chmod" and any(t.literal and CHMOD_MODE.fullmatch(t.value) for t in w[p + 1 :])
        if not mode and "--reference" not in vals:
            ops = ops[1:]
        tree = "R" in flags or "--recursive" in flags
    else:
        tree = base == "rm" and bool(flags & {"r", "R", "--recursive"})
    return [Write(k, t, None, tree) for t in ops]


def _find(k: int, w: Sequence[Tok], p: int) -> list[Write]:
    """The folders find changes: its starting points, when it has -delete or runs a writer with -exec, -ok…."""
    roots: list[Tok] = []
    j = p + 1
    while j < len(w) and w[j].literal and w[j].value in ("-H", "-L", "-P", "-E", "-X", "-s", "-x", "-d"):
        j += 1
    while j < len(w) and not (w[j].literal and (w[j].value[:1] in ("-", "(", "!") or w[j].value == "\\(")):
        roots.append(w[j])
        j += 1
    acts = False
    for i in range(j, len(w)):
        v = w[i].value if w[i].literal else ""
        if v == "-delete":
            acts = True
        elif v in ("-exec", "-execdir", "-ok", "-okdir") and i + 1 < len(w):
            acts = acts or PurePath(str(w[i + 1].value)).name in FIND_WRITERS
    return [Write(k, t) for t in roots] if acts else []  # under a reserved folder (not whatever holds one)


def writes(segs: Sequence[Seg], info: Sequence[Runs]) -> list[Write]:
    """The files the line names as what it writes, each with its segment: the targets of its output redirections
    (> >> >| &> <>, not a descriptor nor /dev/null, stdout or stderr), the operands of tee, the destination of cp, mv,
    ln, install, rsync, ditto and scp (and the sources of mv), the files of sed -i, gsed -i and perl -i, the operands of
    rm, rmdir, unlink, truncate, touch, shred, chmod, chown, chgrp and chflags, dd's of=, curl -o and wget -O, and the
    starting points of a find that deletes; behind the wrappers runs() skips (env, sudo, timeout, foreman lock … run).
    Words are given as they are, literal or not: the caller resolves them. What a command nested in a word or a string
    writes (sh -c, $(…)) is its own line's."""
    out: list[Write] = []
    for k, (seg, r) in enumerate(zip(segs, info)):
        toks = seg.toks
        for a, b in zip(toks, toks[1:]):
            if a.kind != "redir" or ">" not in a.raw or b.kind != "target":
                continue
            if a.raw.endswith("&") and (b.value.isdigit() or b.value == "-"):  # >&N, 2>&1, >&-: a descriptor
                continue
            if b.literal and b.value in SAFE_DEVICES:
                continue
            out.append(Write(k, b))
        w = _words(seg)
        p = r.prog
        if p >= len(w) or not w[p].literal:
            continue
        name = PurePath(w[p].value).name
        if name == "tee":
            out += [Write(k, t) for t in _args(w, p + 1)[0]]
        elif name in COPY_PROGS:
            out += _copy(k, name, w, p)
        elif name in INPLACE_PROGS:
            out += _inplace(k, name, w, p)
        elif name in OPERAND_PROGS or name in OWNER_PROGS:
            out += _operands(k, name, w, p)
        elif name == "dd":
            out += [Write(k, piece(t, str(t.value)[3:])) for t in w[p + 1 :] if str(t.value).startswith("of=")]
        elif name in ("curl", "wget"):
            vals = _args(w, p + 1, "o" if name == "curl" else "O")[2]
            out += [Write(k, t) for key in ("o", "O", "--output", "--output-document") for t in vals.get(key, [])]
        elif name == "find":
            out += _find(k, w, p)
    return out
