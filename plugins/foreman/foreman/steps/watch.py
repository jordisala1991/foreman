"""`foreman watch`: one line each time something starts needing the owner. It decides from the backlog and main's CI
result (`state/ci.json`), never from the text of events.jsonl."""

import time
from typing import Any, NamedTuple

from .. import util
from ..core.project.config import Project, ProjectError
from ..core.review import reviews
from ..core.rules import decide
from ..core.tasks import backlog, questions
from ..providers import registry

EVERY_S = 10


def pause() -> None:
    time.sleep(EVERY_S)


def _line(text: str) -> str:
    return " ".join(text.split())


def _waits_for_accept(p: Project, i: dict[str, Any]) -> bool:
    c, v = i.get("check"), i.get("review")
    if i["status"] != "in review" or i.get("blocked") or i.get("accepted") or not c or not v or c.get("head") != reviews.verdict_tip(v):
        return False
    has_ci = c.get("ci") in ("pass", "fail", "none")  # the provider has CI (a result is stored; `none`: no checks on the PR). Local stores ""
    return decide.after_check(c, p.for_task(i).get("merge", "autonomy"), has_ci, i.get("resolving", ""), int(i.get("failure_streak") or 0)) == "owner"


class Need(NamedTuple):
    """One thing that waits for the owner: `key` names it across polls, `line` is what watch prints, `text` what the owner
    decides on as stored (the first line of an accept's summary, as the review description shows it); `task` is "" for
    main, `since` the stamp stored when it began (None for blocked, which stores none)."""

    key: str
    kind: str
    task: str
    since: str | None
    text: str
    line: str


def found(p: Project) -> list[Need]:
    """What needs the owner now, in the order questions, accepts, blocked, main. A parent agent needs him only through its
    open question, which a silent end of its run files (kind `stopped`): between wakeups it has no session expected alive."""
    with registry.view_scope(p):
        d = registry.backlog(p).load()
    out: list[Need] = []
    for q in questions.open_questions(d):
        stopped = q.get("kind") == "stopped"
        line = _line(f"{q['task']} {'stopped without asking the owner' if stopped else 'asks the owner'} ({q['id']}): {q['text']}")
        out.append(Need(f"q:{q['id']}", "stopped" if stopped else "question", q["task"], q.get("asked"), q["text"], line))
    all_tasks = backlog.tasks(d)
    open_tasks = [i for i in all_tasks if i["status"] not in backlog.CLOSED]
    for i in all_tasks:
        if _waits_for_accept(p, i):
            report: backlog.Report = i.get("report") or {}
            summary = report.get("summary", "").strip().splitlines()
            line = _line(f"{i['id']} waits for the owner's accept: {i['title']}")
            out.append(Need(f"accept:{i['id']}", "accept", i["id"], i["check"].get("at"), summary[0] if summary else "", line))
    for i in open_tasks:
        if i.get("blocked"):
            out.append(Need(f"blocked:{i['id']}", "blocked", i["id"], None, i["blocked"], _line(f"{i['id']} blocked: {i['blocked']}")))
    ci = util.read_json(p.state / "ci.json", {})
    if ci.get("result") == "fail":
        out.append(Need(f"ci:{ci.get('since')}", "main", "", ci.get("since"), "tests fail on main", _line(f"main: tests fail (since {ci.get('since')})")))
    return out


def needs(p: Project) -> dict[str, str]:
    """What needs the owner now: key → watch's line, in `found`'s order."""
    return {n.key: n.line for n in found(p)}


def at_start(found: dict[str, str]) -> dict[str, str]:
    """The needs shown at start: the accepts already waiting."""
    return {k: v for k, v in found.items() if k.startswith("accept:")}


def run(p: Project, once: bool) -> None:
    """Prints the accepts already waiting at start, then each need that appears after it (the other needs at start are
    `foreman list`'s). A poll that cannot read foreman's own state is skipped. It makes no platform call, nor reads the
    Board: it shows the snapshot the loop wrote (`registry.view_scope`), and without one it stops with `the loop has not
    read the Board yet`. Under `once`, returns after the first line printed after the start."""
    seen = needs(p)
    for line in at_start(seen).values():
        print(line, flush=True)
    while True:
        pause()
        try:
            now = needs(p)
        except (backlog.BacklogError, ProjectError, OSError, ValueError):  # foreman's own state unreadable this poll
            continue
        for key, line in now.items():
            if key not in seen:
                print(line, flush=True)
                if once:
                    return
        seen = now
