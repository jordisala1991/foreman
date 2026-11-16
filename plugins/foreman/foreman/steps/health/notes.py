from pathlib import Path

from ... import launcher, util
from ...core.project import registry
from ...core.project.config import Project


def python_note() -> list[str]:
    """bin/foreman's line when the hook found no Python new enough (see bin/foreman)."""
    try:
        text = registry.python_note_file().read_text(encoding="utf-8").strip()
    except OSError:
        return []
    return ["! " + text] if text else []


def shared_folders(projects: list[Project]) -> list[str]:
    """A folder two projects claim: `foreman` refuses to pick one there."""
    return [f"! {f} is claimed by two projects: {' and '.join(names)}" for f, names in registry.shared(projects).items()]


def old_links(p: Project) -> list[str]:
    """The links `foreman init` made before the plugin (FN-143), in the home or a repo: the plugin replaces them, so they
    are to be deleted."""
    out: list[str] = []
    for folder in dict.fromkeys([p.home, *p.repos]):
        where = "the home" if folder == p.home else str(folder)
        found = [rel for rel in (".claude/fm.md", ".claude/skills/define-task") if (folder / rel).is_symlink()]
        lines = [f"! {where} still has the old link {rel} (FN-143): the plugin replaces it; delete it" for rel in found]
        if folder == p.home and found[:1] == [".claude/fm.md"] and _includes_fm_md(p.home / "CLAUDE.md"):
            lines[0] += ", and remove the line @.claude/fm.md from CLAUDE.md"
        out += lines
    return out


def _includes_fm_md(claude_md: Path) -> bool:
    try:
        lines = claude_md.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):  # no CLAUDE.md, or one fm cannot read: the link is still named
        return False
    return "@.claude/fm.md" in (line.strip() for line in lines)


def repos_note(p: Project) -> str:
    n = len(p.repos)
    return f"note: {p.name} has {n} repos; its tasks run in the first one, {p.repo}" if n > 1 else ""


def versions(p: Project) -> list[str]:
    """Which fm this command, the loop and Claude Code's install are; and that fm@fm cannot start the launchd jobs."""
    hb = util.json_object(util.read_json(p.state / "loop.json", {})) or {}
    try:
        now, why = launcher.installed().version, ""
    except launcher.LauncherError as e:
        now, why = "none", str(e)
    out = [f"versions: this command {launcher.ROOT.name} · the loop {hb.get('version') or 'unknown'} · installed {now}"]
    return out + [f"! fm@fm cannot start the loop and the panel: {why}"] if why else out
