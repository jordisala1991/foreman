"""The loop's step «blockers» (before launch): a task whose blockers are all done is released once, and one whose blocker
closed without being done is blocked with a reason, for the owner to decide."""

from . import backlog, blockers
from .config import Project


def run(p: Project) -> None:
    said: list[tuple[str, str]] = []
    with backlog.edit(p) as d:
        tasks = backlog.tasks(d)
        for i in tasks:
            if i["status"] not in ("backlog", "ready") or not i.get("blocked_by") or i.get("blocked") or i.get("released"):
                continue
            if lost := blockers.dead(i, tasks):
                n = i["id"]
                i["blocked"] = f"{', '.join(lost)} closed without being done: fm edit {n} --blocked-by …, then fm unblock {n}, or fm cancel {n}"
                said.append((i["id"], f"blocked: {i['blocked']}"))
            elif not blockers.pending(i, tasks):
                i["released"] = True
                said.append((i["id"], f"unblocked: {', '.join(i['blocked_by'])} done"))
    for tid, text in said:
        backlog.event(p, tid, text)
