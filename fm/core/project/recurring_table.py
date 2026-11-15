"""The `[[recurring]]` tables of fm.toml (DESIGN.md `## Recurring tasks`), read strictly. Pure: raises ValueError,
`config` turns it into a ProjectError. Apart from `recurring` because `config` imports this and `recurring` imports
the backlog, which imports `config`."""

import re
from datetime import timedelta
from typing import Any

from ... import util

TEXTS = ("name", "title", "goal", "criteria", "detail", "model", "effort", "type", "every")
SETTINGS = (*TEXTS, "every_merges", "triage")
MAX_SPAN = timedelta(days=36500)  # so that a due date always fits in a date
UNITS = {"h": timedelta(hours=1), "d": timedelta(days=1), "w": timedelta(weeks=1)}


def interval(text: object) -> timedelta | None:
    """`12h`, `7d` or `2w` as a duration; None for anything else, or for more than 100 years."""
    m = re.fullmatch(r"([1-9]\d{0,5})([hdw])", text) if isinstance(text, str) else None
    return int(m[1]) * UNITS[m[2]] if m and int(m[1]) * UNITS[m[2]] <= MAX_SPAN else None


def parse(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The entries by name: every setting present, the texts empty and `triage` true when not set."""
    listed: object = raw.get("recurring", [])
    rows = util.json_list(listed)
    tables = [t for t in (util.json_object(r) for r in rows) if t is not None]
    if not isinstance(listed, list) or len(tables) != len(rows):
        raise ValueError("[[recurring]] holds tables")
    out: dict[str, dict[str, Any]] = {}
    for n, t in enumerate(tables, 1):
        name = t.get("name")
        named = isinstance(name, str) and name != ""
        where = f'[[recurring]] "{name}"' if named else f"[[recurring]] #{n}"
        _check_settings(where, t)
        _check_trigger(where, t)
        if name in out:
            raise ValueError(f"{where} is declared twice")
        out[str(name)] = {**{k: "" for k in TEXTS}, "every_merges": None, "every": None, "triage": True, **t}
    return out


def _check_settings(where: str, t: dict[str, Any]) -> None:
    for k in t:
        if k not in SETTINGS:
            raise ValueError(f"{where} has no setting '{k}'")
    for k in ("name", "title"):
        if not isinstance(t.get(k), str) or not t[k]:
            raise ValueError(f"{where} needs a {k} (a non-empty text)")
    for k in TEXTS:
        if not isinstance(t.get(k, ""), str):
            raise ValueError(f"{where} {k} is a text")
    if not isinstance(t.get("triage", True), bool):
        raise ValueError(f"{where} triage is true or false")


def _check_trigger(where: str, t: dict[str, Any]) -> None:
    if "every_merges" in t and "every" in t:
        raise ValueError(f"{where} has both every_merges and every: use one")
    if "every_merges" not in t and "every" not in t:
        raise ValueError(f"{where} needs every_merges or every")
    merges = t.get("every_merges")
    if "every_merges" in t and (not isinstance(merges, int) or isinstance(merges, bool) or merges < 1):
        raise ValueError(f"{where} every_merges is a whole number of 1 or more")
    if "every" in t and interval(t["every"]) is None:
        raise ValueError(f"{where} every: {t['every']!r} is not an interval (a number and h, d or w: 12h, 7d, 2w)")
