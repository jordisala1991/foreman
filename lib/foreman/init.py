"""`foreman init`: a new project's home, made in one go and without questions (the mechanical part of starting).

    foreman init HOME --name NAME [--owner TEXT] [--language en|es] [--repo KEY=PATH]… [--local-ci [KEY=]CMD]…
                 [--slots N] [--every-min N] [--sample [--prefix PX]] [--mode active|inactive] [--no-register]
                 [--dry-run]

Each step is idempotent: a step whose result is already there says so and writes nothing; one that would replace
something different stops the whole command with exit 2 before anything is written (every step is planned and
checked first, then written):

1. `HOME/foreman.toml`, minimal: `[project]` (name, `config_schema`, and owner and language when given), `[paths]
   worktrees` only when the default folder (`<home>/../wt`) would fall inside a repo (the agents' worktrees never go
   inside one), `[agents] production_slots` and `[loop] every_min` when they differ from the default, and one
   `[repos.KEY]` per repo with only what differs from REPO_DEFAULTS (its main branch, its remote or `"none"`, its
   GitHub repo, its `local_ci`). It is validated with config.problems before it is written. One that exists with
   other text is never rewritten: it is kept as it is when it names this project and no option that shapes it is
   given (keep_toml: the steps below are then made from it, so a home with no backlog gets one), else exit 2.
2. `HOME/rules.md`, the starting rules in the project's language (i18n `init`). One that exists is kept as it is:
   it is the owner's to fill in.
3. `notes/` and `reviews/`.
4. The backlog (Backlog.init) and, with --sample, one issue in triage with the id `<PX>-1` (PX: the first two letters
   of the name, or --prefix) so the board is not empty.
5. The registry (config.registry_path): a `[[project]]` entry (name, home, mode) appended to its text, which is kept
   byte for byte; written atomically under a lock and read back with registry_scan (put back as it was if it no
   longer reads). A name or home already registered with other values is exit 2; a registry that does not read is
   left alone (exit 2). --no-register skips it.

Then it prints `foreman check` of the new project and what to do next. --dry-run writes nothing: it prints what each
step would do, with the two files and the registry entry whole.
Nothing is written outside HOME, the registry (and the lock next to the backlog). It installs nothing: the plugin
and the panel service (`foreman panel service install`) are separate.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import backlog, config, i18n, util

if TYPE_CHECKING:
    from .config import Table

NAME = r"^[a-z0-9][a-z0-9._-]*$"  # [project] name (config.problems)
REPO_KEY = r"^[a-z0-9][a-z0-9_-]*$"
PREFIX = r"^[A-Z]{2}$"  # the default [ids] issue pattern is two capitals, a dash and a number
GITHUB = re.compile(r"github\.com[:/]+([\w.-]+/[\w.-]+?)(?:\.git)?/?$")
LANGUAGES = ("en", "es")  # the languages of lib/foreman/i18n/


class InitError(Exception):
    """What stops `foreman init` before it writes anything (exit 2)."""


def add_parser(sp: Any) -> None:
    a = sp.add_parser("init", help="make a new project's home and register it (foreman.toml, rules, backlog)")
    a.add_argument("home", help="the project's home: the folder for foreman.toml, the backlog, notes and reports")
    a.add_argument("--name", required=True, help="the project's name (lowercase letters, digits, . _ -)")
    a.add_argument("--owner", help="who decides (default: owner)")
    a.add_argument("--language", choices=LANGUAGES, help="of the notes and notices (default: en)")
    a.add_argument("--repo", action="append", default=[], metavar="KEY=PATH", help="a code repo (repeat for each)")
    a.add_argument(
        "--local-ci",
        action="append",
        default=[],
        metavar="[KEY=]CMD",
        help="the command that checks a repo's code (KEY= may be left out with one repo)",
    )
    a.add_argument("--slots", type=int, metavar="N", help="production agents at a time (default 1)")
    a.add_argument("--every-min", type=int, metavar="N", help="minutes between loop passes (default 60)")
    a.add_argument("--sample", action="store_true", help="a sample issue in triage, so the board is not empty")
    a.add_argument("--prefix", help="with --sample: the two capitals of its id (default: from the name)")
    a.add_argument("--mode", choices=config.MODES, default="active", help="in the registry (default active)")
    a.add_argument("--no-register", action="store_true", help="leave the registry alone")
    a.add_argument("--dry-run", action="store_true", help="write nothing: print what each step would do")


# ---------- the plan ----------


@dataclass
class Repo:
    key: str
    path: Path
    table: dict[str, Any] = field(default_factory=dict)  # only the keys that differ from REPO_DEFAULTS


@dataclass
class Plan:
    home: Path
    name: str
    mode: str
    language: str
    owner: str
    toml: str = ""
    rules: str = ""
    sample: tuple[str, str, str, str] | None = None  # (id, title, detail, criterion)
    register: bool = True
    # each step as decided: (what, said after writing, said by --dry-run); the same text twice when already there
    steps: list[tuple[str, str, str]] = field(default_factory=list)
    writes: dict[str, bool] = field(default_factory=dict)  # step -> it has something to write

    def say(self, what: object, done: str, would: str | None = None) -> None:
        text = str(what)
        if text.startswith(f"{self.home}/"):  # the home's own files by their name (the first line names the home)
            text = text[len(f"{self.home}/") :]
        self.steps.append((text, done, would or done))


def toml_value(v: Any) -> str:
    """A TOML value: a JSON string is a TOML basic string (same escapes); ints as they are."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    return json.dumps(str(v), ensure_ascii=False)


def git_root(path: Path) -> Path | None:
    """The top of the git checkout `path` (or its nearest existing parent) is in, or None."""
    d = path
    while not d.exists() and d != d.parent:
        d = d.parent
    top = util.git(d, "rev-parse", "--show-toplevel", check=False)
    return Path(top).resolve() if top else None


def inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def repo_table(path: Path, local_ci: str) -> dict[str, Any]:
    """The [repos.KEY] keys of a repo that differ from foreman's defaults, read from git."""
    t: dict[str, Any] = {}
    top = git_root(path)
    is_git = top is not None and top == path
    if is_git:
        main = util.git(path, "symbolic-ref", "--short", "HEAD", check=False)
        if main and main != config.REPO_DEFAULTS["main"]:
            t["main"] = main
    remotes = util.git(path, "remote", check=False).split() if is_git else []
    remote = "origin" if "origin" in remotes else (remotes[0] if remotes else "none")
    if remote != config.REPO_DEFAULTS["remote"]:
        t["remote"] = remote
    if remote == "none":
        t["remote_checks"] = "none"
    else:
        m = GITHUB.search(util.git(path, "remote", "get-url", remote, check=False))
        if m:
            t["github"] = m.group(1)
    if local_ci:
        t["local_ci"] = local_ci
    return t


def parse_repos(args: argparse.Namespace, cwd: Path) -> list[Repo]:
    repos: list[Repo] = []
    for text in args.repo:
        key, sep, where = text.partition("=")
        if not sep:  # a bare path: the key is its folder's name
            where, key = text, Path(text).expanduser().resolve().name.lower()
        if not re.match(REPO_KEY, key):
            raise InitError(f"--repo {text}: the key {key!r} must be lowercase letters, digits, _ and -")
        if any(r.key == key for r in repos):
            raise InitError(f"--repo {text}: repo {key} given twice")
        path = (cwd / Path(where).expanduser()).resolve()
        if not path.is_dir():
            raise InitError(f"--repo {text}: {path} is not a folder")
        repos.append(Repo(key, path))
    ci: dict[str, str] = {}
    for text in args.local_ci:
        key, sep, cmd = text.partition("=")
        if not (sep and any(r.key == key for r in repos)):
            if len(repos) != 1:
                raise InitError(
                    f"--local-ci {text}: name its repo, KEY=CMD (repos: {', '.join(r.key for r in repos) or 'none'})"
                )
            key, cmd = repos[0].key, text
        if not cmd.strip():
            raise InitError(f"--local-ci {text}: no command")
        if key in ci:
            raise InitError(f"--local-ci: repo {key} given twice")
        ci[key] = cmd.strip()
    for r in repos:
        r.table = repo_table(r.path, ci.get(r.key, ""))
    return repos


def worktrees_folder(home: Path, repos: list[Repo]) -> Path | None:
    """The [paths] worktrees to write, or None for the default: the default (`<home>/../wt`) when it is inside no
    repo, else `<repo>-wt` beside the repo that holds it."""
    default = home.parent / "wt"
    roots = [r.path for r in repos]
    top = git_root(home)
    if top:
        roots.append(top)
    for root in sorted(roots, key=lambda p: len(str(p))):
        if inside(default, root):
            return root.parent / f"{root.name}-wt"
    return None


def render_toml(plan: Plan, args: argparse.Namespace, repos: list[Repo], worktrees: Path | None) -> str:
    out = [
        f"# foreman's configuration of {plan.name}, written by `foreman init`: only what differs from the defaults.",
        "# Every key and its default: `foreman docs config`; the values foreman uses: `foreman config`.",
        "",
        "[project]",
        f"name = {toml_value(plan.name)}",
    ]
    if args.owner:
        out.append(f"owner = {toml_value(args.owner)}")
    if args.language and args.language != config.DEFAULTS["project"]["language"]:
        out.append(f"language = {toml_value(args.language)}")
    out.append(f"config_schema = {config.CONFIG_SCHEMA}")
    if worktrees:
        out += ["", "[paths]", f"worktrees = {toml_value(worktrees)}   # the agents' worktrees, outside the repo"]
    if args.slots is not None and args.slots != config.DEFAULTS["agents"]["production_slots"]:
        out += ["", "[agents]", f"production_slots = {args.slots}"]
    if args.every_min is not None and args.every_min != config.DEFAULTS["loop"]["every_min"]:
        out += ["", "[loop]", f"every_min = {args.every_min}"]
    for r in repos:
        rel = os.path.relpath(r.path, plan.home) if inside(plan.home, r.path) else str(r.path)
        out += ["", f"[repos.{r.key}]", f"path = {toml_value(rel)}"]
        out += [f"{k} = {toml_value(v)}" for k, v in r.table.items()]
    return "\n".join(out) + "\n"


def render_rules(plan: Plan, repos: list[Repo]) -> str:
    t = i18n.strings(plan.language)["init"]
    checks = [t["check_repo"].format(repo=r.key, command=r.table["local_ci"]) for r in repos if r.table.get("local_ci")]
    owner = plan.owner if plan.owner != config.DEFAULTS["project"]["owner"] else t["owner"]
    return t["rules"].format(name=plan.name, owner=owner, checks="\n".join(checks) or t["check_none"])


def sample_prefix(name: str, given: str | None) -> str:
    if given:
        if not re.match(PREFIX, given):
            raise InitError(f"--prefix {given}: two capital letters, such as DE")
        return given
    letters = re.sub(r"[^a-z]", "", name).upper()[:2]
    if len(letters) < 2:
        raise InitError(f"--sample: the name {name!r} has fewer than two letters for the issue ids: give --prefix")
    return letters


def make_plan(args: argparse.Namespace, cwd: Path) -> Plan:
    """Every step decided and checked, nothing written: InitError for anything that would stop it."""
    if not re.match(NAME, args.name):
        raise InitError(f"--name {args.name}: lowercase letters, digits, . _ - (starting with a letter or digit)")
    for flag, v in (("--slots", args.slots), ("--every-min", args.every_min)):
        if v is not None and v < 1:
            raise InitError(f"{flag} {v}: a whole number, 1 or more")
    if args.prefix and not args.sample:
        raise InitError("--prefix goes with --sample (the sample issue's id)")
    home = (cwd / Path(args.home).expanduser()).resolve()
    if home.exists() and not home.is_dir():
        raise InitError(f"{home} is not a folder")
    language = args.language or config.DEFAULTS["project"]["language"]
    plan = Plan(
        home,
        args.name,
        args.mode,
        language,
        args.owner or config.DEFAULTS["project"]["owner"],
        register=not args.no_register,
    )
    repos = parse_repos(args, cwd)
    plan.toml = render_toml(plan, args, repos, worktrees_folder(home, repos))
    err, warn = config.problems(parse_toml(plan.toml))
    if err or warn:  # a bug of init's, never the user's: what it writes must load strictly
        raise InitError("foreman.toml would not load: " + "; ".join(err + warn))
    keep_toml(plan, args)
    plan.rules = render_rules(plan, repos)
    if args.sample:
        t = i18n.strings(language)["init"]
        iid = f"{sample_prefix(args.name, args.prefix)}-1"
        plan.sample = (iid, t["sample_title"], t["sample_detail"], t["sample_criterion"])
    plan_files(plan)
    plan_backlog(plan)
    if plan.register:
        plan_registry(plan, config.registry_path().read_text() if config.registry_path().exists() else "")
    else:
        plan.say("registry", "left alone (--no-register)")
    return plan


TOML_OPTIONS = ("owner", "language", "repo", "local_ci", "slots", "every_min")  # what only shapes foreman.toml


def keep_toml(plan: Plan, args: argparse.Namespace) -> None:
    """An existing foreman.toml with other text: kept as it is when it names this project and no option would shape
    it (the rest of the home, the backlog among it, is then made from it); else InitError, as init never rewrites
    it."""
    f = plan.home / "foreman.toml"
    if not f.exists() or f.read_text() == plan.toml:
        return
    given = [f"--{o.replace('_', '-')}" for o in TOML_OPTIONS if getattr(args, o) not in (None, [])]
    if given:
        raise InitError(
            f"{f} exists with other values: init never rewrites it (edit it, or init another folder; without "
            f"{', '.join(given)} it is kept as it is and only what is missing is made)"
        )
    text = f.read_text()
    from . import toml_reader

    try:
        raw = parse_toml(text)
    except toml_reader.TOMLDecodeError as e:
        raise InitError(f"{f} does not read: {e}") from e
    err, _ = config.problems(raw)
    if err:
        raise InitError(f"{f}: " + "; ".join(err))
    name = raw.get("project", {}).get("name")
    if name != plan.name:
        raise InitError(f"{f} is the home of {name!r}, not of {plan.name!r}: init never rewrites it")
    merged = config.merge_defaults(raw)
    plan.toml, plan.language, plan.owner = text, merged["project"]["language"], merged["project"]["owner"]


def parse_toml(text: str) -> Table:
    from . import toml_reader

    return toml_reader.loads(text)


def plan_files(plan: Plan) -> None:
    f = plan.home / "foreman.toml"
    if f.exists():
        if f.read_text() != plan.toml:
            raise InitError(f"{f} exists with other values: init never rewrites it (edit it, or init another folder)")
        plan.say(f, "already there")
    else:
        plan.writes["toml"] = True
        plan.say(f, "written", "would be written")
    r = rules_path(plan)
    if r.exists():
        plan.say(r, "already there (kept as it is)")
    else:
        plan.writes["rules"] = True
        plan.say(r, "written", "would be written")
    for d, p in folders(plan).items():
        if p.is_dir():
            plan.say(f"{p}/", "already there")
        elif p.exists():
            raise InitError(f"{p} exists and is not a folder")
        else:
            plan.writes[d] = True
            plan.say(f"{p}/", "made", "would be made")


def project_of(plan: Plan) -> config.Project:
    """The project as foreman.toml will load it (in memory: it may not be written yet)."""
    return config.Project(plan.home, config.merge_defaults(parse_toml(plan.toml)), plan.mode)


def rules_path(plan: Plan) -> Path:
    """The rules file as foreman.toml names it (the default for the one init writes; a kept one may name another)."""
    p = project_of(plan)
    return p.path(p.raw["project"]["rules"])


def folders(plan: Plan) -> dict[str, Path]:
    p = project_of(plan)
    return {"notes": p.notes_dir, "reviews": p.reviews_dir}


def plan_backlog(plan: Plan) -> None:
    b = backlog.Backlog(project_of(plan))
    data: backlog.Data = {}
    if not b.exists():
        plan.writes["backlog"] = True
        plan.say(b.data_path, "made", "would be made")
    else:
        try:
            data = b.load()
        except backlog.BacklogError as e:
            raise InitError(f"{b.data_path}: {e}") from e
        plan.say(b.data_path, "already there")
    if plan.sample:
        iid = plan.sample[0]
        if data and any(i["id"] == iid for _, i, _ in b.iter_issues(data)):
            plan.say(f"sample issue {iid}", "already there")
        else:
            plan.writes["sample"] = True
            plan.say(f"sample issue {iid}", "added, in triage", "would be added, in triage")


def registry_entry(plan: Plan) -> str:
    return (
        f"[[project]]\nname = {toml_value(plan.name)}\nhome = {toml_value(plan.home)}\nmode = {toml_value(plan.mode)}\n"
    )


def appended(text: str, entry: str) -> str:
    """The registry's text with `entry` after it: what was there is kept byte for byte."""
    if not text:
        return entry
    return text + ("" if text.endswith("\n") else "\n") + "\n" + entry


def plan_registry(plan: Plan, text: str) -> None:
    """Decides the registry step from its text: already there, to append, or InitError."""
    reg = config.registry_path()
    try:
        entries, broken, _ = config.registry_scan(parse_toml(text) if text else {})
    except (config.ConfigError, ValueError) as e:
        raise InitError(f"{reg} does not read ({e}): init leaves it alone; fix it first") from e
    for e in [*entries, *broken]:
        home = e["home"].resolve() if e["home"] else None
        same_name, same_home = e["name"] == plan.name, home == plan.home
        if same_name and same_home and e["mode"] == plan.mode and e in entries and not e["config"]:
            plan.say(f"{reg}: {plan.name}", "already there")
            return
        if same_name:
            raise InitError(
                f"{reg}: {plan.name} is already registered (home {e['home']}, mode {e['mode']}): "
                "pick another name, or change that entry by hand"
            )
        if same_home:
            raise InitError(f"{reg}: {plan.home} is already the home of {e['name']}")
    plan.writes["registry"] = True
    plan.say(f"{reg}: {plan.name}", f"added, {plan.mode}", f"would be added, {plan.mode}")


# ---------- writing ----------


def write(plan: Plan) -> None:
    if plan.writes.get("toml"):
        util.write_text_atomic(plan.home / "foreman.toml", plan.toml)
    if plan.writes.get("rules"):
        util.write_text_atomic(rules_path(plan), plan.rules)
    for d, p in folders(plan).items():
        if plan.writes.get(d):
            p.mkdir(parents=True, exist_ok=True)
    p = config.load_project(plan.home, plan.mode, strict=True)
    b = backlog.Backlog(p)
    if plan.writes.get("backlog"):
        b.init()
    if plan.sample and plan.writes.get("sample"):
        iid, title, detail, criterion = plan.sample
        with b.mutate() as data:
            b.add_issue(data, None, iid, title, status="triage", detail=detail, criterion=criterion)
    if plan.writes.get("registry"):
        write_registry(plan)


def write_registry(plan: Plan) -> None:
    """Appends the entry under a lock (in the state root, so no file appears beside the registry), checking again
    what another writer may have added meanwhile, and puts the old text back if the new one does not read."""
    reg = config.registry_path()
    with util.file_lock(reg, config.state_root() / "locks-registry"):
        before = reg.read_text() if reg.exists() else ""
        plan.steps = [s for s in plan.steps if not s[0].startswith(f"{reg}:")]
        plan_registry(plan, before)
        if not plan.writes.get("registry"):
            return
        util.write_text_atomic(reg, appended(before, registry_entry(plan)))
        try:
            entries, _, _ = config.registry_scan()
            if not any(e["name"] == plan.name for e in entries):
                raise config.ConfigError(f"{plan.name} is not among its projects")
        except config.ConfigError as e:
            util.write_text_atomic(reg, before)
            raise InitError(f"{reg}: the new entry did not read back ({e}); put back as it was") from e


# ---------- the command ----------


def dry_run(plan: Plan) -> None:
    print(f"foreman init {plan.home} (dry run: nothing written)")
    for what, _, would in plan.steps:
        print(f"  {what}: {would}")
    if plan.writes.get("toml"):
        print(f"\n--- {plan.home / 'foreman.toml'}\n{plan.toml}", end="")
    if plan.writes.get("rules"):
        print(f"\n--- {rules_path(plan)}\n{plan.rules}", end="")
    if plan.writes.get("registry"):
        print(f"\n--- appended to {config.registry_path()}\n{registry_entry(plan)}", end="")


def run(args: argparse.Namespace) -> int:
    try:
        plan = make_plan(args, Path.cwd())
        if args.dry_run:
            dry_run(plan)
            return 0
        write(plan)
    except (InitError, config.ConfigError, backlog.BacklogError) as e:
        print(f"foreman init: {e}", file=sys.stderr)
        return 2
    print(f"foreman init {plan.home}")
    for what, done, _ in plan.steps:
        print(f"  {what}: {done}")
    print()
    from . import cli

    rc = cli.check(config.load_project(plan.home, plan.mode))
    print()
    print(
        f"Next: open a new Claude Code session in {plan.home} and type `/foreman:setup` there for a guided first pass "
        "(or run `foreman start`)."
    )
    if not plan.register:
        print("The project is not in the registry (--no-register): foreman's hooks do not act for it until it is.")
    elif plan.mode == "inactive":
        print('The project is registered as inactive: foreman\'s hooks act for it once its mode is "active".')
    return rc
