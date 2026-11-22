"""`foreman watch`: one line each time something starts needing the owner. It decides from the backlog, main's CI result
(`state/ci.json`) and Claude Code's session listing, never from the text of events.jsonl."""

import time
from typing import Any

from .. import util
from ..core.project.config import Project, ProjectError
from ..core.review import reviews
from ..core.rules import decide
from ..core.tasks import backlog, questions
from ..providers import registry
from ..providers.runner import NO_ROWS, Rows, ref_of

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


def needs(p: Project, rows: Rows) -> dict[str, str]:
    """What needs the owner now: key → one line, in the order questions, accepts, blocked, main, parents."""
    with registry.view_scope(p):
        d = registry.backlog(p).load()
    found: dict[str, str] = {}
    asked = {q["task"] for q in questions.open_questions(d)}
    for q in questions.open_questions(d):
        found[f"q:{q['id']}"] = _line(f"{q['task']} asks the owner ({q['id']}): {q['text']}")
    open_tasks = [i for i in backlog.tasks(d) if i["status"] not in backlog.CLOSED]
    for i in backlog.tasks(d):
        if _waits_for_accept(p, i):
            found[f"accept:{i['id']}"] = _line(f"{i['id']} waits for the owner's accept: {i['title']}")
    for i in open_tasks:
        if i.get("blocked"):
            found[f"blocked:{i['id']}"] = _line(f"{i['id']} blocked: {i['blocked']}")
    ci = util.read_json(p.state / "ci.json", {})
    if ci.get("result") == "fail":
        found[f"ci:{ci.get('since')}"] = _line(f"main: tests fail (since {ci.get('since')})")
    for i in open_tasks:
        agent: backlog.Agent = i.get("agent") or {}
        ref = ref_of(agent)
        if agent.get("kind") == "parent" and i["id"] not in asked and rows.alive(ref) and not rows.busy(ref):
            found[f"parent:{i['id']}"] = _line(f"{i['id']} parent agent waits for the owner in its chat: {i['title']}")
    return found


def at_start(found: dict[str, str]) -> dict[str, str]:
    """The needs shown at start: the accepts already waiting."""
    return {k: v for k, v in found.items() if k.startswith("accept:")}


def keep_parents(now: dict[str, str], seen: dict[str, str]) -> None:
    """A listing that could not be read says nothing of the parents: `now` keeps the ones `seen` had."""
    now.update({k: v for k, v in seen.items() if k.startswith("parent:")})


def run(p: Project, once: bool, rows: Rows) -> None:
    """Prints the accepts already waiting at start, then each need that appears after it; `rows` is the listing the caller
    read (the other needs at start are `foreman list`'s). A poll that cannot read the listing keeps the parents' entries as they were; one that cannot
    read foreman's own state is skipped. It makes no platform call, nor reads the Board: it shows the snapshot the loop wrote
    (`registry.view_scope`), and without one it stops with `the loop has not read the Board yet`. Under `once`,
    returns after the first line printed after the start."""
    seen = needs(p, rows)
    for line in at_start(seen).values():
        print(line, flush=True)
    while True:
        pause()
        polled = registry.runner(p).rows()
        try:
            now = needs(p, polled or NO_ROWS)
        except (backlog.BacklogError, ProjectError, OSError, ValueError):  # foreman's own state unreadable this poll
            continue
        if polled is None:
            keep_parents(now, seen)
        for key, line in now.items():
            if key not in seen:
                print(line, flush=True)
                if once:
                    return
        seen = now
