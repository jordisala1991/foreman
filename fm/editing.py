"""Editing a task before its agent starts (pure)."""

from collections.abc import Mapping

from . import backlog

FIELDS = ("title", "claim", "criteria", "detail")


def refusal(i: backlog.Issue, fields: Mapping[str, str]) -> str:
    """Why this edit cannot go on; empty if it can."""
    if not fields:
        return "nothing to change: give --title, --claim, --criteria or --detail"
    if i["status"] in backlog.CLOSED:
        return f"{i['id']} is {i['status']}: it is closed"
    if i["status"] not in ("triage", "backlog", "ready"):
        return f"{i['id']} is {i['status']}: its agent already has the old text"
    return ""


def edited(i: backlog.Issue, fields: Mapping[str, str]) -> backlog.Issue:
    """The task with the given fields replaced; criteria or detail on a planned task drop its plan."""
    new = dict(i)
    new["acceptance"] = {**i.get("acceptance", {}), **{k: v for k, v in fields.items() if k in ("claim", "criteria")}}
    for k in ("title", "detail"):
        if k in fields:
            new[k] = fields[k]
    if i["status"] in ("backlog", "ready") and ("criteria" in fields or "detail" in fields):
        new.pop("plan_file", None)
        new["status"] = "backlog"
    return new
