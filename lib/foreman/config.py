"""Project configuration (foreman.toml), the global registry and where each project keeps its state.

A project lives in its *home*: a small directory (usually its own git repo) with `foreman.toml`, its backlog, notes and
reviews. The registry (`~/.config/foreman/projects.toml`, or $FOREMAN_REGISTRY) lists the homes:

    [[project]]
    name = "example"
    home = "~/Projects/example/ops"
    mode = "active"            # "inactive": set up, hooks off
    config = ""                # optional: foreman.toml somewhere else (a project whose home must not be touched)

    [panel]                    # optional: the user's defaults for `foreman panel`
    language = "en"            # interface language
    who = "owner"              # the name the panel uses when the owner takes a lock

    [resources.gpu]            # optional: a machine resource, declared once for every product (MACHINE_KEYS)
    dir = "~/.gpu-lock"        # paths: `~` expanded, relative ones against the registry's folder
    busy = "~/.config/foreman/bin/gpu-busy.sh"

    [machine.project]          # optional: settings every product inherits, shaped like a foreman.toml
    owner = "Ada"              # (MACHINE_SETTINGS: which keys; the rest stay each product's own)

    [machine.deny]
    patterns = ["npm publish"]

A product uses a machine resource by declaring `[resources.NAME]` in its foreman.toml with only its own keys
(PRODUCT_KEYS: guard, allow), or an empty table. The machine wins: the merged table is resolved once, when the project
loads (load_project), so `foreman lock`, the guard, `foreman wait` and the loop pass all read the same one; machine
keys a product repeats there are ignored with a warning (machine_resources, merge_machine).

The registry's [machine] holds settings every product inherits (machine_settings, merge_settings): defaults <
machine < product. A scalar of the product wins; lists add up (the machine's first, without repeats); tables merge
key by key with the same rule. A product removes an inherited deny rule with `[deny] drop` (a pattern as written, or
"group:NAME"). A bad value in [machine] leaves out only that value, with a warning in every product.

Which project a command is about, in order: --project NAME, $FOREMAN_HOME (a home directory), $FOREMAN_PROJECT
(a registry name), then the current directory (inside a home, a repo, a worktree or the workspace of a registered
project: see Project.owns).
Hooks only use the registry and only act for `active` projects: outside them they do nothing.
Every relative path in foreman.toml is relative to the home; `~` is expanded. The schema is in docs/config.md.

Reading is tolerant: a section or key this foreman does not know (a newer foreman's, or a typo) is a *warning*, kept
in Project.warnings, and the project still loads; a bad value of a known key is an *error* and the project does not
load. The panel, the hooks and the rest of the CLI read tolerantly and show the warnings; `foreman check` and the
selftest are strict (a warning fails them), and so is `foreman integrate` for unknown keys in [repos.*].
`[project] config_schema` says which schema a foreman.toml was written for; CONFIG_SCHEMA is the highest this foreman
knows, and it goes up with every new key, so a file from a newer foreman says so instead of looking like a typo.
In the registry, an entry this foreman cannot use (an unknown mode, no name or home) leaves that project out, listed
as broken, and the others load.
"""

from __future__ import annotations

import copy
import os
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict

from . import toml_reader

if TYPE_CHECKING:
    from .util import PathLike

MERGE_METHODS = ("squash", "merge", "rebase")
MODES = ("active", "inactive")
# where a value of `foreman config --json` comes from (Project.as_dict): foreman's default, the product's foreman.toml,
# the registry (a machine resource's keys, a [machine] setting), both (a list or table the registry's [machine] and
# the foreman.toml add to) or the environment (FOREMAN_STATE_HOME, for the default state folder)
SOURCES = ("default", "foreman.toml", "registry", "registry+foreman.toml", "environment")
# The highest foreman.toml schema this foreman understands. It goes up by one with every new key or section (and
# tests/config_schema.json records the keys of each schema: the selftest fails if they differ). Keys that ship in the
# same release share one schema: add them to the newest list of tests/config_schema.json while it is unreleased.
CONFIG_SCHEMA = 21
# Keys foreman no longer reads, (section, key): what to tell the owner. One left in a foreman.toml is a warning (it
# loads, ignored; `foreman check` names it), never an error, so an old file keeps working until it is cleaned.
REMOVED: dict[tuple[str, str], str] = {
    ("agents", "name_phrase"): "agents get their name from foreman's launch line",
    ("strings", "panel"): "the panel's texts are foreman's; [strings] still overrides the CLI's",
    ("loop", "renew"): "the orchestrator's memory always renews in place (docs/orchestrator.md, «Renewal»)",
    ("notices", "info"): "foreman's notices are the same for every product; a check of the product's own says its kind",
    ("notices", "off"): "foreman's notices are the same for every product; a check of the product's own says its kind",
    ("loop", "reload"): "the desktop app loads a new version by itself; elsewhere the pass asks the owner for "
    "/reload-plugins",
    ("ids", "initiative"): "the backlog has no initiatives since schema 5",
    ("ids", "milestone"): "the backlog has no milestones since schema 5",
    ("paths", "board"): "the board is always <state>/board.md",
    ("backlog", "auto_close_parent"): "an integration that leaves no sub-issue to do always closes the issue",
    ("loop", "skip_idle"): "a pass with no actionable notice is never sent to the model",
    ("panel", "language"): "the panel's language is the registry's [panel] language",
    ("agents", "planner_types"): "only planner agents count as planners",
    ("project", "owner_plain"): "foreman's checks of what the owner reads are always on",
}
REGISTRY_KNOWN = {
    "project": {"name", "home", "mode", "config"},
    "panel": {"language", "who"},
    "resources": None,
    "machine": None,
}

# A TOML table as read, or a section of the merged configuration: its keys are the schema's (docs/config.md) and its
# values whatever TOML holds, so a reader takes each value with the type its key documents (checked by `problems`).
Table = dict[str, Any]


class RegistryEntry(TypedDict):
    """A registry [[project]] this foreman can use (registry_scan): `home` is set, `mode` one of MODES."""

    name: str
    home: Path | None
    mode: Any  # a str of MODES here; whatever the registry wrote in a BrokenEntry
    config: Path | None  # None unless the entry names its foreman.toml
    warnings: list[str]  # its unknown keys


class _Broken(TypedDict):
    name: str
    home: Path | None
    mode: Any
    config: Path | None
    error: str


class BrokenEntry(_Broken, total=False):
    """A registry [[project]] left out (registry_scan, registered_projects): why in `error`. `warnings` only when it
    was read as a RegistryEntry first."""

    warnings: list[str]


# machine_resources(): ({name: table} of the usable ones, {name: [error]} of the ones left out, warnings)
Machine = tuple[dict[str, Table], dict[str, list[str]], list[str]]


class ConfigError(Exception):
    pass


def state_root() -> Path:
    """Base of every transient state: ~/.local/state/foreman (or $FOREMAN_STATE_HOME)."""
    return Path(os.environ.get("FOREMAN_STATE_HOME") or Path.home() / ".local/state/foreman").expanduser()


def registry_path() -> Path:
    return Path(os.environ.get("FOREMAN_REGISTRY") or Path.home() / ".config/foreman/projects.toml").expanduser()


def expand(path: PathLike, base: PathLike) -> Path:
    p = Path(os.path.expanduser(str(path)))
    return p if p.is_absolute() else (Path(base) / p)


# Defaults: everything a foreman.toml may leave out.
DEFAULTS: dict[str, Any] = {
    "project": {
        "owner": "owner",
        "language": "en",
        "rules": "rules.md",
        "config_schema": CONFIG_SCHEMA,
        "owner_knows": [],
    },
    "paths": {"notes": "notes", "reviews": "reviews", "state": None, "worktrees": None, "holdout": None},
    "backlog": {
        "file": "backlog.json",
        "page": "pages/backlog.md",
        "archive_page": "pages/backlog-archive.md",
        "decisions_page": "pages/decisions.md",
        "decisions_section": "",
    },
    "ids": {
        "project": r"^[A-Z]\d+$",
        "issue": r"^[A-Z]{2}-\d+$",
        "legacy": "",
        "decision": r"^[A-Z][A-Za-z0-9-]* [A-Z]\d+$",
        "criterion_required": [],
        "needs_criterion": ["backlog", "ready"],
        "report_prefixes": ["reviews/"],
    },
    "agents": {
        "production_slots": 1,
        "ready_target": 2,
        "production_types": ["dev"],
        "production_worktree": "",
        "stalled_min": 20,
    },
    "loop": {
        "prompt": "Orchestrator loop",
        "checks": [],
        "disk_low_gb": 30,
        "every_min": 60,
        "context_rotate": 200000,
        "lock_long_min": 60,
    },
    "deny": {
        "patterns": [],
        "message": "",
        "grants": {},
        "grant_minutes": 30,
        "ask_minutes": 5,
        "drop": [],
    },
    "panel": {"title": "", "wiki": "", "wiki_first": []},
    "publish": {"means": []},
    "strings": {},
    "check": {"guard_table": ""},
}
REPO_DEFAULTS: dict[str, Any] = {
    "path": None,
    "github": "",
    "main": "main",
    "remote": "origin",
    "merge": "squash",
    "local_ci": "",
    "ci_runs": [""],
    "ci_summary": "",
    "main_ci": None,
    "remote_checks": "required",
    "post_create": [],
    "publish_on_merge": False,
    "require_approval": False,
    "risky_paths": [],
    "review": "off",
    "review_paths": [],
    "review_lines": 400,
    "public": False,
    "privacy_scan": "",
    "pre_merge_report": "",
    "post_merge": "",
    "plan_check_heading": "",
    "worktree": "{worktrees}/{repo}-{name}",
}
RESOURCE_DEFAULTS: dict[str, Any] = {
    "guard": [],
    "allow": [],
    "busy": "",
    "busy_wait": 600,
    "slots": [],
    "dir": "",
    "queue": "",
    "next": "",
    "jumped": "",
    "log": "",
    "short_max": 300,
}
# A machine resource ([resources.NAME] in the registry) owns the keys that define the one lock every product shares:
# where it lives (the slots name its folders), its users outside the lock and how lock.sh treats them. Each product
# keeps PRODUCT_KEYS (which of its commands need the lock).
MACHINE_KEYS = ("dir", "queue", "next", "jumped", "log", "busy", "busy_wait", "short_max", "slots")
MACHINE_PATHS = ("dir", "queue", "next", "jumped", "log")
PRODUCT_KEYS = ("guard", "allow")
RESOURCE_NAME = r"^[a-z0-9][a-z0-9-]*$"
SOURCE_KINDS = ("heartbeat", "glob", "log")  # a [[recurring]] source reads one of these (notes.last_time)
SOURCE_KEYS = {*SOURCE_KINDS, "label", "file", "max_h", "at"}
KNOWN = {k: set(v) for k, v in DEFAULTS.items()} | {"repos": None, "resources": None, "recurring": None}
# The registry's [machine] (machine_settings): the keys of each section it may set, inherited by every product (None:
# every key of the section). PRODUCT_SETTINGS are each product's own (None: the whole section): its paths, its ids, its
# orchestrator's prompt, the commands foreman runs for it (loop.checks) and what removes an inherited rule (deny.drop).
# Every key of DEFAULTS is in exactly one of the two (test_machine_settings): a new key has to say which.
MACHINE_SETTINGS: dict[str, tuple[str, ...] | None] = {
    "project": ("owner", "language", "owner_knows"),
    "agents": ("production_slots", "ready_target", "production_types", "stalled_min"),
    "loop": ("disk_low_gb", "every_min", "context_rotate", "lock_long_min"),
    "deny": ("patterns", "message", "grants", "grant_minutes", "ask_minutes"),
    "strings": None,
}
PRODUCT_SETTINGS: dict[str, tuple[str, ...] | None] = {
    "project": ("name", "rules", "config_schema"),
    "paths": None,
    "backlog": None,
    "ids": None,
    "agents": ("production_worktree",),
    "loop": ("prompt", "checks"),
    "deny": ("drop",),
    "panel": None,
    "publish": None,
    "check": None,
    "repos": None,
    "resources": None,
    "recurring": None,
}
DROP_GROUP = "group:"  # [deny] drop = ["group:NAME"]: the whole group NAME the machine gives


class DenyOrigin(TypedDict):
    """Where each deny rule of a loaded project comes from (Project.deny_origin): "machine" (the registry's
    [machine.deny]), "product" (its foreman.toml) or "both"; `dropped`: the entries of its `[deny] drop` that removed
    something inherited."""

    patterns: dict[str, str]
    grants: dict[str, dict[str, str]]
    dropped: list[str]


# machine_settings(): ({section: {key: value}} of the valid [machine] values, warnings)
MachineSettings = tuple[Table, list[str]]


class Project:
    """A loaded foreman.toml. `raw` is the merged dict; the properties are the resolved paths."""

    def __init__(
        self,
        home: PathLike,
        raw: Table,
        mode: str = "active",
        warnings: Iterable[str] = (),
        resource_origin: Mapping[str, str] | None = None,
        declared: Table | None = None,
        config_file: PathLike | None = None,
        origin: Mapping[tuple[str, str], str] | None = None,
        deny_origin: DenyOrigin | None = None,
    ) -> None:
        self.home = Path(home).expanduser().resolve()
        self.mode = mode
        self.raw = raw
        self.name: str = raw["project"]["name"]
        self.warnings = list(warnings)  # what a tolerant read ignored (unknown keys, a newer schema)
        # {resource: "machine" | "project"}: where each of `resources` comes from (the registry or this foreman.toml)
        self.resource_origin: dict[str, str] = dict(
            resource_origin or dict.fromkeys(raw.get("resources", {}), "project")
        )
        # foreman.toml as read, before the defaults (what `sources` tells from a default), and the file it came from
        self.declared: Table = declared if declared is not None else {}
        self.config_file: Path | None = Path(config_file) if config_file else None
        # {(section, key): "registry" | "registry+foreman.toml"}: the values the registry's [machine] gave or added to
        # (merge_settings); any other value comes from foreman.toml or the default
        self.origin: dict[tuple[str, str], str] = dict(origin or {})
        self.deny_origin: DenyOrigin = deny_origin or {
            "patterns": {str(p): "product" for p in raw.get("deny", {}).get("patterns", [])},
            "grants": {g: {str(p): "product" for p in v} for g, v in (raw.get("deny", {}).get("grants") or {}).items()},
            "dropped": [],
        }
        self.registered = False  # loaded from a registry entry (load_entry): its mode is the registry's

    def __repr__(self) -> str:
        return f"Project({self.name!r}, {str(self.home)!r}, {self.mode})"

    # sections
    def section(self, key: str) -> Table:
        return self.raw.get(key, {})

    @property
    def owner(self) -> str:
        return self.raw["project"]["owner"]

    @property
    def language(self) -> str:
        return self.raw["project"]["language"]

    def path(self, value: PathLike) -> Path:
        return expand(value, self.home)

    @property
    def state(self) -> Path:
        s = self.raw["paths"].get("state")
        return self.path(s) if s else state_root() / self.name

    @property
    def notes_dir(self) -> Path:
        return self.path(self.raw["paths"]["notes"])

    @property
    def reviews_dir(self) -> Path:
        return self.path(self.raw["paths"]["reviews"])

    @property
    def worktrees_dir(self) -> Path:
        w = self.raw["paths"].get("worktrees")
        return self.path(w) if w else self.home.parent / "wt"

    @property
    def holdout_dir(self) -> Path:
        """The product's folder of hidden acceptance checks ([paths] holdout, default
        ~/.local/share/foreman/<name>/holdout): only planners and the orchestrator reach it (guard.py, HOLDOUT)."""
        h = self.raw["paths"].get("holdout")
        return self.path(h) if h else Path.home() / ".local/share/foreman" / self.name / "holdout"

    @property
    def board(self) -> Path:
        return self.state / "board.md"

    @property
    def repos(self) -> dict[str, Table]:
        return self.raw.get("repos", {})

    def repo(self, key: str) -> Table:
        if key not in self.repos:
            raise ConfigError(f"{self.name}: no repo {key!r} (repos: {', '.join(self.repos) or 'none'})")
        r = dict(self.repos[key])
        r["key"] = key
        r["path"] = self.path(r["path"]) if r.get("path") else None
        return r

    @property
    def resources(self) -> dict[str, Table]:
        """{name: table}, already merged with the machine's when the registry declares the resource (load_project)."""
        return self.raw.get("resources", {})

    @property
    def recurring(self) -> list[Table]:
        return self.raw.get("recurring", [])

    @property
    def active(self) -> bool:
        """Hooks act only for active projects; an inactive one is set up but switched off."""
        return self.mode == "active"

    def as_dict(self) -> dict[str, Any]:
        """The resolved configuration as `foreman config --json` prints it (docs/extending.md «Reading the
        configuration»): every key with its value after the defaults, `[paths]` and each repo's
        `path` as absolute paths, and in `sources` the same shape with where each value comes from: SOURCES."""
        from . import __version__, commands

        declared = self.declared
        config: Table = {}
        sources: Table = {}

        def origin(table: object, key: str) -> str:
            return "foreman.toml" if isinstance(table, dict) and key in table else "default"

        for sec in DEFAULTS:
            config[sec] = dict(self.raw[sec])
            sources[sec] = {k: self.origin.get((sec, k)) or origin(declared.get(sec), k) for k in config[sec]}
        sources["project"]["name"] = "foreman.toml"
        config["paths"] = {
            "notes": str(self.notes_dir),
            "reviews": str(self.reviews_dir),
            "state": str(self.state),
            "worktrees": str(self.worktrees_dir),
            "holdout": str(self.holdout_dir),
        }
        if sources["paths"]["state"] == "default" and os.environ.get("FOREMAN_STATE_HOME"):
            sources["paths"]["state"] = "environment"
        config["repos"], sources["repos"] = {}, {}
        for key in self.repos:
            r = self.repo(key)
            r.pop("key")
            r["path"] = str(r["path"]) if r["path"] else None
            config["repos"][key] = r
            sources["repos"][key] = {k: origin(declared.get("repos", {}).get(key), k) for k in r}
        config["resources"], sources["resources"] = {}, {}
        for name, res in self.resources.items():
            config["resources"][name] = dict(res)
            machine = self.resource_origin.get(name) == "machine"
            sources["resources"][name] = {
                k: "registry" if machine and k in MACHINE_KEYS else origin(declared.get("resources", {}).get(name), k)
                for k in res
            }
        config["recurring"] = [dict(t) for t in self.recurring]
        sources["recurring"] = "foreman.toml" if "recurring" in declared else "default"
        return {
            "foreman": __version__,
            "api": commands.API,
            "name": self.name,
            "home": str(self.home),
            "config_file": str(_resolved(self.config_file)) if self.config_file else None,
            "mode": self.mode,
            "registered": self.registered,
            "config": config,
            "sources": sources,
            "warnings": list(self.warnings),
        }

    def inherited(self) -> tuple[int, int, int]:
        """(keys, rules, dropped): how many settings the registry's [machine] gives this project (deny rules apart),
        how many deny rules (a pattern or a group's pattern) it inherits, and how many `[deny] drop` entries remove
        something inherited (`foreman check`)."""
        keys = sum(1 for (sec, key) in self.origin if not (sec == "deny" and key in ("patterns", "grants")))
        d = self.deny_origin
        rules = sum(o != "product" for o in d["patterns"].values())
        rules += sum(o != "product" for g in d["grants"].values() for o in g.values())
        return keys, rules, len(d["dropped"])

    def _own_roots(self) -> list[Path]:
        """Resolved home and repo paths: what no other project can claim."""
        return [
            _resolved(p) for p in [self.home] + [self.path(r["path"]) for r in self.repos.values() if r.get("path")]
        ]

    def worktree_prefixes(self) -> list[str]:
        """Path prefixes of this project's worktrees: each repo's `worktree` template cut at {name}/{branch}, and
        [agents] production_worktree."""
        out: list[str] = []
        for key, r in self.repos.items():
            cut = r["worktree"].format(worktrees=self.worktrees_dir, repo=key, name="\0", branch="\0").split("\0")[0]
            out.append(cut)
        if self.raw["agents"].get("production_worktree"):
            out.append(self.raw["agents"]["production_worktree"])
        res = []
        for p in out:
            q = self.path(p)
            # keep a trailing "-" or "/" meaningful: resolve the folder, then glue the last part back
            res.append(str(_resolved(q.parent) / q.name) + ("/" if p.endswith("/") else ""))
        return res

    def worktrees_shared(self) -> bool:
        """The worktrees folder is shared when it holds (or is) the home or a repo (e.g. a folder of projects)."""
        w = _resolved(self.worktrees_dir)
        return any(_inside(root, w) for root in self._own_roots())

    def workspace(self, others: Iterable[Project] = ()) -> Path | None:
        """The folder that holds the home and every repo (the home's parent), or None. It counts only if the project
        has repos, all of them inside it, its worktrees folder is not shared, it is not the user's home (or above), and
        it holds no home or repo of the `others` projects."""
        ws = _resolved(self.home.parent)
        repos = self._own_roots()[1:]
        if not repos or not all(_inside(r, ws) for r in repos) or self.worktrees_shared():
            return None
        if _inside(Path.home().resolve(), ws):
            return None
        for o in others:
            if o is not self and any(_inside(r, ws) for r in o._own_roots()):
                return None
        return ws

    def owns(self, cwd: PathLike, others: Iterable[Project] = ()) -> bool:
        """True if `cwd` belongs to the project. Its paths are:
        - the home and each repo, with everything below them;
        - the worktrees folder, whole, unless it is shared (it holds the home or a repo, like a folder of projects):
          then only the paths that start with a worktree prefix (`worktree_prefixes`: `{worktrees}/{repo}-` and
          production_worktree), so the shared folder itself and its other entries are nobody's;
        - the workspace (`workspace`): the home's parent when it holds every repo and no home or repo of `others`
          (the other registered projects), e.g. home `X/ops` with repos `X/app` and `X/api` owns `X`.
        """
        try:
            c = Path(cwd).expanduser().resolve()
        except OSError:
            return False
        roots = self._own_roots()
        if self.worktrees_shared():
            bare = str(_resolved(self.worktrees_dir)) + "/"  # a prefix that is the whole shared folder claims nothing
            if any(p != bare and (str(c).startswith(p) or str(c) + "/" == p) for p in self.worktree_prefixes()):
                return True
        else:
            roots.append(_resolved(self.worktrees_dir))
        ws = self.workspace(others)
        if ws:
            roots.append(ws)
        return any(_inside(c, root) for root in roots)


def _resolved(path: PathLike) -> Path:
    try:
        return Path(path).resolve()
    except OSError:
        return Path(path)


def _inside(path: Path, root: Path) -> bool:
    """True if `path` is `root` or below it."""
    return path == root or root in path.parents


def merge_defaults(raw: Table) -> Table:
    out: Table = {}
    for key, default in DEFAULTS.items():
        sec = dict(default)
        sec.update(raw.get(key, {}))
        out[key] = sec
    out["project"]["name"] = raw.get("project", {}).get("name")
    out["repos"] = {k: {**REPO_DEFAULTS, **v} for k, v in raw.get("repos", {}).items()}
    out["resources"] = {k: {**RESOURCE_DEFAULTS, **v} for k, v in raw.get("resources", {}).items()}
    out["recurring"] = list(raw.get("recurring", []))
    return out


def problems(raw: Table) -> tuple[list[str], list[str]]:
    """(errors, warnings) of a foreman.toml as read. Errors are bad values of known keys (the project cannot load);
    warnings are sections and keys this foreman does not know and a schema newer than CONFIG_SCHEMA (it loads)."""
    err: list[str] = []
    warn: list[str] = []
    schema = raw.get("project", {}).get("config_schema") if isinstance(raw.get("project"), dict) else None
    if schema is not None:
        if not isinstance(schema, int) or isinstance(schema, bool) or schema < 1:
            err.append("[project] config_schema must be a whole number, 1 or more")
        elif schema > CONFIG_SCHEMA:
            warn.append(
                f"written for a newer foreman (config schema {schema}, this foreman knows {CONFIG_SCHEMA}): "
                "update foreman; until then what it does not know is ignored"
            )
    for key in raw:
        if key in KNOWN:
            continue
        gone = [
            (o, why) for (sec, o), why in REMOVED.items() if sec == key and isinstance(raw[key], dict) and o in raw[key]
        ]
        if gone:  # a whole section foreman no longer reads: say why, key by key
            warn.extend(
                f"[{key}] {o} is no longer used ({why}): delete it from foreman.toml (ignored)" for o, why in gone
            )
        else:
            warn.append(f"unknown section [{key}] (ignored)")
    for key, allowed in KNOWN.items():
        if key not in raw:
            continue
        if key == "recurring":
            if not isinstance(raw[key], list) or not all(isinstance(t, dict) for t in raw[key]):
                err.append("[[recurring]] must be an array of tables")
            continue
        if not isinstance(raw[key], dict):
            err.append(f"[{key}] must be a table")
            continue
        for (sec, old), why in REMOVED.items():
            if sec == key and old in raw[key]:
                where = f"[{sec}.{old}]" if isinstance(raw[key][old], dict) else f"[{sec}] {old}"
                warn.append(f"{where} is no longer used ({why}): delete it from foreman.toml (ignored)")
        if key == "strings":  # its keys are the texts' keys (i18n), checked below
            continue
        if allowed:
            removed = {o for s, o in REMOVED if s == key}
            extra = set(raw[key]) - allowed - removed - ({"name"} if key == "project" else set())
            if extra:
                warn.append(f"[{key}]: unknown keys {', '.join(sorted(extra))} (ignored)")
    if err:
        return err, warn
    name = raw.get("project", {}).get("name")
    if not name or not re.match(r"^[a-z0-9][a-z0-9._-]*$", str(name)):
        err.append("[project] name is required (lowercase letters, digits, . _ -)")
    for key, r in raw.get("repos", {}).items():
        if not isinstance(r, dict):
            err.append(f"[repos.{key}] must be a table")
            continue
        extra = set(r) - set(REPO_DEFAULTS)
        if extra:
            warn.append(f"[repos.{key}]: unknown keys {', '.join(sorted(extra))} (ignored)")
        if "merge" in r and r["merge"] not in MERGE_METHODS:
            err.append(f"[repos.{key}] merge must be one of {', '.join(MERGE_METHODS)}")
        if r.get("remote_checks", "required") not in ("required", "none"):
            err.append(f"[repos.{key}] remote_checks must be required or none")
        if "remote" in r and not (isinstance(r["remote"], str) and re.match(r"^[A-Za-z0-9][\w.-]*$", r["remote"])):
            err.append(f"[repos.{key}] remote must be a git remote name, or none for a repo without a remote")
        if "ci_runs" in r and not (
            isinstance(r["ci_runs"], list) and r["ci_runs"] and all(isinstance(c, str) for c in r["ci_runs"])
        ):
            err.append(
                f"[repos.{key}] ci_runs must be a non-empty list of strings (each one run of local_ci, "
                "its value in {ci})"
            )
        if not isinstance(r.get("main_ci", ""), str):
            err.append(f'[repos.{key}] main_ci must be a command (a string; "" runs nothing on main)')
        if not isinstance(r.get("ci_summary", ""), str):
            err.append(f"[repos.{key}] ci_summary must be a command (a string)")
        if not isinstance(r.get("post_merge", ""), str):
            err.append(f"[repos.{key}] post_merge must be a command (a string)")
        if r.get("review", "off") not in ("auto", "off"):
            err.append(f"[repos.{key}] review must be auto or off")
        paths = r.get("review_paths", [])
        if not (isinstance(paths, list) and all(isinstance(x, str) and x for x in paths)):
            err.append(f'[repos.{key}] review_paths must be a list of paths ("dir/", "file", "glob*" or "file:text")')
        lines = r.get("review_lines", 400)
        if isinstance(lines, bool) or not isinstance(lines, int) or lines < 0:
            err.append(f"[repos.{key}] review_lines must be a whole number of lines, 0 or more (0: size never asks)")
    for key, r in raw.get("resources", {}).items():
        if not isinstance(r, dict):
            err.append(f"[resources.{key}] must be a table")
            continue
        extra = set(r) - set(RESOURCE_DEFAULTS)
        if extra:
            warn.append(f"[resources.{key}]: unknown keys {', '.join(sorted(extra))} (ignored)")
        if not re.match(RESOURCE_NAME, key):
            err.append(f"[resources.{key}]: resource names are lowercase letters, digits and -")
    deny = raw.get("deny", {})
    for pat in deny.get("patterns", []):
        try:
            re.compile(pat)
        except (re.error, TypeError) as e:
            err.append(f"[deny] bad pattern {pat!r}: {e}")
    drop = deny.get("drop", [])
    if not (isinstance(drop, list) and all(isinstance(d, str) and d.strip() for d in drop)):
        err.append(
            "[deny] drop must be a list of inherited rules to remove: a pattern as the registry's [machine.deny] "
            'writes it, or "group:NAME" for a whole group'
        )
    grant_problems(deny, err, warn)
    for key in ("project", "issue", "legacy", "decision"):
        pat = raw.get("ids", {}).get(key)
        if pat:
            try:
                re.compile(pat)
            except (re.error, TypeError) as e:
                err.append(f"[ids] {key}: {e}")
    knows = raw.get("project", {}).get("owner_knows", [])
    if not (isinstance(knows, list) and all(isinstance(t, str) and t.strip() for t in knows)):
        err.append('[project] owner_knows must be a list of terms (strings) the owner knows, such as ["CI", "PR"]')
    rot = raw.get("loop", {}).get("context_rotate", 0)
    if isinstance(rot, bool) or not isinstance(rot, int) or rot < 0:
        err.append("[loop] context_rotate must be a whole number of tokens >= 0 (0 turns it off)")
    types = raw.get("agents", {}).get("production_types", [])
    if not (isinstance(types, list) and all(isinstance(t, str) and t.strip() for t in types)):
        err.append('[agents] production_types must be a list of agent types (strings), such as ["dev"]')
    long_min = raw.get("loop", {}).get("lock_long_min", 60)
    if isinstance(long_min, bool) or not isinstance(long_min, int) or long_min < 1:
        err.append("[loop] lock_long_min must be a whole number of minutes, 1 or more")
    string_problems(raw.get("strings", {}), err, warn)
    for n, t in enumerate(raw.get("recurring", [])):
        if not isinstance(t, dict):
            continue
        if not t.get("name") or not t.get("sources"):
            err.append(f"[[recurring]] #{n + 1}: needs name and sources")
            continue
        where = f"[[recurring]] {t['name']!r}"
        if not isinstance(t["sources"], list) or not all(isinstance(s, dict) for s in t["sources"]):
            err.append(f"{where}: sources must be a list of tables")
            continue
        for s in t["sources"]:
            if sum(k in s for k in SOURCE_KINDS) != 1:
                err.append(f"{where}: a source needs one of {', '.join(SOURCE_KINDS)}: {s}")
            extra = set(s) - SOURCE_KEYS
            if extra:
                warn.append(f"{where}: unknown source key {', '.join(sorted(extra))} (ignored)")
            if "at" in s:
                if not (isinstance(s["at"], str) and re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", s["at"])):
                    err.append(f'{where}: at must be a local time "HH:MM" (00:00 to 23:59), not {s["at"]!r}')
                if "max_h" in s:
                    err.append(f"{where}: a source has at (daily at a time) or max_h (hours between runs), not both")
            elif "max_h" in s and (
                isinstance(s["max_h"], bool) or not isinstance(s["max_h"], (int, float)) or s["max_h"] <= 0
            ):
                err.append(f"{where}: max_h must be a number of hours above 0, not {s['max_h']!r}")
    return err, warn


def grant_problems(deny: Table, err: list[str], warn: list[str]) -> None:
    """[deny.grants] (groups: a name of lowercase letters, digits and -, and a list of regexes), grant_minutes (1 or
    more) and ask_minutes (0 or more: 0 waits until the owner answers)."""
    groups = deny.get("grants", {})
    if not isinstance(groups, dict):
        err.append('[deny.grants] must be a table of groups: name = ["regex", …]')
        groups = {}
    for name, pats in groups.items():
        if not re.match(r"^[a-z0-9][a-z0-9-]*$", str(name)):
            err.append(f"[deny.grants] {name!r}: a group name is lowercase letters, digits and -")
        if not (isinstance(pats, list) and pats and all(isinstance(p, str) for p in pats)):
            err.append(f"[deny.grants] {name}: must be a non-empty list of regexes (strings)")
            continue
        for pat in pats:
            try:
                re.compile(pat)
            except re.error as e:
                err.append(f"[deny.grants] {name}: {pat!r}: {e}")
        both = set(pats) & set(map(str, deny.get("patterns", [])))
        if both:
            warn.append(
                f"[deny.grants] {name}: in patterns too (patterns win: nobody runs it): {', '.join(sorted(both))}"
            )
    for key, low in (("grant_minutes", 1), ("ask_minutes", 0)):
        v = deny.get(key, low)
        if isinstance(v, bool) or not isinstance(v, int) or v < low:
            err.append(f"[deny] {key} must be a whole number of minutes, {low} or more")


def string_problems(section: Table, err: list[str], warn: list[str]) -> None:
    """Errors and warnings of [strings] (keys of lib/foreman/i18n/en.json): i18n.override_problems. An old
    [strings.panel] table is left out here: REMOVED warns about it."""
    from . import i18n

    texts = {k: v for k, v in section.items() if k != i18n.OLD_PANEL_KEY}
    e, w = i18n.override_problems(texts, i18n.strings("en"), "[strings]")
    err += e
    warn += w


def validate(raw: Table) -> list[str]:
    """Every problem of a foreman.toml, read strictly (errors and warnings; empty = fine)."""
    err, warn = problems(raw)
    return err + warn


def unknown_repo_keys(project: Project) -> list[tuple[str, list[str]]]:
    """[(repo, [keys])] of [repos.*] keys this foreman does not know (`foreman integrate` refuses them)."""
    out: list[tuple[str, list[str]]] = []
    for key, r in project.repos.items():
        extra = sorted(set(r) - set(REPO_DEFAULTS))
        if extra:
            out.append((key, extra))
    return out


def machine_resources(reg: Table | None = None) -> Machine:
    """(resources, errors, warnings) of the registry's [resources.NAME] tables (`reg`: the registry as read; default
    read_registry(), which raises ConfigError if it does not parse). resources: {name: table} of the valid ones with
    every RESOURCE_DEFAULTS key (guard and allow empty) and their paths resolved (`~` expanded, a relative
    one against the registry's folder); errors: {name: [text]} of the ones left out (a bad value); warnings: what is
    ignored (unknown keys, a product's own keys). This is the one resolution every reader uses (load_project merges
    it, lock.resource_config falls back on it, the panel can list it)."""
    reg = read_registry() if reg is None else reg
    section = reg.get("resources", {})
    resources: dict[str, Table] = {}
    errors: dict[str, list[str]] = {}
    warnings: list[str] = []
    if not isinstance(section, dict):
        return {}, {"": ["registry: [resources] must be a table of [resources.NAME] tables"]}, []
    base = registry_path().parent
    for name, table in section.items():
        where = f"registry: [resources.{name}]"
        err: list[str] = []
        if not isinstance(table, dict):
            errors[name] = [f"{where} must be a table"]
            continue
        if not re.match(RESOURCE_NAME, name):
            err.append(f"{where}: resource names are lowercase letters, digits and -")
        for key in ("busy", *MACHINE_PATHS):
            if key in table and not isinstance(table[key], str):
                err.append(f"{where}: {key} must be a string")
        for key in ("busy_wait", "short_max"):
            v = table.get(key, 0)
            if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                err.append(f"{where}: {key} must be a whole number of seconds >= 0, not {v!r}")
        slots = table.get("slots", [])
        if not isinstance(slots, list) or not all(isinstance(x, str) for x in slots):
            err.append(f"{where}: slots must be a list of strings")
        own = sorted(set(table) & set(PRODUCT_KEYS))
        if own:
            warnings.append(
                f"{where}: {', '.join(own)} {'is' if len(own) == 1 else 'are'} each product's own "
                "(declare them in its foreman.toml): ignored"
            )
        extra = sorted(set(table) - set(RESOURCE_DEFAULTS))
        if extra:
            warnings.append(f"{where}: unknown keys {', '.join(extra)} (ignored)")
        if err:
            errors[name] = err
            continue
        res = dict(RESOURCE_DEFAULTS)
        res.update({k: v for k, v in table.items() if k in MACHINE_KEYS})
        res["slots"] = list(res["slots"])
        for key in MACHINE_PATHS:
            if res[key]:
                res[key] = str(expand(res[key], base))
        resources[name] = res
    return resources, errors, warnings


def merge_machine(resources: dict[str, Table], machine: Machine) -> tuple[dict[str, Table], dict[str, str], list[str]]:
    """(resources, origin, warnings): a product's merged [resources.*] tables (after merge_defaults) with the machine's
    (`machine`: the result of machine_resources). For a resource the machine declares, the table is the machine's plus
    the product's guard and allow: the machine wins, and machine keys the product writes there are ignored
    with a warning. One the machine declares but cannot use (a bad value) keeps the product's own keys, with a warning
    that says so. origin: {name: "machine" | "project"}."""
    mres, merrors, _ = machine
    out: dict[str, Table] = {}
    origin: dict[str, str] = {}
    warnings: list[str] = []
    for name, res in resources.items():
        if name in mres:
            out[name] = {**res, **mres[name], **{k: res[k] for k in PRODUCT_KEYS if k in res}}
            origin[name] = "machine"
        else:
            out[name] = res
            origin[name] = "project"
            if name in merrors:
                warnings.append(
                    f"[resources.{name}]: the registry's machine resource does not load ("
                    + "; ".join(merrors[name])
                    + "): this project's own keys are used until it is fixed"
                )
    return out, origin, warnings


def _machine_error(text: str) -> str:
    """A problem of a [machine] value, worded as problems() words the foreman.toml's: «[deny] …» → «registry:
    [machine.deny] …»."""
    return "registry: " + re.sub(r"^\[", "[machine.", text) + " (left out)"


def machine_settings(reg: Table | None = None) -> MachineSettings:
    """(settings, warnings) of the registry's [machine] (`reg`: the registry as read; default read_registry()).
    settings: {section: {key: value}} of the values every product inherits (MACHINE_SETTINGS), each checked as
    problems() checks a foreman.toml's; warnings: what is ignored (a product's own section or key, an unknown one, a
    newer config_schema) and each bad value, left out alone: one bad regex of `patterns` or one bad group of `grants`
    leaves the rest in, so a typo in the registry never stops a product from loading (its guard keeps guarding).
    `[machine.project] config_schema` is only checked, never inherited."""
    reg = read_registry() if reg is None else reg
    section = reg.get("machine", {})
    if not isinstance(section, dict):
        return {}, ["registry: [machine] must be a table of foreman.toml sections (ignored)"]
    out: Table = {}
    warn: list[str] = []
    for sec, table in section.items():
        where = f"registry: [machine.{sec}]"
        if sec not in MACHINE_SETTINGS:
            warn.append(
                f"{where} is each product's own (declare it in its foreman.toml): ignored"
                if sec in PRODUCT_SETTINGS
                else f"registry: [machine]: unknown section [{sec}] (ignored)"
            )
            continue
        if not isinstance(table, dict):
            warn.append(f"{where} must be a table (ignored)")
            continue
        allowed = MACHINE_SETTINGS[sec]
        checked_only = ("config_schema",) if sec == "project" else ()  # checked below, never inherited
        others = sorted(k for k in table if allowed is not None and k not in allowed and k not in checked_only)
        own = [k for k in others if k in DEFAULTS[sec] or (sec, k) == ("project", "name")]
        unknown = [k for k in others if k not in own]
        if sec == "project" and "config_schema" in table:
            e, w = problems({"project": {"config_schema": table["config_schema"]}})
            warn += [f"{where}: {x}" for x in w] + [_machine_error(x) for x in e if "config_schema" in x]
        if own:
            warn.append(
                f"{where}: {', '.join(own)} {'is' if len(own) == 1 else 'are'} each product's own "
                f"(declare {'it' if len(own) == 1 else 'them'} in its foreman.toml): ignored"
            )
        if unknown:
            warn.append(f"{where}: unknown keys {', '.join(unknown)} (ignored)")
        keep: Table = {}
        for key, value in table.items():
            if allowed is not None and key not in allowed:
                continue
            default = DEFAULTS[sec].get(key)
            if isinstance(default, list) and not isinstance(value, list):
                warn.append(_machine_error(f"[{sec}] {key} must be a list"))
                continue
            if isinstance(default, dict) and not isinstance(value, dict):
                warn.append(_machine_error(f"[{sec}] {key} must be a table"))
                continue
            if (sec, key) == ("deny", "patterns"):  # each regex alone
                good = []
                for pat in value:
                    e, _ = problems({"project": {"name": "machine"}, "deny": {"patterns": [pat]}})
                    warn += [_machine_error(x) for x in e]
                    good += [] if e else [pat]
                keep[key] = good
            elif (sec, key) == ("deny", "grants"):  # each group alone
                groups: Table = {}
                for name, pats in dict(value).items():
                    e: list[str] = []
                    grant_problems({"grants": {name: pats}}, e, [])
                    warn += [_machine_error(x) for x in e]
                    if not e:
                        groups[name] = pats
                keep[key] = groups
            elif sec == "strings":  # each text alone; the warnings (unknown keys, half plurals) of the whole table
                e = []
                string_problems({key: value}, e, [])
                warn += [_machine_error(x) for x in e]
                if not e:
                    keep[key] = value
            else:
                one: Table = {"project": {"name": "machine"}}
                one.setdefault(sec, {})[key] = value
                e, _ = problems(one)
                warn += [_machine_error(x) for x in e]
                if not e:
                    keep[key] = value
        if sec == "strings":
            w: list[str] = []
            string_problems(table, [], w)
            warn += [
                f"registry: [machine.strings]{x[len('[strings]') :]}" if x.startswith("[strings]") else x for x in w
            ]
        if sec == "deny":
            w = []
            grant_problems({"patterns": keep.get("patterns", []), "grants": keep.get("grants", {})}, [], w)
            warn += [f"registry: [machine.{x[1:]}" for x in w]
        out[sec] = keep
    return out, warn


def _combine(machine: Any, product: Any) -> Any:
    """A product's value on top of the machine's: lists add up (the machine's first, without repeats), tables merge
    key by key with the same rule, and any other value (or two of different types) is the product's."""
    if isinstance(machine, list) and isinstance(product, list):
        return list(machine) + [x for x in product if x not in machine]
    if isinstance(machine, dict) and isinstance(product, dict):
        out = dict(machine)
        for k, v in product.items():
            out[k] = _combine(machine[k], v) if k in machine else v
        return out
    return product


def merge_settings(
    merged: Table, raw: Table, settings: Table
) -> tuple[dict[tuple[str, str], str], DenyOrigin, list[str]]:
    """Merges the registry's [machine] (`settings`: machine_settings()[0]) into a product's merged configuration
    (`merged`, after merge_defaults; `raw`: its foreman.toml as read), in place: defaults < machine < product.
    Before merging, the product's `[deny] drop` removes what it names from the machine's deny rules (a pattern from
    `patterns` and from every group, or "group:NAME" the whole group). Returns (origin, deny_origin, warnings):
    origin {(section, key): "registry" | "registry+foreman.toml"}, deny_origin (DenyOrigin), and the warnings: a
    drop that removes nothing inherited, a pattern denied at one level and granted in a group at the other."""
    origin: dict[tuple[str, str], str] = {}
    warn: list[str] = []
    mdeny = settings.get("deny", {})
    mpats = list(mdeny.get("patterns", []))
    mgroups = {g: list(v) for g, v in mdeny.get("grants", {}).items()}
    dropped: list[str] = []
    for d in merged["deny"].get("drop", []):
        hit = False
        if d.startswith(DROP_GROUP) and d[len(DROP_GROUP) :] in mgroups:
            del mgroups[d[len(DROP_GROUP) :]]
            hit = True
        else:
            if d in mpats:
                mpats.remove(d)
                hit = True
            for pats in mgroups.values():
                if d in pats:
                    pats.remove(d)
                    hit = True
        if hit:
            dropped.append(d)
        else:
            warn.append(f"[deny] drop {d!r}: nothing inherited from the registry's [machine.deny] matches it (ignored)")
    machine = dict(settings)
    if "deny" in settings:
        machine["deny"] = {**mdeny}
        if "patterns" in mdeny:
            machine["deny"]["patterns"] = mpats
        if "grants" in mdeny:
            machine["deny"]["grants"] = {g: v for g, v in mgroups.items() if v}
    for sec, table in machine.items():
        declared = raw.get(sec, {})
        for key, mval in table.items():
            if isinstance(mval, (list, dict)) and not mval:
                continue  # nothing to inherit
            if key not in declared:
                merged[sec][key] = copy.deepcopy(mval)  # the machine's table is shared by every product
                origin[(sec, key)] = "registry"
            elif isinstance(mval, (list, dict)):
                merged[sec][key] = _combine(mval, declared[key])
                origin[(sec, key)] = "registry+foreman.toml"
    pdeny = raw.get("deny", {})
    ppats = set(map(str, pdeny.get("patterns", [])))
    pgroups = {g: set(map(str, v)) for g, v in (pdeny.get("grants") or {}).items()}
    mp = set(map(str, machine.get("deny", {}).get("patterns", [])))
    mg = {g: set(map(str, v)) for g, v in machine.get("deny", {}).get("grants", {}).items()}

    def where(p: str, mine: set[str], theirs: set[str]) -> str:
        return "both" if p in mine and p in theirs else "machine" if p in theirs else "product"

    deny = merged["deny"]
    deny_origin: DenyOrigin = {
        "patterns": {str(p): where(str(p), ppats, mp) for p in deny.get("patterns", [])},
        "grants": {
            g: {str(p): where(str(p), pgroups.get(g, set()), mg.get(g, set())) for p in v}
            for g, v in (deny.get("grants") or {}).items()
        },
        "dropped": dropped,
    }
    for g, pats in sorted(deny_origin["grants"].items()):
        # denied at one level, granted at the other (the same level warns in its own check)
        cross = sorted(
            p
            for p in pats
            if p in deny_origin["patterns"]
            and not (p in ppats and p in pgroups.get(g, set()))
            and not (p in mp and p in mg.get(g, set()))
        )
        if cross:
            warn.append(
                f"[deny.grants] {g}: in patterns too, one in the registry's [machine.deny] and the other in "
                f"foreman.toml (patterns win: nobody runs it): {', '.join(cross)}"
            )
    return origin, deny_origin, warn


def load_project(
    home: PathLike,
    mode: str = "active",
    config_file: PathLike | None = None,
    strict: bool = False,
    warnings: Iterable[str] = (),
    machine: Machine | None = None,
    settings: MachineSettings | None = None,
) -> Project:
    """The project of a home. Errors raise ConfigError; warnings (and the registry's `warnings` for it) are kept in
    Project.warnings, or raise too with strict=True. `machine`: machine_resources() of the registry, and `settings`:
    its machine_settings(), read here if not given (registered_projects reads them once for all); a resource the
    registry declares is merged with it, and so are the registry's [machine] settings (merge_settings)."""
    home = Path(home).expanduser()
    f = Path(config_file).expanduser() if config_file else home / "foreman.toml"
    try:
        raw = toml_reader.loads(f.read_text())
    except OSError as e:
        raise ConfigError(f"cannot read {f}: {e}") from e
    except toml_reader.TOMLDecodeError as e:
        raise ConfigError(f"{f}: {e}") from e
    err, warn = problems(raw)
    if err:
        raise ConfigError(f"{f}: " + "; ".join(err))
    merged = merge_defaults(raw)
    declared = {name: raw["resources"][name] for name in merged["resources"]}
    reg_warn: list[str] = []
    if machine is None or settings is None:
        try:
            reg = read_registry()
        except ConfigError as e:
            reg = None
            if machine is None and declared:  # its [machine] settings too; `foreman check` shows the registry's error
                warn.append(f"machine resources not applied (the registry does not read: {e})")
        if machine is None:
            machine = machine_resources(reg) if reg is not None else ({}, {}, [])
        if settings is None:
            settings = machine_settings(reg) if reg is not None else ({}, [])
    reg_warn += settings[1]
    origin, deny_origin, settings_warn = merge_settings(merged, raw, settings[0])
    warn += settings_warn
    for name, own in declared.items():
        repeated = sorted(k for k in own if k in MACHINE_KEYS)
        if name in machine[0] and repeated:
            warn.append(
                f"[resources.{name}]: {', '.join(repeated)} {'is' if len(repeated) == 1 else 'are'} declared "
                f"by the machine (registry [resources.{name}]): ignored here; remove "
                + ("it" if len(repeated) == 1 else "them")
            )
    merged["resources"], resource_origin, merge_warn = merge_machine(merged["resources"], machine)
    warn += merge_warn
    if strict and (warn or reg_warn):
        raise ConfigError("; ".join(([f"{f}: " + "; ".join(warn)] if warn else []) + reg_warn))
    return Project(
        home,
        merged,
        mode,
        [f"{f.name}: {w}" for w in warn] + reg_warn + list(warnings),
        resource_origin,
        raw,
        f,
        origin,
        deny_origin,
    )


def read_registry() -> Table:
    """The registry file as a dict ({} if there is none)."""
    f = registry_path()
    if not f.exists():
        return {}
    try:
        return toml_reader.loads(f.read_text())
    except toml_reader.TOMLDecodeError as e:
        raise ConfigError(f"{f}: {e}") from e


def registry_scan(reg: Table | None = None) -> tuple[list[RegistryEntry], list[BrokenEntry], list[str]]:
    """(entries, broken, warnings) of the registry. entries: [{name, home, mode, config, warnings}] (`config` is None
    unless the entry names its foreman.toml; `warnings`: its unknown keys); broken: the entries this foreman cannot
    use, [{name, home, mode, config, error}] (an unknown mode, no name or home): the others still load; warnings: the
    registry's own unknown sections and keys, and the problems of its machine resources (machine_resources: what is
    ignored, and the ones left out with their error). An unreadable registry raises ConfigError. `reg`: the registry
    as already read."""
    reg = read_registry() if reg is None else reg
    entries: list[RegistryEntry] = []
    broken: list[BrokenEntry] = []
    warnings: list[str] = []
    _, merrors, mwarnings = machine_resources(reg)
    warnings += mwarnings + [f"{e} (resource left out)" for errs in merrors.values() for e in errs]
    for key in reg:
        if key not in REGISTRY_KNOWN:
            warnings.append(f"registry: unknown section [{key}] (ignored)")
    if isinstance(reg.get("panel"), dict):
        extra = set(reg["panel"]) - REGISTRY_KNOWN["panel"]
        if extra:
            warnings.append(f"registry: [panel]: unknown keys {', '.join(sorted(extra))} (ignored)")
    projects = reg.get("project", [])
    if not isinstance(projects, list):
        raise ConfigError(f"{registry_path()}: [[project]] must be an array of tables")
    for n, p in enumerate(projects, 1):
        if not isinstance(p, dict):
            broken.append(
                {
                    "name": f"#{n}",
                    "home": None,
                    "mode": "",
                    "config": None,
                    "error": f"registry: [[project]] #{n} is not a table",
                }
            )
            continue
        name, mode = str(p.get("name") or f"#{n}"), p.get("mode", "active")
        e: RegistryEntry = {
            "name": name,
            "home": Path(str(p["home"])).expanduser() if p.get("home") else None,
            "mode": mode,
            "config": Path(str(p["config"])).expanduser() if p.get("config") else None,
            "warnings": [],
        }
        extra = set(p) - REGISTRY_KNOWN["project"]
        if extra:
            e["warnings"].append(f"registry: unknown keys {', '.join(sorted(extra))} (ignored)")
        if not p.get("name") or not p.get("home"):
            broken.append({**e, "error": f"registry: [[project]] {name} needs name and home"})
        elif mode not in MODES:
            broken.append(
                {
                    **e,
                    "error": f"registry: unknown mode {mode!r} (this foreman knows {', '.join(MODES)}): "
                    "a newer foreman's? the product stays out until foreman is updated or the "
                    "mode fixed",
                }
            )
        else:
            entries.append(e)
    return entries, broken, warnings


def panel_settings() -> dict[str, Any]:
    """The registry's [panel] table with its defaults: language (en) and who (owner)."""
    panel = read_registry().get("panel", {})
    return {"language": "en", "who": "owner", **(panel if isinstance(panel, dict) else {})}


def load_entry(
    e: RegistryEntry, strict: bool = False, machine: Machine | None = None, settings: MachineSettings | None = None
) -> Project:
    home = e["home"]
    assert home is not None  # registry_scan lists an entry only with its home
    p = load_project(
        home, e["mode"], e["config"], strict=strict, warnings=e.get("warnings", ()), machine=machine, settings=settings
    )
    p.registered = True
    return p


def registered_projects(
    errors: list[str] | None = None, broken: list[BrokenEntry] | None = None, warnings: list[str] | None = None
) -> list[Project]:
    """Every registry project that loads (tolerantly: with its warnings). The others are left out and listed in
    `errors` (as text), `broken` ({name, home, mode, config, error}) if given; the registry's own warnings go to
    `warnings`."""
    reg = read_registry()
    entries, bad, reg_warnings = registry_scan(reg)
    machine = machine_resources(reg)
    settings = machine_settings(reg)
    out: list[Project] = []
    for e in entries:
        try:
            out.append(load_entry(e, machine=machine, settings=settings))
        except ConfigError as err:
            bad.append({**e, "error": str(err)})
    for b in bad:
        if errors is not None:
            errors.append(f"{b['name']}: {b['error']}")
        if broken is not None:
            broken.append(b)
    if warnings is not None:
        warnings.extend(reg_warnings)
    return out


def project_for_cwd(cwd: PathLike, active_only: bool = True) -> Project | None:
    """The registered project that owns `cwd` (deepest home wins), or None. Used by the hooks."""
    try:
        projects = registered_projects()
    except ConfigError:
        return None
    hits = [p for p in projects if (p.active or not active_only) and p.owns(cwd, projects)]
    if not hits:
        return None
    return max(hits, key=lambda p: len(str(p.home)))


def best_effort(entry: BrokenEntry) -> Project | None:
    """A Project for a broken registry entry, unvalidated, only to tell where it lives and who orchestrates it (the
    hooks' notice that it does not load); None if not even that is possible."""
    home = entry.get("home")
    if not home:
        return None
    f = entry.get("config") or Path(home) / "foreman.toml"
    try:
        raw = toml_reader.loads(Path(f).read_text())
    except (OSError, ValueError):
        raw = {}
    for candidate in (raw, {}):
        try:
            r = {k: v for k, v in candidate.items() if k in KNOWN}
            project = r.get("project")
            r["project"] = {**(project if isinstance(project, dict) else {}), "name": entry["name"]}
            return Project(home, merge_defaults(r), entry.get("mode") or "active")
        except Exception:  # noqa: BLE001 (a broken registry entry is skipped, the next one is tried)
            continue
    return None


def broken_for_cwd(cwd: PathLike) -> tuple[BrokenEntry, Project] | None:
    """(entry, best-effort Project) of a broken registry project that would own `cwd` and whose hooks would act (its
    mode is active or one this foreman never knew), or None: the hooks say it does not load instead of keeping
    quiet (they fail open: no guard for it until it is fixed)."""
    try:
        broken: list[BrokenEntry] = []
        projects = registered_projects(broken=broken)
    except ConfigError:
        return None
    hits: list[tuple[BrokenEntry, Project]] = []
    for e in broken:
        if e.get("mode") == "inactive":  # their hooks never acted
            continue
        p = best_effort(e)
        if p is None:
            continue
        try:
            if p.owns(cwd, projects) or _inside(Path(cwd).expanduser().resolve(), p.home):
                hits.append((e, p))
        except (OSError, ValueError, KeyError):
            continue
    return max(hits, key=lambda h: len(str(h[1].home))) if hits else None


def resolve(name: str | None = None, cwd: PathLike | None = None) -> Project:
    """The project a CLI command is about (see the module docstring). Raises ConfigError if none."""
    if name:
        entries, broken, _ = registry_scan()
        for e in entries:
            if e["name"] == name:
                return load_entry(e)
        for e in broken:
            if e["name"] == name:
                raise ConfigError(f"{name}: {e['error']}")
        raise ConfigError(f"no project {name!r} in {registry_path()}")
    if os.environ.get("FOREMAN_HOME"):
        return load_project(os.environ["FOREMAN_HOME"])
    if os.environ.get("FOREMAN_PROJECT"):
        return resolve(os.environ["FOREMAN_PROJECT"])
    cwd = Path(cwd or os.getcwd())
    p = project_for_cwd(cwd, active_only=False)
    if p:
        return p
    for d in [cwd, *cwd.parents]:  # an unregistered home: a foreman.toml above us
        if (d / "foreman.toml").exists():
            return load_project(d)
    raise ConfigError("no project here: use --project NAME, FOREMAN_HOME=<home>, or run inside a registered project")


def maybe_resolve(name: str | None = None, cwd: PathLike | None = None) -> Project | None:
    try:
        return resolve(name, cwd)
    except ConfigError:
        return None
