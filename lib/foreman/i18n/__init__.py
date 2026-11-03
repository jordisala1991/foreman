"""Texts a person reads (generated pages, hook notices for the owner), by language: en.json, es.json.
A project picks one with [project] language and may override any key in [strings] (e.g. to keep the exact
wording of pages it already has). The panel's texts (panel/strings.<language>.json) are foreman's: a product does not
override them (the old [strings.panel] table is ignored, with a `foreman check` warning: config.REMOVED).
Code, JSON keys and CLI messages stay in English.

An override is checked against the English file (`override_problems`, which config.problems uses): an unknown key is
a warning (`foreman check` fails, the project loads), a value of the wrong kind an error; a plural pair (`x_1` for
one, `x_n` for any other number) given only in half is a warning."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..config import Project

HERE = Path(__file__).parent
PANEL = Path(__file__).resolve().parents[3] / "panel"  # the panel's own texts: panel/strings.<language>.json
OLD_PANEL_KEY = "panel"  # [strings.panel], removed: ignored (config.REMOVED warns)
PLURAL = ("_1", "_n")


def _read(folder: Path, name: str) -> dict[str, Any]:
    return json.loads((folder / name).read_text())


def _merge(base: dict[str, Any], overrides: dict[str, Any] | None) -> dict[str, Any]:
    for k, v in (overrides or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            base[k] = {**base[k], **v}
        else:
            base[k] = v
    return base


def strings(language: str = "en", overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    base = _read(HERE, "en.json")
    f = HERE / f"{language}.json"
    if language != "en" and f.exists():
        base.update(json.loads(f.read_text()))
    return _merge(base, overrides)


def panel_strings(language: str = "en") -> dict[str, Any]:
    """The panel's texts: panel/strings.en.json, then the language's over them."""
    base = _read(PANEL, "strings.en.json")
    f = PANEL / f"strings.{language}.json"
    if language != "en" and f.exists():
        base.update(json.loads(f.read_text()))
    return base


def half_plurals(overrides: dict[str, Any], base: dict[str, Any]) -> list[tuple[str, str]]:
    """[(given, missing)]: plural pairs of `base` (key_1 and key_n) of which `overrides` sets only one."""
    out = []
    for k in overrides:
        stem, end = k[:-2], k[-2:]
        if end not in PLURAL:
            continue
        other = stem + (PLURAL[1] if end == PLURAL[0] else PLURAL[0])
        if k in base and other in base and other not in overrides:
            out.append((k, other))
    return out


def override_problems(overrides: dict[str, Any], base: dict[str, Any], where: str) -> tuple[list[str], list[str]]:
    """(errors, warnings) of a table of text overrides against `base` (the English texts). `where` names the table
    in the messages ("[strings]")."""
    err, warn = [], []
    unknown = []
    for k, v in overrides.items():
        if k not in base:
            unknown.append(k)
        elif isinstance(base[k], dict):
            if not isinstance(v, dict):
                err.append(f"{where} {k} must be a table of texts (its keys: {', '.join(sorted(base[k]))})")
                continue
            extra = sorted(set(v) - set(base[k]))
            if extra:
                warn.append(f"{where} {k}: unknown keys {', '.join(extra)} (ignored)")
            err += [f"{where} {k}.{sk} must be a text (a string)" for sk, sv in v.items() if not isinstance(sv, str)]
        elif not isinstance(v, str):
            err.append(f"{where} {k} must be a text (a string)")
    if unknown:
        warn.append(f"{where}: unknown keys {', '.join(sorted(unknown))} (ignored)")
    for given, missing in half_plurals(overrides, base):
        warn.append(
            f"{where} {given} is set without {missing}: a plural pair is used whole, so both keep foreman's "
            f"texts until you set {missing} too"
        )
    return err, warn


def project_overrides(project: Project) -> dict[str, Any]:
    """A project's [strings] (without an old [strings.panel] table, ignored)."""
    return {k: v for k, v in project.section("strings").items() if k != OLD_PANEL_KEY}


def for_project(project: Project) -> dict[str, Any]:
    return strings(project.language, project_overrides(project))
