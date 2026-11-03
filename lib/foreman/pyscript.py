"""A Python program read from a heredoc that cannot start processes: its prose is text, not a command (guard.py).

The guard compares what an interpreter reads raw: a heredoc to python that only edits a file, with a
sentence inside that names a forbidden command, was refused. read() lets such a program through as text when an
allow-list gate says it cannot start a process, and keeps every one-word literal (a file name such as `.env`)
compared. The gate is by what the program may import and name, never by keywords:

- the command is `python…`/`pypy…` (no path, or one in a system folder: `./python3` may be any script) reading its
  program from stdin (`python3`, `python3 -`), with only flags that take no value (NO_VALUE_FLAGS) and no PYTHON*
  variable in front (PYTHONPATH, PYTHONSTARTUP…) (program());
- the body is at most MAX_BODY bytes and parses (ast) with the Python that runs the guard;
- no `__` anywhere (`__import__`, `().__class__…`, `__builtins__`) and no attribute that starts with `_`;
- it imports only from ALLOWED_MODULES, with absolute imports;
- no name, attribute or imported name is in FORBIDDEN or starts with FORBIDDEN_PREFIXES (os, sys, subprocess,
  importlib, system, Popen…: `glob.os.system` and `from pathlib import os` are refused by name), and no bare name is
  in BUILTINS (exec, eval, compile, getattr, vars, globals, input…; `re.compile` is an attribute and passes).

What passes the gate is compared with every comment and every string literal that holds whitespace (prose) blanked;
a one-word literal stays where it was, and an f-string with prose keeps its one-word literals on lines of their own.
What does not pass is compared raw, as before. guard.py decides the rest: one quoted heredoc, nothing that runs the
program's output, and no file the program may write that the line then runs (shell.may_run()).
"""

from __future__ import annotations

import ast
import contextlib
import io
import re
import tokenize
import warnings
from collections.abc import Iterator, Sequence
from typing import TYPE_CHECKING

from .shell import SYSTEM_BIN

if TYPE_CHECKING:
    from .shell import Tok

MAX_BODY = 64 * 1024  # bytes of program read; a longer body is compared raw
MAX_LINE = 256 * 1024  # characters of a command line (or of a string run inside it) whose programs are read
PYTHON = re.compile(r"(python|pypy)[\d.]*")
NO_VALUE_FLAGS = re.compile(r"-[IuBEsSOqbP]+")  # python3 -I -u -: the program still comes from stdin
ALLOWED_MODULES = frozenset(
    (
        *("re", "pathlib", "json", "textwrap", "difflib", "collections", "datetime", "string", "math"),
        *("itertools", "functools", "glob", "fnmatch", "tomllib"),
    )
)
BUILTINS = frozenset(  # builtins that run code, reach any attribute or read the terminal (as bare names)
    (
        *("exec", "eval", "compile", "getattr", "setattr", "delattr", "vars", "globals", "locals"),
        *("breakpoint", "input", "help", "license", "credits", "copyright"),
    )
)
FORBIDDEN = frozenset(  # as names, attributes and imported names
    (
        # modules an allowed module imports (glob.os, warnings.sys…) or that start processes or load code
        *("os", "sys", "subprocess", "importlib", "ctypes", "posix", "nt", "pty", "shutil", "socket"),
        *("multiprocessing", "signal", "builtins", "runpy", "code", "codeop", "pdb", "bdb", "cmd", "platform"),
        *("types", "inspect", "gc", "threading", "asyncio", "concurrent", "webbrowser", "pydoc", "imp"),
        *("pkgutil", "zipimport", "site", "sysconfig", "operator", "marshal", "pickle", "select", "selectors"),
        *("fcntl", "resource", "mmap", "posixpath", "ntpath", "genericpath", "warnings", "tempfile"),
        # calls that start a process, and the reflection that reaches an object by a string
        *("system", "startfile", "getoutput", "getstatusoutput", "check_call", "check_output"),
        *("get_field", "Formatter", "attrgetter", "methodcaller"),
    )
)
FORBIDDEN_PREFIXES = ("exec", "spawn", "popen", "fork", "posix_spawn")  # execv, spawnl, Popen, forkpty… (any case)
STRING_UNITS = ("FSTRING", "TSTRING")  # tokenize names of f-strings (3.12+) and t-strings (3.14+): _START … _END
FAILS = (SyntaxError, ValueError, MemoryError, RecursionError)


def program(w: Sequence[Tok], prog: int) -> bool:
    """True if the words (prog: the index of the program) run python reading its program from stdin, with flags
    that take no value, and no PYTHON* variable is set in front."""
    path = w[prog].value if prog < len(w) and w[prog].literal else ""
    if not PYTHON.fullmatch(path.rsplit("/", 1)[-1]) or ("/" in path and not path.startswith(SYSTEM_BIN)):
        return False  # ./python3 may be any script
    if any(re.match(r"PYTHON\w*\+?=", t.raw) for t in w[:prog]):
        return False
    rest = list(w[prog + 1 :])
    if rest and rest[-1].literal and rest[-1].value == "-":
        rest.pop()
    return all(t.literal and NO_VALUE_FLAGS.fullmatch(t.value) for t in rest)


def _forbidden(name: str, attr: bool) -> bool:
    """True if a name may not appear; attr: it is an attribute (`re.compile` is not the builtin compile)."""
    return name in FORBIDDEN or name.lower().startswith(FORBIDDEN_PREFIXES) or (not attr and name in BUILTINS)


def _names(tree: ast.AST) -> Iterator[tuple[str, bool]]:
    """Every name the program uses or imports, attribute names included, with whether it is an attribute. Raises
    ValueError on an import that is not allowed (relative, or outside ALLOWED_MODULES) or an attribute that starts
    with `_`."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            yield node.id, False
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):
                raise ValueError(node.attr)
            yield node.attr, True
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.ImportFrom) and node.level:
                raise ValueError("relative import")
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for mod in mods:
                parts = mod.split(".")
                if parts[0] not in ALLOWED_MODULES or any(p.startswith("_") for p in parts):
                    raise ValueError(mod)
            for a in node.names:
                yield from ((x, False) for x in (a.name, a.asname) if x)


def _parse(text: str, mode: str = "exec") -> ast.AST:
    """ast.parse without its warnings (an invalid escape would print a SyntaxWarning from inside the hook)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return ast.parse(text, mode=mode)


def gate(body: str) -> ast.AST | None:
    """The program's tree when it passes the allow-list gate (module doc), else None."""
    if len(body.encode("utf-8", "surrogatepass")) > MAX_BODY or "__" in body:
        return None
    try:
        tree = _parse(body)
        if any(_forbidden(n, attr) for n, attr in _names(tree)):
            return None
    except FAILS:
        return None
    return tree


def _words(node: ast.AST) -> list[str]:
    """The one-word str literals under a node."""
    return [
        c.value
        for c in ast.walk(node)
        if isinstance(c, ast.Constant) and isinstance(c.value, str) and c.value and not re.search(r"\s", c.value)
    ]


def _prose(text: str) -> bool:
    """True if a string unit (its source text) holds whitespace, as written or once its escapes are read."""
    if re.search(r"\s", text):
        return True
    try:
        node = _parse(text, "eval")
    except FAILS:
        return True  # cannot tell: blanked
    return any(
        isinstance(c, ast.Constant) and isinstance(c.value, (str, bytes)) and re.search(r"\s", str(c.value))
        for c in ast.walk(node)
    )


def _units(body: str) -> list[tuple[int, int]]:
    """The spans of the comments and string units (a literal; a whole f-string or t-string, with what is nested in
    it) of a program, in order. Raises what tokenize raises."""
    starts = [0]
    for line in body.splitlines(keepends=True):
        starts.append(starts[-1] + len(line))

    def at(pos: tuple[int, int]) -> int:
        row, col = pos
        return starts[row - 1] + col if row - 1 < len(starts) else len(body)

    spans: list[tuple[int, int]] = []
    depth, first = 0, 0
    for tok in tokenize.generate_tokens(io.StringIO(body).readline):
        kind = tokenize.tok_name.get(tok.type, "")
        if kind.startswith(STRING_UNITS) and kind.endswith("_START"):
            first = at(tok.start) if not depth else first
            depth += 1
        elif kind.startswith(STRING_UNITS) and kind.endswith("_END"):
            depth -= 1
            if not depth:
                spans.append((first, at(tok.end)))
        elif not depth and tok.type in (tokenize.COMMENT, tokenize.STRING):
            spans.append((at(tok.start), at(tok.end)))
    return spans


def blank(body: str) -> str | None:
    """The program with its prose blanked: every comment removed, and every string unit that holds whitespace
    replaced by "" (the one-word literals nested in such an f-string follow, one per line, at the end). None if
    tokenize cannot read it."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            spans = _units(body)
    except (tokenize.TokenError, *FAILS):
        return None
    parts: list[str] = []
    kept: list[str] = []
    end = 0
    for a, b in spans:
        parts.append(body[end:a])
        end, text = b, body[a:b]
        if text.startswith("#"):
            continue
        if not _prose(text):
            parts.append(text)
            continue
        if re.match(r"[A-Za-z]*[fFtT]", text):  # an f-string or a t-string: its nested literals stay seen
            with contextlib.suppress(*FAILS):
                kept += [repr(x) for x in _words(_parse(text, "eval"))]
        parts.append('""')
    parts.append(body[end:])
    return "".join(parts) + "".join(f"\n{x}" for x in kept)


def read(body: str) -> tuple[str, set[str]] | None:
    """(what the guard compares of a program that passes the gate: its text with the prose blanked, and its one-word
    str literals: the files it may write), or None when it does not pass (the caller compares it raw)."""
    tree = gate(body)
    text = blank(body) if tree is not None else None
    return None if tree is None or text is None else (text, set(_words(tree)))
