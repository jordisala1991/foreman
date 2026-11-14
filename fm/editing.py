"""Editing a task before its agent starts (pure)."""

from collections.abc import Mapping
from typing import Any

from . import backlog, blockers

FIELDS = ("title", "claim", "criteria", "detail")


def refusal(i: backlog.Issue, fields: Mapping[str, Any], issues: list[backlog.Issue]) -> str:
    """Why this edit cannot go on; empty if it can."""
    if not fields:
        return "nothing to change: give --title, --claim, --criteria, --detail or --blocked-by"
    if i["status"] in backlog.CLOSED:
        return f"{i['id']} is {i['status']}: it is closed"
    if i["status"] not in ("triage", "backlog", "ready"):
        return f"{i['id']} is {i['status']}: its agent already has the old text"
    if "blocked_by" in fields:
        return blockers.refusal(issues, i["id"], fields["blocked_by"])
    return ""


def edited(i: backlog.Issue, fields: Mapping[str, Any]) -> backlog.Issue:
    """The task with the given fields replaced; criteria or detail on a planned task drop its plan."""
    new = dict(i)
    new["acceptance"] = {**i.get("acceptance", {}), **{k: v for k, v in fields.items() if k in ("claim", "criteria")}}
    for k in ("title", "detail"):
        if k in fields:
            new[k] = fields[k]
    if "blocked_by" in fields:
        new.pop("released", None)
        if fields["blocked_by"]:
            new["blocked_by"] = fields["blocked_by"]
        else:
            new.pop("blocked_by", None)
    if i["status"] in ("backlog", "ready") and ("criteria" in fields or "detail" in fields):
        new.pop("plan_file", None)
        new["status"] = "backlog"
    return new
