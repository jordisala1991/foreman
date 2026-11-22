"""`foreman health --json`: what `foreman health` prints, as one object (DESIGN.md «The JSON reads»)."""

from typing import Any

from ...core.project import registry
from ...core.project.config import Project
from ...providers.runner import Rows
from .. import schedule
from . import checks, health, notes, orca

FINDING = ("key", "title", "meaning", "evidence", "hypothesis")


def build(p: Project, rows: Rows) -> dict[str, Any]:
    """The loop's beat, the versions, the recurring entries (`chain` None when an entry has no chain step open), the
    findings seen from outside the loop, and the "!" lines health prints above them."""
    v = notes.facts(p)
    warnings = [*notes.python_note(), *notes.shared_folders(registry.loaded()), *notes.versions(p)[1:], *notes.old_links(p), *orca.warnings(p)]
    return {
        "loop": checks.loop_beat(p),
        "versions": {"loop": v["loop"], "this": v["this"], "installed": v["installed"]},
        "recurring": [{**r, "chain": r.get("chain")} for r in schedule.rows(p)],
        "findings": [{**{k: f[k] for k in FINDING}, "repo": f.get("repo", "")} for f in health.findings(p, rows, outside=True)],
        "warnings": warnings,
    }
