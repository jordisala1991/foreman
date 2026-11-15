"""`fm init`: a home with fm.toml, backlog.json and instructions.md (the owner's own rules for the project). What
already exists is never changed. The define-task skill, fm's instructions and the fm command reach Claude Code through
the plugin `fm@fm`: init prints the two lines that install it."""

from pathlib import Path

from . import backlog, util
from .config import Project

FM_BIN = Path(__file__).resolve().parent.parent / "bin/fm"
INSTALL = ("claude plugin marketplace add jordisala1991/foreman#foreman-next", "claude plugin install fm@fm")
RULES = "Your own rules for this project go here: the language, how to talk to the owner, the project's rules.\n"


def init(home: Path, prefix: str, repo: str) -> list[str]:
    home.mkdir(parents=True, exist_ok=True)
    _fm_toml(home, prefix, repo)
    p = Project.load(home)
    if not p.backlog_file.exists():
        util.write_json(p.backlog_file, backlog.empty())
    if not (home / "instructions.md").exists():
        (home / "instructions.md").write_text(RULES, encoding="utf-8")
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
