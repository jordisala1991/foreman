"""`foreman selftest`: static checks of the package and the test suite (tests/) on fake projects.

Each test builds its own project in a temporary directory (under $TMPDIR) with its own registry and state root, so
the real ~/.config/foreman, ~/.local/state/foreman and every real repo stay untouched. No GPU, no network: `gh` is
a fake (tests/fake_gh.py) and the remotes are bare repos in the temporary directory. No test reads another product's
files on this machine. The static checks include the version and release checks of release.py.

The tests run by class in parallel worker processes (`-j N`; by default the machine's cores divided by the selftest
runs alive on it, at least 2: RunSlot), the golden corpus's classes and then the classes with more tests first; each
worker has its own $TMPDIR subtree, so no two tests share a folder, a port or a lock, and the parent merges the
results into one summary in unittest's format. `-j 1` runs everything in this process, one test after another.

The golden corpus (tests/test_golden.py, tests/golden.py) compares foreman's outputs and state with tests/golden/
(and dev/tests/test_golden_dev.py its developer tool's with dev/tests/golden/);
`--update-golden` runs only it and rewrites those files (update_golden_files).

The code quality checks (quality.py: ruff, ruff format, pyright at the versions of pyproject.toml) run in a thread of
the parent while the workers run the tests: `--quality=auto|require|off`, `--quality-only` (the static and quality
checks without the tests), `--fix` (ruff's safe fixes, then `--quality-only`). Beside them, in another thread, the
hooks module's check (mods.py: `claude plugin validate --strict` and `claude plugin test`): `--mods=auto|require|off`,
same meaning; also run by `--quality-only`. The static checks read tests/mod/contract.ts (mod_contract).
`--coverage` runs the suite under coverage.py instead (cover.py): the total and a table per module.
"""

from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import re
import selectors
import shutil
import subprocess
import sys
import tempfile
import time
import tokenize
import traceback
import unittest
import warnings
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import mods as mods_checks
from . import quality as quality_checks
from . import release

if TYPE_CHECKING:
    from .util import PathLike

ROOT = Path(__file__).resolve().parents[2]


# Python code in a file: a statement at the start of a line, or a docstring
PYTHON_LINE = re.compile(r"^\s*(import \w|from [\w.]+ import |def \w+\(|class \w+[(:]|\"\"\"|\'\'\')", re.M)
# a Python interpreter run on the launcher: sys.executable whose next argument is BIN, ".../bin/foreman" or
# "bin" / "foreman" (also in a shell line of an f-string: "{sys.executable}" "{BIN}")
RUNS_LAUNCHER = re.compile(
    r"sys\.executable\}?[\"']?,?\s*(?:str\()?[^,\]\n#]*"
    r"(?:\bBIN\b|bin/foreman|[\"']bin[\"']\s*/\s*[\"']foreman)"
)


def launcher_checks() -> list[str]:
    """bin/foreman is a plain POSIX sh launcher (lib/main.py is the Python entry), and no code in lib/ or tests/ runs
    it through a Python interpreter."""
    problems = []
    launcher = ROOT / "bin/foreman"
    text = launcher.read_text()
    if not text.startswith("#!/bin/sh\n") or PYTHON_LINE.search(text):
        problems.append("bin/foreman: a POSIX sh launcher only (#!/bin/sh, no Python in it; the entry is lib/main.py)")
    r = subprocess.run(["sh", "-n", str(launcher)], capture_output=True, text=True, check=False)
    if r.returncode:
        problems.append(f"sh -n bin/foreman: {r.stderr.strip()}")
    for f in (
        sorted((ROOT / "lib").rglob("*.py"))
        + sorted((ROOT / "tests").rglob("*.py"))
        + sorted((ROOT / "dev").rglob("*.py"))
    ):
        if f.resolve() == Path(__file__).resolve():  # the pattern's own description
            continue
        src = f.read_text()
        for m in RUNS_LAUNCHER.finditer(src):
            line = src.count("\n", 0, m.start()) + 1
            problems.append(
                f"{f.relative_to(ROOT)}:{line}: runs bin/foreman with Python; run the launcher itself, "
                "or lib/main.py (helpers.ENTRY) with sys.executable"
            )
    return problems


IMPORTS_DEV = re.compile(r"^\s*(?:from|import)\s+foreman_dev\b", re.M)


def dev_checks() -> list[str]:
    """The developer tool (dev/) stays out of the product: nothing in lib/ or hooks/ imports foreman_dev."""
    problems = []
    for f in sorted((ROOT / "lib").rglob("*.py")) + sorted((ROOT / "hooks").rglob("*.py")):
        m = IMPORTS_DEV.search(f.read_text())
        if m:
            line = m.string.count("\n", 0, m.start()) + 1
            problems.append(
                f"{f.relative_to(ROOT)}:{line}: imports foreman_dev, the developer tool (dev/, not the product)"
            )
    return problems


# The oldest Python foreman runs on, read from bin/foreman's min_python (the one place it is written); py39_checks
# keeps the code to it, and its lists of newer names assume it is 3.9 (the test says so if min_python moves)
_MIN_PYTHON = re.search(r"^min_python=(\d+)\.(\d+)\b", (ROOT / "bin/foreman").read_text(), re.M)
if _MIN_PYTHON is None:
    raise RuntimeError("bin/foreman has no min_python=X.Y line")
OLDEST = (int(_MIN_PYTHON.group(1)), int(_MIN_PYTHON.group(2)))
# lib/main.py checks the version before importing anything, so it must parse on older Pythons still
ENTRY_OLDEST = (3, 6)
# names that exist only in newer Pythons (module: names, through `module.name` or `from module import name`; None:
# the whole module)
NEWER_NAMES = {
    "datetime": {"UTC"},
    "enum": {"StrEnum"},
    "itertools": {"pairwise", "batched"},
    "hashlib": {"file_digest"},
    "contextlib": {"chdir", "aclosing"},
    "sys": {"orig_argv", "stdlib_module_names", "exception"},
    "asyncio": {"TaskGroup", "timeout", "Runner"},
    "tomllib": None,
    "typing": {
        "TypeAlias",
        "ParamSpec",
        "Concatenate",
        "TypeGuard",
        "Self",
        "Never",
        "LiteralString",
        "Required",
        "NotRequired",
        "assert_never",
        "assert_type",
        "reveal_type",
        "override",
        "TypeVarTuple",
        "Unpack",
    },
}
NEWER_BUILTINS = {"aiter", "anext", "ExceptionGroup", "BaseExceptionGroup", "EncodingWarning"}
NEWER_METHODS = {"hardlink_to", "enterContext", "enterClassContext", "add_note", "bit_count", "assertNoLogs"}
# keyword arguments added after 3.9: (function or method name, keyword)
NEWER_KEYWORDS = {
    ("zip", "strict"),
    ("dataclass", "slots"),
    ("dataclass", "kw_only"),
    ("Popen", "process_group"),
    ("run", "process_group"),
    ("rmtree", "onexc"),
    ("realpath", "strict"),
    ("glob", "root_dir"),
    ("relative_to", "walk_up"),
    ("NamedTemporaryFile", "delete_on_close"),
}
TRIPLE_QUOTES = ('"' * 3, "'" * 3)


def py_sources() -> list[Path]:
    """Every Python file of foreman, its tests and their helpers, and its developer tool's (dev/, with its script
    dev/foreman-dev; bin/ is sh: launcher_checks)."""
    return (
        sorted((ROOT / "lib").rglob("*.py"))
        + sorted((ROOT / "tests").rglob("*.py"))
        + sorted((ROOT / "hooks").rglob("*.py"))
        + sorted((ROOT / "dev").rglob("*.py"))
        + [f for f in (ROOT / "dev/foreman-dev",) if f.is_file()]
    )


def py39_checks(files: Iterable[Path] | None = None) -> list[str]:
    """The code runs on Python 3.9 (OLDEST), checked without one: `ast.parse(feature_version=(3, 9))` (match, except*,
    type statements and type parameters), and what feature_version lets through, found in the tree: f-strings that
    reuse their quote, hold a backslash or a comment in a replacement field (3.12), `a[*b]` (3.11), `a[x := 1]`
    (3.10), an annotation `X | Y` evaluated at run time (a module without `from __future__ import annotations`), and
    the standard library's newer names, builtins, methods and keywords listed above (tomllib: through toml_reader).
    What it misses: behaviour that changed under the same name (datetime.fromisoformat reads more forms since 3.11,
    argparse's help texts, docstrings dedented since 3.13), `int | str` outside an annotation (isinstance), and any
    newer API not in the lists. The suite on a 3.9 (`selftest --python /usr/bin/python3`) catches those it runs."""
    problems = []
    for f in files or py_sources():
        rel = f.relative_to(ROOT) if f.is_relative_to(ROOT) else f
        src = f.read_text()
        version = ENTRY_OLDEST if f == ROOT / "lib/main.py" else OLDEST
        try:
            tree = ast.parse(src, str(f), feature_version=version)
        except SyntaxError as e:
            problems.append(f"{rel}:{e.lineno}: not Python {'.'.join(map(str, version))}: {e.msg}")
            continue
        if rel.parts[:3] == ("lib", "foreman", "_tomli"):
            continue  # vendored as it is upstream (it parses on 3.9; tomli supports 3.8+)
        problems += [f"{rel}:{line}: {why}" for line, why in newer_than_39(tree, src, f.name)]
    return problems


def newer_than_39(tree: ast.Module, src: str, name: str) -> list[tuple[int, str]]:
    """(line, why) for each construct of `tree` that feature_version lets through but Python 3.9 rejects or lacks."""
    data = src.encode()  # ast's columns count UTF-8 bytes
    starts = [0]
    for ln in data.splitlines(True):
        starts.append(starts[-1] + len(ln))

    def text(node: Any) -> str:  # any ast node with a position
        return data[
            starts[node.lineno - 1] + node.col_offset : starts[node.end_lineno - 1] + node.end_col_offset
        ].decode()

    def has_comment(expr: str) -> bool:
        try:
            return any(
                t.type == tokenize.COMMENT for t in tokenize.generate_tokens(io.StringIO(f"({expr}\n)").readline)
            )
        except (tokenize.TokenError, SyntaxError):
            return False

    parts: list[
        tuple[tuple[int, int], tuple[int, int], str]
    ] = []  # 3.12+: ((row, byte col) start, end, quote) of each f-string part, innermost last

    def fstring_parts() -> list[tuple[tuple[int, int], tuple[int, int], str]]:
        """Each f-string part's span and quote (implicitly concatenated parts have their own), from the tokens."""
        if not parts and sys.version_info >= (3, 12):  # the FSTRING_* tokens are 3.12+
            lines, stack = src.split("\n"), []

            def at(pos: tuple[int, int]) -> tuple[int, int]:
                return pos[0], len(lines[pos[0] - 1][: pos[1]].encode())

            try:
                for t in tokenize.generate_tokens(io.StringIO(src).readline):
                    if t.type == tokenize.FSTRING_START:
                        rest = t.string.lstrip("rRfFbBuU")
                        stack.append((at(t.start), rest if rest in TRIPLE_QUOTES else rest[:1]))
                    elif t.type == tokenize.FSTRING_END and stack:
                        begin, quote = stack.pop()
                        parts.append((begin, at(t.end), quote))
            except (tokenize.TokenError, SyntaxError):
                pass
            parts.append(((0, 0), (0, 0), ""))  # never empty: the source is tokenised once
        return parts

    def quote_around(v: ast.FormattedValue) -> str | None:
        """The quote of the f-string part holding the field `v` (the innermost one)."""
        here, quote, found = (v.lineno, v.col_offset), None, None
        for begin, end, q in fstring_parts():
            if begin <= here < end and (found is None or begin > found):
                found, quote = begin, q
        return quote

    def call_name(func: ast.expr) -> str:
        return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")

    future = any(
        isinstance(n, ast.ImportFrom) and n.module == "__future__" and any(a.name == "annotations" for a in n.names)
        for n in tree.body
    )
    out = []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.JoinedStr) and sys.version_info >= (3, 12):  # exact positions inside f-strings
            whole = text(node)
            first = whole[len(re.match(r"[A-Za-z]*", whole).group(0)) :][:3]
            first = first if first in TRIPLE_QUOTES else first[:1]
            for v in node.values:
                if isinstance(v, ast.FormattedValue):
                    quote = quote_around(v) or first  # the quote of the part holding the field
                    inner, field = text(v.value), text(v)  # the expression; the whole {…} field
                    if quote in inner or "\\" in inner or ("#" in field and has_comment(field[1:-1])):
                        out.append(
                            (
                                v.value.lineno,
                                "an f-string field with its own quote, a backslash or a comment (Python 3.12+)",
                            )
                        )
        elif isinstance(node, ast.Subscript):
            sl = node.slice
            if isinstance(sl, ast.Starred) or (
                isinstance(sl, ast.Tuple) and any(isinstance(e, ast.Starred) for e in sl.elts)
            ):
                out.append((line, "a starred subscript a[*b] (Python 3.11+)"))
            elif isinstance(sl, ast.NamedExpr) and not text(node)[len(text(node.value)) :].lstrip()[
                1:
            ].lstrip().startswith("("):
                out.append((line, "an unparenthesized := in a subscript (Python 3.10+)"))
        elif not future and isinstance(node, (ast.AnnAssign, ast.arg, ast.FunctionDef, ast.AsyncFunctionDef)):
            ann = node.returns if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else node.annotation
            if ann is not None and any(isinstance(n, ast.BinOp) and isinstance(n.op, ast.BitOr) for n in ast.walk(ann)):
                out.append(
                    (
                        line,
                        "an annotation X | Y evaluated at run time (Python 3.10+): Optional/Union, or "
                        "`from __future__ import annotations`",
                    )
                )
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.attr in (NEWER_NAMES.get(node.value.id) or ()):
                out.append((line, f"{node.value.id}.{node.attr} is newer than Python 3.9"))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for m in mods:
                if m in NEWER_NAMES and NEWER_NAMES[m] is None and name != "toml_reader.py":
                    out.append(
                        (
                            line,
                            f"import {m}: newer than Python 3.9"
                            + (" (use foreman.toml_reader)" if m == "tomllib" else ""),
                        )
                    )
            if isinstance(node, ast.ImportFrom):
                out += [
                    (line, f"{node.module}.{a.name} is newer than Python 3.9")
                    for a in node.names
                    if a.name in (NEWER_NAMES.get(node.module or "") or ())
                ]
        elif isinstance(node, ast.Name) and node.id in NEWER_BUILTINS and isinstance(node.ctx, ast.Load):
            out.append((line, f"{node.id} is newer than Python 3.9"))
        elif isinstance(node, ast.Call):
            fn = call_name(node.func)
            if isinstance(node.func, ast.Attribute) and fn in NEWER_METHODS:
                out.append((line, f".{fn}() is newer than Python 3.9"))
            out += [
                (line, f"{fn}({k.arg}=…) is newer than Python 3.9")
                for k in node.keywords
                if (fn, k.arg) in NEWER_KEYWORDS
            ]
    return sorted(set(out))


MOD_CONTRACT = ROOT / "tests/mod/contract.ts"
MOD_CONTRACT_KEYS = {"name", "op", "python", "engine", "stdin", "run", "status"}


def mod_contract(path: PathLike = MOD_CONTRACT) -> list[dict[str, Any]]:
    """The cases of tests/mod/contract.ts: leading `//` comment lines, then `export default ` and a JSON list of
    objects with MOD_CONTRACT_KEYS (the module's tests import it; the golden corpus reads it here). ValueError when
    the file is anything else."""
    lines = Path(path).read_text().splitlines(keepends=True)
    while lines and lines[0].lstrip().startswith("//"):
        lines.pop(0)
    text = "".join(lines)
    if not text.startswith("export default "):
        raise ValueError("does not start with `export default ` after its comment lines")
    cases = json.loads(text[len("export default ") :])
    if not isinstance(cases, list) or not cases:
        raise ValueError("is not a non-empty JSON list")
    for n, c in enumerate(cases):
        if not isinstance(c, dict) or set(c) != MOD_CONTRACT_KEYS:
            raise ValueError(f"case {n}: needs exactly the keys {sorted(MOD_CONTRACT_KEYS)}")
    return cases


def static_checks() -> list[str]:
    problems = []
    try:
        mod_contract()
    except (OSError, ValueError) as e:
        problems.append(f"contract {MOD_CONTRACT.relative_to(ROOT)}: {e}")
    for f in py_sources():
        try:
            compile(f.read_text(), str(f), "exec")
        except SyntaxError as e:
            problems.append(f"compile {f.relative_to(ROOT)}: {e}")
    problems += py39_checks()
    problems += launcher_checks()
    problems += dev_checks()
    for f in sorted(ROOT.rglob("*.sh")):
        r = subprocess.run(["bash", "-n", str(f)], capture_output=True, text=True, check=False)
        if r.returncode:
            problems.append(f"bash -n {f.relative_to(ROOT)}: {r.stderr.strip()}")
    for f in [
        ROOT / ".claude-plugin/plugin.json",
        ROOT / "hooks/hooks.json",
        ROOT / "hooks/classic.json",
        *sorted((ROOT / "lib").rglob("*.json")),
        *sorted((ROOT / "panel").glob("*.json")),
    ]:
        try:
            json.loads(f.read_text())
        except (OSError, ValueError) as e:
            problems.append(f"json {f.relative_to(ROOT)}: {e}")
    langs = [json.loads(f.read_text()) for f in sorted((ROOT / "panel").glob("strings.*.json"))]
    if langs and any(set(x) != set(langs[0]) for x in langs):
        problems.append("panel/strings.*.json: the languages have different keys")
    texts = {f.name: json.loads(f.read_text()) for f in sorted((ROOT / "lib/foreman/i18n").glob("*.json"))}
    every = set().union(*texts.values()) if texts else set()
    for name, d in texts.items():
        if set(d) != every:
            problems.append(f"lib/foreman/i18n/{name}: keys missing from it: {sorted(every - set(d))}")
    node = shutil.which("node")
    if node:  # the panel's script parses (skipped without node)
        r = subprocess.run([node, "--check", str(ROOT / "panel/app.js")], capture_output=True, text=True, check=False)
        if r.returncode:
            problems.append(f"node --check panel/app.js: {r.stderr.strip()[:300]}")
    # foreman's skills and agents, and the agent types of the examples (examples/*/.claude/agents/, docs/extending.md)
    headed = [f for d in ("skills", "agents") for f in sorted((ROOT / d).rglob("*.md"))]
    headed += sorted((ROOT / "examples").rglob(".claude/agents/*.md"))
    for f in headed:
        text = f.read_text()
        if (
            not text.startswith("---\n")
            or "\nname:" not in text.split("\n---", 2)[0] + "\n"
            or "description:" not in text.split("\n---", 2)[0]
        ):
            problems.append(f"frontmatter {f.relative_to(ROOT)}: needs name and description")
    problems += release.check(ROOT)  # one version source, CHANGELOG section, tags and the release branch
    return problems


# The test folders: foreman's (tests/) and its developer tool's (dev/tests/, dev/foreman-dev: not part of the product,
# tested here so it keeps working). Their module names never repeat: each folder is a top level of its own.
TEST_DIRS = (ROOT / "tests", ROOT / "dev/tests")


def test_paths() -> None:
    """The test folders on sys.path, and dev/ (the developer tool's package, foreman_dev), once each."""
    for d in (ROOT / "dev", *reversed(TEST_DIRS)):
        if str(d) not in sys.path:
            sys.path.insert(0, str(d))


def discover(pattern: str | None = None) -> unittest.TestSuite:
    test_paths()
    loader = unittest.TestLoader()
    if pattern:
        loader.testNamePatterns = [f"*{pattern}*"]
    return unittest.TestSuite(
        loader.discover(str(d), pattern="test_*.py", top_level_dir=str(d)) for d in TEST_DIRS if d.is_dir()
    )


def flatten(suite: unittest.TestSuite) -> Iterator[unittest.TestCase]:
    for t in suite:
        if isinstance(t, unittest.TestSuite):
            yield from flatten(t)
        else:
            yield t


def summary(
    ok: bool,
    run_: int,
    failures: int,
    errors: int,
    skipped: int,
    problems: list[str],
    t0: float,
    quality: quality_checks.Report | None = None,
    mods: quality_checks.Report | None = None,
) -> None:
    q = f", {quality.word}" if quality is not None else ""
    q += f", {mods.word}" if mods is not None else ""
    print(
        f"selftest: {'PASS' if ok else 'FAIL'}: {run_} tests, {failures} failures, {errors} errors, {skipped} "
        f"skipped, static {len(problems)} problems{q}, {time.monotonic() - t0:.0f} s, Python {PYTHON_VERSION}"
    )


PYTHON_VERSION = ".".join(str(x) for x in sys.version_info[:3])


def run(
    verbose: bool = False,
    pattern: str | None = None,
    jobs: int | None = None,
    python: str | None = None,
    update_golden: bool = False,
    quality: str | None = None,
    quality_only: bool = False,
    fix: bool = False,
    mods: str | None = None,
    coverage: bool = False,
) -> int:
    """The selftest in this Python, or with `python` (a path or a name on the PATH): the same selftest run by that
    interpreter (lib/main.py), so the suite can be run on the oldest Python foreman supports and on the usual one.
    `update_golden`: only the golden corpus, rewriting its files (update_golden_files). `quality`: the mode of the
    code quality checks (auto by default; off by default with `python`: the tools do not depend on the interpreter);
    `quality_only`: the static and quality checks without the tests; `fix`: ruff's safe fixes, then those checks.
    `mods`: the mode of the hooks module's check (mods.py; auto by default, off by default with `python`), run with
    the quality checks. `coverage`: the suite under coverage.py with a table per module (cover.py; `quality` says
    whether a missing coverage is a SKIP or a failure)."""
    if coverage:
        from . import cover

        return cover.run(ROOT, quality or "auto", jobs, pattern)
    if python:
        return run_with(
            python, verbose, pattern, jobs, update_golden, quality or "off", quality_only, fix, mods or "off"
        )
    quality = quality or "auto"
    mods = mods or "auto"
    if update_golden:
        return update_golden_files(verbose, pattern, jobs)
    if fix:
        rc = quality_checks.fix(ROOT, out=lambda s: print(s, flush=True))
        return run_quality_only(quality, mods) or rc
    if quality_only:
        return run_quality_only(quality, mods)
    t0 = time.monotonic()
    warnings.simplefilter("ignore", ResourceWarning)
    suite = discover(pattern)
    with RunSlot() as slot:  # an explicit -j wins, and the run still counts for the others
        explicit = jobs is not None
        jobs = slot.jobs if jobs is None else jobs
        if jobs <= 1:
            return run_serial(suite, verbose, t0, quality=quality, mods=mods)
        print(f"selftest: {jobs} workers ({'-j' if explicit else slot.why + ', counted again as they change'})")
        sys.stdout.flush()
        share = None if explicit else slot.share
        return run_parallel(suite, verbose, jobs, t0, quality=quality, mods=mods, share=share)


# ---------- the machine's cores, shared by the selftests running on it ----------

SLOTS_ENV = "FOREMAN_SELFTEST_SLOTS"  # the folder of the runs' slots (tests point it at their own)
MIN_JOBS = 2


def slots_dir() -> Path:
    """Where each live selftest run keeps a locked file: the user's temporary folder, shared by every session of
    the machine's user (not foreman's state: a selftest writes nothing there)."""
    return Path(os.environ.get(SLOTS_ENV) or Path(tempfile.gettempdir()) / "foreman-selftest-runs")


class RunSlot:
    """A selftest run's place among those running on the machine: a file of its own in slots_dir(), locked (flock)
    while it runs, so a run that ends or dies frees it by itself. On entering: `live`, the runs holding their file
    (this one included; files nobody holds are removed), and `jobs`, the workers for a run without -j: the cores
    divided by the live runs, at least MIN_JOBS. `share()` counts them again while the run goes (run_parallel stops a
    worker over the share and adds one under it). Without flock or a writable folder, every core and no sharing."""

    def __init__(self, cores: int | None = None) -> None:
        self.cores = cores or os.cpu_count() or 1
        self.live = 1
        self.why = f"{self.cores} cores"
        self.file: Any = None
        self.path: Path | None = None
        self.counted = time.monotonic()

    @property
    def jobs(self) -> int:
        return max(MIN_JOBS, self.cores // self.live) if self.cores > 1 else 1

    def share(self) -> int:
        """`jobs` with the live runs counted again (at most once a second): a run that starts or ends meanwhile
        changes this one's share."""
        now = time.monotonic()
        if self.path is not None and now - self.counted >= 1:
            self.counted = now
            d = self.path.parent
            self.live = 1 + sum(1 for f in d.glob("run-*") if f != self.path and held(f))
        return self.jobs

    def __enter__(self) -> RunSlot:
        try:
            import fcntl

            d = slots_dir()
            d.mkdir(parents=True, exist_ok=True)
            # locked before it gets its run- name: a run counting the others never sees it unheld
            fd, path = tempfile.mkstemp(prefix=f"new-{os.getpid()}-", dir=d)
            self.file, self.path = os.fdopen(fd, "w"), Path(path)
            fcntl.flock(self.file, fcntl.LOCK_EX)
            named = d / self.path.name.replace("new-", "run-", 1)
            os.rename(self.path, named)
            self.path = named
            self.live = 1 + sum(1 for f in d.glob("run-*") if f != self.path and held(f))
        except (ImportError, OSError):
            self.live = 1
        self.why = f"{self.cores} cores, {self.live} selftest run{'s' if self.live != 1 else ''} on this machine"
        return self

    def __exit__(self, *exc: object) -> None:
        if self.file is not None and self.path is not None:
            with contextlib.suppress(OSError):
                self.path.unlink()
            self.file.close()


def held(f: Path) -> bool:
    """Whether a live run holds this slot file; one that nobody holds is removed."""
    import fcntl

    try:
        with open(f) as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return True
            with contextlib.suppress(OSError):
                f.unlink()
            return False
    except OSError:  # gone meanwhile
        return False


def run_quality_only(mode: str, mods: str = "off") -> int:
    """`selftest --quality-only`: the static checks, the quality checks and the mods check (at the same time), no
    tests."""
    t0 = time.monotonic()
    bg = quality_checks.Background(ROOT, mode)
    mg = mods_checks.Background(ROOT, mods)
    problems = static_report()
    q = quality_report(bg)
    m = quality_report(mg)
    ok = not problems and not q.problems and not m.problems
    print(
        f"selftest: {'PASS' if ok else 'FAIL'}: quality only, static {len(problems)} problems, {q.word}, {m.word}, "
        f"{time.monotonic() - t0:.0f} s, Python {PYTHON_VERSION}"
    )
    return 0 if ok else 1


def quality_report(bg: quality_checks.Background) -> quality_checks.Report:
    """Waits for the quality (or mods) checks started in `bg` and prints their lines and summary."""
    q = bg.wait()
    for line in q.lines:
        print(line)
    if q.line:
        print(q.line)
    sys.stdout.flush()
    return q


def run_with(
    python: str,
    verbose: bool,
    pattern: str | None,
    jobs: int | None,
    update_golden: bool = False,
    quality: str = "off",
    quality_only: bool = False,
    fix: bool = False,
    mods: str = "off",
) -> int:
    exe = shutil.which(python)
    if not exe:
        print(f"selftest: no Python at {python}", file=sys.stderr)
        return 2
    args = (
        [exe, str(ROOT / "lib/main.py"), "selftest"]
        + (["-v"] if verbose else [])
        + (["-j", str(jobs)] if jobs is not None else [])
        + (["--update-golden"] if update_golden else [])
        + [f"--quality={quality}", f"--mods={mods}"]
        + (["--quality-only"] if quality_only else [])
        + (["--fix"] if fix else [])
        + ([pattern] if pattern else [])
    )
    env = {k: v for k, v in os.environ.items() if k not in ("FOREMAN_PYTHON_MIN", "FOREMAN_PYTHON_PROBE")}
    sys.stdout.flush()
    return subprocess.run(args, env=env, check=False).returncode


# ---------- the golden corpus (tests/golden.py) ----------

GOLDEN_DIRS = (ROOT / "tests/golden", ROOT / "dev/tests/golden")  # foreman's corpus and its developer tool's
GOLDEN_TESTS = ("test_golden.", "test_golden_dev.")  # the modules of the corpus: what --update-golden runs


def golden_snapshot() -> dict[str, bytes]:
    """{path from the repo's root: bytes} of every golden file."""
    return {
        f.relative_to(ROOT).as_posix(): f.read_bytes()
        for d in GOLDEN_DIRS
        if d.exists()
        for f in sorted(d.rglob("*"))
        if f.is_file()
    }


def update_golden_files(verbose: bool, pattern: str | None, jobs: int | None) -> int:
    """`selftest --update-golden`: the corpus's tests with FOREMAN_UPDATE_GOLDEN=1 (each writes its transcript as it
    is now instead of comparing it), then what changed under the golden folders (GOLDEN_DIRS): each file added,
    changed (+/- lines) or left over (no test wrote it: removed when the whole corpus ran and passed). The rest of the
    suite does not run."""
    t0 = time.monotonic()
    warnings.simplefilter("ignore", ResourceWarning)
    before = golden_snapshot()
    start = time.time()
    os.environ["FOREMAN_UPDATE_GOLDEN"] = "1"
    try:
        suite = unittest.TestSuite(t for t in flatten(discover(pattern)) if t.id().startswith(GOLDEN_TESTS))
        jobs = (os.cpu_count() or 1) if jobs is None else jobs
        rc = (
            run_serial(suite, verbose, t0, static=False)
            if jobs <= 1
            else run_parallel(suite, verbose, jobs, t0, static=False)
        )
    finally:
        os.environ.pop("FOREMAN_UPDATE_GOLDEN", None)
    after = golden_snapshot()
    stale = [k for k in after if (ROOT / k).stat().st_mtime < start]
    changed = 0
    for k in sorted(after):
        if k in stale:
            continue
        if k not in before:
            print(f"golden: added {k} ({len(after[k].splitlines())} lines)")
            changed += 1
        elif before[k] != after[k]:
            import difflib

            d = list(
                difflib.unified_diff(
                    before[k].decode(errors="replace").splitlines(),
                    after[k].decode(errors="replace").splitlines(),
                    lineterm="",
                )
            )[2:]
            plus, minus = sum(x.startswith("+") for x in d), sum(x.startswith("-") for x in d)
            print(f"golden: changed {k} (+{plus} -{minus} lines)")
            changed += 1
    for k in [] if pattern else stale:  # with a pattern, only some tests ran: the others' files stay as they are
        if rc == 0:
            (ROOT / k).unlink()
            print(f"golden: removed {k} (no test writes it any more)")
            changed += 1
        else:
            print(f"golden: {k} was not written (a test failed): left as it is")
    print(f"golden: {changed} file{'s' if changed != 1 else ''} changed" if changed else "golden: no change")
    return rc


def static_report() -> list[str]:
    problems = static_checks()
    for p in problems:
        print(f"FAIL {p}")
    print(f"static checks: {'ok' if not problems else f'{len(problems)} problems'}", flush=True)
    return problems


def run_serial(
    suite: unittest.TestSuite, verbose: bool, t0: float, static: bool = True, quality: str = "off", mods: str = "off"
) -> int:
    bg = quality_checks.Background(ROOT, quality) if static else None
    mg = mods_checks.Background(ROOT, mods) if static else None
    problems = static_report() if static else []
    q = quality_report(bg) if bg else None
    m = quality_report(mg) if mg else None
    result = unittest.TextTestRunner(verbosity=2 if verbose else 1, stream=sys.stdout).run(suite)
    ok = result.wasSuccessful() and not problems and not (q and q.problems) and not (m and m.problems)
    summary(ok, result.testsRun, len(result.failures), len(result.errors), len(result.skipped), problems, t0, q, m)
    for t, why in result.skipped:
        print(f"  skipped {t.id()}: {why}")
    return 0 if ok else 1


# ---------- parallel: a queue of test classes, one class at a time per worker ----------


def classes(suite: unittest.TestSuite) -> tuple[dict[str, list[str]], unittest.TestSuite]:
    """{class key: [test ids]} for the tests a worker can load by name, and the tests it cannot (a module that failed
    to import shows up as unittest's own failed test): those run here, in the parent."""
    out, local = {}, unittest.TestSuite()
    for t in flatten(suite):
        mod = type(t).__module__
        if mod.startswith("unittest"):
            local.addTest(t)
            continue
        out.setdefault(f"{mod}.{type(t).__qualname__}", []).append(t.id())
    return out, local


def result_dict(result: unittest.TextTestResult, text: str, seconds: float) -> dict[str, Any]:
    def desc(test: Any) -> str:
        return result.getDescription(test) if hasattr(test, "_testMethodName") else str(test)

    return {
        "run": result.testsRun,
        "text": text,
        "s": round(seconds, 3),
        "errors": [[desc(t), tb] for t, tb in result.errors],
        "failures": [[desc(t), tb] for t, tb in result.failures],
        "skipped": [[t.id(), why] for t, why in result.skipped],
        "expected": len(result.expectedFailures),
        "unexpected": [desc(t) for t in result.unexpectedSuccesses],
    }


def run_tests(suite: unittest.TestSuite, verbose: bool) -> dict[str, Any]:
    stream = io.StringIO()
    result = unittest.TextTestResult(unittest.runner._WritelnDecorator(stream), True, 2 if verbose else 1)
    t, cpu = time.monotonic(), cpu_seconds()
    suite(result)
    return dict(result_dict(result, stream.getvalue(), time.monotonic() - t), cpu=round(cpu_seconds() - cpu, 3))


def cpu_seconds() -> float:
    """user+sys of this process, of the children it waited for and of the commands the tests' fork server ran for
    it (tests/forkentry.py: its children are not ours until it ends)."""
    import resource

    forked = getattr(sys.modules.get("forkentry"), "child_cpu", 0.0)
    return forked + sum(
        u.ru_utime + u.ru_stime
        for u in (resource.getrusage(resource.RUSAGE_SELF), resource.getrusage(resource.RUSAGE_CHILDREN))
    )


def worker() -> None:
    """A worker process: reads a JSON list of test ids per line on stdin, runs them, answers one JSON line on stdout.
    The protocol keeps its own copies of stdin and stdout: the tests (and what they start) see /dev/null and a log
    file instead, so nothing they read or print can mix with it."""
    proto_in = os.fdopen(os.dup(0), "r")
    proto_out = os.fdopen(os.dup(1), "w")
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)
    log = os.open(os.path.join(tempfile.gettempdir(), "worker.log"), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
    os.dup2(log, 1)
    os.dup2(log, 2)
    warnings.simplefilter("ignore", ResourceWarning)
    verbose = os.environ.get("FOREMAN_SELFTEST_VERBOSE") == "1"
    test_paths()
    loader = unittest.TestLoader()
    for line in proto_in:
        ids = json.loads(line)
        try:
            out = run_tests(loader.loadTestsFromNames(ids), verbose)
        except Exception:  # noqa: BLE001 (the class could not even load: one error for it, the worker goes on)
            out = {
                "run": 0,
                "text": "E",
                "s": 0,
                "errors": [[ids[0].rsplit(".", 1)[0], traceback.format_exc()]],
                "failures": [],
                "skipped": [],
                "expected": 0,
                "unexpected": [],
            }
        sys.stdout.flush()
        sys.stderr.flush()
        proto_out.write(json.dumps(out) + "\n")
        proto_out.flush()


class Worker:
    def __init__(self, root: PathLike, n: int, verbose: bool) -> None:
        self.tmp = Path(root) / f"w{n}"
        self.tmp.mkdir()
        env = dict(os.environ, TMPDIR=str(self.tmp), FOREMAN_SELFTEST_VERBOSE="1" if verbose else "0")
        code = f"import sys; sys.path.insert(0, {str(ROOT / 'lib')!r}); from foreman import selftest; selftest.worker()"
        self.proc = subprocess.Popen(
            [sys.executable, "-c", code],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            env=env,
            cwd=str(ROOT),
        )
        self.job: str | None = None

    def send(self, key: str, ids: list[str]) -> None:
        self.job = key
        assert self.proc.stdin is not None  # stdin=PIPE
        self.proc.stdin.write(json.dumps(ids) + "\n")
        self.proc.stdin.flush()

    def log_tail(self, n: int = 30) -> str:
        try:
            return "".join((self.tmp / "worker.log").read_text(errors="replace").splitlines(True)[-n:])
        except OSError:
            return ""

    def stop(self) -> None:
        with contextlib.suppress(OSError):
            assert self.proc.stdin is not None  # stdin=PIPE
            self.proc.stdin.close()
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()


def run_parallel(
    suite: unittest.TestSuite,
    verbose: bool,
    jobs: int,
    t0: float,
    static: bool = True,
    quality: str = "off",
    mods: str = "off",
    share: Callable[[], int] | None = None,
) -> int:
    """`share`: how many workers this run may have now (RunSlot.share: the machine's cores among the live runs); a
    worker that ends a class while the run has more is stopped, and one is added when the share grows. Without it,
    `jobs` workers to the end."""
    todo, local = classes(suite)
    # the golden corpus's classes (one long test each) and then the classes with more tests first, so a long one
    # does not start last
    queue = sorted(todo, key=lambda k: (not k.startswith(GOLDEN_TESTS), -len(todo[k]), k))
    results = []
    root = tempfile.mkdtemp(prefix="foreman-selftest-")
    workers, sel = [], selectors.DefaultSelector()
    spawned = 0

    def start() -> Worker:
        nonlocal spawned
        w = Worker(root, spawned, verbose)
        spawned += 1
        workers.append(w)
        assert w.proc.stdout is not None  # stdout=PIPE
        sel.register(w.proc.stdout, selectors.EVENT_READ, w)
        return w

    def busy() -> int:
        return sum(1 for w in workers if w.job)

    def feed(w: Worker) -> None:
        w.job = None
        if queue and (share is None or busy() < share()):
            key = queue.pop(0)
            w.send(key, todo[key])
        elif queue:  # over this run's share of the machine: the worker ends
            assert w.proc.stdout is not None  # stdout=PIPE
            sel.unregister(w.proc.stdout)
            w.stop()

    def grow() -> None:
        while share is not None and queue and busy() < share():
            feed(start())

    def take(out: dict[str, Any]) -> None:
        results.append(out)
        sys.stdout.write(out["text"])
        sys.stdout.flush()

    try:
        for _ in range(min(jobs, len(queue))):
            feed(start())
        # the static and quality checks (the tools in a thread) while the workers run
        bg = quality_checks.Background(ROOT, quality) if static else None
        mg = mods_checks.Background(ROOT, mods) if static else None
        problems = static_report() if static else []
        q = quality_report(bg) if bg else None
        m = quality_report(mg) if mg else None
        if local.countTestCases():
            take(run_tests(local, verbose))
        while any(w.job for w in workers):
            for k, _ in sel.select():
                w = k.data
                line = w.proc.stdout.readline()
                if line:
                    take(dict(json.loads(line), key=w.job))
                    feed(w)
                    grow()
                    continue
                # the worker died in the middle of a class: an error for that class, a new worker for the rest
                sel.unregister(w.proc.stdout)
                w.proc.wait()
                take(
                    {
                        "run": 0,
                        "text": "E",
                        "s": 0,
                        "failures": [],
                        "skipped": [],
                        "expected": 0,
                        "unexpected": [],
                        "errors": [
                            [
                                w.job,
                                f"the worker process ended (exit {w.proc.returncode}) while running it; "
                                f"the end of its output:\n{w.log_tail()}",
                            ]
                        ],
                    }
                )
                w.job = None
                if queue:
                    feed(start())
    finally:
        for w in workers:
            w.stop()
        sel.close()
        shutil.rmtree(root, ignore_errors=True)
    if os.environ.get("FOREMAN_SELFTEST_TIMES"):  # {class: [wall s, cpu s]}, to find the costly ones
        Path(os.environ["FOREMAN_SELFTEST_TIMES"]).write_text(
            json.dumps({r["key"]: [r["s"], r.get("cpu", 0)] for r in results if r.get("key")}, indent=1, sort_keys=True)
        )
    return report(results, problems, t0, q, m)


def report(
    results: list[dict[str, Any]],
    problems: list[str],
    t0: float,
    quality: quality_checks.Report | None = None,
    mods: quality_checks.Report | None = None,
) -> int:
    """The end of unittest's text output (errors, failures, the count and OK/FAILED) and the selftest line."""
    errors = [e for r in results for e in r["errors"]]
    failures = [f for r in results for f in r["failures"]]
    skipped = [s for r in results for s in r["skipped"]]
    unexpected = [u for r in results for u in r["unexpected"]]
    expected = sum(r["expected"] for r in results)
    ran = sum(r["run"] for r in results)
    out = sys.stdout
    if results and results[-1]["text"] and not results[-1]["text"].endswith("\n"):
        out.write("\n")
    for flavour, items in (("ERROR", errors), ("FAIL", failures)):
        for desc, tb in items:
            out.write(
                f"{unittest.TextTestResult.separator1}\n{flavour}: {desc}\n{unittest.TextTestResult.separator2}\n{tb}\n"
            )
    for desc in unexpected:
        out.write(f"{unittest.TextTestResult.separator1}\nUNEXPECTED SUCCESS: {desc}\n")
    elapsed = time.monotonic() - t0
    out.write(f"{unittest.TextTestResult.separator2}\nRan {ran} test{'s' if ran != 1 else ''} in {elapsed:.3f}s\n\n")
    ok_tests = not errors and not failures and not unexpected
    infos = [
        f"{k}={v}"
        for k, v in (
            ("failures", len(failures)),
            ("errors", len(errors)),
            ("skipped", len(skipped)),
            ("expected failures", expected),
            ("unexpected successes", len(unexpected)),
        )
        if v
    ]
    out.write(("OK" if ok_tests else "FAILED") + (f" ({', '.join(infos)})" if infos else "") + "\n")
    ok = ok_tests and not problems and not (quality and quality.problems) and not (mods and mods.problems)
    summary(ok, ran, len(failures), len(errors), len(skipped), problems, t0, quality, mods)
    for tid, why in skipped:
        print(f"  skipped {tid}: {why}")
    return 0 if ok else 1
