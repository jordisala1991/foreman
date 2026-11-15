"""`fm init`: a home with fm.toml, backlog.json, and the shared skill and instructions linked from `fm/shared/` of the
checkout it runs from (a merge reaches every home when that checkout moves). What already exists is never changed: a
file of the owner's is reported, with its difference from the shared one."""

import difflib
import tomllib
from pathlib import Path

from . import backlog, util
from .config import Project

SHARED = Path(__file__).parent / "shared"
IMPORT = "@.claude/fm.md"
FM_BIN = Path(__file__).resolve().parent.parent / "bin/fm"


def init(home: Path, prefix: str, repo: str) -> list[str]:
    home.mkdir(parents=True, exist_ok=True)
    lines = _fm_toml(home, prefix, repo)
    p = Project.load(home)
    if not p.backlog_file.exists():
        util.write_json(p.backlog_file, backlog.empty())
    for path, target in ((".claude/fm.md", SHARED / "instructions.md"), (".claude/skills/define-task", SHARED / "skills/define-task")):
        lines += _link(home, path, target)
    return lines + _claude_md(home, p.name)


def _fm_toml(home: Path, prefix: str, repo: str) -> list[str]:
    f = home / "fm.toml"
    if f.exists():
        if tomllib.loads(f.read_text(encoding="utf-8")).get("project", {}).get("fm"):
            return []
        return [f'fm.toml has no [project] fm: add  fm = "{FM_BIN}"']
    f.write_text(
        f'[project]\nname = "{home.name}"\nprefix = "{prefix}"\nrepo = "{repo}"\nmain = "main"\nfm = "{FM_BIN}"\n\n'
        '[agents]\nslots = 2\nmodel = "sonnet"\neffort = "medium"\n'
        'planning_model = "sonnet"\nplanning_effort = "high"\nreview_model = "sonnet"\nreview_effort = "high"\n\n'
        '[merge]\nautonomy = "manual"  # manual | low | medium\nprovider = "local"\nstrategy = "squash"  # squash | merge\ntest = ""\n',
        encoding="utf-8",
    )
    return []


def _link(home: Path, path: str, target: Path) -> list[str]:
    at = home / path
    if at.is_symlink() and at.readlink() == target:
        return []
    if not at.exists() and not at.is_symlink():
        at.parent.mkdir(parents=True, exist_ok=True)
        at.symlink_to(target)
        return [f"linked {path}"]
    ours, theirs = (t / "SKILL.md" if t.is_dir() else t for t in (target, at))
    diff = list(difflib.unified_diff(_text(theirs), _text(ours), f"{path} (home)", f"{path} (shared)"))
    if not diff:
        return [f"{path} is a copy of the shared one: left as it is"]
    return [f"{path} differs from the shared one: left as it is", "".join(diff).rstrip("\n")]


def _text(f: Path) -> list[str]:
    return f.read_text(encoding="utf-8").splitlines(keepends=True) if f.is_file() else []


def _claude_md(home: Path, name: str) -> list[str]:
    f = home / "CLAUDE.md"
    if not f.exists():
        f.write_text(
            f"# {name}: the project's home\n\n{IMPORT}\n\nYour own rules go here: the language, how to talk to the owner, the project's rules.\n",
            encoding="utf-8",
        )
        return []
    if IMPORT in (line.strip() for line in f.read_text(encoding="utf-8").splitlines()):
        return []
    return [f"CLAUDE.md does not pull in the shared instructions: add the line {IMPORT}"]
