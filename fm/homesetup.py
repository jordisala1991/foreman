"""`fm init`: a home with fm.toml, backlog.json and an empty instructions.md (the owner's own rules for the project,
given to every session in it). What already exists is never changed. The define-task skill, fm's instructions and the
fm command reach Claude Code through the plugin `fm@fm`: init prints the two lines that install it."""

from pathlib import Path

from . import backlog, util
from .config import Project

INSTALL = ("claude plugin marketplace add jordisala1991/foreman#foreman-next", "claude plugin install fm@fm")


def init(home: Path, prefix: str, repo: str) -> list[str]:
    home.mkdir(parents=True, exist_ok=True)
    _fm_toml(home, prefix, repo)
    p = Project.load(home)
    if not p.backlog_file.exists():
        util.write_json(p.backlog_file, backlog.empty())
    if not (home / "instructions.md").exists():
        (home / "instructions.md").touch()  # empty: the owner writes the project's rules in it
    return ["To get the define-task skill, fm's instructions and the fm command in your Claude Code sessions, run:", *INSTALL]


def _fm_toml(home: Path, prefix: str, repo: str) -> None:
    f = home / "fm.toml"
    if f.exists():
        return
    f.write_text(
        f'[project]\nname = "{home.name}"\nprefix = "{prefix}"\nrepo = "{repo}"\nmain = "main"\n\n'
        '[agents]\nslots = 2\nmodel = "sonnet"\neffort = "medium"\n'
        'planning_model = "sonnet"\nplanning_effort = "high"\nreview_model = "sonnet"\nreview_effort = "high"\n\n'
        '[merge]\nautonomy = "manual"  # manual | low | medium\nprovider = "local"\nstrategy = "squash"  # squash | merge\ntest = ""\n',
        encoding="utf-8",
    )
