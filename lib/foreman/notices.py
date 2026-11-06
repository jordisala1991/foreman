"""The notice contract of the loop: what a notice is and what is done with one, apart from computing
foreman's own notices (loop.py).

A pass is a list of notices, `{"kind", "actionable", "text", "key"}`: each check decides whether its case asks for
action, `render()` builds the text for the model at the end, and a pass with no actionable notice is not sent to
the model (hooks_mod.tick). Nothing ever decides by the text. `key` names the case (the
same kind with the same data is the same key). In doubt, a notice is actionable: a pass too many costs a little,
a pass that keeps quiet with something pending leaves the product stopped.

The project's own checks ([loop] checks in foreman.toml: shell commands run in the home) are notices too: each output
line is one (a JSON object line says its kind and whether it is actionable, a plain line is an actionable `check`:
check_notice, run_checks). The kinds foreman emits and when each is actionable are in the table of
docs/orchestrator.md («The loop pass»); KINDS lists them. A check cannot speak as foreman: a line that names one of
KINDS is a `check` notice.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime
from typing import TYPE_CHECKING, TypedDict

from . import commands

if TYPE_CHECKING:
    from .config import Project

INFO = "(info) "  # how render() marks a notice that does not ask for action

KINDS = (
    "paused",
    "config_warnings",
    "context_rotate",
    "foreman_behind",
    "sessions_behind",
    "loop_stale",
    "wip_production",
    "wip_ready",
    "dead_agents",
    "delivery_incomplete",
    "orchestrator_only_denied",
    "stalled_agents",
    "quota_high",
    "quota_limit",
    "nameless_agents",
    "lock_foreign",
    "lock_long",
    "lock_queue",
    "ready_to_integrate",
    "review_needed",
    "review_findings",
    "measure_delivered",
    "loose_worktrees",
    "branch_no_worktree",
    "loose_branches",
    "orphan_in_progress",
    "backlog_problems",
    "backlog_unreadable",
    "hung_shells",
    "recurring_late",
    "inbox",
    "decision_due",
    "disk_low",
    "check",
    "check_failed",
    "summary_failed",
)  # the kinds foreman itself emits (each one's `actionable` rule: the table in docs/orchestrator.md); a project
# check may use any other kind of its own


class Notice(TypedDict):
    """One notice of the loop pass (module docstring); `notice()` builds it."""

    kind: str
    actionable: bool
    text: str
    key: str


def notice(kind: str, actionable: object, text: str, key: str | None = None) -> Notice:
    """One notice of the loop pass (module docstring)."""
    return {"kind": kind, "actionable": bool(actionable), "text": text, "key": key or kind}


def check_notice(cmd: str, line: str) -> Notice:
    """The notice of one output line of a project check. A line that is a JSON object with a non-empty string `text`
    is `{"kind", "actionable", "text"}` (optional `key`): `kind` defaults to `check`, `actionable` to true (anything
    but a JSON boolean counts as true: in doubt, actionable). Any other line is plain text: a `check` notice,
    actionable. A kind of foreman's own (KINDS) is not the check's to give: such a line is a `check` notice, so
    foreman's kinds keep meaning what foreman says. The key is
    `check:<cmd>:<kind>:<key or text>`, the same for the same case."""
    line = line.strip()
    if line.startswith("{"):
        try:
            d = json.loads(line)
        except ValueError:
            d = None
        if isinstance(d, dict) and isinstance(d.get("text"), str) and d["text"].strip():
            kind = d["kind"] if isinstance(d.get("kind"), str) and d["kind"].strip() else "check"
            if kind.strip() in KINDS:
                kind = "check"
            actionable = d["actionable"] if isinstance(d.get("actionable"), bool) else True
            case = d["key"] if isinstance(d.get("key"), str) and d["key"].strip() else d["text"].strip()
            return notice(kind.strip(), actionable, d["text"].strip(), f"check:{cmd}:{kind.strip()}:{case}")
    return notice("check", True, line, f"check:{cmd}:check:{line}")


def line_problems(line: str) -> list[str]:
    """What a project check's output line does that check_notice reads otherwise than it likely means (the warnings
    of `foreman check --run-checks`): a line that starts like a JSON object and does not parse, or has no `text`
    (a plain `check` notice); a kind of foreman's own, reserved or not (a `check` notice); an `actionable` that is not
    a JSON boolean (actionable); a `key` that is not a string (ignored). A plain text line has none."""
    line = line.strip()
    if not line.startswith("{"):
        return []
    try:
        d = json.loads(line)
    except ValueError as e:
        return [f"not valid JSON ({e}): the loop reads it as a plain `check` notice, actionable"]
    if not isinstance(d, dict):
        return ["JSON but not an object: the loop reads it as a plain `check` notice, actionable"]
    if not (isinstance(d.get("text"), str) and d["text"].strip()):
        return ["a JSON object without a non-empty string `text`: the loop reads it as a plain `check` notice"]
    out = []
    kind = d.get("kind")
    if kind is not None and not isinstance(kind, str):
        out.append("`kind` is not a string: the loop reads it as `check`")
    elif isinstance(kind, str) and kind.strip() in KINDS:
        out.append(
            f"kind `{kind.strip()}` is one of foreman's own (docs/orchestrator.md): the loop reads it as a `check` "
            "notice; give it a kind of the product's own"
        )
    if "actionable" in d and not isinstance(d["actionable"], bool):
        out.append(f"`actionable` is {d['actionable']!r}, not true or false: the loop reads it as true")
    if "key" in d and not isinstance(d["key"], str):
        out.append("`key` is not a string: the loop ignores it and the case is the text")
    return out


class CheckRun(TypedDict):
    """One project check run by `foreman check --run-checks` (try_checks)."""

    cmd: str
    rc: int | None  # None: cut at the point's time limit
    stderr: str
    lines: list[tuple[Notice, list[str]]]  # each output line's notice and its line_problems
    failed: str  # why the loop would add a check_failed notice ("" when it would not)


def try_checks(project: Project) -> list[CheckRun]:
    """Each [loop] checks command run once as the loop runs it (commands.run at loop.checks, in the home), with the
    notice each output line gives and what is wrong with it (line_problems). Nothing is marked or recorded: the
    loop's own state is untouched."""
    out: list[CheckRun] = []
    for cmd in project.section("loop").get("checks", []):
        r = commands.run("loop.checks", cmd, project=project, cwd=project.home)
        lines = [(check_notice(cmd, ln), line_problems(ln)) for ln in r.stdout.splitlines() if ln.strip()]
        failed = (
            f"timed out ({commands.POINTS['loop.checks'].timeout:g} s)"
            if r.timed_out
            else f"exit code {r.rc} (0 or 1 is normal)"
            if r.rc not in (0, 1)
            else ""
        )
        out.append(
            {
                "cmd": cmd,
                "rc": None if r.timed_out else r.rc,
                "stderr": r.stderr,
                "lines": [] if r.timed_out else lines,
                "failed": failed,
            }
        )
    return out


def run_checks(project: Project) -> list[Notice]:
    """The project's own checks ([loop] checks) as notices, one per output line (check_notice: a JSON object line
    says its kind and whether it is actionable; a plain line is an actionable `check`). A check that times out or
    ends with a code other than 0 or 1 is `check_failed`, actionable."""
    out: list[Notice] = []
    for run in try_checks(project):
        out += [n for n, _ in run["lines"]]
        if run["failed"]:
            why = "timed out" if run["rc"] is None else f"failed (rc {run['rc']})"
            if run["rc"] is None:
                why += f" ({commands.POINTS['loop.checks'].timeout:g} s)"
            else:
                why += f": {run['stderr'].strip()[:200]}"
            out.append(notice("check_failed", True, f"check `{run['cmd']}` {why}", f"check_failed:{run['cmd']}"))
    return out


def changed_mark(project: Project, name: str, value: str | None, mark: bool = True) -> bool:
    """True if `value` differs from what <state>/<name> holds (with mark, it records it); value None removes the mark.
    A notice that is actionable only when its case appears or changes (config warnings, low disk) uses it."""
    f = project.state / name
    try:
        old = f.read_text()
    except OSError:
        old = None
    if value is None:
        if mark and old is not None:
            f.unlink(missing_ok=True)
        return False
    if mark and old != value:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(value)
    return old != value


def read_keys(project: Project, name: str) -> list[str]:
    """The keys of the «already notified» mark <state>/<name> (one per line), in the order stored; none when absent."""
    try:
        return [k for k in (project.state / name).read_text().split("\n") if k]
    except OSError:
        return []


def write_keys(project: Project, name: str, keys: Iterable[str], limit: int | None = None) -> None:
    """Store `keys` as the mark <state>/<name>, in the order given (repeats dropped): when `limit` characters are
    exceeded, the oldest whole keys go and the newest stay."""
    f = project.state / name
    f.parent.mkdir(parents=True, exist_ok=True)
    kept = list(dict.fromkeys(keys))
    while limit and len("\n".join(kept)) > limit and len(kept) > 1:
        kept.pop(0)
    f.write_text("\n".join(kept))


def idle(ns: Iterable[Notice]) -> bool:
    """True if no notice asks for action: such a pass is not sent to the model."""
    return not any(n["actionable"] for n in ns)


def render(ns: list[Notice], head: str | None = None) -> str:
    """The text the model reads, built at the end: the head and one line per notice, information-only ones marked."""
    head = head or f"Loop pass {datetime.now():%H:%M}"
    if not ns:
        return head + ": nothing asks for action."
    lines = "\n".join(f"- {'' if n['actionable'] else INFO}{n['text']}" for n in ns)
    if idle(ns):
        return head + ": nothing asks for action; for information:\n" + lines
    return head + f". Act on each line (those marked {INFO.strip()} only inform):\n" + lines
