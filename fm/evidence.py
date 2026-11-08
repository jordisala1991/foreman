"""Images of a task's report (screenshots as proof). The report names them as bare file names, `![what](x.png)`, saved
in `reports/ID/`; GitHub gets them on the `fm-evidence` branch (the loop pushes it, never an agent), the panel serves them
itself. Pure text rules: no I/O here."""

import re

BRANCH = "fm-evidence"
KINDS = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
MAX_BYTES = 2_000_000

NAME = re.compile(r"[A-Za-z0-9._-]+")
IMAGE = re.compile(r"(!\[[^\]]*\]\()([^()\s]+)(\))")


def kind(name: str) -> str:
    """The content type of an image file name; '' when it is not an image fm publishes."""
    return KINDS.get(name[name.rfind(".") :].lower(), "") if "." in name else ""


def bare(name: str) -> bool:
    """A plain file name (letters, digits, `.`, `_`, `-`; no dot first) with an image extension."""
    return bool(NAME.fullmatch(name)) and not name.startswith(".") and bool(kind(name))


def names(md: str) -> list[str]:
    """File names of the report's images whose target is a bare file name, in order, once each."""
    found = [m.group(2) for m in IMAGE.finditer(md) if bare(m.group(2))]
    return list(dict.fromkeys(found))


def relink(md: str, urls: dict[str, str]) -> str:
    """The report with the target of each image named in `urls` replaced; the others stay as written."""
    return IMAGE.sub(lambda m: m.group(1) + urls.get(m.group(2), m.group(2)) + m.group(3), md)
