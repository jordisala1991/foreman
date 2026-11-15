"""Which fm runs: the folder this code runs from (`ROOT`), and the version of the plugin `fm@fm` Claude Code has installed.

The launchd jobs start through `main`: the plist carries this file's text (`python3 -c`), which reads Claude Code's record
of the installed version (`~/.claude/plugins/installed_plugins.json`), sets `FM_PINNED` to its folder and runs that
folder's `bin/fm`. Each version folder never changes, so a job runs one version from start to exit; a job started that
way exits when the record names another folder (`stale`), and launchd starts it again on the new one.

The record's format is Claude Code's, undocumented: fm reads format version 2, keys off `installPath` (its
`gitCommitSha` is known to lag behind updates) and says in one line what does not fit. Standard library only: under
`python3 -c` it runs alone."""

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

ROOT = Path(globals().get("__file__", ".")).resolve().parent.parent  # under `python3 -c` (the plist) there is no __file__
PINNED = "FM_PINNED"
PLUGIN = "fm@fm"


@dataclass(frozen=True)
class Installed:
    path: Path
    version: str


class LauncherError(Exception):
    pass


def record() -> Path:
    return Path.home() / ".claude/plugins/installed_plugins.json"


def installed() -> Installed:
    """The version of fm@fm installed for the user, or LauncherError with one line saying why there is none."""
    f = record()
    try:
        data: Any = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise LauncherError(f"{PLUGIN} is not installed in Claude Code ({f}: {e})") from None
    top = _object(data)
    if top.get("version") != 2:
        raise LauncherError(f"{f} has format version {top.get('version')}, fm reads 2: update fm")
    entries: Any = _object(top.get("plugins")).get(PLUGIN)
    listed = cast(list[Any], entries) if isinstance(entries, list) else []
    users = [u for u in map(_object, listed) if u.get("scope") == "user"]
    if not users:
        raise LauncherError(f"{PLUGIN} is not installed in Claude Code: run claude plugin install {PLUGIN}")
    if len(users) > 1:
        raise LauncherError(f"{f}: unexpected format for {PLUGIN} ({len(users)} user installs)")
    path, version = users[0].get("installPath"), users[0].get("version")
    if not isinstance(path, str) or not isinstance(version, str):
        raise LauncherError(f"{f}: unexpected format for {PLUGIN} (no installPath or version)")
    if not (Path(path) / "bin/fm").is_file():
        raise LauncherError(f"{PLUGIN} {version}: its folder {path} is missing")
    return Installed(Path(path).resolve(), version)


def _object(v: object) -> dict[str, Any]:
    """The JSON value as an object, or {} (as util.json_object, which this file cannot import: it runs alone)."""
    return cast(dict[str, Any], v) if isinstance(v, dict) else {}


def pinned() -> str:
    """The version folder a launchd job was started on, or "" for any other run."""
    return os.environ.get(PINNED, "")


def stale() -> str:
    """Why a job started by `main` must exit (another version is installed, or none), or ""."""
    if not (pin := pinned()):
        return ""
    try:
        now = installed()
    except LauncherError as e:
        return str(e)
    return "" if now.path == Path(pin) else f"{PLUGIN} {now.version} is installed now; this runs {Path(pin).name}"


def from_plugin() -> bool:
    """Whether this code runs from a version folder of the installed plugin (a sibling of the installed one)."""
    try:
        return installed().path.parent == ROOT.parent
    except LauncherError:
        return False


def source() -> str:
    """This file's text: what a launchd plist runs with `python3 -c`."""
    return Path(__file__).read_text(encoding="utf-8")


def main(argv: list[str]) -> None:
    try:
        now = installed()
    except LauncherError as e:
        sys.exit(f"fm: {e}")
    os.environ[PINNED] = str(now.path)
    fm = str(now.path / "bin/fm")
    os.execv(sys.executable, [sys.executable, fm, *argv])


if __name__ == "__main__":
    main(sys.argv[1:])
