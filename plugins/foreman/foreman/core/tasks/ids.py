"""A task's id on GitHub (DESIGN.md «Ids»): `PREFIX-<issue number>`, or the old id a migrated task keeps through
`<home>/legacy-ids.json` ({"FN-140": 272, …})."""

from collections.abc import Mapping

from ... import util
from .backlog import BacklogError


def task_id(prefix: str, number: int, legacy: Mapping[str, int]) -> str:
    """The id of issue #number: its legacy id when the map holds the number, else PREFIX-number."""
    return next((tid for tid, n in legacy.items() if n == number), f"{prefix}-{number}")


def legacy_ids(raw: object) -> dict[str, int]:
    """The legacy map as read from its file (None: there is none)."""
    if raw is None:
        return {}
    o = util.json_object(raw)
    if o is None or not all(isinstance(n, int) and not isinstance(n, bool) for n in o.values()):
        raise BacklogError(f"legacy-ids.json: expected an object of id → issue number, found {raw!r}"[:300])
    return {str(k): int(v) for k, v in o.items()}
