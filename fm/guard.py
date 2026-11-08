"""The guard: a short reminder, not a cage. A PreToolUse hook in every agent session that stops a handful of actions,
recognised by the name of the program run (never by a word inside a string or a message), and tells the agent to ask
the owner with `fm ask`. The owner allows it once from the panel (`fm allow QID`).

The list (DESIGN.md): publish · push to main · delete what is not yours · change the project's config · the owner's
own rules (`[guard] ask` in fm.toml). Agents work for the owner: a way around this list is not a hole to close, the
review before integrating is the safety net. Do not grow this file for a new trick.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
import tempfile
from pathlib import Path
from typing import Any

from . import backlog, util
from .config import Project

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
    "push-main": "pushing to the main branch (or forcing a push) skips the owner's integration",
    "delete": "it deletes something that is not this task's (outside its worktree, or another branch)",
    "config": "it changes the project's config (fm.toml)",
}


def segments(cmd: str) -> list[list[str]]:
    """The simple commands of a shell line, as words; operators inside quotes are words, not separators."""
    lex = shlex.shlex(cmd.replace("\n", " ; "), posix=True, punctuation_chars=";&|<>")
    lex.whitespace_split = True
    out: list[list[str]] = [[]]
    try:
        for tok in lex:
            if tok in OPERATORS:
                out.append([])
            else:
                out[-1].append(tok)
    except ValueError:  # unbalanced quotes: fall back to plain words
        return [cmd.split()]
    return [words(s) for s in out if s]


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


def words(w: list[str]) -> list[str]:
    w = list(w)
    while w and (re.match(r"^\w+=", w[0]) or w[0] in ("sudo", "env", "command", "exec", "time", "nohup")):
        w = w[1:]
    if w:
        w[0] = os.path.basename(w[0])
    return w


def _mine(path: str, cwd: str, own: list[Path]) -> bool:
    p = Path(os.path.expanduser(path))
    p = (Path(cwd) / p if not p.is_absolute() else p).resolve()
    roots = [*own, Path(tempfile.gettempdir()).resolve(), Path("/tmp").resolve()]
    return any(p == r or r in p.parents for r in roots)


def check_bash(cmd: str, cwd: str, own: list[Path], branch: str, config: Path, extra: list[dict[str, str]], mains: set[str] = MAIN) -> str | None:  # noqa: C901, PLR0912, PLR0913
    """The name of the first action of the list that `cmd` runs, or None."""
    for w in segments(cmd):
        if not w:
            continue
        seg = " ".join(w)
        prog, args = w[0], w[1:]
        while prog == "git" and args[:1] and args[0] in ("-C", "-c"):
            args = args[2:]
        sub = next((a for a in args if not a.startswith("-")), "")
        if (prog, sub) in PUBLISH and (prog != "gh" or (args[1:2] and args[1] in ("create", "upload", "merge", "edit", "delete"))):
            return "publish"
        if prog == "git" and sub == "push":
            rest = args[args.index("push") + 1 :]
            refs = [a for a in rest if not a.startswith("-")]
            if {"--delete", "-d"} & set(rest) or any(r.startswith(":") for r in refs[1:]):
                return "delete"
            if {"-f", "--force", "--force-with-lease", "--mirror", "--all"} & set(rest):
                return "push-main"
            dests = [r.split(":")[-1].removeprefix("refs/heads/") for r in refs[1:]] or [branch]
            if any((d if d != "HEAD" else branch) in mains | {""} for d in dests):
                return "push-main"
        if (
            prog == "git"
            and sub == "branch"
            and any(a in ("-d", "-D", "--delete") for a in args)
            and any(a != branch for a in args[1:] if not a.startswith("-"))
        ):  # noqa: E501
            return "delete"
        if prog == "git" and sub == "worktree" and "remove" in args:
            return "delete"
        if prog in ("rm", "rmdir", "unlink", "trash") or (prog == "find" and "-delete" in args):
            targets = [a for a in args if not a.startswith("-")] if prog != "find" else args[:1]
            if any(not _mine(t, cwd, own) for t in targets):
                return "delete"
        if writes(w, cwd, config.resolve()):
            return "config"
        for rule in extra:
            if re.search(rule.get("pattern", "$^"), seg):
                return rule.get("name", "owner rule")
    return None


def check(p: Project, tool: str, inp: dict[str, Any], cwd: str, issue: dict[str, Any] | None) -> str | None:
    own = [Path(issue["worktree"]).resolve()] if issue and issue.get("worktree") else []
    branch = (issue or {}).get("branch", "")
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        f = inp.get("file_path") or inp.get("notebook_path") or ""
        if f and Path(cwd, f).resolve() == p.config_file.resolve():
            return "config"
        return None
    if tool == "Bash":
        return check_bash(str(inp.get("command", "")), cwd, own, branch, p.config_file, p.get("guard", "ask"), MAIN | {p.get("project", "main")})
    return None


def granted(p: Project, iid: str, action: str) -> bool:
    """Spends the owner's one-time allowance for this issue and action, if there is one."""
    with util.locked_json(p.state / "grants.json", []) as grants:
        for g in grants:
            if g["issue"] == iid and g["action"] == action and not g.get("used"):
                g["used"] = util.now()
                return True
    return False


def grant(p: Project, iid: str, action: str) -> None:
    with util.locked_json(p.state / "grants.json", []) as grants:
        grants.append({"issue": iid, "action": action, "at": util.now()})


def hook(stdin: str) -> str:
    """The PreToolUse hook: the JSON to print (empty: let it run). Never raises: a guard error lets the call run."""
    try:
        home, iid = os.environ.get("FM_HOME"), os.environ.get("FM_ISSUE", "")
        if not home:
            return ""
        d = json.loads(stdin or "{}")
        p = Project.load(home)
        try:
            issue = backlog.get(backlog.load(p), iid)
        except backlog.BacklogError:
            issue = None
        action = check(p, d.get("tool_name", ""), util.sub(d, "tool_input"), d.get("cwd") or os.getcwd(), issue)
        if not action or granted(p, iid, action):
            return ""
        util.append_jsonl(
            p.state / "guard.jsonl", {"time": util.now(), "issue": iid, "action": action, "tool": d.get("tool_name"), "input": d.get("tool_input")}
        )
        why = WHY.get(action, "the owner asked to be asked first")
        msg = (
            f"The owner asked to be asked before this ({action}: {why}). Do not look for another way to do it. "
            f'If it is needed, run `fm ask --action {action} "what you want to run and why"` and end your turn; '
            "if he allows it, you will be told, and it passes once."
        )
        return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": msg}})
    except Exception as e:  # noqa: BLE001 - a broken guard must not stop the work
        print(f"fm guard error (allowed): {e}", file=sys.stderr)
        return ""
