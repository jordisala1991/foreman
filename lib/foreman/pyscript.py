"""A Python program read from a heredoc that cannot start processes: its prose is text, not a command (guard.py).

The guard compares what an interpreter reads raw: a heredoc to python that only edits a file, with a
sentence inside that names a forbidden command, was refused. read() lets such a program through as text when an
allow-list gate says it cannot start a process, and keeps every one-word literal (a file name such as `.env`)
compared. The gate is by what the program may import and name, never by keywords:

- the command is `python…`/`pypy…` (no path, or one in a system folder: `./python3` may be any script) reading its
  program from stdin (`python3`, `python3 -`), with only flags that take no value (NO_VALUE_FLAGS) and no PYTHON*
  variable in front (PYTHONPATH, PYTHONSTARTUP…) (program());
- the body is at most MAX_BODY bytes and parses (ast) with the Python that runs the guard;
- no identifier holds `__` (`__import__`, `().__class__…`, `__builtins__`; read from the tree, so a full-width `＿`
  counts: Python reads it as `_`) and no attribute starts with `_`;
- it imports only from ALLOWED_MODULES, with absolute imports;
- an attribute of an imported module is a public member of it (members(): its `__all__`, else its names without
  `_`) that is not a module, or a submodule the program imports by name; the same for `from M import X`; a module's
  name is used only to read its attributes (`m = re` would hide it);
- no name, attribute or imported name is in FORBIDDEN or starts with FORBIDDEN_PREFIXES (os, sys, subprocess,
  importlib, system, Popen…), no bare name is in BUILTINS (exec, eval, compile, getattr, vars, globals, input…), and
  a BUILTINS attribute is only a module's member (`re.compile` passes, `re.enum.bltns.eval` does not).

What passes the gate is compared with every comment and every string literal that holds whitespace (prose) blanked;
a one-word literal stays where it was, and an f-string with prose keeps its one-word literals on lines of their own,
as does each word with a `/` of a prose literal (a path with a space in it). What does not pass is compared raw, as
before. read() also gives the files the program writes (writes()); guard.py compares raw a program that writes one
it cannot name, one that may shadow an allowed module, or one the line then runs (shell.may_run()), and it needs one
quoted heredoc and nothing that runs the program's output.
"""

from __future__ import annotations

import ast
import contextlib
import io
import re
import tokenize
import warnings
from collections.abc import Iterator, Sequence
from functools import cache
from importlib import import_module
from types import ModuleType
from typing import TYPE_CHECKING, NamedTuple

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
PATHS = frozenset(("Path", "PurePath", "PosixPath", "PurePosixPath", "WindowsPath", "PureWindowsPath"))
# Methods that write the object they are called on (a Path), and those that also write their one argument: a method
# of the same name called with another number of arguments is not a Path's (str.replace, dict.copy)
WRITES_SELF = frozenset(
    ("write_text", "write_bytes", "touch", "unlink", "mkdir", "rmdir", "chmod", "lchmod", "symlink_to", "hardlink_to")
)
WRITES_BOTH = frozenset(("rename", "replace", "copy", "move", "copy_into", "move_into", "link_to"))
WRITE_MODE = re.compile(r"[wax+]")


class Program(NamedTuple):
    """What the guard reads of a program that passes the gate: its text with the prose blanked, the files it writes
    as written (None: one it cannot name) and its one-word str literals."""

    text: str
    writes: frozenset[str] | None
    words: frozenset[str]


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


@cache
def members(mod: str) -> frozenset[str]:
    """The public members of an allowed module that are not modules (its `__all__`, else its names without `_`), in
    the Python that runs the guard. Raises ImportError when it has no such module (tomllib before 3.11)."""
    m = import_module(mod)
    names = getattr(m, "__all__", None) or [n for n in dir(m) if not n.startswith("_")]
    return frozenset(n for n in names if isinstance(n, str) and not isinstance(getattr(m, n, None), ModuleType))


def _identifiers(node: ast.AST) -> Iterator[str]:
    """The identifiers a node holds: its str fields, but a constant's value and a type comment."""
    if isinstance(node, (ast.Constant, ast.TypeIgnore)):
        return
    for _, value in ast.iter_fields(node):
        if isinstance(value, str):
            yield value
        elif isinstance(value, list):
            yield from (x for x in value if isinstance(x, str))


def _modules(tree: ast.AST) -> tuple[dict[str, str], set[str]]:
    """({a name an `import` binds: its module}, every dotted module the program imports by name, with its parents)."""
    bound: dict[str, str] = {}
    dotted: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                parts = a.name.split(".")
                dotted.update(".".join(parts[: i + 1]) for i in range(len(parts)))
                bound[a.asname or parts[0]] = a.name if a.asname else parts[0]
    return bound, dotted


def _module_of(node: ast.AST, bound: dict[str, str], dotted: set[str]) -> str | None:
    """The module an expression is: an imported module's name, or a submodule of it the program imports by name."""
    if isinstance(node, ast.Name):
        return bound.get(node.id)
    if isinstance(node, ast.Attribute):
        mod = _module_of(node.value, bound, dotted)
        return f"{mod}.{node.attr}" if mod and f"{mod}.{node.attr}" in dotted else None
    return None


def _names(tree: ast.AST) -> Iterator[tuple[str, bool]]:
    """Every name the program uses or imports, attribute names included, with whether it is an attribute. Raises
    ValueError on an import that is not allowed (relative, or outside ALLOWED_MODULES), an identifier with `__`, an
    attribute that starts with `_`, a module's attribute or an imported name that is not a member of it (members()),
    a module's name used other than to read its attributes, or a BUILTINS attribute of anything but a module."""
    bound, dotted = _modules(tree)
    read = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)}
    for node in ast.walk(tree):
        if any("__" in x for x in _identifiers(node)):
            raise ValueError("__")
        if isinstance(node, ast.Name):
            if node.id in bound and id(node) not in read:
                raise ValueError(node.id)  # m = re; m.enum: the module under another name
            yield node.id, False
        elif isinstance(node, ast.Attribute):
            mod = _module_of(node.value, bound, dotted)
            sub = f"{mod}.{node.attr}" in dotted
            if node.attr.startswith("_") or (mod is None and node.attr in BUILTINS):
                raise ValueError(node.attr)
            if mod is not None and not sub and node.attr not in members(mod):
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
                if isinstance(node, ast.ImportFrom) and a.name not in members(mods[0]):
                    raise ValueError(a.name)  # from json import decoder; from M import * too
                yield from ((x, False) for x in (a.name, a.asname) if x)


def _parse(text: str, mode: str = "exec") -> ast.AST:
    """ast.parse without its warnings (an invalid escape would print a SyntaxWarning from inside the hook)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return ast.parse(text, mode=mode)


def gate(body: str) -> ast.AST | None:
    """The program's tree when it passes the allow-list gate (module doc), else None."""
    if len(body.encode("utf-8", "surrogatepass")) > MAX_BODY:
        return None
    try:
        tree = _parse(body)
        if any(_forbidden(n, attr) for n, attr in _names(tree)):
            return None
    except (*FAILS, ImportError):
        return None
    return tree


def words(node: ast.AST) -> list[str]:
    """The one-word str literals under a node."""
    return [
        c.value
        for c in ast.walk(node)
        if isinstance(c, ast.Constant) and isinstance(c.value, str) and c.value and not re.search(r"\s", c.value)
    ]


def _kept(node: ast.AST) -> list[str]:
    """What stays seen of a prose unit: its one-word str literals (nested in an f-string) and each word with a `/`
    of the others (`"/opt/My App/x"`: a path with a space)."""
    out: list[str] = []
    for c in ast.walk(node):
        if isinstance(c, ast.Constant) and isinstance(c.value, str) and c.value:
            words = c.value.split()
            out += words if len(words) == 1 and words[0] == c.value else [x for x in words if "/" in x]
    return out


def _path(node: ast.AST, paths: set[str], seen: frozenset[str], known: dict[str, list[ast.expr]]) -> set[str] | None:
    """The files an expression may be, when they are known: a str literal, `Path(literals…)` (paths: the names a
    Path class goes by), or a name the program only ever assigns such values (known); else None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.Call) and not node.keywords and all(isinstance(a, ast.Constant) for a in node.args):
        f = node.func
        name = f.id if isinstance(f, ast.Name) else ""
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
            name = f"{f.value.id}.{f.attr}"
        if name in paths and all(isinstance(a.value, str) for a in node.args):  # type: ignore[attr-defined]
            return {"/".join(a.value for a in node.args) or "."}  # type: ignore[attr-defined]
        return None
    if isinstance(node, ast.Name) and node.id in known and node.id not in seen:
        out: set[str] = set()
        for v in known[node.id]:
            got = _path(v, paths, seen | {node.id}, known)
            if got is None:
                return None
            out |= got
        return out
    return None


def writes(tree: ast.AST) -> frozenset[str] | None:
    """The files a program that passed the gate writes, as written (relative to its folder), or None when it may
    write one it cannot name: `open` with a mode that writes (or one not written as a literal), a Path's method that
    writes (WRITES_SELF, WRITES_BOTH), or either of them taken without being called."""
    binds: dict[str, int] = {}  # how many times each name is bound, any way (an assignment, an import, a def…)
    known: dict[str, list[ast.expr]] = {}  # the values of the plain assignments to a name
    imported: dict[str, str] = {}  # a name an import binds -> what it is (pathlib.Path, pathlib)
    calls: dict[int, ast.Call] = {}
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names = [node.id]
        elif isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            for t in node.targets if isinstance(node, ast.Assign) else [node.target]:
                if isinstance(t, ast.Name):
                    known.setdefault(t.id, []).append(node.value)
        elif isinstance(node, ast.Call):
            calls[id(node.func)] = node
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                name = a.asname or a.name.split(".")[0]
                names.append(name)
                imported[name] = f"{node.module}.{a.name}" if isinstance(node, ast.ImportFrom) else a.name
        elif isinstance(node, (ast.arg, ast.ExceptHandler, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names = [x for x in (getattr(node, "arg", None), getattr(node, "name", None)) if x]
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names = list(node.names)
        for n in names:
            binds[n] = binds.get(n, 0) + 1
    known = {n: v for n, v in known.items() if binds.get(n) == len(v)}
    once = {n: what for n, what in imported.items() if binds.get(n) == 1}
    paths = {n for n, what in once.items() if what in {f"pathlib.{p}" for p in PATHS}}
    paths |= {f"{n}.{p}" for n, what in once.items() if what == "pathlib" for p in PATHS}
    out: set[str] = set()

    def add(node: ast.AST) -> bool:
        got = _path(node, paths, frozenset(), known)
        out.update(got or ())
        return got is not None

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "open":  # the builtin, or what the program bound to it
            attr, obj = "", None
        elif isinstance(node, ast.Attribute) and node.attr in (*WRITES_SELF, *WRITES_BOTH, "open"):
            attr, obj = node.attr, node.value
        else:
            continue
        call = None if obj is None and "open" in binds else calls.get(id(node))
        if call is None or any(isinstance(a, ast.Starred) for a in call.args) or None in [k.arg for k in call.keywords]:
            return None  # o = open, w = p.write_text: a writer taken to be called elsewhere; f(*a), f(**k)
        kw = {k.arg: k.value for k in call.keywords}
        args = list(call.args)
        if obj is None:  # open(file, mode): the file is the object
            obj = args.pop(0) if args else kw.get("file")
        if attr in ("", "open"):
            m = kw.get("mode", args[0] if args else None)
            if m is None or (isinstance(m, ast.Constant) and not WRITE_MODE.search(str(m.value))):
                continue  # it reads
        elif attr in WRITES_BOTH:
            dest = args[0] if len(args) == 1 else (kw.get("target") if not args else None)
            if dest is None:
                continue  # str.replace(a, b), dict.copy(), datetime.replace(year=…): not a Path's
            if not add(dest):
                return None
        if obj is None or not add(obj):
            return None
    return frozenset(out)


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
    replaced by "" (what stays seen of it, _kept(), follows one per line at the end). None if tokenize cannot read
    it."""
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
        with contextlib.suppress(*FAILS):
            kept += [repr(x) for x in _kept(_parse(text, "eval"))]
        parts.append('""')
    parts.append(body[end:])
    return "".join(parts) + "".join(f"\n{x}" for x in kept)


def read(body: str) -> Program | None:
    """What the guard reads of a program that passes the gate (Program), or None when it does not pass (the caller
    compares it raw)."""
    tree = gate(body)
    text = blank(body) if tree is not None else None
    if tree is None or text is None:
        return None
    return Program(text, writes(tree), frozenset(words(tree)))
