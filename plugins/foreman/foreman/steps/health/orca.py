"""Health of a project that runs its agents through Orca (D8): the program, the app answering, Orca's hooks."""

from pathlib import Path

from ...core.project.config import Project
from ...providers.orca import cli, hooks
from .checks import Finding, finding

SETTINGS = Path.home() / ".claude" / "settings.json"


def findings(p: Project, settings: Path = SETTINGS) -> list[Finding]:
    if p.get("agents", "runner") != "orca":
        return []
    if not cli.found():
        looked = f"Looked for: {cli.binary()} ($ORCA_BIN, orca on PATH, then the app's own copy)."
        return [finding("orca-missing", "The orca program is not found", "Agents cannot start: the runner is orca.", looked)]
    if events := hooks.missing(settings):
        meaning = "No agent state ever appears, so every agent reads waiting."
        return [finding("orca-hooks", "Orca's hooks are not in Claude's settings", meaning, f"{settings} lacks Orca's hook for: {', '.join(events)}.")]
    return []


def warnings(p: Project) -> list[str]:
    """Printed by `foreman health`, never filed: a closed Orca is not a defect."""
    if p.get("agents", "runner") == "orca" and cli.found() and not cli.running():
        return ["! Orca is not running or does not answer `orca status`: agents cannot start until it is open"]
    return []
