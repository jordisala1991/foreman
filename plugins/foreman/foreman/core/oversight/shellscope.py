"""The folder and variables a command line has when each of its simple commands runs: `cd DIR` and `NAME=value` are
followed inside the one line; a variable not set in it stays as written (the environment is never read). A `cd` or an
assignment next to `&`, `|`, `||` or `|&` may not carry on (a subshell, or only on failure): it leaves the folder unknown
and the variable unset. A value of several words or with a glob leaves the variable unset (the shell splits it)."""

import os
import re
from collections.abc import Iterator
from pathlib import Path

UNKNOWN = "?"  # the folder after a `cd` that cannot be resolved
ASSIGN = re.compile(r"^(\w+)=(.*)$", re.S)
VAR = re.compile(r"\$\{(\w+)\}|\$(\w+)")
SPLITS = re.compile(r"[\s*?\[]")
NO_CARRY = {"&", "|", "||", "|&"}
WRAPPERS = ("sudo", "env", "command", "exec", "time", "nohup")


def _expand(word: str, env: dict[str, str]) -> str:
    return VAR.sub(lambda m: env.get(m[1] or m[2], m[0]), word)


def words(w: list[str]) -> list[str]:
    """The command itself: leading assignments and wrappers (sudo, env, …) dropped, the program by its base name."""
    w = list(w)
    while w and (ASSIGN.match(w[0]) or w[0] in WRAPPERS):
        w = w[1:]
    if w:
        w[0] = os.path.basename(w[0])
    return w


def _cd(args: list[str], folder: str) -> str:
    dirs = [a for a in args if a == "-" or not a.startswith("-")]
    d = dirs[0] if dirs else "~"
    if d == "-" or "$" in d or "`" in d:
        return UNKNOWN
    d = os.path.expanduser(d)
    if folder == UNKNOWN and not os.path.isabs(d):
        return UNKNOWN
    return str(Path(folder, d).resolve())


def walk(cmds: list[tuple[str, list[str]]], cwd: str) -> Iterator[tuple[list[str], str]]:
    """Each simple command of `cmds` (operator before it and raw words, as `guard.segments` gives them) as its expanded
    words and the folder it runs in (a line of only assignments yields nothing). A command prefixed by `NAME=value` forgets NAME."""
    env: dict[str, str] = {}
    folder = cwd
    for n, (before, raw) in enumerate(cmds):
        unsure = before in NO_CARRY or (n + 1 < len(cmds) and cmds[n + 1][0] in NO_CARRY)
        w = [_expand(a, env) for a in words(raw)]
        if w:
            yield w, folder
        body = raw[1:] if raw[:1] == ["export"] else raw
        sets = [m for m in map(ASSIGN.match, body) if m]
        if body and len(sets) == len(body):
            for m in sets:
                value = _expand(m[2], env)
                if unsure or SPLITS.search(value):
                    env.pop(m[1], None)
                else:
                    env[m[1]] = value
            continue
        for m in map(ASSIGN.match, raw):
            if not m:
                break
            env.pop(m[1], None)
        if w[:1] == ["cd"]:
            folder = UNKNOWN if unsure else _cd(w[1:], folder)
