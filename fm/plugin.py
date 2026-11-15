"""fm as a Claude Code plugin (`fm@fm`, from this repo's marketplace `fm`): what a session opened in a folder of a
registered project is given at its start (the hook `fm session-context`), and the values `fm project` shows the skill.
A session in no project's folder, or one of fm's own agents, is given nothing."""

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from . import config
from .config import Project, Unreadable

SHARED = Path(__file__).parent / "shared"
PLUGIN = "fm@fm"
PLACEHOLDERS = ("project", "prefix", "home", "repos", "main", "slots")


def values(p: Project) -> dict[str, str]:
    return {
        "project": p.name,
        "prefix": str(p.get("project", "prefix")),
        "home": str(p.home),
        "repos": ", ".join(str(r) for r in p.repos),
        "main": str(p.get("project", "main")),
        "slots": str(p.get("agents", "slots")),
    }


def render(text: str, vals: Mapping[str, str]) -> str:
    for k, v in vals.items():
        text = text.replace("{" + k + "}", v)
    return text


def instructions(p: Project) -> str:
    """The shared instructions with the project's values, then the owner's rules from <home>/instructions.md."""
    text = render((SHARED / "instructions.md").read_text(encoding="utf-8"), values(p))
    own = p.home / "instructions.md"
    rules = own.read_text(encoding="utf-8").strip() if own.is_file() else ""
    return text + (f"\n\n## Rules of this project (instructions.md in its home)\n\n{rules}\n" if rules else "")


def session_context(stdin: str, env: Mapping[str, str], projects: Sequence[Project | Unreadable]) -> str:
    """The SessionStart hook's output for a session in exactly one registered project's folder; "" otherwise."""
    if "FM_TASK" in env:  # an agent fm launched: its brief is its only instructions
        return ""
    try:
        cwd: Any = json.loads(stdin)["cwd"]
    except (json.JSONDecodeError, TypeError, KeyError):  # not Claude Code's event: no folder to go by
        return ""
    if not isinstance(cwd, str):
        return ""
    found = config.owners(projects, Path(cwd))
    if len(found) != 1 or not isinstance(p := found[0][0], Project):
        return ""
    return json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": instructions(p)}})
