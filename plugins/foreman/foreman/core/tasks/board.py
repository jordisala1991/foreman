"""The Board's vocabulary (DESIGN.md «Every key of a task»): its Status options, the fields fm keeps on it and the label of
an open question. The names are the contract: fm finds every field and option by name."""

from dataclasses import dataclass

from ..project import config
from . import backlog, tasktypes

STATUS = tuple(s[0].upper() + s[1:] for s in backlog.STATUSES)  # Triage … In progress … Duplicate; `.lower()` maps back
LABEL = "needs-owner"


@dataclass(frozen=True)
class Field:
    name: str
    kind: str  # SINGLE_SELECT | TEXT | NUMBER, as GitHub names a field's data type
    options: tuple[str, ...] = ()


FIELDS = (
    Field("Status", "SINGLE_SELECT", STATUS),
    Field("Model", "SINGLE_SELECT", config.MODELS),
    Field("Effort", "SINGLE_SELECT", config.EFFORTS),
    Field("Priority", "SINGLE_SELECT", tuple(p for p in backlog.PRIORITIES if p != "none")),
    Field("Task type", "SINGLE_SELECT", tasktypes.TYPES),
    Field("Repo key", "TEXT"),
    Field("Source", "TEXT"),
    Field("Blocked", "TEXT"),
    Field("Lens", "TEXT"),
    Field("Max steps", "NUMBER"),
    Field("Max lines", "NUMBER"),
)
