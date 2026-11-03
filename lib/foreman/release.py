"""Version and release checks, run by `foreman selftest` (static checks) on the plugin's own folder.

One source of the version: `.claude-plugin/plugin.json` `version`; the package reads it (lib/foreman/__init__.py).
A release is a commit that bumps that version, a CHANGELOG.md section for it, a tag `<name>--v<version>` on that
commit and the `release` branch moved to the tag (CONTRIBUTING.md). Installed copies only change when the version
does, so `release` must only ever sit on the tag of its own version.

Checks:
- plugin.json has a semantic version and the package reports the same one;
- the marketplace manifest declares no version for its plugins (plugin.json wins) and no absolute source path;
- CHANGELOG.md has an `[Unreleased]` section and one for the current version;
- in a git checkout (skipped otherwise, e.g. an installed copy): a version tag on HEAD names the current version,
  and a `release` branch, if any, sits on the tag of the version it carries.
"""

from __future__ import annotations

import functools
import json
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from . import util

if TYPE_CHECKING:
    from .util import PathLike

SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
RELEASE_BRANCH = "release"


_git = functools.partial(util.git, check=False)  # "" when git fails


def tag_for(name: str, version: str | None) -> str:
    """The tag of a version, as `claude plugin tag` names it: <plugin name>--v<version>."""
    return f"{name}--v{version}"


def _is_git_root(root: PathLike) -> bool:
    if not shutil.which("git"):
        return False
    top = _git(root, "rev-parse", "--show-toplevel")
    return bool(top) and Path(top).resolve() == Path(root).resolve()


def check(root: PathLike, code_version: str | None = None) -> list[str]:
    """Problems (strings) with the version and release state of the plugin folder `root`; [] when all is well."""
    root = Path(root)
    problems: list[str] = []
    try:
        manifest = json.loads((root / ".claude-plugin/plugin.json").read_text())
    except (OSError, ValueError) as e:
        return [f"version: .claude-plugin/plugin.json unreadable ({e})"]
    name, version = manifest.get("name"), manifest.get("version")
    if not isinstance(version, str) or not SEMVER.match(version):
        return [f"version: .claude-plugin/plugin.json version {version!r} is not a semantic version (X.Y.Z)"]
    if code_version is None:
        from . import __version__ as code_version
    if code_version != version:
        problems.append(f"version: the package reports {code_version!r}, .claude-plugin/plugin.json says {version!r}")

    market = root / ".claude-plugin/marketplace.json"
    if market.exists():
        try:
            entries = json.loads(market.read_text()).get("plugins", [])
        except (OSError, ValueError, AttributeError) as e:
            entries = []
            problems.append(f"version: .claude-plugin/marketplace.json unreadable ({e})")
        for p in entries if isinstance(entries, list) else []:
            if not isinstance(p, dict):
                continue
            if "version" in p:
                problems.append(
                    f"version: marketplace.json declares a version for {p.get('name')!r}; "
                    "only .claude-plugin/plugin.json does"
                )
            src = p.get("source")
            if isinstance(src, str) and not src.startswith("./"):
                problems.append(f"version: marketplace.json source {src!r} of {p.get('name')!r} is not relative (./…)")

    try:
        changelog = (root / "CHANGELOG.md").read_text()
    except OSError:
        problems.append("version: no CHANGELOG.md")
    else:
        heads = set(re.findall(r"^## \[([^\]]+)\]", changelog, re.M))
        if "Unreleased" not in heads:
            problems.append("version: CHANGELOG.md has no '## [Unreleased]' section")
        if version not in heads:
            problems.append(f"version: CHANGELOG.md has no '## [{version}]' section for the current version")

    if name and _is_git_root(root):
        prefix = f"{name}--v"
        on_head = [t for t in (_git(root, "tag", "--points-at", "HEAD") or "").split() if t.startswith(prefix)]
        for t in on_head:
            if t != tag_for(name, version):
                problems.append(f"version: HEAD is tagged {t} but .claude-plugin/plugin.json says {version}")
        rel = _git(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{RELEASE_BRANCH}^{{commit}}")
        if rel:
            try:
                rel_version = json.loads(_git(root, "show", f"{rel}:.claude-plugin/plugin.json") or "").get("version")
            except (ValueError, AttributeError):
                rel_version = None
            tag = tag_for(name, rel_version)
            tagged = _git(root, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}")
            if tagged != rel:
                problems.append(
                    f"version: branch {RELEASE_BRANCH} carries {rel_version} but is not on tag {tag}: "
                    "it only moves to the tagged commit that bumps the version (CONTRIBUTING.md)"
                )
    return problems
