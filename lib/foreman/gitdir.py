"""Where git keeps a repo's own files, read from the disk without running git (the guard calls it on every write).

- checkout_of(folder): the checkout that holds a folder, the nearest folder up with a `.git` entry (a folder, or the
  file of a worktree or a submodule), one stat per level (the guard's main-checkout rule and its git class).
- common_dir(checkout): the git directory a checkout shares with every worktree of its repo: its `.git` folder, or,
  for a `.git` file (`gitdir: X`), X's `commondir` joined to X (a worktree) or X itself (a submodule).
- config_values(path): the `section.key` = value pairs of a git config file that name other files (core.hooksPath,
  include.path, includeIf.*.path), as git reads them: sections and keys case-insensitive, quotes and escapes, `#`
  and `;` comments. Best effort: a file that cannot be read gives nothing.
- config_paths(path): the folders and files such a config makes git run or read: an absolute core.hooksPath (`~`
  expanded; a relative one is a folder of the work tree, which a diff shows) and every included file (a relative
  one from the including file's folder, as named and as the file it reaches), followed into the included files up
  to MAX_INCLUDES deep.

Nothing here raises for a missing or unreadable file; a caller that gets None or [] knows nothing more.
"""

from __future__ import annotations

import os
import re

MAX_INCLUDES = 5  # how deep config_paths follows include.path into included files
MAX_CONFIG = 1 << 20  # a config file larger than this is not read (bytes)

SECTION = re.compile(r'\s*\[\s*([A-Za-z0-9.-]+)(?:\s+"((?:[^"\\]|\\.)*)")?\s*\]')
KEY = re.compile(r"\s*([A-Za-z][A-Za-z0-9-]*)\s*(=\s*(.*))?$")
PATH_KEYS = ("core.hookspath", "include.path")  # and includeif.<condition>.path


def checkout_of(folder: str, resolve: bool = True) -> str | None:
    """The checkout that holds a folder (it need not exist): the nearest folder up from its real path (resolve=False:
    from the folder as given, made absolute; for a path already real, or one realpath of the result later) that has
    a `.git` entry (a folder, or a worktree's or a submodule's file), not case-folded; None outside any."""
    d = os.path.realpath(folder) if resolve else os.path.abspath(folder)
    while True:
        if os.path.lexists(os.path.join(d, ".git")):
            return d
        up = os.path.dirname(d)
        if up == d:
            return None
        d = up


def _line(path: str) -> str | None:
    """The first line of a small file, stripped, or None."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.readline(4096).strip()
    except OSError:
        return None


def common_dir(checkout: str) -> str | None:
    """The real path of the git directory a checkout (a real path, as checkout_of gives it) shares with every
    worktree of its repo, or None when it cannot be read (no `.git`, a `.git` file without `gitdir:`)."""
    dot = os.path.join(checkout, ".git")
    if os.path.isdir(dot):
        return os.path.realpath(dot) if os.path.islink(dot) else dot
    first = _line(dot)
    if not first or not first.startswith("gitdir:"):
        return None
    gitdir = os.path.join(checkout, first[len("gitdir:") :].strip())
    common = _line(os.path.join(gitdir, "commondir"))
    return os.path.realpath(os.path.join(gitdir, common) if common else gitdir)


def _value(raw: str) -> str:
    """A config value as git reads it: quotes removed, escapes \\" \\\\ \\n \\t, a comment after it cut."""
    out: list[str] = []
    quoted = False
    i = 0
    while i < len(raw):
        c = raw[i]
        if c == "\\" and i + 1 < len(raw):
            out.append({"n": "\n", "t": "\t", "b": "\b"}.get(raw[i + 1], raw[i + 1]))
            i += 2
            continue
        if c == '"':
            quoted = not quoted
        elif c in "#;" and not quoted:
            break
        else:
            out.append(c)
        i += 1
    return "".join(out).strip()


def config_values(path: str) -> list[tuple[str, str]]:
    """[(name, value)] of the keys of a git config file that name files (PATH_KEYS, includeif.*.path), in order;
    the name is `section.key` lowercased (`includeif.<condition>.path` keeps no condition: `includeif.path`)."""
    try:
        with open(path, "rb") as f:
            data = f.read(MAX_CONFIG + 1)
    except OSError:
        return []
    if len(data) > MAX_CONFIG or b"path" not in data.lower():  # most configs name no file: nothing to parse
        return []
    text = data.decode("utf-8", errors="replace")
    out: list[tuple[str, str]] = []
    section = ""
    for raw in text.splitlines():
        line = raw
        m = SECTION.match(line)
        if m:
            section = m.group(1).lower()
            if m.group(2) is None and "." in section:  # the old [section.sub] form: the subsection is not a key
                section = section.split(".", 1)[0]
            line = line[m.end() :]
            if not line.strip() or line.lstrip()[0] in "#;":
                continue
        k = KEY.match(line)
        if not k or not section:
            continue
        name = f"{section}.{k.group(1).lower()}"
        if name in PATH_KEYS or name == "includeif.path":
            out.append((name, _value(k.group(3) or "")))
    return out


def _expand(value: str, base: str) -> str | None:
    """A path value of git config as git takes it: `~/` and `~user/` expanded, a relative one joined to base (None
    for a value git would not take as a path here: empty, or `%(prefix)/`)."""
    if not value or value.startswith("%("):
        return None
    if value.startswith("~"):
        value = os.path.expanduser(value)
    return os.path.normpath(os.path.join(base, value))


def config_paths(path: str, depth: int = 0) -> list[tuple[str, bool]]:
    """[(path, is a folder)] of what a git config file makes git run or read besides itself: an absolute
    core.hooksPath (a folder) and each included file, then the same of each included file (MAX_INCLUDES deep)."""
    out: list[tuple[str, bool]] = []
    for name, value in config_values(path):
        if name == "core.hookspath":
            if value.startswith(("/", "~")):
                full = _expand(value, "/")
                if full:
                    out.append((full, True))
            continue
        # a relative include: from the folder of the file as named and of the file it reaches (a link)
        bases = (
            [""] if value.startswith(("/", "~")) else [os.path.dirname(path), os.path.dirname(os.path.realpath(path))]
        )
        for full in dict.fromkeys(_expand(value, b) for b in bases):
            if full is None:
                continue
            out.append((full, False))
            if depth < MAX_INCLUDES:
                out += config_paths(full, depth + 1)
    return out
