"""The guard: a short reminder, not a cage. A PreToolUse hook in every agent session that stops a handful of actions,
recognised by the name of the program run (never by a word inside a string or a message), and tells its agent to ask
the owner with `foreman ask`. The owner allows it once from the panel (`foreman allow QID`).

The list (DESIGN.md): publish · push to main · delete what is not yours · change the project's config · the owner's
own rules (`[guard] ask` in foreman.toml). Agents work for the owner: a way around this list is not a hole to close, the
review before the merge is the safety net. Do not grow this file for a new trick.
"""

import json
import os
import re
import shlex
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from ... import util
from ..project.config import Project
from ..review import evidence
from ..tasks.backlog import Local
from . import shellscope

PUBLISH = {
    ("npm", "publish"),
    ("pnpm", "publish"),
    ("yarn", "publish"),
    ("cargo", "publish"),
    ("twine", "upload"),
    ("gh", "release"),
    ("gh", "pr"),
    ("gh", "repo"),
    ("docker", "push"),
    ("uv", "publish"),
    ("poetry", "publish"),
}
MAIN = {"main", "master"}
OPERATORS = {";", "&&", "||", "|", "&", ";;", "|&"}
REDIRECTS = {">", ">>", ">|", "&>", "&>>"}
WHY = {
    "publish": "publishing goes outside this machine",
    "push-main": "pushing to the main branch (or forcing a push) skips the owner's review and accept",
    "delete": "it deletes something that is not this task's (outside its worktree, or another branch)",
    "push-evidence": "the evidence branch is written by the loop, not by agents",
    "config": "it changes the project's config (foreman.toml)",
}


def segments(cmd: str) -> list[tuple[str, list[str]]]:
    """The simple commands of a shell line, each as the operator before it ("" for the first) and its raw words (none
    between two operators); operators inside quotes are words, not separators."""
    lex = shlex.shlex(cmd.replace("\n", " ; "), posix=True, punctuation_chars=";&|<>")
    lex.whitespace_split = True
    out: list[tuple[str, list[str]]] = [("", [])]
    try:
        for tok in lex:
            if tok in OPERATORS:
                out.append((tok, []))
            else:
                out[-1][1].append(tok)
    except ValueError:  # unbalanced quotes: fall back to plain words
        return [("", cmd.split())]
    return out


def writes(w: list[str], cwd: str, target: Path) -> bool:
    """`w` writes or removes the file `target`: as a redirect target, through tee / sed -i / perl -i, as the
    destination of mv / cp, or as an argument of rm / mv / truncate."""

    def hits(arg: str) -> bool:
        return Path(cwd, os.path.expanduser(arg)).resolve() == target

    prog, args = w[0], w[1:]
    if any(tok in REDIRECTS and n + 1 < len(w) and hits(w[n + 1]) for n, tok in enumerate(w)):
        return True
    plain = [a for a in args if not a.startswith("-") and a not in REDIRECTS]
    if prog in ("tee", "rm", "truncate", "unlink") or (prog in ("sed", "perl") and any(a.startswith("-i") for a in args)):
        return any(hits(a) for a in plain)
    if prog in ("mv", "cp", "install", "ln"):
        return bool(plain) and (hits(plain[-1]) or (prog == "mv" and any(hits(a) for a in plain)))
    return False


def _mine(path: str, cwd: str, sc: "Scope", worktree: bool = True) -> bool:
    """`path` is under the temp dirs, the task's own scratch folders or (with `worktree`) its worktree. A variable or a
    folder the command does not resolve counts as outside."""
    p = Path(os.path.expanduser(path))
    if "$" in path or "`" in path or (not p.is_absolute() and cwd == shellscope.UNKNOWN):
        return False
    p = (Path(cwd) / p if not p.is_absolute() else p).resolve()
    roots = [*(sc.own if worktree else []), Path(tempfile.gettempdir()).resolve(), Path("/tmp").resolve()]
    if any(p == r or r in p.parents for r in roots):
        return True
    if not (sc.task and sc.scratch and sc.scratch in p.parents):
        return False
    name = p.relative_to(sc.scratch).parts[0]
    return name == sc.task or name.startswith(sc.task + "-")


@dataclass
class Scope:
    """Where a command runs and what it may touch: its folder, its agent's own folders and branch, foreman.toml, the owner's rules."""

    cwd: str
    own: list[Path]
    branch: str
    config: Path
    extra: list[dict[str, str]]
    mains: set[str] = field(default_factory=lambda: set(MAIN))
    scratch: Path | None = None  # the project's scratch root, resolved
    task: str = ""  # the task whose scratch folders are own


def _git_args(args: list[str]) -> list[str]:
    while args[:1] and args[0] in ("-C", "-c"):
        args = args[2:]
    return args


def _publishes(prog: str, sub: str, args: list[str]) -> bool:
    if (prog, sub) not in PUBLISH:
        return False
    return prog != "gh" or bool(args[1:2] and args[1] in ("create", "upload", "merge", "edit", "delete"))


def _gh_deletes(prog: str, sub: str, args: list[str]) -> bool:
    return prog == "gh" and sub in ("repo", "project") and args[1:2] == ["delete"]


def _push(args: list[str], sc: Scope) -> str | None:
    rest = args[args.index("push") + 1 :]
    refs = [a for a in rest if not a.startswith("-")]
    if {"--delete", "-d"} & set(rest) or any(r.startswith(":") for r in refs[1:]):
        return "delete"
    if {"-f", "--force", "--force-with-lease", "--mirror", "--all"} & set(rest) or any(r.startswith("+") for r in refs[1:]):
        return "push-main"
    dests = [r.split(":")[-1].removeprefix("refs/heads/") for r in refs[1:]] or [sc.branch]
    if evidence.BRANCH in dests:
        return "push-evidence"
    if any((d if d != "HEAD" else sc.branch) in sc.mains | {""} for d in dests):
        return "push-main"
    return None


def _git(sub: str, args: list[str], sc: Scope, here: bool) -> str | None:
    if sub == "push":
        return _push(args, sc)
    if sub == "branch" and any(a in ("-d", "-D", "--delete") for a in args):
        return "delete" if any(a != sc.branch for a in args[1:] if not a.startswith("-")) else None
    if sub == "worktree" and "remove" in args:
        targets = [a for a in args[args.index("remove") + 1 :] if not a.startswith("-")]
        return None if here and targets and all(_mine(t, sc.cwd, sc, worktree=False) for t in targets) else "delete"
    return None


def _removes(prog: str, args: list[str], sc: Scope) -> bool:
    if not (prog in ("rm", "rmdir", "unlink", "trash") or (prog == "find" and "-delete" in args)):
        return False
    targets = [a for a in args if not a.startswith("-")] if prog != "find" else args[:1]
    return any(not _mine(t, sc.cwd, sc) for t in targets)


def _segment(w: list[str], sc: Scope) -> str | None:
    prog, args = w[0], w[1:]
    here = args[:1] not in (["-C"], ["-c"])  # git runs in the command's folder
    if prog == "git":
        args = _git_args(args)
    sub = next((a for a in args if not a.startswith("-")), "")
    if _gh_deletes(prog, sub, args):
        return "delete"
    if _publishes(prog, sub, args):
        return "publish"
    found = _git(sub, args, sc, here) if prog == "git" else None
    if found:
        return found
    if _removes(prog, args, sc):
        return "delete"
    if writes(w, sc.cwd, sc.config.resolve()):
        return "config"
    seg = " ".join(w)
    return next((rule.get("name", "owner rule") for rule in sc.extra if re.search(rule.get("pattern", "$^"), seg)), None)


def check_bash(cmd: str, sc: Scope) -> str | None:
    """The name of the first action of the list that `cmd` runs, or None."""
    for w, cwd in shellscope.walk(segments(cmd), sc.cwd):
        found = _segment(w, replace(sc, cwd=cwd))
        if found:
            return found
    return None


def main_branches(p: Project) -> set[str]:
    """The branch names `push-main` protects: main and master, the project's main and the main of every repo (DESIGN.md «Several repos», Guard row)."""
    return MAIN | {p.get("project", "main")} | {str(r["main"]) for r in p.cfg["repos"].values()}


def check(p: Project, d: dict[str, Any], local: Local | None, tid: str) -> str | None:
    """The action of the list that the hook's call `d` (tool_name, tool_input, cwd) runs, or None."""
    tool, cwd = d.get("tool_name", ""), d.get("cwd") or os.getcwd()
    inp: dict[str, Any] = d.get("tool_input") or {}
    own = [Path(local.worktree).resolve()] if local and local.worktree else []
    branch = local.branch if local else ""
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        f = inp.get("file_path") or inp.get("notebook_path") or ""
        if f and Path(cwd, f).resolve() == p.config_file.resolve():
            return "config"
        return None
    if tool == "Bash":
        scope = Scope(cwd, own, branch, p.config_file, p.get("guard", "ask"), main_branches(p), p.scratch.resolve(), tid)
        return check_bash(str(inp.get("command", "")), scope)
    return None


def granted(p: Project, tid: str, action: str) -> bool:
    """Spends the owner's one-time allowance for this task and action, if there is one."""
    with util.locked_json(p.state / "grants.json", []) as grants:
        for g in grants:
            if g["task"] == tid and g["action"] == action and not g.get("used"):
                g["used"] = util.now()
                return True
    return False


def grant(p: Project, tid: str, action: str) -> None:
    with util.locked_json(p.state / "grants.json", []) as grants:
        grants.append({"task": tid, "action": action, "at": util.now()})


def hook(stdin: str, local: Callable[[Project, str], Local | None]) -> str:
    """The PreToolUse hook: the JSON to print (empty: let it run). `local` finds the task's worktree and branch. Never raises:
    a guard error lets the call run."""
    try:
        home, tid = os.environ.get("FOREMAN_HOME"), os.environ.get("FOREMAN_TASK", "")
        if not home:
            return ""
        d = json.loads(stdin or "{}")
        p = Project.load(home)
        action = check(p, d, local(p, tid), tid)
        if not action or granted(p, tid, action):
            return ""
        util.append_jsonl(
            p.state / "guard.jsonl", {"time": util.now(), "task": tid, "action": action, "tool": d.get("tool_name"), "input": d.get("tool_input")}
        )
        why = WHY.get(action, "the owner asked to be asked first")
        msg = (
            f"The owner asked to be asked before this ({action}: {why}). Do not look for another way to do it. "
            f'If it is needed, run `foreman ask --action {action} "what you want to run and why"` and end your turn; '
            "if he allows it, you will be told, and it passes once."
        )
        return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": msg}})
    except Exception as e:  # a broken guard must not stop the work
        print(f"foreman guard error (allowed): {e}", file=sys.stderr)
        return ""
