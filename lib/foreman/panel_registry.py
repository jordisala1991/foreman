"""The panel's view of the registry: the projects (reloaded when a config file changes), the panel settings and the
resources of every project.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import config
from .panel_cache import file_sig

if TYPE_CHECKING:
    from pathlib import Path

    from .config import Project, Table


class Registry:
    """The registry's projects, reloaded when the registry or a foreman.toml changes (the registry is read and parsed
    again only when its own file changes). Read tolerantly: a project with warnings is in `projects` (its
    Project.warnings shown on it); one that does not load is in `broken` ({name, mode, error}), shown in the picker
    with its error; `errors` lists both kinds of problem as text."""

    def __init__(self) -> None:
        self._sig: tuple[Any, ...] | None = None
        self._read: tuple[Any, ...] | None = None  # (registry path, its file_sig) when `_reg` and `_files` were read
        self._reg: Table | config.ConfigError = {}
        self._files: list[Path] = []
        self.projects: dict[str, Project] = {}
        self.broken: list[dict[str, Any]] = []
        self.errors: list[str] = []
        self.settings: dict[str, Any] = {}
        self.machine: dict[str, Table] = {}

    def read(self) -> None:
        """The registry (or its ConfigError) and the config files to watch, read again when the registry changes."""
        path = config.registry_path()
        key = (str(path), file_sig([path]))  # taken before reading: a change while reading is seen next time
        if key == self._read:
            return
        try:
            self._reg = config.read_registry()
            entries, broken, _ = config.registry_scan(self._reg)
        except config.ConfigError as e:
            self._reg, entries, broken = e, [], []
        self._read = key
        self._files = [path] + [x["config"] or h / "foreman.toml" for x in entries + broken if (h := x.get("home"))]

    def refresh(self) -> None:
        self.read()
        files = self._files
        sig = (tuple(map(str, files)), file_sig(files))
        if sig == self._sig:
            return
        errors, broken, warnings = [], [], []
        try:
            if isinstance(self._reg, config.ConfigError):
                raise self._reg
            projects = config.registered_projects(errors, broken, warnings, reg=self._reg)
            settings = config.panel_settings(self._reg)
            machine = config.machine_resources(self._reg)[0]
        except config.ConfigError as e:
            projects, settings, errors, machine = [], {"language": "en", "who": "owner"}, [str(e)], {}
        self.projects = {p.name: p for p in projects}
        self.machine = machine
        self.broken = [
            {"name": b["name"], "mode": b.get("mode") or "", "error": b["error"]}
            for b in broken
            if b["name"] not in self.projects
        ]
        self.errors, self.settings, self._sig = errors + warnings, settings, sig

    def get(self, name: str) -> Project | None:
        self.refresh()
        return self.projects.get(name)

    def language(self) -> str:
        return self.settings.get("language") or "en"

    def resources(self) -> dict[str, tuple[Table, list[str], bool]]:
        """{name: (config, [project names], machine?)}. A machine resource of the registry (config.machine_resources,
        the one resolution) gives the machine's table whatever the registry order and lists the products that use it
        (possibly none: then no active product can take it). Any other resource: the first project that declares it
        gives its config."""
        self.refresh()
        out = {name: (res, [], True) for name, res in self.machine.items()}
        for p in self.projects.values():
            for name, res in p.resources.items():
                if name in self.machine:  # the machine wins (merge_machine): the product only uses it
                    out[name][1].append(p.name)
                    continue
                cfg, users, _ = out.get(name, (res, [], False))
                users.append(p.name)
                out[name] = (cfg, users, False)
        return out

    def busy_cwd(self, users: list[str], machine: bool) -> Path | None:
        """Where a resource's `busy` runs: the registry's folder for a machine resource (its relative paths resolve
        there too, and it belongs to no product), else the home of the first project that declares it."""
        if machine:
            return config.registry_path().parent
        return self.projects[users[0]].home if users else None


REG = Registry()
