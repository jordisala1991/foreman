"""The folder and variables a command line has when each of its simple commands runs: `cd DIR` and `NAME=value` are
followed inside the one line; a variable not set in it stays as written (the environment is never read)."""

import os
import re
from collections.abc import Iterator
from pathlib import Path

UNKNOWN = "?"  # the folder after a `cd` that cannot be resolved
ASSIGN = re.compile(r"^(\w+)=(.*)$", re.S)
VAR = re.compile(r"\$\{(\w+)\}|\$(\w+)")
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


def walk(cmds: list[list[str]], cwd: str) -> Iterator[tuple[list[str], str]]:
    """Each simple command of `cmds` (raw words, as `guard.segments` gives them) as its expanded words and the folder
    it runs in. A `cd` or a line of only assignments yields nothing; a command prefixed by `NAME=value` forgets NAME."""
    env: dict[str, str] = {}
    folder = cwd
    for raw in cmds:
        body = raw[1:] if raw[:1] == ["export"] else raw
        sets = [m for m in map(ASSIGN.match, body) if m]
        if body and len(sets) == len(body):
            for m in sets:
                env[m[1]] = _expand(m[2], env)
            continue
        for m in map(ASSIGN.match, raw):
            if not m:
                break
            env.pop(m[1], None)
        w = [_expand(a, env) for a in words(raw)]
        if w[:1] == ["cd"]:
            folder = _cd(w[1:], folder)
        elif w:
            yield w, folder
