"""`foreman-dev snapshot DEST [--commit REV]`: the public snapshot of foreman's repository, into an empty folder.

It takes the tracked tree at a commit (`git archive`, so nothing untracked or ignored gets in) and leaves out what
only the private repository keeps (EXCLUDED); the CHANGELOG is replaced by a short one that starts the public history
(CHANGELOG). It never creates a remote, pushes or calls gh: publishing the folder is a separate step for whoever owns
the repository (CONTRIBUTING.md, «Cutting the public snapshot»).

EXCLUDED is the one list of what the snapshot leaves out: the publish-safety tests (tests/test_config.py) skip the same
paths when they scan the working tree, and scan an exported snapshot too.
"""

from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # the repository dev/ lives in

# What the public snapshot leaves out, as paths relative to the repository's root (a trailing / is a folder):
# - ops/: the project's own home (backlog, notes, reports, its foreman.toml), private;
# - tests/live/: probes run by hand against real Claude Code sessions, with the logs of those sessions (session ids,
#   temporary folders); the selftest never runs them.
EXCLUDED = ("ops/", "tests/live/")

CHANGELOG = """# Changelog

All notable changes to foreman are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the version follows [Semantic Versioning](https://semver.org/). The version lives in `.claude-plugin/plugin.json`;
each release is tagged `foreman--vX.Y.Z` and the `release` branch points at the latest one (CONTRIBUTING.md).

Besides the usual sections, a release may have **Config** (new `foreman.toml` keys and the `config_schema` they need)
and **Upgrade notes** (anything a user has to do: state, backlog, service or registry changes).

## [Unreleased]

### Added

- First public release of foreman.

## [{version}]

The version the public history starts from; the changes before it were not published.
"""


def excluded(rel: str | Path) -> bool:
    """Whether the snapshot leaves out the path `rel` (relative to the repository's root, `/` separators)."""
    rel = Path(rel).as_posix()
    return any(rel == p.rstrip("/") or (p.endswith("/") and rel.startswith(p)) for p in EXCLUDED)


def changelog(version: str) -> str:
    """The snapshot's CHANGELOG: an [Unreleased] entry for the first public release and the current version's section
    (the release check of the selftest wants both)."""
    return CHANGELOG.format(version=version)


class SnapshotError(Exception):
    pass


def export(dest: str | Path, commit: str = "HEAD", root: str | Path = ROOT) -> dict:
    """Write the snapshot of `root` at `commit` into `dest` (absent or empty; created if absent).

    Returns {"commit", "files", "bytes"}. Raises SnapshotError when `dest` holds anything or git cannot read the
    commit."""
    root, dest = Path(root), Path(dest)
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise SnapshotError(f"{dest} is not an empty folder")
    sha = _git(root, "rev-parse", "--verify", f"{commit}^{{commit}}").decode().strip()
    archive = _git(root, "archive", "--format=tar", sha)
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        members = [m for m in tar.getmembers() if not excluded(m.name.rstrip("/"))]
        if hasattr(tarfile, "data_filter"):  # Python 3.12+ and the security backports: the safe filter, no warning
            tar.extractall(dest, members=members, filter="data")
        else:
            tar.extractall(dest, members=members)
    version = json.loads((dest / ".claude-plugin/plugin.json").read_text())["version"]
    (dest / "CHANGELOG.md").write_text(changelog(version))
    files = [f for f in dest.rglob("*") if f.is_file() and not f.is_symlink()]
    return {"commit": sha, "files": len(files), "bytes": sum(f.stat().st_size for f in files)}


def _git(root: Path, *args: str) -> bytes:
    p = subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=False)
    if p.returncode != 0:
        raise SnapshotError(f"git {' '.join(args)}: {p.stderr.decode(errors='replace').strip()}")
    return p.stdout


def add_parser(sp: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sp.add_parser(
        "snapshot",
        help="export the public snapshot of foreman's repository into an empty folder (never pushes)",
        description="Export the tracked tree at a commit into an empty folder without "
        + ", ".join(EXCLUDED)
        + ", with a CHANGELOG that starts the public history. It never creates a remote, pushes or calls gh.",
    )
    p.add_argument("dest", help="the folder to write into: absent or empty")
    p.add_argument("--commit", default="HEAD", help="the commit to export (default: HEAD)")


def run(args: argparse.Namespace) -> int:
    try:
        r = export(args.dest, args.commit)
    except SnapshotError as e:
        print(f"foreman-dev snapshot: {e}", file=sys.stderr)
        return 2
    print(f"{r['files']} files, {r['bytes'] / 1024:.0f} KB of {r['commit'][:12]} in {args.dest}")
    return 0
