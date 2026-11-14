"""A mechanic's diagnosis of a health task: the sections it must have, and how it is stored in the task."""

from . import backlog, util
from .config import Project

SECTIONS = ("Seen", "Inferred", "Same area before", "Experiment", "Root cause", "Proposed fix")
GONE = "Symptom gone"


def missing_sections(text: str) -> list[str]:
    """The required `## ` headings the text lacks, in SECTIONS order. A symptom already gone needs only Seen."""
    found = {line[3:].strip().lower() for line in text.splitlines() if line.startswith("## ")}
    required = SECTIONS[:1] if GONE.lower() in found else SECTIONS
    return [s for s in required if s.lower() not in found]


def record(p: Project, iid: str, title: str, body: str, **marker: str) -> bool:
    """Appends the diagnosis to the task's detail and marks it; False (nothing changed) if it already has one."""
    with backlog.edit(p) as d:
        i = backlog.get(d, iid)
        if i.get("diagnosis"):
            return False
        i["detail"] = (i.get("detail", "") + f"\n\n# {title}\n{body.strip()}").strip()
        i["diagnosis"] = {"at": util.now(), **marker}
    return True
