"""The plan gate: a ready task with a plan is launched only when its declared risk is covered by the project's
`[integrate] autonomy` level (the same table the branch gate uses, risk.AUTONOMY); otherwise it waits for the owner
(`fm approve-plan` / `fm reject-plan`). A task with no plan file (trivial) never waits. No status, no config key: the
decision is made live at launch and recorded as `plan_approved {by, at}`."""

import re
from pathlib import Path
from typing import Any

from . import backlog, util
from .config import Project
from .risk import allows

FACTORS = (
    ("sensitive paths", "high"),
    ("architecture", "high"),
    ("open decision", "high"),
    ("new dependency", "medium"),
    ("data-format", "medium"),
    ("data format", "medium"),
    ("size", "medium"),
)
ORDER = ("low", "medium", "high")


def declared(text: str) -> str:
    """The text of the plan's 'Declared risk' section (a bullet line or a heading), '' when missing."""
    m = re.search(r"^[ \t]*(?:[-*][ \t]*)?(?:#+[ \t]*)?\**declared risk\**[ \t]*:?\**[ \t]*:?(.*?)(?=^[ \t]*(?:[-*][ \t]+\*\*|#)|\Z)", text, re.I | re.M | re.S)
    return m.group(1).strip() if m else ""


def plan_risk(text: str) -> tuple[str, list[str]]:
    """(level, reasons) from the Declared risk section; missing, empty or unrecognised is high (fail safe)."""
    body = declared(text).lower()
    found = [(name, level) for name, level in FACTORS if name in body]
    if found:
        return max((lv for _, lv in found), key=ORDER.index), [name for name, _ in found]
    if re.search(r"\bnone\b", body):
        return "low", ["declares no risk"]
    return "high", ["Declared risk missing, empty or unrecognised"]


def risk_of(i: dict[str, Any]) -> str:
    try:
        return plan_risk(Path(i["plan_file"]).read_text(encoding="utf-8"))[0]
    except OSError:
        return "high"


def needs_plan_approval(i: dict[str, Any]) -> bool:
    return i["status"] == "ready" and bool(i.get("plan_file")) and not i.get("plan_approved")


def pending(p: Project, i: dict[str, Any]) -> bool:
    """The plan waits for the owner: not approved, and the autonomy level does not cover its risk."""
    return needs_plan_approval(i) and not allows(p.get("integrate", "autonomy"), risk_of(i))


def passes(p: Project, i: dict[str, Any]) -> bool:
    """True when the loop may launch this ready issue; records the approval when the autonomy level gives it."""
    if not needs_plan_approval(i):
        return True
    if pending(p, i):
        return False
    level = p.get("integrate", "autonomy")
    backlog.update(p, i["id"], plan_approved={"by": f"autonomy level {level}", "at": util.now()})
    backlog.event(p, i["id"], f"plan approved by autonomy level {level}")
    return True


def check_waiting(p: Project, iid: str) -> dict[str, Any]:
    i = backlog.get(backlog.load(p), iid)
    if not needs_plan_approval(i):
        raise SystemExit(f"fm: {iid} has no plan waiting for approval (it must be ready, with a plan, not yet approved)")
    return i


def approve(p: Project, iid: str, by: str = "owner") -> None:
    check_waiting(p, iid)
    backlog.update(p, iid, plan_approved={"by": by, "at": util.now()})
    backlog.event(p, iid, f"plan approved by {by}")


def reject(p: Project, iid: str, note: str) -> None:
    i = check_waiting(p, iid)
    with backlog.edit(p) as d:
        it = backlog.get(d, iid)
        it["detail"] = (it.get("detail", "") + f"\n\nPlan rejected by the owner: {note}").strip()
        it["status"] = "backlog"
        it.pop("plan_approved", None)
    backlog.event(p, i["id"], f"plan rejected: {note}")
